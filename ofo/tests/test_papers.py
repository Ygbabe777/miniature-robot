import pytest
import json

from ofo.papers import chunk_text, extract_pdf_text, parse_json, propose_specs


class FakeLLM:
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.replies.pop(0)


from cards import CARD  # noqa: E402
GOOD = {"name": "ORB 15-60", "template": "orb", "ranges": {"or_minutes": {"min": 15, "max": 60, "step": 15}},
        "timeframes": ["5min", "15min"], "rationale": "paper claims range expansion after the open", "hypothesis": CARD}


def test_valid_and_invalid_proposals_are_separated():
    bad_tpl = {**GOOD, "template": "exec_python"}
    out_of_bounds = {**GOOD, "ranges": {"or_minutes": [9999]}}
    reply = "Sure! ```json\n" + json.dumps({"strategies": [GOOD, bad_tpl, out_of_bounds, GOOD]}) + "\n```"
    specs, rejected = propose_specs(FakeLLM([reply]), "some paper text", source="Paper X")
    assert len(specs) == 1 and specs[0].source == "Paper X"       # duplicate dropped
    assert len(rejected) == 2 and any("unknown template" in r["reason"] for r in rejected)


def test_garbage_reply_is_rejected_not_fatal():
    specs, rejected = propose_specs(FakeLLM(["I cannot do that", "{not json"]),
                                    "strategy entry " * 1700)   # 2 relevant chunks of 20k chars
    assert specs == [] and len(rejected) == 2


def test_prompt_never_offers_control_templates():
    llm = FakeLLM(['{"strategies":[]}'])
    propose_specs(llm, "hello")
    assert "random_control" not in llm.calls[0][0]["content"]


def test_chunking_and_parse():
    assert chunk_text("") == [] and len(chunk_text("a" * 15000, 6000, 300)) == 3
    assert parse_json('xx {"a": 1} yy') == {"a": 1}


def test_pdf_text_extraction(tmp_path):
    content = b"BT /F1 12 Tf 72 700 Td (Opening range breakout works) Tj ET"
    pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
           b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
           b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
           b"4 0 obj<</Length " + str(len(content)).encode() + b">>stream\n" + content + b"\nendstream endobj\n"
           b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\nstartxref\n0\n%%EOF")
    p = tmp_path / "t.pdf"; p.write_bytes(pdf)
    assert "Opening range breakout" in extract_pdf_text(str(p))


def test_only_strategy_relevant_chunks_are_sent():
    from ofo.papers import select_chunks
    chunks = ["References: Smith 2019. Jones 2020. Acknowledgements to the committee.",
              "The breakout strategy uses an entry signal when volatility exceeds a threshold parameter.",
              "Thanks to our funders and families.",
              "Backtest of the momentum rule: intraday futures return and stop levels."]
    got = select_chunks(chunks, 2)
    assert got == [chunks[1], chunks[3]]
    assert select_chunks(["nothing useful here at all"], 5) == ["nothing useful here at all"]


def test_an_llm_that_is_down_or_blocked_is_never_recorded_as_read_nothing_found(tmp_path):
    """The reported bug: the Researcher searched but proposed nothing, because LLM errors (budget pause, key refused, network) were
    swallowed as 'unparseable reply' and the paper was marked as read."""
    from ofo.agents.loop import ResearchLoop
    from ofo.backtest import MNQ
    from ofo.events import EventLog
    from ofo.llm.client import LLMError
    from ofo.prop import PRESETS
    from ofo.registry import TrialRegistry
    from test_research import synth

    class Down:
        def chat(self, *a, **k):
            raise LLMError("budget paused")
    with pytest.raises(LLMError):
        propose_specs(Down(), "strategy entry futures " * 40, source="x")
    ideas = tmp_path / "ideas"; ideas.mkdir()
    (ideas / "p.txt").write_text("Opening range breakout after the cash open tends to continue " * 20)
    ev = EventLog(tmp_path / "e.jsonl")
    loop = ResearchLoop(synth(n_sessions=40, seed=1), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"), Down(), ev, tmp_path / "w")
    assert loop.ingest_ideas(ideas) == 0
    assert loop.processed_ideas == set()                                   # still to be read once the AI is back
    assert any(e["type"] == "error" and "AI non utilizzabile" in e["message"] for e in ev.tail())
    assert loop.ingest_log[-1]["error"] and "budget paused" in loop.ingest_log[-1]["error"]


def test_garbage_replies_are_retried_twice_before_a_paper_is_given_up(tmp_path):
    from ofo.agents.loop import ResearchLoop
    from ofo.backtest import MNQ
    from ofo.events import EventLog
    from ofo.prop import PRESETS
    from ofo.registry import TrialRegistry
    from test_research import synth
    ideas = tmp_path / "ideas"; ideas.mkdir()
    (ideas / "p.txt").write_text("Opening range breakout after the cash open tends to continue " * 20)
    loop = ResearchLoop(synth(n_sessions=40, seed=1), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"), FakeLLM(["garbage"] * 3),
                        EventLog(tmp_path / "e.jsonl"), tmp_path / "w")
    for k in (1, 2):
        loop.ingest_ideas(ideas)
        assert loop.processed_ideas == set() and loop.idea_attempts and max(loop.idea_attempts.values()) == k
    loop.ingest_ideas(ideas)
    assert len(loop.processed_ideas) == 1                                  # third failure: given up


def test_missing_hypothesis_card_gets_one_repair_round():
    incomplete = {k: v for k, v in GOOD.items() if k != "hypothesis"}
    fixed = json.dumps({"strategies": [GOOD]})
    llm = FakeLLM([json.dumps({"strategies": [incomplete]}), fixed])
    specs, rejected = propose_specs(llm, "some paper text", source="Paper X")
    assert len(specs) == 1 and specs[0].hypothesis["previsione_falsificabile"] and rejected == []
    assert len(llm.calls) == 2 and "hypothesis" in llm.calls[1][-1]["content"] and "REJECTED" in llm.calls[1][-1]["content"]
    # still incomplete after the repair: the strategy is NOT thrown away (that left the lab with no proposals at all); it is accepted,
    # the card is flagged as incomplete, nothing is invented, and the deterministic gates decide its fate
    llm2 = FakeLLM([json.dumps({"strategies": [incomplete]})] * 2)
    specs2, rej2 = propose_specs(llm2, "some paper text", source="Paper X")
    assert len(specs2) == 1 and rej2 == [] and len(specs2[0].hypothesis_missing) == 10
    assert all(v == "(non dichiarato dal Researcher)" for v in specs2[0].hypothesis.values())
    partial = {**GOOD, "hypothesis": {**CARD, "segnale": "x", "criterio_rigetto": ""}}
    specs3, _ = propose_specs(FakeLLM([json.dumps({"strategies": [partial]})] * 2), "some paper text", source="Paper X")
    assert specs3[0].hypothesis_missing == ["segnale", "criterio_rigetto"] and specs3[0].hypothesis["regola_ingresso"] == CARD["regola_ingresso"]


def test_a_weak_model_that_ignores_the_card_still_gives_the_lab_strategies_to_test(tmp_path):
    """The reported regression: after the hypothesis card became mandatory the Researcher proposed nothing."""
    from ofo.agents.loop import ResearchLoop
    from ofo.backtest import MNQ
    from ofo.events import EventLog
    from ofo.prop import PRESETS
    from ofo.registry import TrialRegistry
    from test_research import synth
    no_card = {k: v for k, v in GOOD.items() if k != "hypothesis"}
    ideas = tmp_path / "ideas"; ideas.mkdir()
    (ideas / "p.txt").write_text("Opening range breakout after the cash open tends to continue " * 20)
    ev = EventLog(tmp_path / "e.jsonl")
    loop = ResearchLoop(synth(n_sessions=40, seed=1), MNQ, PRESETS["50k"], TrialRegistry(tmp_path / "r.json"),
                        FakeLLM([json.dumps({"strategies": [no_card]})] * 2), ev, tmp_path / "w")
    assert loop.ingest_ideas(ideas) == 1
    assert any(e["type"] == "proposal" and "incompleta" in e["message"] for e in ev.tail())
    assert loop.ingest_log[-1]["proposals"] == 1
