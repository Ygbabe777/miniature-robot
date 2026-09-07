import pytest
from pydantic import ValidationError

from strategies.schemas.hypothesis import Direction, Hypothesis
from strategies.schemas.strategy import StrategyStatus, is_transition_allowed


def _hypothesis(**overrides):
    defaults = dict(
        hypothesis_id="h1", statement="X predicts Y", economic_rationale="because Z",
        measurable_variables=["x", "y"], expected_direction=Direction.POSITIVE, expected_horizon="5m",
        falsification_criteria="reject if p >= 0.05", required_dataset="NQ 5m OHLCV",
        statistical_test="OLS-HAC",
    )
    defaults.update(overrides)
    return Hypothesis(**defaults)


def test_hypothesis_requires_falsification_criteria():
    with pytest.raises(ValidationError):
        _hypothesis(falsification_criteria="   ")


def test_hypothesis_valid_construction():
    h = _hypothesis()
    assert h.status.value == "PROPOSED"


@pytest.mark.parametrize("current,target,expected", [
    (StrategyStatus.DISCOVERED, StrategyStatus.RESEARCHED, True),
    (StrategyStatus.DISCOVERED, StrategyStatus.LIVE, False),
    (StrategyStatus.BACKTESTING, StrategyStatus.LIVE, False),
    (StrategyStatus.LIVE_CANDIDATE, StrategyStatus.HUMAN_APPROVAL, True),
    (StrategyStatus.HUMAN_APPROVAL, StrategyStatus.LIVE, True),
    (StrategyStatus.PAPER_TRADING, StrategyStatus.LIVE_CANDIDATE, True),
    (StrategyStatus.LIVE, StrategyStatus.MONITORING, True),
    (StrategyStatus.MONITORING, StrategyStatus.LIVE, True),
    (StrategyStatus.REVIEW, StrategyStatus.PAPER_TRADING, True),
    (StrategyStatus.RETIRED, StrategyStatus.ACTIVE, False),
])
def test_state_machine_transitions(current, target, expected):
    assert is_transition_allowed(current, target) is expected


def test_kill_switch_always_allowed_from_any_state():
    for status in StrategyStatus:
        assert is_transition_allowed(status, StrategyStatus.KILLED) is True


def test_cannot_skip_backtesting_to_go_straight_to_paper_trading():
    assert is_transition_allowed(StrategyStatus.PROTOTYPE, StrategyStatus.PAPER_TRADING) is False
