import pytest
from pydantic import ValidationError

from src.schemas import (AgentMessage, AnalystOutput, RiskVerdict, Scenario, StrategistOutput,
                         confidence_label, worst_verdict)


def scen(**kw):
    base = dict(id="S1", direction="LONG", setup_type="x", entry_zone=[10, 9], invalidation=5, target_1=20,
                expected_rr=2, thesis="t", failure_condition="f", confidence=50)
    base.update(kw)
    return base


def test_entry_zone_sorted_and_validated():
    assert Scenario(**scen()).entry_zone == [9, 10]
    with pytest.raises(ValidationError):
        Scenario(**scen(entry_zone=[1, 2, 3]))


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        AnalystOutput(agent="price_action", confidence=101)
    with pytest.raises(ValidationError):
        AnalystOutput(agent="price_action", confidence="alta")


def test_strategist_max_two_scenarios():
    with pytest.raises(ValidationError):
        StrategistOutput(agent="strategist", confidence=10, scenarios=[scen(id="a"), scen(id="b"), scen(id="c")])


def test_strategist_requires_no_trade_when_empty():
    with pytest.raises(ValidationError):
        StrategistOutput(agent="strategist", confidence=10)
    assert StrategistOutput(agent="strategist", confidence=10, no_trade=True).no_trade


def test_message_addressee_validated():
    with pytest.raises(ValidationError):
        AgentMessage(addressed_to="nobody", message="x")
    assert AgentMessage(addressed_to="options_flow", message_type="CHALLENGE", message="x").message_type.value == "CHALLENGE"


def test_worst_verdict_veto_wins():
    assert worst_verdict(RiskVerdict.APPROVED, RiskVerdict.VETO, RiskVerdict.APPROVED_WITH_CAUTION) == RiskVerdict.VETO
    assert worst_verdict(RiskVerdict.APPROVED, RiskVerdict.APPROVED) == RiskVerdict.APPROVED


def test_confidence_labels():
    assert [confidence_label(v) for v in (0, 20, 21, 40, 41, 60, 61, 80, 81, 100)] == [
        "praticamente nulla", "praticamente nulla", "debole", "debole", "moderata", "moderata",
        "buona", "buona", "elevata", "elevata"]
