"""Connettore GexBot contro un server locale che imita api.gex.bot/v2 (nessuna rete esterna)."""
import json
import random
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from src.config import GexBotCfg
from src.gexbot import (NY, GexBotClient, GexBotError, MockGexBotClient, fetch_options_levels,
                        merge_into_levels, refresh_levels)
from tests.conftest import DATE

KEY = "gex-secret-key-ABCDEF123456"
TS = datetime(2026, 10, 8, 8, 0, tzinfo=NY).timestamp()


@pytest.fixture()
def api():
    st = {"calls": [], "headers": [], "fail_first": 0, "ts": TS, "mult": 1.0, "add": 40.0, "omit": []}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urlparse(self.path)
            st["calls"].append(self.path)
            st["headers"].append({k: v for k, v in self.headers.items() if k in ("Authorization", "User-Agent", "Accept")})
            if st["fail_first"] > 0:
                st["fail_first"] -= 1
                self.send_response(429)
                self.end_headers()
                return
            if self.headers.get("Authorization") != f"Bearer {KEY}":
                self.send_response(401)
                self.end_headers()
                return
            if u.path == "/v2/futures/conversion":
                q = parse_qs(u.query)
                body = {"future_contract": f"{q['future'][0]}Z6", "multiplier": st["mult"], "additive": st["add"]}
            elif u.path.endswith("/classic/zero"):
                t = u.path.split("/")[2]
                base = 28900.0 if t == "NQ_NDX" else 6400.0
                body = {"timestamp": st["ts"], "ticker": t, "spot": base, "zero_gamma": base - 60, "major_pos_vol": base + 250,
                        "major_neg_vol": base - 150, "major_pos_oi": base + 300, "major_neg_oi": base - 200, "strikes": []}
                for k in st["omit"]:
                    body.pop(k, None)
            else:
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = GexBotCfg(base_url=f"http://127.0.0.1:{srv.server_address[1]}/v2", backoff_base_seconds=0.001, max_retries=2)
    yield cfg, st
    srv.shutdown()


def client(cfg, key=KEY):
    return GexBotClient(cfg, key, sleep=lambda s: None, rng=random.Random(1))


def test_fetch_converts_to_future_and_maps_levels(api):
    cfg, st = api
    res = fetch_options_levels(client(cfg), cfg, DATE, "nq")
    assert res.options["gamma_flip"] == 28900 - 60 + 40 and res.options["call_wall"] == 28900 + 250 + 40
    assert res.options["put_wall"] == 28900 - 150 + 40 and res.warnings == []
    d = res.options["gexbot"]
    assert d["conversion"]["future_contract"] == "NQZ6" and d["raw_ndx"]["zero_gamma"] == 28840
    assert "options.call_wall" in res.provenance and res.provenance["options.call_wall"]["mapping"] == "major_pos_vol"
    assert any("/NQ_NDX/classic/zero" in c for c in st["calls"]) and any("ticker=NDX&future=NQ" in c for c in st["calls"])


def test_headers_are_sent_per_docs(api):
    cfg, st = api
    fetch_options_levels(client(cfg), cfg, DATE)
    h = st["headers"][0]
    assert h["Authorization"] == f"Bearer {KEY}" and h["User-Agent"] == "ofo-council/1.0" and h["Accept"] == "application/json"


def test_oi_basis_and_es_instrument(api):
    cfg, st = api
    cfg.wall_basis = "oi"
    res = fetch_options_levels(client(cfg), cfg, DATE, "es")
    assert res.options["call_wall"] == 6400 + 300 + 40 and res.options["put_wall"] == 6400 - 200 + 40
    assert any("/ES_SPX/classic/zero" in c for c in st["calls"]) and any("ticker=SPX&future=ES" in c for c in st["calls"])
    assert "es.options.gamma_flip" in res.provenance


def test_retry_on_429_then_ok(api):
    cfg, st = api
    st["fail_first"] = 2
    assert fetch_options_levels(client(cfg), cfg, DATE).options["gamma_flip"]
    assert len(st["calls"]) == 4  # 2x429 + ok + conversione


def test_429_exhausted_raises_sanitized(api):
    cfg, st = api
    st["fail_first"] = 99
    with pytest.raises(GexBotError) as ex:
        fetch_options_levels(client(cfg), cfg, DATE)
    assert "429" in str(ex.value) and KEY not in str(ex.value)


def test_bad_key_and_missing_key(api, monkeypatch):
    cfg, _ = api
    with pytest.raises(GexBotError) as ex:
        client(cfg, "wrong-key").classic("NQ_NDX", "zero")
    assert "401" in str(ex.value) and "wrong-key" not in str(ex.value)
    with pytest.raises(GexBotError):
        client(cfg, None).classic("NQ_NDX", "zero")


def test_staleness_and_lookahead_warnings(api):
    cfg, st = api
    st["ts"] = datetime(2026, 10, 5, 8, 0, tzinfo=NY).timestamp()
    assert any("vecchi" in w for w in fetch_options_levels(client(cfg), cfg, DATE).warnings)
    st["ts"] = datetime(2026, 10, 8, 10, 0, tzinfo=NY).timestamp()
    assert any("DOPO" in w for w in fetch_options_levels(client(cfg), cfg, DATE).warnings)


def test_missing_fields_are_not_invented(api):
    cfg, st = api
    st["omit"] = ["major_neg_vol"]
    res = fetch_options_levels(client(cfg), cfg, DATE)
    assert "put_wall" not in res.options and any("major_neg_vol" in w for w in res.warnings)


def test_invalid_conversion_aborts_not_unconverted(api):
    cfg, st = api
    st["mult"] = 0
    with pytest.raises(GexBotError) as ex:
        fetch_options_levels(client(cfg), cfg, DATE)
    assert "NON convertiti" in str(ex.value)


def test_merge_preserves_other_fields_and_records_previous(project, api):
    cfg, _ = api
    path = project.path("data_dir") / "levels.json"
    before = json.loads(path.read_text())
    res = fetch_options_levels(client(cfg), cfg, DATE)
    merge_into_levels(path, res, DATE)
    after = json.loads(path.read_text())
    assert after["levels"] == before["levels"] and after["spot"] == before["spot"] and after["es"] == before["es"]
    assert after["options"]["gamma_flip"] == 28880.0 and after["options"]["source"] == "gexbot"
    assert after["provenance"]["options.gamma_flip"]["previous_value"] == before["options"]["gamma_flip"]
    assert path.with_suffix(".json.bak").exists()


def test_refresh_failure_keeps_existing_levels(project, api):
    cfg, st = api
    project.gexbot.base_url, project.gexbot.max_retries, project.gexbot.backoff_base_seconds = cfg.base_url, 0, 0.001
    st["fail_first"] = 99
    path = project.path("data_dir") / "levels.json"
    before = path.read_text()
    msgs, warns = refresh_levels(project, DATE, path, mock=False, client=client(cfg))
    assert msgs == [] and any("NON modificati" in w for w in warns) and path.read_text() == before


def test_refresh_mock_is_labelled(project):
    path = project.path("data_dir") / "levels.json"
    msgs, warns = refresh_levels(project, DATE, path, mock=True)
    doc = json.loads(path.read_text())
    assert len(msgs) == 2 and any("SINTETICI" in w for w in warns)
    assert doc["options"]["source"] == "gexbot_mock" and doc["provenance"]["es.options.call_wall"]["source"] == "gexbot_mock"
    assert abs(doc["es"]["options"]["call_wall"] - doc["es"]["spot"]) < 200  # scala ES, non NQ


def test_pipeline_uses_gexbot_levels_and_reports_provenance(project):
    from src.pipeline import run_pipeline
    from tests.conftest import opts
    refresh_levels(project, DATE, project.path("data_dir") / "levels.json", mock=True)
    res = run_pipeline(opts(project))
    assert "options.call_wall" in res.report["data_provenance"]
    assert "Origine dei livelli importati" in res.paths["report_md"].read_text()


def test_key_never_in_artifacts(project, monkeypatch):
    from src.pipeline import run_pipeline
    from tests.conftest import opts
    monkeypatch.setenv("GEXBOT_API_KEY", KEY)
    refresh_levels(project, DATE, project.path("data_dir") / "levels.json", mock=True)
    res = run_pipeline(opts(project))
    for p in res.paths.values():
        assert KEY not in p.read_text(encoding="utf-8")
    from src.security import redact
    assert KEY not in redact(f"GEXBOT_API_KEY={KEY} Authorization: Bearer {KEY}")
