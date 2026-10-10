import datetime as dt

from ofo.massive import key_date, select_keys

KEYS = ["x/2025/11/2025-11-03.csv.gz", "x/2025/11/2025-11-05.csv.gz",
        "x/2025/12/2025-12-01.csv.gz", "x/readme.txt"]


def test_key_date_and_selection_ignore_layout():
    assert key_date(KEYS[0]) == dt.date(2025, 11, 3)
    assert key_date("x/readme.txt") is None
    got = select_keys(KEYS, dt.date(2025, 11, 4), dt.date(2025, 12, 31))
    assert got == [KEYS[1], KEYS[2]]
