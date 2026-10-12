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


# ---- the Researcher's access to the Obsidian memory, and idea connections ----
from ofo import obsidian as ob2
from test_autopilot import make
from test_lab import bars_csv  # noqa: F401


def _graph():
    return {"concepts": {
        "opening range": {"papers": ["P"], "strategies": ["ORB a"], "tested": 2, "approved": 0, "gates": {}, "related": ["gap"], "lessons": ["2 tested, none approved"], "owner_notes": []},
        "gap": {"papers": ["P"], "strategies": [], "tested": 0, "approved": 0, "gates": {}, "related": ["opening range"], "lessons": [], "owner_notes": []},
        "volatility regime": {"papers": ["Q"], "strategies": [], "tested": 0, "approved": 0, "gates": {}, "related": [], "lessons": [], "owner_notes": ["la volatilità conta"]},
        "vwap": {"papers": [], "strategies": ["V"], "tested": 3, "approved": 1, "gates": {}, "related": [], "lessons": [], "owner_notes": []}}}


def test_connection_candidates_prefer_owner_links_then_untested_or_successful_and_skip_done():
    g = _graph()
    best = kn.connection_candidates(g, owner_pairs=[("gap", "opening range")], n=10)
    assert best[0][:2] == ("gap", "opening range")                              # the owner's own link outranks the rest
    top = kn.connection_candidates(g, n=1)[0]
    assert top[:2] == ("volatility regime", "vwap")                              # untested + owner note + approved neighbour
    assert kn.connection_candidates(g, done={"volatility regime|vwap"}, n=1)[0][:2] != ("volatility regime", "vwap")
    assert all(not (a, b) == ("gap", "opening range") or s < 5 for a, b, s in kn.connection_candidates(g, n=10))   # already combined: demoted


def test_read_notes_gives_owner_text_and_links_and_never_leaves_OFO(tmp_path):
    root = ob2.ensure_vault(tmp_path)
    (tmp_path / "private.md").write_text("secret diary", encoding="utf-8")                       # outside OFO/: never read
    ob2.write_note(root / "Concetti" / "Concetto - gap.md", {"tags": ["ofo/concept"]}, "# gap", "x")
    p = root / "Concetti" / "Concetto - gap.md"
    p.write_text(p.read_text(encoding="utf-8") + "Penso che funzioni con [[Concetto - vwap]].\n", encoding="utf-8")
    (root / "Ideas" / "mia.md").write_text("Idea: [[Concetto - gap]] e [[Concetto - volatility regime]]", encoding="utf-8")
    notes = ob2.read_notes(tmp_path)
    assert "private" not in notes and notes["Concetto - gap"]["annotation"].startswith("Penso che funzioni")
    assert ob2.owner_pairs(notes) == [("gap", "volatility regime"), ("gap", "vwap")]


def test_memory_text_carries_the_owners_notes():
    mem = kn.memory_text(_graph())
    assert "OWNER" in mem and "la volatilità conta" in mem


def test_connect_ideas_reads_the_vault_proposes_hybrids_and_writes_a_linked_idea_note(tmp_path, bars_csv):
    lab = make(tmp_path, bars_csv, llm={"researcher": Fake({"strategies": [{**SPEC, "name": "Hybrid ORB gap"}], "concepts": ["opening range"]})})
    vault = tmp_path / "vault"; vault.mkdir()
    lab.set_obsidian(str(vault))
    root = vault / "OFO"
    ob2.write_note(root / "Concetti" / "Concetto - opening range.md", {"tags": ["ofo/concept"]}, "# opening range", "x")
    ob2.write_note(root / "Concetti" / "Concetto - volatility regime.md", {"tags": ["ofo/concept"]}, "# vr", "x")
    (root / "Ideas" / "mia.md").write_text("[[Concetto - opening range]] [[Concetto - volatility regime]]", encoding="utf-8")
    sd = lab.work / "specs_auto"; sd.mkdir(parents=True)
    (sd / "p.json").write_text(json.dumps({"strategies": [], "concepts": ["opening range", "volatility regime"]}))
    lab.scout().state["papers"]["a:1"] = {"title": "T", "status": "downloaded", "file": "web/p.pdf", "query": "x topic"}
    assert lab.connect_ideas(now=100_000.0) == 1
    assert list(lab.pending.values())[0]["origin"] == "connection"
    idea = (root / "Idee" / "opening range x volatility regime.md").read_text(encoding="utf-8")
    assert "[[Concetto - opening range|opening range]]" in idea and "[[Hybrid ORB gap]]" in idea
    assert lab.connect_ideas(now=100_100.0) == 0                                 # rate limited
    assert "opening range|volatility regime" in (lab.data / "connections.json").read_text()
