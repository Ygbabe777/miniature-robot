"""Market Intelligence inside the Lab: real data only, no second vault, the hidden block never reaches the MIE, pipeline/Judge unchanged,
the verdict is stored behind the access label, restart restores the cycle, the HTTP views are served."""
import json
import urllib.error
import urllib.request

import pytest

from ofo.app import server as SRV
from ofo.app.lab import Lab
from ofo.mie.schema import RESEARCHER_ACCESS
from test_mie_engine import fake_study, the_question
from test_research import synth


def mk(tmp_path, vault=None, n=180, monkeypatch=None):
    csv = tmp_path / "b.csv.gz"
    if not csv.exists():
        synth(n_sessions=n, seed=4).to_csv(csv)
    lab = Lab(str(csv), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    if vault:
        lab.obs["vault"] = str(vault)
    lab.mie.cfg = {**lab.mie.cfg}
    return lab


def narrow(lab):
    from ofo.mie import questions as Q
    _, eng, _ = lab.mie.parts()
    eng.questions = Q.standard_questions(lab.mie.cfg)[:3]


def with_hypothesis(lab):
    """A real hypothesis card derived by the engine from a (fabricated, test-only) supported study of this data version."""
    _, eng, _ = lab.mie.parts()
    q = the_question()
    did = q["definition"]["id"]
    dv = "dvtest"
    eng.studies[dv] = {did: {**fake_study(did), "data_version": dv}}
    eng.derive_hypotheses(dv, {did: q})
    return eng, next(iter(eng.hyps.values()))


def get(url, path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url + path, headers={"X-OFO": "1"}), timeout=30).read())


def post(url, path):
    return json.loads(urllib.request.urlopen(urllib.request.Request(url + path, data=b"{}", method="POST", headers={"X-OFO": "1"}), timeout=30).read())


def test_before_any_observation_every_view_says_not_available(tmp_path):
    lab = mk(tmp_path)
    ov = lab.mie.overview()
    assert ov["available"] is False and ov["reason"] == "NOT AVAILABLE"
    assert lab.mie.intelligence()["available"] is False
    assert lab.mie.cycle_view()["status"]["status"] == "idle" and lab.mie.cycle_view()["reason"] == "NOT AVAILABLE"
    assert lab.mie.memory_view()["available"] is False
    assert lab.state()["mie"]["available"] is False


def test_a_tick_shows_real_numbers_and_never_the_hidden_block(tmp_path):
    lab = mk(tmp_path)
    narrow(lab)
    out = lab.mie.tick()
    assert "error" not in out, out
    ov = lab.mie.overview()
    ep = lab.epoch_state()["epoch"]
    assert ov["available"] and ov["sessions"] > 50 and ov["bars"] > 0 and ov["order_flow"] == "NOT AVAILABLE"
    assert ov["period"][1] < ep["start"], "the dashboard period must end before the hidden epoch starts"
    assert ov["epoch"] == {"start": ep["start"], "end": ep["end"]}
    assert ov["counts"]["market_sessions_development"] == ov["sessions"]
    assert sum(ov["regime_share"].values()) >= 1.0 - 1e-6
    iv = lab.mie.intelligence()
    assert iv["available"] and all(s["n_events"] >= 0 and s["status"] in ("OBSERVED", "TESTED", "SUPPORTED", "INCONCLUSIVE", "CONTRADICTED") for s in iv["studies"])
    assert "PROVEN" not in json.dumps(iv)
    assert lab.mie.cycle_view()["status"]["status"] == "active"


def test_the_memory_lives_in_the_existing_vault_and_only_under_ofo(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Mie note").mkdir(parents=True)
    (vault / "Mie note" / "privata.md").write_text("niente da toccare", encoding="utf-8")
    lab = mk(tmp_path, vault=vault)
    narrow(lab)
    lab.mie.tick()
    mem = lab.mie.memory_view()
    assert mem["shared_vault"] is True and mem["root"].startswith(str(vault / "OFO"))
    assert mem["available"] and mem["notes_intelligence"] > 0
    assert (vault / "Mie note" / "privata.md").read_text(encoding="utf-8") == "niente da toccare"
    outside = [p for p in vault.rglob("*.md") if "OFO" not in p.relative_to(vault).parts and p.parent.name != "Mie note"]
    assert outside == []
    assert not (tmp_path / "d" / "memory").exists(), "no second memory when a vault exists"


def test_the_pipeline_result_is_written_back_and_the_verdict_stays_behind_its_label(tmp_path):
    lab = mk(tmp_path)
    mem, _, _ = lab.mie.parts()
    eng, h = with_hypothesis(lab)
    lab.mie.after_study("nope", "x", f"MIE {h['id']}", "APPROVED")                      # unknown study: dev stages empty, still written
    notes = {n["front"].get("ofo_kind"): n for n in mem.index() if n["front"].get("ofo_hypothesis") == h["id"] and n["front"].get("ofo_kind") in ("validation", "verdict")}
    assert notes["validation"]["access"] == "dev" and notes["verdict"]["access"] == "locked_derived"
    got = mem.retrieve({"text": f"{h['id']} verdetto del Judge APPROVED", "market": lab.primary}, allowed=RESEARCHER_ACCESS, k=20)
    assert got and all(g["provenance"]["kind"] != "verdict" for g in got) and all(g["access"] in RESEARCHER_ACCESS for g in got)
    assert not any("APPROVED" in g["text"] for g in got)
    lab.mie.after_study("nope", "x", "qualcosa di altro", "APPROVED")                    # a non-MIE source writes nothing
    assert sum(1 for n in mem.index() if n["front"].get("ofo_kind") == "verdict") == 1


def test_mie_hypotheses_enter_the_existing_pipeline_with_origin_mie_and_a_trace(tmp_path):
    lab = mk(tmp_path)
    eng, h = with_hypothesis(lab)
    assert not lab.pending and eng._submit(h) >= 1                                       # nothing runs until a mission submits it, then it is queued
    queued = [v for v in lab.pending.values() if v["origin"] == "mie"]
    assert queued and all(str(v["spec"].source) == f"MIE {h['id']}" for v in queued)
    assert lab.registry.data["locked_looks"] == 0, "queuing a hypothesis never looks at the hidden block"
    assert lab.mie.trace("inesistente")["error"]
    assert lab.mie.hypothesis_of("inesistente") is None


def test_restart_restores_the_cycle_and_the_studies(tmp_path):
    lab = mk(tmp_path)
    narrow(lab)
    lab.mie.tick()
    before = lab.mie.cycle_view()["status"]
    n_studies = len(lab.mie.intelligence()["studies"])
    lab2 = Lab(str(tmp_path / "b.csv.gz"), data_dir=str(tmp_path / "d"), ideas_dir=str(tmp_path / "i"))
    after = lab2.mie.cycle_view()["status"]
    assert after["id"] == before["id"] and after["status"] == "active"
    assert len(lab2.mie.intelligence()["studies"]) == n_studies
    assert lab2.mie.overview()["available"]


def test_the_scheduler_waits_for_a_free_engine(tmp_path):
    lab = mk(tmp_path)
    lab.mie.last_tick_at = 0.0
    assert lab.mie.due() is True
    lab.busy["job"] = 1
    assert lab.mie.due() is False
    lab.busy["job"] = 0
    lab.mie.enabled = False
    assert lab.mie.due() is False


def test_the_agents_are_busy_only_while_the_mie_really_works(tmp_path):
    lab = mk(tmp_path)
    assert not any(a in lab.working_agents() for a in ("market_observer", "structure_engine", "behavior_analyzer", "obsidian_memory", "research_director"))
    with lab.working("mie"):
        lab.events.emit("behavior_analyzer", "working", "studio")
        assert "behavior_analyzer" in lab.working_agents()
    assert "behavior_analyzer" not in lab.working_agents()


def test_http_views_are_served_and_tick_is_a_post(tmp_path):
    lab = mk(tmp_path)
    narrow(lab)
    lab.mie.tick()
    srv, url = SRV.serve_background(lab)
    try:
        for p in ("overview", "intelligence", "memory", "cycle", "status"):
            assert isinstance(get(url, f"/api/mie/{p}"), dict)
        assert get(url, "/api/mie/overview")["available"] is True
        assert get(url, "/api/mie/trace?slug=inesistente")["error"]
        assert "mie" in get(url, "/api/state")
        assert post(url, "/api/mie/cycle/start")["status"] == "active"
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(url + "/api/mie/tick", data=b"{}", method="POST"), timeout=10)          # no X-OFO header: refused
    finally:
        srv.shutdown()


def test_the_wing_in_the_3d_lab_moves_only_with_real_events(tmp_path):
    """The five MIE scientists exist, are idle without events, work when their agent emits, and a link lights up only for the real consecutive pipeline order."""
    import threading
    pw = pytest.importorskip("playwright.sync_api")
    lab = mk(tmp_path)
    srv = SRV.make_server(lab, port=8798)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            try:
                b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
            except Exception:
                pytest.skip("no chromium available")
            pg = b.new_page(viewport={"width": 1300, "height": 800})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:8798/")
            pg.wait_for_timeout(4000)
            five = ["mie_observer", "mie_structure", "mie_behavior", "mie_memory", "mie_director"]
            st = pg.evaluate("LabScene.states()")
            assert all(k in st for k in five) and all(st[k] in ("idle", "standby") for k in five), st
            assert not any(pg.evaluate("Object.values(LabScene.mieLinks())")), "nothing is linked without events"
            send = lambda a, t, m="": pg.evaluate("LabScene.onEvent(%s)" % json.dumps({"agent": a, "type": t, "message": m, "data": {}}))
            send("market_observer", "working", "audit")
            assert pg.evaluate("LabScene.states().mie_observer") == "working"
            send("research_director", "working", "missione")                                        # skipping the pipeline order: no link
            assert not pg.evaluate("LabScene.mieLinks().mie_director")
            send("market_observer", "working", "audit"); send("structure_engine", "working", "regimi")
            assert pg.evaluate("LabScene.mieLinks().mie_structure") is True
            pg.evaluate("LabScene.focus('mie')")
            pg.wait_for_timeout(1500)
            assert pg.evaluate("document.querySelector('#inspector').hidden") is False
            assert "NOT AVAILABLE" in pg.evaluate("document.querySelector('#inspector').innerText")          # no cycle yet: said, not invented
            pg.evaluate("MIE.open('overview')")
            pg.wait_for_timeout(1200)
            assert "NOT AVAILABLE" in pg.evaluate("document.querySelector('#mie-panel').innerText")
            assert not errs, errs
            b.close()
    finally:
        srv.shutdown()


def test_the_dashboard_prints_exactly_the_backend_numbers(tmp_path):
    import threading
    pw = pytest.importorskip("playwright.sync_api")
    lab = mk(tmp_path)
    narrow(lab)
    lab.mie.tick()
    ov, iv = lab.mie.overview(), lab.mie.intelligence()
    srv = SRV.make_server(lab, port=8799)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            try:
                b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
            except Exception:
                pytest.skip("no chromium available")
            pg = b.new_page(viewport={"width": 1300, "height": 800})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:8799/")
            pg.wait_for_timeout(3500)
            pg.evaluate("MIE.open('overview')")
            pg.wait_for_timeout(1500)
            txt = pg.evaluate("document.querySelector('#mie-panel').innerText")
            assert ov["period"][0] in txt and ov["period"][1] in txt and f"{ov['bars']:,}" in txt and str(ov["sessions"]) in txt
            assert ov["epoch"]["start"] in txt and "NOT AVAILABLE" in txt                       # order flow is never invented
            pg.evaluate("MIE.open('intelligence')")
            pg.wait_for_timeout(800)
            txt = pg.evaluate("document.querySelector('#mie-panel').innerText")
            assert all(s["status"] in txt for s in iv["studies"]) and "PROVEN" not in txt
            pg.evaluate("MIE.open('cycle')")
            pg.wait_for_timeout(800)
            assert "baseline" in pg.evaluate("document.querySelector('#mie-panel').innerText").lower()
            assert not errs, errs
            b.close()
    finally:
        srv.shutdown()


def test_the_chart_is_real_development_data_and_scientists_point_at_it_only_while_working(tmp_path):
    import threading
    pw = pytest.importorskip("playwright.sync_api")
    lab = mk(tmp_path)
    assert lab.mie.chart()["available"] is False                                   # nothing observed yet: said, not drawn
    narrow(lab)
    lab.mie.tick()
    ch, ep = lab.mie.chart(), lab.epoch_state()["epoch"]
    assert ch["available"] and ch["scope"] == "development" and len(ch["candles"]) > 20
    assert max(c[0] for c in ch["candles"])[:10] < ep["start"], "no candle may come from the protected block"
    assert all(e["available_at"][:10] < ep["start"] for e in ch["events"]) and all(s["confirmed_at"][:10] < ep["start"] for s in ch["swings"])
    for v in ch["levels"].values():
        assert min(c[3] for c in ch["candles"]) - 1e3 < v < max(c[2] for c in ch["candles"]) + 1e3
    srv = SRV.make_server(lab, port=8800)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pw.sync_playwright() as p:
            try:
                b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--no-sandbox"])
            except Exception:
                pytest.skip("no chromium available")
            pg = b.new_page(viewport={"width": 1300, "height": 800})
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.goto("http://127.0.0.1:8800/")
            pg.wait_for_timeout(4000)
            for _ in range(30):                                                   # the page fetches the chart after loading: wait for it, not for a fixed time
                if pg.evaluate("LabScene.chartGeo()"):
                    break
                pg.wait_for_timeout(1000)
            assert pg.evaluate("LabScene.chartGeo().n") == 5
            assert not any(pg.evaluate("Object.values(LabScene.mieBeams())")), "no beam while nobody works"
            pg.evaluate("LabScene.onEvent(%s)" % json.dumps({"agent": "structure_engine", "type": "working", "message": "x", "data": {}}))
            pg.wait_for_timeout(1500)
            beams = pg.evaluate("LabScene.mieBeams()")
            assert beams["mie_structure"] is True and not beams["mie_memory"]
            pg.evaluate("MIE.open('chart')")
            pg.wait_for_timeout(800)
            assert pg.evaluate("document.querySelector('#mie-cv').width") > 0
            assert not errs, errs
            b.close()
    finally:
        srv.shutdown()
