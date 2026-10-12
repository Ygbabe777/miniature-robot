import json

from ofo import knowledge as kn
from ofo import obsidian as ob
from ofo.papers import propose_specs
from test_lab import SPEC


class Fake:
    def __init__(self, reply):
        self.reply, self.msgs = reply, []

    def chat(self, messages, **kw):
        self.msgs.append(messages)
        return json.dumps(self.reply)


def study(name, status, gates=None, template="orb"):
    return {"report": {"name": name, "template": template, "rationale": "r", "source": "s", "n_candidates": 4,
                       "surface": {"median_sharpe": 0, "frac_positive": 0.5, "plateau_ratio": 0.5, "robust": False},
                       "wf_oos_pnl": 0.0,
                       "locked": {"status": status, "checks": {g: False for g in (gates or [])}}}}


def test_researcher_gets_the_lab_memory_and_names_concepts():
    llm = Fake({"strategies": [SPEC], "concepts": ["Opening Range", "volatility regime!", "x", 5]})
    info = {}
    specs, _ = propose_specs(llm, "opening range text " * 50, source="paper", memory="WHAT THIS LAB ALREADY LEARNED: orb 3 tested",
                             info=info)
    assert specs and info["concepts"] == ["opening range", "volatility regime"]      # cleaned, junk dropped
    assert "ALREADY LEARNED" in llm.msgs[0][1]["content"]


def test_graph_links_papers_strategies_and_concepts_and_builds_memory(tmp_path):
    sd = tmp_path / "specs_auto"
    sd.mkdir()
    (sd / "arxiv_1.json").write_text(json.dumps({"strategies": [{"name": "ORB a"}], "concepts": ["opening range", "gap"]}))
    papers = {"arxiv:1": {"title": "Paper One", "status": "downloaded", "file": "web/arxiv_1.pdf", "query": "opening range breakout"},
              "arxiv:2": {"title": "Paper Two", "status": "downloaded", "file": "web/arxiv_2.pdf", "query": "gap fade"}}
    g = kn.build([study("ORB a", "REJECTED", ["dsr"]), study("ORB b", "REJECTED", ["dsr", "bust_risk"])], papers, sd)
    c = g["concepts"]
    assert g["paper_concepts"]["Paper One"] == ["opening range", "gap", "opening range breakout"]
    assert "ORB a" in c["opening range"]["strategies"] and c["opening range"]["tested"] >= 1
    assert "gap" in c["opening range"]["related"]
    assert any("none approved" in x for x in c["opening range"]["lessons"])
    assert c["gap fade"]["tested"] == 0 and "no strategy tested yet" in c["gap fade"]["lessons"][0]
    mem = kn.memory_text(g)
    assert "opening range" in mem and "usually fails: dsr" in mem and "gap fade" in mem and "NEW mechanism" in mem
    obs = ob.sync(tmp_path / "v", [study("ORB a", "REJECTED", ["dsr"])], [], [], {"arxiv:1": {**papers["arxiv:1"], "status": "downloaded"}}, [], "it", graph=g) \
        if (tmp_path / "v").mkdir() is None else None
    root = tmp_path / "v" / "OFO"
    assert obs["concepts"] >= 3
    note = (root / "Concetti" / "Concetto - opening range.md").read_text(encoding="utf-8")
    assert "[[Paper - Paper One|Paper One]]" in note and "[[ORB a]]" in note and "[[Concetto - gap|gap]]" in note
    assert "[[Concetto - opening range|opening range]]" in (root / "Paper" / "Paper - Paper One.md").read_text(encoding="utf-8")


def test_empty_lab_has_no_memory():
    assert kn.memory_text({"concepts": {}}) == ""
