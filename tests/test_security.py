import json
import re
from pathlib import Path

from src.security import REDACTED, contains_secret, redact, redact_obj

ROOT = Path(__file__).resolve().parent.parent


def test_redacts_bearer_and_keys(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "supersecretvalue123")
    txt = "Authorization: Bearer abcdef123456 e OMNIROUTE_API_KEY=supersecretvalue123 sk-abcdefgh12345678"
    out = redact(txt)
    assert "abcdef123456" not in out and "supersecretvalue123" not in out and "sk-abcdefgh12345678" not in out
    assert REDACTED in out


def test_redact_literal_env_key_anywhere(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "zzz-literal-key-987")
    assert "zzz-literal-key-987" not in redact("errore con zzz-literal-key-987 nel testo")


def test_redact_obj_nested(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "nestedsecret999")
    o = redact_obj({"a": ["x nestedsecret999"], "b": {"c": "Bearer tok123456"}})
    assert "nestedsecret999" not in json.dumps(o) and "tok123456" not in json.dumps(o)


def test_contains_secret_false_for_plain_text():
    assert not contains_secret("Nessun segreto qui, solo analisi NQ 29000")


def test_gitignore_protects_secrets():
    gi = (ROOT / ".gitignore").read_text()
    for entry in (".env", "__pycache__/", ".venv/", "transcripts/", "reports/"):
        assert entry in gi


def test_no_hardcoded_key_in_sources():
    pat = re.compile(r"(sk-[A-Za-z0-9]{16,})|(OMNIROUTE_API_KEY\s*=\s*['\"][^'\"]+['\"])")
    for p in list(ROOT.glob("src/*.py")) + list(ROOT.glob("*.py")) + [ROOT / "config.yaml"]:
        assert not pat.search(p.read_text(encoding="utf-8")), p


def test_no_broker_execution_code():
    forbidden = ["place_" + "order", "submit_" + "order", "cancel_" + "order", "modify_" + "order",
                 "import " + "ib_insync", "import " + "alpaca", "ccxt", "tradovate", "metatrader"]
    for p in list(ROOT.glob("src/*.py")) + list(ROOT.glob("*.py")) + list(ROOT.glob("tools/*.py")):
        text = p.read_text(encoding="utf-8").lower()
        for f in forbidden:
            assert f.lower() not in text, f"{f} in {p}"
