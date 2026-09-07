"""Formal Strategy Specification (spec section 8) + evolution metadata (section 9).

Strategies are NEVER represented only as Python code. `StrategySpec` is the
source of truth; `strategies/generator/codegen.py` compiles it into an
executable `Strategy` object the backtesting engine can run. This is what
lets the Strategy Architect Agent mutate strategies systematically instead
of hand-editing code.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class StrategyStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    RESEARCHED = "RESEARCHED"
    HYPOTHESIS = "HYPOTHESIS"
    PROTOTYPE = "PROTOTYPE"
    BACKTESTING = "BACKTESTING"
    VALIDATION = "VALIDATION"
    ROBUSTNESS = "ROBUSTNESS"
    OOS = "OOS"
    PAPER_TRADING = "PAPER_TRADING"
    LIVE_CANDIDATE = "LIVE_CANDIDATE"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    LIVE = "LIVE"
    MONITORING = "MONITORING"
    REVIEW = "REVIEW"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"
    KILLED = "KILLED"


# Legal forward/backward transitions for the promotion pipeline (spec section 38/39).
# A strategy CAN move backward (e.g. LIVE -> PAPER_TRADING on degradation).
ALLOWED_TRANSITIONS: dict[StrategyStatus, set[StrategyStatus]] = {
    StrategyStatus.DISCOVERED: {StrategyStatus.RESEARCHED, StrategyStatus.REJECTED},
    StrategyStatus.RESEARCHED: {StrategyStatus.HYPOTHESIS, StrategyStatus.REJECTED},
    StrategyStatus.HYPOTHESIS: {StrategyStatus.PROTOTYPE, StrategyStatus.REJECTED},
    StrategyStatus.PROTOTYPE: {StrategyStatus.BACKTESTING, StrategyStatus.REJECTED},
    StrategyStatus.BACKTESTING: {StrategyStatus.VALIDATION, StrategyStatus.REJECTED},
    StrategyStatus.VALIDATION: {StrategyStatus.ROBUSTNESS, StrategyStatus.REJECTED},
    StrategyStatus.ROBUSTNESS: {StrategyStatus.OOS, StrategyStatus.REJECTED},
    StrategyStatus.OOS: {StrategyStatus.PAPER_TRADING, StrategyStatus.REJECTED},
    StrategyStatus.PAPER_TRADING: {StrategyStatus.LIVE_CANDIDATE, StrategyStatus.RESEARCHED, StrategyStatus.REJECTED},
    StrategyStatus.LIVE_CANDIDATE: {StrategyStatus.HUMAN_APPROVAL, StrategyStatus.PAPER_TRADING},
    StrategyStatus.HUMAN_APPROVAL: {StrategyStatus.LIVE, StrategyStatus.PAPER_TRADING, StrategyStatus.REJECTED},
    StrategyStatus.LIVE: {StrategyStatus.MONITORING},
    StrategyStatus.MONITORING: {StrategyStatus.REVIEW, StrategyStatus.LIVE, StrategyStatus.ACTIVE},
    StrategyStatus.REVIEW: {StrategyStatus.ACTIVE, StrategyStatus.PAUSED, StrategyStatus.RETIRED,
                            StrategyStatus.PAPER_TRADING, StrategyStatus.RESEARCHED},
    StrategyStatus.ACTIVE: {StrategyStatus.MONITORING, StrategyStatus.PAUSED, StrategyStatus.RETIRED},
    StrategyStatus.PAUSED: {StrategyStatus.ACTIVE, StrategyStatus.RETIRED},
    StrategyStatus.RETIRED: set(),
    StrategyStatus.REJECTED: set(),
    StrategyStatus.KILLED: set(),
}


def is_transition_allowed(current: StrategyStatus, target: StrategyStatus) -> bool:
    if target == StrategyStatus.KILLED:
        return True  # kill switch can always fire, from any state
    return target in ALLOWED_TRANSITIONS.get(current, set())


class FeatureSpec(BaseModel):
    name: str
    expression: str = Field(description="How the feature is computed, e.g. 'ofi_5m', 'atr_14'")
    lookback: int


class SignalLogic(BaseModel):
    """Boolean/threshold conditions combined with AND/OR, e.g.

    {"all_of": ["ofi > threshold", "volatility_regime == 'high'"]}
    """

    all_of: list[str] = Field(default_factory=list)
    any_of: list[str] = Field(default_factory=list)


class RiskModel(BaseModel):
    stop_loss: str | None = None          # e.g. "1.5 * atr_14"
    take_profit: str | None = None
    max_risk_per_trade_pct: float = 1.0
    max_position_size: float | None = None
    time_stop_bars: int | None = None


class ExecutionModel(BaseModel):
    order_type: str = "market"  # market, limit, stop
    commission_per_contract: float = 2.25
    slippage_ticks: float = 1.0
    assume_bid_ask_spread: bool = True
    trading_hours: str | None = None  # e.g. "09:30-16:00 America/New_York"


class Constraints(BaseModel):
    regime_filters: list[str] = Field(default_factory=list)  # e.g. ["trending", "high_liquidity"]
    session_filters: list[str] = Field(default_factory=list)
    max_correlated_exposure: float | None = None


class StrategySpec(BaseModel):
    strategy_id: str
    name: str
    hypothesis_id: str | None = None

    market: str  # e.g. "NQ", "ES", "MNQ"
    timeframe: str  # e.g. "5m", "15m", "1h"

    features: list[FeatureSpec] = Field(default_factory=list)
    signal_logic: SignalLogic
    entry_logic: SignalLogic
    exit_logic: SignalLogic
    risk_model: RiskModel = Field(default_factory=RiskModel)
    execution_model: ExecutionModel = Field(default_factory=ExecutionModel)
    parameters: dict[str, float | int | str] = Field(default_factory=dict)
    constraints: Constraints = Field(default_factory=Constraints)

    # Evolution metadata (spec section 9)
    parent_strategy_id: str | None = None
    mutation_reason: str | None = None
    parameter_change: dict | None = None
    expected_effect: str | None = None
    actual_effect: str | None = None

    version: int = 1
    status: StrategyStatus = StrategyStatus.DISCOVERED
    created_at: datetime = Field(default_factory=datetime.utcnow)
