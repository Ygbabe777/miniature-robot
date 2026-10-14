"""Windows opens text files as cp1252 unless told otherwise: a ligature (U+FB01) from a paper then crashes the Scout with a 'charmap' error."""
import re
import time
from pathlib import Path

import pytest

from ofo.agents.base import AgentError
from ofo.agents.loop import _bounded


def test_every_text_read_and_write_in_the_package_names_its_encoding():
    bad = []
    for f in Path("src").rglob("*.py"):
        s = f.read_text(encoding="utf-8")
        for m in re.finditer(r"\.(write_text|read_text)\(", s):
            j, d = m.end(), 1
            while d:
                d += (s[j] == "(") - (s[j] == ")"); j += 1
            if "encoding" not in s[m.end():j - 1]:
                bad.append(f"{f}:{s[:m.start()].count(chr(10)) + 1}")
    assert not bad, bad


def test_a_slow_ai_call_never_holds_the_study_longer_than_the_deadline():
    assert _bounded(lambda: 7, 5) == 7
    t = time.time()
    with pytest.raises(AgentError):
        _bounded(lambda: time.sleep(5), 0.3)
    assert time.time() - t < 2
    with pytest.raises(ValueError):
        _bounded(lambda: (_ for _ in ()).throw(ValueError("x")), 5)
