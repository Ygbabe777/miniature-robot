import datetime as dt
import json

import pytest

from ofo.budget import BudgetBlocked, BudgetGovernor
from ofo.llm.client import LLMError
from ofo.llm.pool import ModelPool


def at(y, m, d, h=10):
    return dt.datetime(y, m, d, h).timestamp()


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


TUESDAY = at(2026, 10, 13)          # the owner's second working day


def gov(tmp_path, clock=None, **cfg):
    cfg.setdefault("daily_limit", True)
    return BudgetGovernor(tmp_path / "b.json", {"weekly_usd": 15, "remaining_usd": 12, **cfg}, now=clock or Clock(TUESDAY))


def test_daily_allowance_is_the_balance_split_over_the_working_days_left(tmp_path):
    g = gov(tmp_path)
    assert g.days_left() == 4 and g.remaining() == 12.0                     # Tue, Wed, Thu, Fri
    assert abs(g.daily_cap() - 3.0) < 1e-9 and abs(g.autonomous_cap() - 2.55) < 1e-9   # 15% kept for the chat
    g.record("researcher", "m", 500_000, 500_000)                           # 1M tokens at the default $3/M = $3.00
    assert abs(g.spent_today() - 3.0) < 1e-9 and abs(g.remaining() - 9.0) < 1e-9
    assert abs(g.daily_cap() - 3.0) < 1e-9                                  # today's cap does not shrink as you spend it
    nxt = BudgetGovernor(tmp_path / "b.json", {"daily_limit": True}, now=Clock(at(2026, 10, 14)))     # tomorrow: 9 / 3 days
    assert nxt.days_left() == 3 and abs(nxt.daily_cap() - 3.0) < 1e-9 and nxt.spent_today() == 0.0


def test_modes_economy_then_pause_but_the_chat_keeps_its_reserve(tmp_path):
    g = gov(tmp_path)
    assert g.mode() == "normal" and g.allow("researcher") == (True, "")
    g.record("researcher", "m", 400_000, 400_000)                           # $2.40 of the $2.55 autonomous cap = 94%
    assert g.mode() == "economy" and g.economy()
    g.record("researcher", "m", 50_000, 50_000)                             # $0.30 more: over the autonomous cap
    assert g.mode() == "paused_day"
    ok, why = g.allow("researcher")
    assert ok is False and "autonomous allowance" in why
    assert g.allow("strategist")[0] is True                                  # the chat still works (cap incl. reserve = $3.00)
    with pytest.raises(BudgetBlocked) as e:
        g.check("scout")
    assert isinstance(e.value, LLMError)                                     # every agent already degrades gracefully on these
    g.record("strategist", "m", 100_000, 100_000)                            # $0.60: total today $3.30 > $3.00
    assert g.allow("strategist")[0] is False


def test_weekend_and_weekly_reserve(tmp_path):
    sat = gov(tmp_path, Clock(at(2026, 10, 17)))                             # Saturday
    assert sat.mode() == "paused_weekend" and sat.days_left() == 0 and sat.daily_cap() == 0.0
    assert sat.allow("researcher")[0] is False and sat.allow("strategist")[0] is True
    low = gov(tmp_path / "x", remaining_usd=0.5)                             # under 5% of $15 = $0.75
    assert low.mode() == "blocked" and low.allow("strategist")[0] is False


def test_new_week_restores_the_full_allowance(tmp_path):
    g = gov(tmp_path)
    g.record("researcher", "m", 1_000_000, 1_000_000)
    assert g.remaining() < 12
    mon = BudgetGovernor(tmp_path / "b.json", {"daily_limit": True}, now=Clock(at(2026, 10, 19)))      # next Monday
    assert abs(mon.remaining() - 15.0) < 1e-9 and mon.days_left() == 5 and abs(mon.daily_cap() - 3.0) < 1e-9


def test_per_model_prices_and_learning_the_real_price_from_balance_reports(tmp_path):
    g = gov(tmp_path, prices={"cheap": {"in": 0.2, "out": 0.4}})
    assert abs(g.cost("cheap", 1_000_000, 1_000_000) - 0.6) < 1e-9
    assert abs(g.cost("other", 1_000_000, 0) - 3.0) < 1e-9                    # unknown model: conservative default
    h = gov(tmp_path / "y")
    h.record("researcher", "m", 1_000_000, 1_000_000)                          # counted 2M tokens (at the default price)
    learned = h.set_remaining(11.0)["learned_usd_per_million"]                # but only $1 really disappeared from $12
    assert abs(learned - 0.5) < 1e-9 and abs(h.blended() - 0.5) < 1e-9 and h.remaining() == 11.0
    h.record("researcher", "m", 1_000_000, 0)                                  # now 1M tokens cost $0.50
    assert abs(h.remaining() - 10.5) < 1e-9
    assert "learned" in h.state()["price_source"]
    reloaded = BudgetGovernor(tmp_path / "y" / "b.json", None, now=Clock(TUESDAY))
    assert abs(reloaded.remaining() - 10.5) < 1e-9 and abs(reloaded.blended() - 0.5) < 1e-9


class FakeClient:
    def __init__(self): self.calls = 0
    def chat_once(self, messages, model=None, temperature=0.2, **kw):
        self.calls += 1
        return {"content": "ok", "model": model, "prompt_tokens": 150_000, "completion_tokens": 150_000, "seconds": 0.0}


def test_pool_records_every_call_and_stops_before_calling_when_the_budget_is_gone(tmp_path):
    g = gov(tmp_path)
    fc = FakeClient()
    pool = ModelPool(fc, {"researcher": ["m"], "strategist": ["m"]}, tmp_path / "led.json", governor=g)
    for i in range(3):                                                         # $0.90 each: 0.9, 1.8, 2.7 (< $2.55 is checked BEFORE each call)
        pool.chat("researcher", [{"role": "user", "content": f"a{i}"}], 0.9)
    assert abs(g.spent_today() - 2.7) < 1e-9 and fc.calls == 3
    with pytest.raises(BudgetBlocked):
        pool.chat("researcher", [{"role": "user", "content": "c"}], 0.9)       # over the autonomous cap now
    assert fc.calls == 3                                                       # not a single token was spent on the refused call
    pool.chat("strategist", [{"role": "user", "content": "d"}], 0.9)           # but chatting with the lab still works (cap $3.00)
    assert fc.calls == 4


def test_cache_hits_cost_nothing(tmp_path):
    g = gov(tmp_path)
    fc = FakeClient()
    pool = ModelPool(fc, {"researcher": ["m"]}, tmp_path / "led.json", cache_dir=tmp_path / "cache", governor=g)
    pool.chat("researcher", [{"role": "user", "content": "same"}], 0.1)
    spent = g.spent_today()
    pool.chat("researcher", [{"role": "user", "content": "same"}], 0.1)
    assert fc.calls == 1 and g.spent_today() == spent


def test_lab_does_not_start_ai_work_when_the_budget_says_no(tmp_path):
    from ofo.app.lab import Lab
    from test_agents import Scripted
    from test_research import synth
    csv = tmp_path / "b.csv.gz"
    synth(n_sessions=120, seed=3).to_csv(csv)
    g = gov(tmp_path / "gov")
    pool = ModelPool(FakeClient(), {"researcher": ["m"]}, tmp_path / "led.json", governor=g)
    lab = Lab(str(csv), data_dir=str(tmp_path / "data"), ideas_dir=str(tmp_path / "ideas"), pool=pool,
              llm={"researcher": Scripted(researcher=["x"]), "planner": Scripted(planner=["x"]), "auditor": Scripted(auditor=["x"])})
    assert lab.llm_allowed("researcher") and not lab.economy()
    assert lab.budget_state()["remaining_usd"] == 12.0
    g.record("researcher", "m", 400_000, 400_000)                              # 94% of today's autonomous cap: economy
    assert lab.economy() and lab.brainstorm(now=10_000.0) == 0 and lab.connect_ideas(now=10_000.0) == 0
    assert lab.scout_gap() >= 45 * 4                                             # searches a quarter as often
    (tmp_path / "ideas").mkdir(exist_ok=True)
    (tmp_path / "ideas" / "a.md").write_text("opening range breakout idea text " * 20)
    lab._ingest_now()
    assert lab.loop().max_chunks == 2                                            # reads 2 chunks of a paper instead of 6
    g.record("researcher", "m", 100_000, 100_000)                                # over the cap: paused
    assert not lab.llm_allowed("researcher") and lab.scout_run() == {"started": False, "reason": "budget"}
    before = lab.loop().processed_ideas.copy()
    (tmp_path / "ideas" / "b.md").write_text("another long idea about vwap " * 20)
    lab._ingest_now()
    assert lab.loop().processed_ideas == before                                 # nothing was read: files wait for tomorrow
    assert lab.autopilot_state()["phase"] in ("budget", "waiting", "testing", "paused")
    st = lab.set_budget(remaining_usd=9.5)
    assert st["remaining_usd"] == 9.5 and lab.state()["budget"]["remaining_usd"] == 9.5


def test_no_daily_limit_by_default_only_the_weekly_allowance_counts(tmp_path):
    g = gov(tmp_path, daily_limit=False)
    g.record("researcher", "m", 1_500_000, 1_500_000)                        # $9.00 in one day: far over the old $3.00 daily cap
    assert g.mode() != "paused_day" and g.allow("researcher") == (True, "")
    assert g.state()["daily_limit"] is False
    g.record("researcher", "m", 500_000, 500_000)                            # remaining $0.00 -> under the safety reserve
    assert g.mode() == "blocked" and g.allow("researcher")[0] is False       # the weekly allowance still holds
    assert BudgetGovernor(tmp_path / "n.json", {"weekly_usd": 15, "remaining_usd": 12}, now=Clock(TUESDAY)).cfg["daily_limit"] is False
    eco = gov(tmp_path / "e", daily_limit=False, remaining_usd=3.0)          # 20% of the week left: save tokens, keep working
    assert eco.mode() == "economy" and eco.allow("researcher")[0] is True
