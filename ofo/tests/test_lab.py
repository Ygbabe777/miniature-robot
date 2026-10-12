import json
import time
import urllib.error
import urllib.request

import pytest

from ofo.app.lab import Lab
from ofo.app.server import serve_background
from test_agents import Scripted
from test_research import synth

SPEC = {"name": "ORB retest small", "template": "orb_retest",
        "ranges": {"or_minutes": [15, 30], "retest_bars": [3, 6], "tol": [0.0, 0.2]},
        "timeframes": ["15min", "30min"], "rationale": "MECCANISMO ... EVIDENZA idea dell'utente ... QUANDO NON FUNZIONA ..."}


@pytest.fixture(scope="module")
def bars_csv(tmp_path_factory):
    p = tmp_path_factory.mktemp("d") / "bars.csv.gz"
    synth(n_sessions=200, seed=3).to_csv(p)
    return str(p)


def call(base, path, body=None, method=None, headers=None, raw=None):
    h = {"X-OFO": "1", **(headers or {})}
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, method=method or ("POST" if data is not None else "GET"), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}") if r.headers["Content-Type"] == "application/json" else r.read()
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def make(tmp_path, bars_csv, llm):
    lab = Lab(bars_csv, data_dir=str(tmp_path / "data"), ideas_dir=str(tmp_path / "ideas"), llm=llm)
    srv, base = serve_background(lab)
    return lab, srv, base


def test_chat_creates_pending_spec_then_runs_and_exposes_charts(tmp_path, bars_csv):
    reply = json.dumps({"reply": "Preparato: 16 combinazioni.", "spec": SPEC, "template_request": None})
    llm = {"strategist": Scripted(strategist=[reply]), "planner": Scripted(planner=["x"] * 3), "auditor": Scripted(auditor=[json.dumps({"summary": "s"})] * 3)}
    lab, srv, base = make(tmp_path, bars_csv, llm)
    try:
        code, out = call(base, "/api/chat", {"message": "creami una strategia ORB con i retest"})
        assert code == 200 and out["spec"]["n_candidates"] == 16 and out["spec"]["origin"] == "chat"
        sid = out["spec"]["id"]
        assert any(p["id"] == sid for p in call(base, "/api/state")[1]["pending"])
        code, job = call(base, "/api/run", {"id": sid})
        assert code == 200 and job["status"] in ("queued", "running")
        for _ in range(240):                                    # up to ~2 min
            st = call(base, "/api/state")[1]
            if st["jobs"] and st["jobs"][-1]["status"] in ("done", "error"):
                break
            time.sleep(0.5)
        j = st["jobs"][-1]
        assert j["status"] == "done", j
        assert not st["pending"]                                # started specs leave the pending list
        assert st["strategies"][0]["name"] == "ORB retest small" and st["trials"] >= 16
        code, d = call(base, f"/api/strategy/{j['slug']}")
        assert code == 200 and "wf_oos" in d["charts"]
        pack = d["charts"]["wf_oos"]
        assert {"equity", "drawdown", "monte_carlo", "metrics", "rolling_sharpe", "histogram"} <= set(pack)
        assert {"sharpe", "sortino", "profit_factor"} <= set(pack["metrics"])
        # the locked block is hidden unless the Judge already used its look
        looked = "checks" in d["report"]["locked"]
        assert ("locked" in d["charts"]) == looked
        ev = call(base, "/api/events?after=0")[1]
        agents = {e["agent"] for e in ev["events"]}
        assert {"you", "strategist", "optimizer", "validator", "judge"} <= agents
    finally:
        srv.shutdown()


def test_missing_logic_is_saved_as_a_template_request(tmp_path, bars_csv):
    reply = json.dumps({"reply": "Manca il trailing stop.", "spec": None,
                        "template_request": {"name": "orb_trailing", "description": "ORB con trailing stop a 2 ATR"}})
    lab, srv, base = make(tmp_path, bars_csv, {"strategist": Scripted(strategist=[reply])})
    try:
        out = call(base, "/api/chat", {"message": "ORB con trailing stop"})[1]
        assert out["spec"] is None and out["template_request"]["name"] == "orb_trailing"
        assert call(base, "/api/state")[1]["template_requests"][0]["name"] == "orb_trailing"
    finally:
        srv.shutdown()


def test_chat_without_ai_degrades_gracefully(tmp_path, bars_csv):
    lab, srv, base = make(tmp_path, bars_csv, None)
    try:
        code, out = call(base, "/api/chat", {"message": "ciao"})
        assert code == 200 and out.get("error") and "AI non configurata" in out["reply"]
        assert call(base, "/api/state")[1]["ai"] is False
    finally:
        srv.shutdown()


def test_upload_ideas_security_and_listing(tmp_path, bars_csv):
    lab, srv, base = make(tmp_path, bars_csv, None)
    try:
        assert call(base, "/api/ideas/paper.pdf", raw=b"%PDF-1.4 x", method="PUT")[1] == {"saved": "paper.pdf"}
        assert call(base, "/api/ideas/paper.pdf", raw=b"%PDF-1.4 y", method="PUT")[1] == {"saved": "paper_1.pdf"}
        assert "error" in call(base, "/api/ideas/evil.exe", raw=b"MZ", method="PUT")[1]
        out = call(base, "/api/ideas/..%2F..%2Fpwned.txt", raw=b"hi", method="PUT")[1]
        assert out == {"saved": "pwned.txt"} and (tmp_path / "ideas" / "pwned.txt").exists()
        assert not (tmp_path.parent / "pwned.txt").exists()               # stayed inside ideas/
        names = {i["name"] for i in call(base, "/api/state")[1]["ideas"]}
        assert {"paper.pdf", "paper_1.pdf", "pwned.txt"} <= names
    finally:
        srv.shutdown()


def test_other_websites_cannot_drive_the_app_and_paths_cannot_escape(tmp_path, bars_csv):
    lab, srv, base = make(tmp_path, bars_csv, None)
    try:
        req = urllib.request.Request(base + "/api/chat", data=b'{"message":"x"}', method="POST")   # no X-OFO header
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 403
        code, _ = call(base, "/..%2F..%2Fetc%2Fpasswd")
        assert code == 404
    finally:
        srv.shutdown()


def test_ui_files_are_served_including_offline_vendor_libraries(tmp_path, bars_csv):
    lab, srv, base = make(tmp_path, bars_csv, None)
    try:
        code, html = call(base, "/")
        assert code == 200 and b"OFO" in html and b"scene.js" in html
        for f in ("scene.js", "ui.js", "charts.js", "lab.css", "vendor/three.min.js", "vendor/chart.umd.min.js",
                  "vendor/OrbitControls.js"):
            code, body = call(base, "/" + f)
            assert code == 200 and len(body) > 500, f
    finally:
        srv.shutdown()


def test_candles_endpoint_serves_the_users_own_data(tmp_path, bars_csv):
    lab, srv, base = make(tmp_path, bars_csv, None)
    try:
        code, d = call(base, "/api/candles?tf=15min&days=3")
        assert code == 200 and d["tf"] == "15min" and d["n_sessions"] == 3 and d["symbol"] == "MNQ"
        c = d["candles"]
        times = [x[0] for x in c]
        assert times == sorted(times) and all(isinstance(t, int) for t in times)
        assert 1.6e9 < times[0] < 1.9e9                                          # real epoch seconds (2023 data), not micro/nanoseconds
        assert all(x[2] >= max(x[1], x[4]) - 1e-9 and x[3] <= min(x[1], x[4]) + 1e-9 for x in c)   # high/low contain open/close
        _, one = call(base, "/api/candles?tf=1min&days=1")
        assert 800 < len(one["candles"]) < 1500                                  # one session of 1-minute bars
        _, daily = call(base, "/api/candles?tf=1D&days=all")
        assert daily["n_sessions"] == 200 and len(daily["candles"]) == 200 and isinstance(daily["candles"][0][0], str)
        _, older = call(base, f"/api/candles?tf=1D&days=5&before={daily['candles'][-1][0]}")
        assert older["to"] < daily["candles"][-1][0]                              # paging backwards works
        assert call(base, "/api/candles?tf=1min&days=all")[1]["error"] == "too_many"
        assert call(base, "/api/candles?tf=7min")[1]["error"] == "bad_tf"
    finally:
        srv.shutdown()


def test_language_choice_reaches_the_strategist_prompt(tmp_path, bars_csv):
    llm = {"strategist": Scripted(strategist=[json.dumps({"reply": "Hello!"})])}
    lab, srv, base = make(tmp_path, bars_csv, llm)
    try:
        out = call(base, "/api/chat", {"message": "hi", "lang": "en"})[1]
        assert out["reply"] == "Hello!" and lab.language == "English"
        assert "English" in llm["strategist"].seen[0][0]["content"]
    finally:
        srv.shutdown()
