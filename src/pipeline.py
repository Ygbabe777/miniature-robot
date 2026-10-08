"""Orchestrazione end-to-end: dati -> feature -> dibattito -> report -> journal."""
from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .agents import AgentRunner, LLMClient, OmniRouteClient, load_prompt
from .clock import SystemClock
from .config import AppConfig, config_hash, load_config
from .council_events import EventType
from .data_loader import prepare_market_data
from .debate_engine import DebateEngine, DebateInputs, DebateResult
from .event_bus import EventBus
from .features import build_evidence, compute_features, cross_check
from .journal import ensure_journal, record_ai_run
from .logging_utils import RunLogger
from .mock_llm import MockLLMClient
from .report_generator import build_report, build_transcript, write_artifacts
from .schemas import AGENT_IDS, DataQuality, FinalStatus
from .security import get_api_key, redact


class ConfigurationError(Exception):
    """Configurazione non utilizzabile (es. chiave API mancante in modalita' reale)."""


@dataclass
class PipelineOptions:
    date: str
    mock: bool = False
    mock_scenario: str = "ok"
    cfg: AppConfig | None = None
    levels_path: Path | None = None
    ohlcv_path: Path | None = None
    es_ohlcv_path: Path | None = None
    client: LLMClient | None = None
    clock: Any = None
    rng: random.Random | None = None
    sleep: Callable[[float], None] | None = None
    run_id: str | None = None
    progress: Callable[[int, int, str, str, int], None] | None = None
    write_files: bool = True
    update_journal: bool = True
    on_event: Callable[[Any], None] | None = None


@dataclass
class PipelineResult:
    run_id: str
    report: dict[str, Any]
    paths: dict[str, Path] = field(default_factory=dict)
    status: FinalStatus = FinalStatus.REVIEW_REQUIRED
    events: list[Any] = field(default_factory=list)


def make_run_id(date: str, clock: Any, rng: random.Random) -> str:
    return f"{date}_{clock.now().strftime('%H-%M-%S')}_{''.join(rng.choices('0123456789abcdef', k=5))}"


def run_pipeline(opts: PipelineOptions) -> PipelineResult:
    cfg = opts.cfg or load_config()
    clock = opts.clock or SystemClock()
    rng = opts.rng or random.Random()
    started = clock.now()
    run_id = opts.run_id or make_run_id(opts.date, clock, rng)
    mode = "MOCK" if opts.mock else "REAL"
    log = RunLogger("pipeline", run_id)

    if opts.client is not None:
        client = opts.client
    elif opts.mock:
        client = MockLLMClient(opts.mock_scenario)
    else:
        key = get_api_key(cfg.root)
        if not key:
            raise ConfigurationError("OMNIROUTE_API_KEY non trovata: impostarla in .env (vedi .env.example) "
                                     "oppure usare --mock.")
        client = OmniRouteClient(cfg.omniroute, key)
    sleep = opts.sleep or ((lambda s: None) if opts.mock else time.sleep)
    prompts_dir = cfg.path("prompts_dir")
    runner = AgentRunner(cfg, client, prompts_dir, clock=clock, sleep=sleep, rng=rng)
    bus = EventBus(run_id, clock)
    if opts.on_event:
        bus.subscribe(opts.on_event)
    progress = opts.progress or (lambda *a: None)
    stages: dict[str, dict[str, Any]] = {}

    def timed_stage(name: str, step: int, label: str, fn: Callable[[], Any]) -> Any:
        t0 = clock.monotonic()
        try:
            out = fn()
            status, err = "OK", None
            return out
        except Exception as exc:  # noqa: BLE001 - riportata nello stage, poi rilanciata
            status, err = "FAILED", redact(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            ms = int((clock.monotonic() - t0) * 1000)
            stages[name] = {"status": status, "duration_ms": ms, "error": err, "output_validation": "n/a"}
            progress(step, 6, label, status, ms)

    levels_path = opts.levels_path or cfg.path("data_dir") / "levels.json"
    ohlcv_path = opts.ohlcv_path or cfg.path("data_dir") / "ohlcv.csv"

    bus.emit(EventType.PIPELINE_STARTED, ui_action="WAKE_ALL", message=f"Avvio analisi NQ del {opts.date}",
             data={"mode": mode, "date": opts.date})

    # [1/6] validazione dati
    es_path = opts.es_ohlcv_path or cfg.path("data_dir") / "ohlcv_es.csv"
    md = timed_stage("data_validation", 1, "Data validation",
                     lambda: prepare_market_data(levels_path, ohlcv_path, opts.date, cfg.features, es_path))
    issues = [i.to_dict() for i in md.issues]
    bus.emit(EventType.DATA_VALIDATED, ui_action="MONITORS_ON",
             message=f"Qualità dati {md.quality.value}: {len(md.errors)} errori, {len(md.warnings)} avvisi",
             data={"quality": md.quality.value, "errors": len(md.errors), "warnings": len(md.warnings)})

    features, evidence, warnings = None, [], [i.message for i in md.warnings]
    quality = md.quality
    debate: DebateResult | None = None

    prompt_versions = {a: load_prompt(prompts_dir, a).version for a in AGENT_IDS}
    run_meta: dict[str, Any] = {
        "run_id": run_id, "date": opts.date, "mode": mode,
        "mock_scenario": opts.mock_scenario if opts.mock else None,
        "data_hash": md.data_hash, "config_hash": config_hash(cfg), "prompt_versions": prompt_versions,
        "models": {a: cfg.model_for(a) for a in AGENT_IDS},
        "fallback_models": {a: cfg.fallback_for(a) for a in AGENT_IDS},
        "started_at": started.isoformat(), "app_version": __version__,
    }

    if quality == DataQuality.RED:
        for a in ("features", "price_action", "options_flow"):
            progress({"features": 2, "price_action": 3, "options_flow": 4}[a], 6, a, "SKIPPED", 0)
        progress(5, 6, "Strategist + Risk + Judge", "SKIPPED", 0)
        from .council_decision import CouncilDecision
        decision = CouncilDecision(FinalStatus.DATA_ERROR,
                                   ["Qualità dati ROSSA: " + "; ".join(i.message for i in md.errors)])
        bus.emit(EventType.FINAL_DECISION, ui_action="FINAL", message="Decisione del Consiglio: DATA_ERROR",
                 data={"status": "DATA_ERROR", "reasons": decision.reasons})
    else:
        def feats() -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
            f = compute_features(md, cfg.features)
            return f, build_evidence(md, f), cross_check(md, f, cfg.features)
        features, evidence, xwarn = timed_stage("features", 2, "Feature calculation", feats)
        warnings += xwarn + features.get("es_warnings", [])
        xwarn = xwarn + features.get("es_warnings", [])
        if xwarn and quality == DataQuality.GREEN:
            quality = DataQuality.YELLOW
        bus.emit(EventType.FEATURES_COMPUTED, ui_action="MONITORS_ON",
                 message=f"Regime: {features['regime']['state']}",
                 data={"regime": features["regime"]["state"], "evidence": len(evidence)})

        stage_cb_state: dict[str, tuple[str, int]] = {}

        def on_stage(name: str, status: str, ms: int, detail: str) -> None:
            stage_cb_state[name] = (status, ms)
            if name == "price_action":
                progress(3, 6, "Price Action", status, ms)
            elif name == "options_flow":
                progress(4, 6, "Options Flow", status, ms)

        engine = DebateEngine(cfg, runner, bus, clock, parallel=cfg.debate.parallel_analysts and not opts.mock,
                              on_stage=on_stage)
        t0 = clock.monotonic()
        debate = engine.run(DebateInputs(run_id, opts.date, mode, features, evidence, quality, warnings))
        total = int((clock.monotonic() - t0) * 1000)
        late = {k: v for k, v in debate.stages.items() if k in ("strategist", "risk_manager", "judge")}
        bad = [k for k, v in late.items() if v["status"] == "FAILED"]
        progress(5, 6, "Strategist + Risk + Judge", "FAILED" if bad else "OK", sum(v["duration_ms"] for v in late.values()) or total)
        stages.update(debate.stages)
        decision = debate.decision  # type: ignore[assignment]
        assert decision is not None

    bus.emit(EventType.REPORT_GENERATED, ui_action="RETURN_TO_DESKS", message="Report generato")
    finished = clock.now()
    run_meta["finished_at"] = finished.isoformat()
    ev_dicts = [e.model_dump(mode="json") for e in bus.events]
    prov = (md.levels.model_extra or {}).get("provenance", {}) if md.levels else {}
    report = build_report(run_meta=run_meta, provenance=prov, quality=quality.value, data_issues=issues, features=features,
                          evidence=evidence, warnings=warnings, debate=debate, decision=decision.to_dict(),
                          events=ev_dicts, stages=stages, max_daily_r=cfg.risk.max_daily_r)
    transcript = build_transcript(run_meta=run_meta, debate=debate, events=ev_dicts, stages=stages)

    paths: dict[str, Path] = {}
    if opts.write_files:
        t0 = clock.monotonic()
        paths = write_artifacts(report, transcript, cfg.path("reports_dir"), cfg.path("transcripts_dir"))
        if opts.update_journal:
            jp = cfg.path("journal")
            ensure_journal(jp)
            record_ai_run(jp, report)
            paths["journal"] = jp
        progress(6, 6, "Report generation", "OK", int((clock.monotonic() - t0) * 1000))
    else:
        progress(6, 6, "Report generation", "OK", 0)
    log.info("run_complete", f"stato {decision.status.value}")
    return PipelineResult(run_id=run_id, report=report, paths=paths, status=decision.status, events=bus.events)
