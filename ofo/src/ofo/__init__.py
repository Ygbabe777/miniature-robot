"""OFO: the deterministic core (backtest, statistics, prop simulation, judge)."""

BUILD = "2026-10-14.13"      # shown in the page footer and /api/state: tells which version is really running


import codecs as _codecs


def _legacy_text(err):
    """Files written by earlier versions on Windows are cp1252, not UTF-8. Read such bytes as cp1252 instead of crashing; the next save rewrites them as UTF-8."""
    if isinstance(err, UnicodeDecodeError):
        return bytes(err.object[err.start:err.end]).decode("cp1252", "replace"), err.end
    raise err


_codecs.register_error("ofo_legacy", _legacy_text)
