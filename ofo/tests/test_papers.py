import json

from ofo.papers import chunk_text, extract_pdf_text, parse_json, propose_specs


class FakeLLM:
    def __init__(self, replies): self.replies, self.calls = list(replies), []
    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.replies.pop(0)


GOOD = {"name": "ORB 15-60", "template": "orb", "ranges": {"or_minutes": {"min": 15, "max": 60, "step": 15}},
        "timeframes": ["5min", "15min"], "rationale": "paper claims range expansion after the open"}


def test_valid_and_invalid_proposals_are_separated():
    bad_tpl = {**GOOD, "template": "exec_python"}
    out_of_bounds = {**GOOD, "ranges": {"or_minutes": [9999]}}
    reply = "Sure! ```json\n" + json.dumps({"strategies": [GOOD, bad_tpl, out_of_bounds, GOOD]}) + "\n```"
    specs, rejected = propose_specs(FakeLLM([reply]), "some paper text", source="Paper X")
    assert len(specs) == 1 and specs[0].source == "Paper X"       # duplicate dropped
    assert len(rejected) == 2 and any("unknown template" in r["reason"] for r in rejected)


def test_garbage_reply_is_rejected_not_fatal():
    specs, rejected = propose_specs(FakeLLM(["I cannot do that", "{not json"]),
                                    "x " * 5000)               # 2 chunks
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
