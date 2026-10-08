"""Caricamento e hashing della configurazione (config.yaml). Nessun segreto."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class OmniRouteCfg(BaseModel):
    base_url: str = "http://localhost:20128"
    chat_path: str = "/v1/chat/completions"
    timeout_seconds: float = 90.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 30.0
    jitter_seconds: float = 0.5
    retry_status_codes: list[int] = [429, 500, 502, 503, 504]


class ModelsCfg(BaseModel):
    price_action: str = "super-120b"
    options_flow: str = "ultra-550b"
    strategist: str = "ultra-550b"
    risk_manager: str = "super-120b"
    judge: str = "gemma-4-31b-it"
    vision_extractor: str = "super-120b"  # DEVE supportare input immagini


class FallbackCfg(BaseModel):
    price_action: str = "gemma-4-31b-it"
    options_flow: str = "super-120b"
    strategist: str = "super-120b"
    risk_manager: str = "gemma-4-31b-it"
    judge: str = "super-120b"
    vision_extractor: str = "gemma-4-31b-it"


class GexBotCfg(BaseModel):
    base_url: str = "https://api.gex.bot/v2"
    ticker: str = "NQ_NDX"
    category: str = "zero"          # full|gex_full|zero|gex_zero|one|gex_one
    convert: bool = True
    conversion_ticker: str = "NDX"  # ticker sorgente per /futures/conversion
    future: str = "NQ"
    conversion_model: str | None = None  # additive|multiplicative|affine (None = default del server)
    es_ticker: str = "ES_SPX"
    es_conversion_ticker: str = "SPX"
    es_future: str = "ES"
    wall_basis: str = "vol"         # vol|oi: quale major_pos/major_neg usare per call/put wall
    max_age_hours: float = 18.0
    timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base_seconds: float = 1.0
    user_agent: str = "ofo-council/1.0"


class IntakeCfg(BaseModel):
    inbox_dir: str = "data/inbox"
    max_file_mb: float = 10.0
    plausible_min: float = 1000.0
    plausible_max: float = 100000.0
    es_plausible_min: float = 1000.0
    es_plausible_max: float = 20000.0
    vision_enabled: bool = True
    max_pdf_pages: int = 20


class DebateCfg(BaseModel):
    max_scenarios: int = 2
    parallel_analysts: bool = True
    judge_min_score: int = 60


class RiskCfg(BaseModel):
    minimum_rr: float = 1.5
    max_daily_r: float = 3.0
    risk_per_scenario_r: float = 1.0


class RegimeCfg(BaseModel):
    high_vol_ratio: float = 1.6
    low_vol_ratio: float = 0.6
    trend_displacement_atr: float = 0.35
    overnight_range_hv_atr: float = 1.0


class IntermarketCfg(BaseModel):
    enabled: bool = True
    es_profile_bin_size: float = 1.0     # punti ES per bin del volume profile
    direction_threshold_pct: float = 0.10  # |rendimento overnight| minimo per dire "su/giù"
    min_correlation: float = 0.60        # sotto: NQ/ES decorrelati (avviso)
    corr_bars: int = 1200                # barre 5m usate per correlazione/beta
    divergence_bps: float = 15.0         # differenza di rendimento overnight NQ-ES oltre cui e' "divergenza"


class FeaturesCfg(BaseModel):
    timeframe_minutes: int = 5
    atr_period: int = 14
    value_area_pct: float = 0.70
    profile_bin_size: float = 5.0
    hvn_ratio: float = 1.3
    lvn_ratio: float = 0.5
    rth_start: str = "09:30"
    rth_end: str = "16:00"
    min_sessions: int = 3
    tick_size: float = 0.25
    regime: RegimeCfg = Field(default_factory=RegimeCfg)
    intermarket: IntermarketCfg = Field(default_factory=IntermarketCfg)


class GroundingCfg(BaseModel):
    tolerance_points_min: float = 2.0
    tolerance_atr_mult: float = 0.5
    rr_tolerance: float = 0.3


class PathsCfg(BaseModel):
    data_dir: str = "data"
    prompts_dir: str = "prompts"
    reports_dir: str = "reports"
    transcripts_dir: str = "transcripts"
    logs_dir: str = "logs"
    journal: str = "journal.csv"


class UiCfg(BaseModel):
    bubble_seconds: float = 6.0
    event_seconds: float = 1.6
    default_speed: float = 1.0
    speeds: list[float] = [0.5, 1.0, 2.0, 4.0]


class AppConfig(BaseModel):
    omniroute: OmniRouteCfg = Field(default_factory=OmniRouteCfg)
    models: ModelsCfg = Field(default_factory=ModelsCfg)
    fallback_models: FallbackCfg = Field(default_factory=FallbackCfg)
    debate: DebateCfg = Field(default_factory=DebateCfg)
    risk: RiskCfg = Field(default_factory=RiskCfg)
    features: FeaturesCfg = Field(default_factory=FeaturesCfg)
    grounding: GroundingCfg = Field(default_factory=GroundingCfg)
    gexbot: GexBotCfg = Field(default_factory=GexBotCfg)
    intake: IntakeCfg = Field(default_factory=IntakeCfg)
    paths: PathsCfg = Field(default_factory=PathsCfg)
    ui: UiCfg = Field(default_factory=UiCfg)
    root: Path = Field(default=Path("."), exclude=True)

    def path(self, name: str) -> Path:
        return self.root / getattr(self.paths, name)

    def model_for(self, agent: str) -> str:
        return getattr(self.models, agent)

    def fallback_for(self, agent: str) -> str | None:
        fb = getattr(self.fallback_models, agent, None)
        return None if fb == self.model_for(agent) else fb


def load_config(path: str | Path | None = None, root: str | Path | None = None) -> AppConfig:
    """Legge config.yaml (default: root del progetto); i path sono relativi a `root`."""
    project_root = Path(__file__).resolve().parent.parent
    cfg_path = Path(path) if path else (Path(root) if root else project_root) / "config.yaml"
    # senza `root` esplicito i percorsi sono relativi alla cartella del config.yaml usato
    base = Path(root) if root else (cfg_path.parent if path else project_root)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    cfg = AppConfig(**(raw or {}))
    return cfg.model_copy(update={"root": base})


def config_hash(cfg: AppConfig) -> str:
    blob = json.dumps(cfg.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
