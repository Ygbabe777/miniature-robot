"""Two different jobs for a strategy, judged separately from today:

  * PASS THE ACCOUNT (evaluation): is it worth trading to reach the profit target quickly? Decided by the deterministic Judge's own
    challenge checks (deflated Sharpe, pass rate within the goal window, bust risk) on the unseen block. Reported for an AGGRESSIVE
    profile (more contracts: faster, more busts) and a PRUDENT one: chance to pass, mean / median days, chance to bust.
  * GET PAYOUTS (funded account): is it worth keeping alive to collect payouts? Decided by the prop-farming review (first-payout gate,
    net present farming value with its 95% lower bound, stress, compliance).

A strategy can be good at one and bad at the other (a fast, risky one passes evaluations and then dies funded). Nothing here relaxes a
gate: it only reads the two verdicts that already exist and says which job each one qualifies for."""
from __future__ import annotations

PASS, PAYOUT = "PASS_ACCOUNT", "PAYOUT"


def _judge_status(rep: dict) -> str | None:
    lk = rep.get("locked") or {}
    return lk.get("judge_status") or lk.get("status")


def classify_roles(rep: dict) -> dict:
    lk = rep.get("locked") or {}
    fr = rep.get("farming") or {}
    js = _judge_status(rep)
    checked = "checks" in lk                                  # the Judge really looked at the unseen block
    pass_ok = bool(checked and js == "APPROVED")
    pay_ok = bool(fr and fr.get("verdict") == "APPROVED" and lk.get("status") == "APPROVED")      # a downgrade (e.g. not reproducible) cancels it
    insufficient = bool(js == "INSUFFICIENT_EVIDENCE" or (fr and fr.get("verdict") == "INSUFFICIENT_EVIDENCE"))
    if pass_ok and pay_ok:
        label = "BOTH"
    elif pass_ok:
        label = PASS
    elif pay_ok:
        label = PAYOUT
    elif insufficient:
        label = "UNDECIDED"
    else:
        label = "NONE"
    failed_pass = [k for k, v in (lk.get("checks") or {}).items() if not v]
    tm = (lk.get("timing") or rep.get("timing") or {})
    prof = {a: (v.get("profiles") or {}) for a, v in (tm.get("accounts") or {}).items()}
    sample = ((lk.get("mc_sample")) or {})
    return {"label": label, "pass_account": {"valid": pass_ok, "failed_checks": failed_pass, "judge_status": js, "profiles": prof,
                                              "basis": tm.get("basis"), "in_sample": tm.get("in_sample")},
            "payout": {"valid": pay_ok, "verdict": fr.get("verdict") if fr else None,
                       "first_failed": (fr or {}).get("first_failed"), "best_ruleset": (fr or {}).get("best_ruleset")},
            "sample": sample}
