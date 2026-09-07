from datetime import datetime, timedelta

import pytest

from backtesting.costs import CostModel
from execution.brokers.paper_broker import PaperBroker
from execution.orders import OrderRequest, OrderSide, OrderType
from execution.position_manager import PositionManager
from risk.kill_switch import KillSwitch
from risk.risk_manager import RiskLimits, RiskManager


def _broker():
    cm = CostModel(commission_per_contract=1, slippage_ticks=1, tick_size=0.25, tick_value=5)
    return PaperBroker(cost_model=cm)


def _order(strategy_id="s1", market="NQ", qty=1.0, side=OrderSide.BUY):
    return OrderRequest(strategy_id=strategy_id, mode="paper", market=market, side=side,
                         order_type=OrderType.MARKET, quantity=qty)


def test_pre_trade_check_blocks_stale_data():
    pm = PositionManager()
    rm = RiskManager(RiskLimits(), _broker(), pm)
    rm.set_strategy_status("s1", "ACTIVE")
    result = rm.pre_trade_check(_order())
    assert result.allowed is False
    assert any("data" in r.lower() for r in result.reasons)


def test_pre_trade_check_passes_when_all_conditions_met():
    broker = _broker()
    pm = PositionManager()
    rm = RiskManager(RiskLimits(), broker, pm)
    rm.set_strategy_status("s1", "ACTIVE")
    rm.record_data_update("NQ", datetime.utcnow())
    result = rm.pre_trade_check(_order())
    assert result.allowed is True, result.reasons


def test_pre_trade_check_blocks_disconnected_broker():
    broker = _broker()
    broker.disconnect()
    pm = PositionManager()
    rm = RiskManager(RiskLimits(), broker, pm)
    rm.set_strategy_status("s1", "ACTIVE")
    rm.record_data_update("NQ", datetime.utcnow())
    result = rm.pre_trade_check(_order())
    assert result.allowed is False
    assert any("connected" in r.lower() for r in result.reasons)


def test_pre_trade_check_blocks_excessive_order_size():
    broker = _broker()
    pm = PositionManager()
    rm = RiskManager(RiskLimits(max_order_size=2), broker, pm)
    rm.set_strategy_status("s1", "ACTIVE")
    rm.record_data_update("NQ", datetime.utcnow())
    result = rm.pre_trade_check(_order(qty=10))
    assert result.allowed is False
    assert any("order size" in r.lower() for r in result.reasons)


def test_pre_trade_check_blocks_daily_loss_breach():
    broker = _broker()
    pm = PositionManager()
    rm = RiskManager(RiskLimits(max_daily_loss=100), broker, pm)
    rm.set_strategy_status("s1", "ACTIVE")
    rm.record_data_update("NQ", datetime.utcnow())
    rm.record_pnl("s1", -500)
    result = rm.pre_trade_check(_order())
    assert result.allowed is False
    assert any("daily loss" in r.lower() for r in result.reasons)


def test_pre_trade_check_blocks_invalid_order_quantity():
    broker = _broker()
    pm = PositionManager()
    rm = RiskManager(RiskLimits(), broker, pm)
    rm.set_strategy_status("s1", "ACTIVE")
    rm.record_data_update("NQ", datetime.utcnow())
    result = rm.pre_trade_check(_order(qty=0))
    assert result.allowed is False
    assert any("positive" in r.lower() for r in result.reasons)


def test_kill_switch_disables_strategy_and_logs_event(db_session):
    broker = _broker()
    broker.set_last_price("NQ", 100.0)
    pm = PositionManager()
    pm.update("s1", "NQ", 2)
    rm = RiskManager(RiskLimits(), broker, pm)
    rm.set_strategy_status("s1", "ACTIVE")

    ks = KillSwitch(broker=broker, position_manager=pm, risk_manager=rm, session=db_session)
    ks.trigger(strategy_id="s1", rule="max_drawdown_breach", message="Drawdown exceeded 20%.")

    result = rm.pre_trade_check(_order())
    assert result.allowed is False
    assert any("disabled" in r.lower() for r in result.reasons)

    from database.models import RiskEvent
    events = db_session.query(RiskEvent).filter_by(strategy_id="s1").all()
    assert len(events) == 1
    assert events[0].severity == "KILL_SWITCH"
