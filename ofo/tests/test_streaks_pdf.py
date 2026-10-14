"""Streak statistics, and the per-strategy PDF (dependency-free) served by the Lab."""
import json
import urllib.request

from ofo.app import server as SRV
from ofo.app.lab import Lab
from ofo.pdf import Doc
from ofo.specs import from_dict
from ofo.streaks import streak_stats
from test_research import synth


def test_streak_statistics_are_exact_and_zero_breaks_a_run():
    s = streak_stats([1, 1, 1, -1, -1, 1, 0, 1, 1, -1])
    assert s["win"] == {"avg": (3 + 1 + 2) / 3, "max": 3, "count": 3}
    assert s["loss"] == {"avg": (2 + 1) / 2, "max": 2, "count": 2}
    assert streak_stats([])["win"] == {"avg": None, "max": 0, "count": 0}
    assert streak_stats([-1, -2, -3])["loss"]["max"] == 3 and streak_stats([-1, -2, -3])["win"]["count"] == 0


def test_the_pdf_writer_makes_a_valid_multi_page_file_with_accents_and_a_chart():
    d = Doc("t")
    d.text("Perché è già così: àèìòù", 12, bold=True)
    for i in range(120):
        d.text(f"riga {i} con (parentesi) e \\ backslash", 10)
    d.chart("equity", [float(i % 7) for i in range(300)])
    b = d.render()
    assert b.startswith(b"%PDF-1.4") and b.rstrip().endswith(b"%%EOF") and b.count(b"/Type /Page ") >= 2
    assert b"startxref" in b and b"parentesi" in b
    xref = int(b.split(b"startxref")[1].split()[0])
    assert b[xref:xref + 4] == b"xref"                                                       # the cross-reference table really sits where startxref says


def test_every_tested_strategy_has_streaks_and_a_downloadable_pdf(tmp_path):
    csv = tmp_path / "b.csv.gz"
    synth(n_sessions=260, seed=6).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    lab.loop().process(from_dict({"name": "EMA pdf", "template": "ema_cross", "ranges": {"fast": [5, 9], "slow": [30, 60]}, "timeframes": ["15min"], "rationale": "t"}))
    st = lab.streaks("EMA_pdf")
    n = len(lab.trades("EMA_pdf")["trades"])
    assert st["available"] and st["trades"]["n"] == n and st["days"]["n"] <= n
    assert st["trades"]["win"]["max"] >= 1 and st["trades"]["loss"]["max"] >= 1 and st["trades"]["win"]["avg"] <= st["trades"]["win"]["max"]
    assert lab.streaks("inesistente")["available"] is False and lab.report_pdf("inesistente") is None
    srv, url = SRV.serve_background(lab)
    try:
        r = urllib.request.urlopen(urllib.request.Request(url + "/api/pdf/EMA_pdf", headers={"X-OFO": "1"}), timeout=60)
        body = r.read()
        assert r.headers["Content-Type"] == "application/pdf" and "EMA_pdf.pdf" in r.headers["Content-Disposition"]
        assert body.startswith(b"%PDF") and b"Verdetto del Judge" in body and b"Serie di vincite e perdite" in body
        assert json.loads(urllib.request.urlopen(urllib.request.Request(url + "/api/streaks/EMA_pdf", headers={"X-OFO": "1"}), timeout=60).read())["available"]
    finally:
        srv.shutdown()
