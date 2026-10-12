import json

import pytest

from ofo import obsidian as ob
from test_autopilot import make, report, RULES  # noqa: F401
from test_lab import SPEC, bars_csv  # noqa: F401
from ofo.dossier import build_dossier


def study(name="ORB retest", status="APPROVED", source="Opening range paper (https://x)"):
    return {"report": {"name": name, "template": "orb", "rationale": "mechanism", "source": source, "n_candidates": 16,
                       "surface": {"median_sharpe": 0.05, "frac_positive": 0.7, "plateau_ratio": 0.8, "robust": True},
                       "wf_oos_pnl": 100.0, "locked": {"status": status, "checks": {"dsr": True, "bust_risk": False},
                                                         "p_pass": 0.6, "p_bust": 0.1, "pnl": 500.0},
                       "exits": {"baseline": {"sharpe": 0.01}, "chosen": True, "best": {"stop_atr": 1.5, "rr": 2.0},
                                 "grid": [{"stop_atr": 1.5, "rr": 2.0, "sharpe": 0.1, "win_rate": 0.4, "n_trades": 50}]}},
            "planner": {"decision": "abandon", "reason": "r"}, "auditor": {"summary": "s", "risk_flags": ["f"]},
            "saved_at": "2025-01-01"}


PAPERS = {"arxiv:1": {"title": "Opening range paper", "url": "https://x", "why": "ORB", "status": "downloaded",
                      "abstract": "We test ORB.", "published": "2025", "source": "arxiv"},
          "arxiv:2": {"title": "Skipped", "status": "skipped"}}


def test_sync_writes_linked_notes_and_keeps_my_text(tmp_path):
    d = build_dossier(report(150, 250), RULES, 2, 1000)
    d["slug"], d["created"] = "orb", "now"
    st = study(name=d["name"])
    ev = [{"t": "2025-03-01T10:00:00+00:00", "agent": "judge", "type": "verdict", "message": "ORB: APPROVED"}]
    import datetime as dt
    res = ob.sync(tmp_path, [st], [d], [{"slug": "orb", "name": d["name"], "status": "live", "account": "50k", "micro": 2,
                                         "forward": {"days": 3, "pnl": 10.0, "equity": [["2025-03-01", 10.0]]}}],
                  PAPERS, ev, "it", dt.datetime(2025, 3, 1, 12))
    root = tmp_path / "OFO"
    assert res["strategies"] == 1 and res["papers"] == 1 and res["ready"] == 1 and res["deployments"] == 1 and res["diary"] == 1
    note = (root / "Strategie" / f"{ob.safe(d['name'])}.md").read_text(encoding="utf-8")
    assert "[[Paper - Opening range paper]]" in note and "[[Test strat (pronta)]]" in note and "tags: [\"ofo/strategy\"" in note
    assert (root / "Paper" / "Paper - Opening range paper.md").exists() and not (root / "Paper" / "Paper - Skipped.md").exists()
    assert "[[Test strat]]" in (root / "Paper" / "Paper - Opening range paper.md").read_text(encoding="utf-8")
    assert "dataview" in (root / "OFO.md").read_text(encoding="utf-8") and (root / "Diario" / "2025-03-01.md").exists()
    # my annotations survive a rewrite, and an unchanged sync writes nothing
    p = root / "Strategie" / f"{ob.safe(d['name'])}.md"
    p.write_text(p.read_text(encoding="utf-8") + "La mia osservazione.\n", encoding="utf-8")
    st2 = study(name=d["name"]); st2["report"]["wf_oos_pnl"] = 999.0
    ob.sync(tmp_path, [st2], [d], [], PAPERS, ev, "it", dt.datetime(2025, 3, 1, 12))
    again = p.read_text(encoding="utf-8")
    assert "La mia osservazione." in again and "999" in again
    assert ob.sync(tmp_path, [st2], [d], [], PAPERS, ev, "it", dt.datetime(2025, 3, 1, 12))["strategies"] == 0


def test_never_touches_a_file_that_is_not_ours_and_sanitises_names(tmp_path):
    root = ob.ensure_vault(tmp_path)
    mine = root / "Strategie" / "Odd name.md"
    mine.write_text("my own note, no marker", encoding="utf-8")
    ob.sync(tmp_path, [study(name="Odd name")], [], [], {}, [], "it")
    assert mine.read_text(encoding="utf-8") == "my own note, no marker"
    assert ob.safe('a/b:c*?"<>|#^[x]') == "a-b-c-x" and ob.safe("../../etc/passwd").count("/") == 0
    with pytest.raises(ValueError):
        ob.ensure_vault(tmp_path / "does-not-exist")


def test_lab_exports_and_reads_notes_from_the_vault(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv)
    vault = tmp_path / "vault"
    vault.mkdir()
    st = lab.set_obsidian(str(vault))
    assert st["vault"] == str(vault) and st["error"] is None and st["last"]["wrote"]["dashboard"] == 1
    assert (vault / "OFO" / "OFO.md").exists()
    (vault / "OFO" / "Ideas" / "mia.md").write_text("Idea: breakout della prima ora con retest", encoding="utf-8")
    (vault / "OFO" / "Ideas" / "_skip.md").write_text("ignored", encoding="utf-8")
    assert lab._unread_ideas() is True
    with pytest.raises(ValueError):
        lab.set_obsidian(str(tmp_path / "nope"))
    again = make(tmp_path, bars_csv)                                  # a restart remembers the vault
    assert again.obsidian_state()["vault"] == str(vault)
