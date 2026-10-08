"""LLM simulato DETERMINISTICO per test e modalita' --mock.

Non e' un modello: e' un insieme di regole che leggono il payload strutturato e producono
JSON valido (o difettoso, per simulare guasti). Ogni run in mock e' marcato MOCK nei report.
"""
from __future__ import annotations

import json
from typing import Any

from .agents import LLMError, LLMResponse

SCENARIOS: dict[str, dict[str, Any]] = {
    "ok": {},
    "malformed_json_extracted": {"faults": {"price_action": ["json_in_prose"]}},
    "malformed_json_recovers": {"faults": {"price_action": ["malformed_json", "ok"]}},
    "malformed_json_fails": {"faults": {"price_action": ["malformed_json"] * 4}},
    "invalid_schema_fails": {"faults": {"options_flow": ["invalid_schema"] * 4}},
    "timeout_recovers": {"faults": {"options_flow": ["timeout", "ok"]}},
    "rate_limited": {"faults": {"price_action": ["http_429", "http_429", "ok"]}},
    "server_error_fallback": {"fail_models": {"options_flow": ["ultra-550b"]}},
    "price_action_down": {"faults": {"price_action": ["http_500"] * 40}},
    "options_flow_down": {"faults": {"options_flow": ["http_500"] * 40}},
    "strategist_down": {"faults": {"strategist": ["http_502"] * 40}},
    "risk_down": {"faults": {"risk_manager": ["http_503"] * 40}},
    "judge_down": {"faults": {"judge": ["http_504"] * 40}},
    "strategist_disagreement": {"bias_override": {"price_action": "LONG", "options_flow": "SHORT"}},
    "risk_veto": {"poor_rr": "all"},
    "risk_veto_partial": {"poor_rr": True},
    "judge_rejection": {"judge_reject": True},
    "hallucinated_level": {"invent_level": True},
    "unsupported_evidence": {"fake_evidence": True},
    "wrong_rr": {"wrong_rr": True},
}


def _tick(x: float) -> float:
    return round(round(x * 4) / 4, 2)


def _levels(evidence: list[dict[str, Any]]) -> list[tuple[str, float, str]]:
    seen: set[float] = set()
    out: list[tuple[str, float, str]] = []
    for e in evidence:
        if e["kind"] != "price" or e["key"] in ("spot", "last_close"):
            continue
        for v in (e["value"] if isinstance(e["value"], list) else [e["value"]]):
            k = _tick(float(v))
            if k not in seen:
                seen.add(k)
                out.append((e["label"], k, e["id"]))
    return out


class _Ev:
    def __init__(self, evidence: list[dict[str, Any]]) -> None:
        self.by_key = {e["key"]: e for e in evidence}

    def id(self, key: str) -> list[str]:
        return [self.by_key[key]["id"]] if key in self.by_key else []

    def val(self, key: str) -> Any:
        return self.by_key[key]["value"] if key in self.by_key else None


def _score_bias(f: dict[str, Any]) -> tuple[str, int]:
    spot, on, prev = f["spot"], f["overnight"], f["prev_session"]
    score = (int(spot > on["vwap"]) + int(spot > prev["poc"]) + int(spot > prev["close"])
             + int(on["close"] > on["open"]))
    return ("LONG" if score >= 3 else "SHORT" if score <= 1 else "NEUTRAL"), score


# ------------------------------------------------------------------ agenti
def _price_action(p: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    f, ev = p["features"], _Ev(p["evidence"])
    bias, score = _score_bias(f)
    bias = cfg.get("bias_override", {}).get("price_action", bias)
    on, prev, reg = f["overnight"], f["prev_session"], f["regime"]
    side = "sopra" if f["spot"] > on["vwap"] else "sotto"
    facts = [
        {"text": f"Spot a {f['spot']}, {side} il VWAP overnight ({on['vwap']}).", "evidence_ids": ev.id("spot") + ev.id("vwap_overnight")},
        {"text": f"Overnight: massimo {on['high']}, minimo {on['low']}.", "evidence_ids": ev.id("overnight_high") + ev.id("overnight_low")},
        {"text": f"Sessione precedente: massimo {prev['high']}, minimo {prev['low']}, close {prev['close']}.", "evidence_ids": ev.id("prev_high") + ev.id("prev_low") + ev.id("prev_close")},
        {"text": f"POC/VAH/VAL sessione precedente: {prev['poc']}/{prev['vah']}/{prev['val']}.", "evidence_ids": ev.id("prev_poc") + ev.id("prev_vah") + ev.id("prev_val")},
        {"text": f"Regime calcolato: {reg['state']}.", "evidence_ids": ev.id("regime")},
    ]
    interp = [{"text": f"Struttura {'rialzista' if bias == 'LONG' else 'ribassista' if bias == 'SHORT' else 'bilanciata'} "
                       f"(punteggio strutturale {score}/4): ipotesi, non fatto.",
               "evidence_ids": ev.id("vwap_overnight") + ev.id("prev_poc")}]
    names = [("VWAP overnight", "vwap_overnight"), ("Max sessione prec.", "prev_high"), ("Min sessione prec.", "prev_low"),
             ("Max overnight", "overnight_high"), ("Min overnight", "overnight_low"), ("POC prec.", "prev_poc"),
             ("VAH prec.", "prev_vah"), ("VAL prec.", "prev_val")]
    key_levels = [{"name": n, "value": ev.val(k), "evidence_ids": ev.id(k)} for n, k in names if ev.val(k) is not None]
    conf = 45 if bias == "NEUTRAL" else min(85, 55 + 8 * abs(score - 2))
    msg = {"LONG": f"Struttura rialzista finché lo spot mantiene il VWAP overnight ({on['vwap']}).",
           "SHORT": f"Struttura ribassista finché lo spot resta sotto il VWAP overnight ({on['vwap']}).",
           "NEUTRAL": "Struttura senza direzionalità chiara: serve accettazione oltre i livelli chiave."}[bias]
    return {"agent": "price_action", "status": "OK", "bias": bias, "confidence": conf, "facts": facts,
            "interpretations": interp, "key_levels": key_levels, "scenarios": [], "warnings": [],
            "messages": [{"addressed_to": "strategist", "message_type": "ANALYSIS", "message": msg,
                          "evidence_ids": ev.id("vwap_overnight")}],
            "reasoning_summary": f"Bias {bias} dalla posizione dello spot rispetto a VWAP, POC e close precedenti."}


def _options_flow(p: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    f, ev = p["features"], _Ev(p["evidence"])
    o = f["options"]
    vals = [o["gamma_flip"], o["call_wall"], o["put_wall"]]
    if not o["available"]:
        return {"agent": "options_flow", "status": "INSUFFICIENT_DATA", "bias": "UNKNOWN", "confidence": 0,
                "facts": [], "interpretations": [], "key_levels": [], "scenarios": [],
                "warnings": ["Dati opzioni mancanti: nessuna analisi di gamma/dealer possibile. Nulla è stato inventato."],
                "messages": [{"addressed_to": "strategist", "message_type": "WARNING",
                              "message": "Non ho dati opzioni: la struttura gamma non è valutabile.", "evidence_ids": []}],
                "reasoning_summary": "Dati opzioni non disponibili."}
    spot = f["spot"]
    facts, kl = [], []
    for label, key, v in (("Gamma flip", "options.gamma_flip", vals[0]), ("Call wall", "options.call_wall", vals[1]),
                          ("Put wall", "options.put_wall", vals[2])):
        if v is not None:
            facts.append({"text": f"{label} a {v}.", "evidence_ids": ev.id(key)})
            kl.append({"name": label, "value": v, "evidence_ids": ev.id(key)})
    warnings: list[str] = []
    if any(v is None for v in vals):
        warnings.append("Struttura opzioni incompleta: bias limitato.")
        bias, conf = "NEUTRAL", 30
    else:
        up, dn = vals[1] - spot, spot - vals[2]
        if spot >= vals[0]:
            bias = "LONG" if up > dn else "NEUTRAL"
        else:
            bias = "SHORT" if dn > up else "NEUTRAL"
        conf = 50 if bias == "NEUTRAL" else 68
        facts.append({"text": f"Spot a {spot - vals[0]:+.2f} punti dal gamma flip; "
                              f"distanza dalla call wall {up:.2f}, dalla put wall {dn:.2f}.",
                      "evidence_ids": ev.id("spot") + ev.id("options.gamma_flip")})
    bias = cfg.get("bias_override", {}).get("options_flow", bias)
    proxy = o.get("gamma_regime_proxy")
    interp = [{"text": f"Proxy regime gamma: {proxy} (stima da spot vs gamma flip, non GEX completo).",
               "evidence_ids": ev.id("gamma_regime_proxy")}] if proxy else []
    msg = {"LONG": "Struttura opzioni coerente con un contesto rialzista: spazio verso la call wall.",
           "SHORT": "Struttura opzioni coerente con un contesto ribassista: spazio verso la put wall.",
           "NEUTRAL": "Struttura opzioni senza vantaggio direzionale chiaro."}[bias if bias != "UNKNOWN" else "NEUTRAL"]
    return {"agent": "options_flow", "status": "OK", "bias": bias, "confidence": conf, "facts": facts,
            "interpretations": interp, "key_levels": kl, "scenarios": [], "warnings": warnings,
            "messages": [{"addressed_to": "strategist", "message_type": "ANALYSIS", "message": msg,
                          "evidence_ids": ev.id("options.gamma_flip")}],
            "reasoning_summary": f"Bias {bias} da gamma flip e distanza dalle wall."}


def _build_scenario(sid: str, direction: str, ref: float, ref_ids: list[str], levels: list[tuple[str, float, str]],
                    atr5: float, tick: float, setup: str, conf: int, min_rr: float, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Costruisce uno scenario ancorato ai livelli. None se non definibile oggettivamente."""
    band = max(0.25 * atr5, tick)
    lo, hi = _tick(ref - band), _tick(ref + band)
    sign = 1 if direction == "LONG" else -1
    entry_worst = hi if sign == 1 else lo
    stops = sorted([l for l in levels if sign * (entry_worst - l[1]) >= atr5 - 1e-9],
                   key=lambda l: sign * (entry_worst - l[1]))
    if not stops:
        return None
    stop_lvl = stops[0]
    inv = _tick(stop_lvl[1] - sign * 0.25 * atr5)
    risk = abs(entry_worst - inv)
    if risk <= 0:
        return None
    ahead = sorted([l for l in levels if sign * (l[1] - entry_worst) > 0], key=lambda l: sign * (l[1] - entry_worst))
    forced_t1: float | None = None
    if cfg.get("poor_rr") == "all":
        forced_t1 = _tick(entry_worst + sign * 0.5 * risk)  # R:R 0.5, volutamente insufficiente
        good = ahead[:1] or [("forzato", forced_t1, "")]
    elif cfg.get("poor_rr"):
        good = ahead[:1]
    else:
        good = [l for l in ahead if abs(l[1] - entry_worst) >= min_rr * risk]
    if not good:
        return None
    t1 = good[0]
    t2 = None if (forced_t1 is not None or cfg.get("invent_level")) else next((l for l in ahead if sign * (l[1] - t1[1]) > 0), None)
    t1v = forced_t1 if forced_t1 is not None else t1[1]
    ids = sorted(set(ref_ids + [stop_lvl[2], t1[2]] + ([t2[2]] if t2 else [])) - {""})
    if cfg.get("invent_level"):
        t1v = _tick(entry_worst + sign * (3.0 * risk + 7.13))
    rr = round(abs(t1v - entry_worst) / risk, 2)
    if cfg.get("wrong_rr"):
        rr = round(rr + 1.0, 2)
    if cfg.get("fake_evidence"):
        ids.append("E999")
    word = "sopra" if sign == 1 else "sotto"
    return {
        "id": sid, "direction": direction, "setup_type": setup, "entry_zone": [lo, hi],
        "invalidation": inv, "target_1": t1v, "target_2": t2[1] if t2 else None, "expected_rr": rr,
        "confirmation_required": [f"Accettazione {word} {ref} con volume in aumento",
                                  "Conferma della struttura su timeframe 5m dopo l'apertura cash"],
        "thesis": f"SE il prezzo mostra accettazione {word} {ref} ALLORA scenario {direction} verso {t1v}.",
        "failure_condition": f"Chiusura 5m {'sotto' if sign == 1 else 'sopra'} {inv}: tesi invalidata.",
        "confidence": conf, "evidence_ids": ids,
    }


def _strategist(p: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    f, ev = p["features"], _Ev(p["evidence"])
    up = p["upstream"]
    pa, of = up.get("price_action"), up.get("options_flow")

    def bias(o: dict[str, Any] | None) -> tuple[str, int]:
        if not o or o["status"] != "OK":
            return "UNKNOWN", 0
        return o["bias"], o["confidence"]

    (pb, pc), (ob, oc) = bias(pa), bias(of)
    rules = p["risk_rules"]
    directional = {"LONG", "SHORT"}
    base = {"agent": "strategist", "status": "OK", "facts": [], "interpretations": [], "key_levels": [],
            "warnings": [], "scenarios": [], "no_trade": True, "bias": "NEUTRAL", "confidence": 0}

    def no_trade(reason: str, mtype: str = "DECISION") -> dict[str, Any]:
        return {**base, "no_trade_reason": reason, "confidence": 20, "reasoning_summary": reason,
                "messages": [{"addressed_to": "all", "message_type": mtype, "message": f"NO TRADE: {reason}",
                              "evidence_ids": []}]}

    if pb in directional and ob in directional and pb != ob:
        out = no_trade(f"PA ({pb}) e Options Flow ({ob}) divergono senza risoluzione: nessuno scenario forzato.", "DISAGREEMENT")
        out["messages"].insert(0, {"addressed_to": "options_flow", "message_type": "CHALLENGE",
                                   "message": f"La tua lettura {ob} non è compatibile con la struttura {pb} di Price Action.",
                                   "evidence_ids": ev.id("options.gamma_flip")})
        return out
    directions = [b for b in (pb, ob) if b in directional]
    if not directions:
        return no_trade("Nessuna direzionalità supportata dall'evidenza: attendere conferma.")
    d = directions[0]
    both = pb == ob == d
    conf = min(85, (pc + oc) // 2) if both else min(45, max(pc, oc))
    atr5 = f["atr"]["atr_5m"] or 0
    tick = 0.25
    levels = _levels(p["evidence"])
    spot = f["spot"]
    sign = 1 if d == "LONG" else -1
    scs = []
    s1 = _build_scenario("S1", d, spot, ev.id("spot"), levels, atr5, tick,
                         "Accettazione sul livello corrente", conf, rules["minimum_rr"], cfg)
    if s1:
        scs.append(s1)
    pull = sorted([l for l in levels if sign * (spot - l[1]) >= atr5], key=lambda l: sign * (spot - l[1]))
    if pull and len(scs) < p.get("max_scenarios", 2):
        s2 = _build_scenario("S2", d, pull[0][1], [pull[0][2]], levels, atr5, tick,
                             f"Pullback su {pull[0][0]}", max(conf - 10, 20), rules["minimum_rr"], cfg)
        if s2:
            scs.append(s2)
    if not scs:
        return no_trade("Ingresso, invalidazione o target non definibili oggettivamente dai livelli disponibili.")
    warnings = []
    if not both:
        warnings.append("Convergenza parziale: una sola fonte supporta la direzione; confidenza ridotta.")
    msgs = []
    if pb == d:
        msgs.append({"addressed_to": "price_action", "message_type": "AGREEMENT",
                     "message": f"Adotto la struttura {d} di Price Action come base degli scenari.", "evidence_ids": ev.id("vwap_overnight")})
    if ob == d:
        msgs.append({"addressed_to": "options_flow", "message_type": "AGREEMENT",
                     "message": "La struttura opzioni è coerente: convergenza confermata.", "evidence_ids": ev.id("options.gamma_flip")})
    elif ob in ("UNKNOWN", "NEUTRAL"):
        msgs.append({"addressed_to": "options_flow", "message_type": "QUESTION",
                     "message": "Senza una lettura direzionale delle opzioni riduco la confidenza.", "evidence_ids": []})
    msgs.append({"addressed_to": "risk_manager", "message_type": "DECISION",
                 "message": f"{len(scs)} scenario/i {d} condizionali da valutare.", "evidence_ids": scs[0]["evidence_ids"][:3]})
    return {**base, "bias": d, "confidence": conf, "scenarios": scs, "no_trade": False, "no_trade_reason": "",
            "warnings": warnings, "messages": msgs,
            "interpretations": [{"text": "Scenari costruiti su livelli presenti nei dati; nessuna previsione.",
                                 "evidence_ids": ev.id("spot")}],
            "reasoning_summary": f"Convergenza {'piena' if both else 'parziale'} verso {d}; {len(scs)} scenario/i condizionale/i."}


def _risk(p: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    rules = p["risk_rules"]
    pre = {x["scenario_id"]: x for x in p["risk_engine_precheck"]}
    assess, vetoed = [], []
    for s in p["scenarios"]:
        entry = s["entry_zone"][1] if s["direction"] == "LONG" else s["entry_zone"][0]
        risk = abs(entry - s["invalidation"])
        rr = round(abs(s["target_1"] - entry) / risk, 2) if risk else None
        reasons = []
        if rr is None or rr < rules["minimum_rr"]:
            verdict = "VETO"
            reasons.append(f"R:R {rr} sotto il minimo {rules['minimum_rr']}")
            vetoed.append(s["id"])
        elif s["confidence"] < 50 or pre.get(s["id"], {}).get("verdict") == "APPROVED_WITH_CAUTION":
            verdict = "APPROVED_WITH_CAUTION"
            reasons.append(f"R:R {rr} valido ma confidenza/convergenza non piena")
        else:
            verdict = "APPROVED"
            reasons.append(f"R:R {rr} >= {rules['minimum_rr']}; invalidazione oggettiva")
        assess.append({"scenario_id": s["id"], "verdict": verdict, "computed_rr": rr, "reasons": reasons,
                       "evidence_ids": s["evidence_ids"][:3]})
    if vetoed:
        msg = {"addressed_to": "strategist", "message_type": "VETO",
               "message": f"R:R insufficiente su {', '.join(vetoed)}: VETO. Non scavalcabile.", "evidence_ids": []}
    else:
        msg = {"addressed_to": "strategist", "message_type": "AGREEMENT",
               "message": "Rischio entro i limiti: R:R valido e invalidazione oggettiva.", "evidence_ids": []}
    return {"agent": "risk_manager", "status": "OK", "confidence": 75, "assessments": assess,
            "warnings": [], "messages": [msg],
            "reasoning_summary": f"{len(vetoed)} veto su {len(assess)} scenari." if assess else "Nessuno scenario."}


def _judge(p: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    det = p["deterministic_findings"]
    pen = {"minor": 3, "major": 10, "critical": 25}
    score = 92 - sum(pen[i["severity"]] for i in det)
    issues = [dict(i) for i in det]
    if cfg.get("judge_reject"):
        score = 25
        issues.append({"type": "contradictory_claim", "severity": "major", "target_agent": "strategist",
                       "scenario_id": None, "evidence_ids": [],
                       "description": "La catena di ragionamento non è coerente con le evidenze disponibili."})
    score = max(0, min(100, score))
    verdict = "VALID" if score >= 80 else "VALID_WITH_ISSUES" if score >= 60 else "REJECTED"
    text = {"VALID": "La catena è coerente e ancorata ai dati.",
            "VALID_WITH_ISSUES": "Catena utilizzabile ma con problemi di ancoraggio da rivedere.",
            "REJECTED": "Catena non affidabile: serve revisione umana."}[verdict]
    return {"agent": "judge", "status": "OK", "confidence": 70, "score": score, "verdict": verdict,
            "issues": issues, "warnings": [],
            "messages": [{"addressed_to": "all", "message_type": "DECISION",
                          "message": f"{text} Punteggio {score}/100.", "evidence_ids": []}],
            "reasoning_summary": text}


_HANDLERS = {"price_action": _price_action, "options_flow": _options_flow, "strategist": _strategist,
             "risk_manager": _risk, "judge": _judge}


class MockLLMClient:
    """Implementa LLMClient. `scenario` seleziona guasti/comportamenti da SCENARIOS."""

    def __init__(self, scenario: str = "ok") -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario mock sconosciuto: {scenario} (validi: {', '.join(SCENARIOS)})")
        self.scenario = scenario
        self.cfg = SCENARIOS[scenario]
        self._counts: dict[str, int] = {}
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, agent: str, model: str, messages: list[dict[str, str]], timeout: float) -> LLMResponse:
        self.calls.append((agent, model))
        n = self._counts.get(agent, 0)
        self._counts[agent] = n + 1
        if model in self.cfg.get("fail_models", {}).get(agent, []):
            raise LLMError("HTTP 500", status_code=500, kind="http", retryable=True)
        faults = self.cfg.get("faults", {}).get(agent, [])
        fault = faults[n] if n < len(faults) else "ok"
        if fault.startswith("http_"):
            code = int(fault.split("_")[1])
            raise LLMError(f"HTTP {code}", status_code=code, kind="http", retryable=code in (429, 500, 502, 503, 504))
        if fault == "timeout":
            raise LLMError(f"timeout dopo {timeout}s", kind="timeout", retryable=True)
        payload = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        payload["max_scenarios"] = payload.get("max_scenarios", 2)
        body = _HANDLERS[agent](payload, self.cfg)
        text = json.dumps(body, ensure_ascii=False)
        if fault == "malformed_json":
            text = text[: len(text) // 2]
        elif fault == "json_in_prose":
            text = f"Ecco l'analisi richiesta:\n```json\n{text}\n```\nSpero sia utile."
        elif fault == "invalid_schema":
            text = json.dumps({"agent": agent, "status": "OK", "confidence": "alta"})
        return LLMResponse(content=text, model=model)
