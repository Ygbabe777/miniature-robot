"""Strategy Architect Agent (spec section 7/8/9).

Converts a `SUPPORTED` hypothesis into one or more `StrategySpec`
candidates. Only accepts hypotheses whose `status == SUPPORTED` — a
strategy built on a hypothesis the Statistical Research Agent could not
support has no business existing.

Every variant beyond the baseline carries `parent_strategy_id`,
`mutation_reason`, and `expected_effect` (spec section 9) — mutations are
never unexplained parameter noise. The number of variants generated is
capped by `MAX_STRATEGY_VARIANTS_PER_HYPOTHESIS` (spec section 40).
"""
from __future__ import annotations

from config import get_settings
from database.models import new_id
from strategies.schemas.hypothesis import Hypothesis, HypothesisStatus
from strategies.schemas.strategy import (
    Constraints,
    ExecutionModel,
    FeatureSpec,
    RiskModel,
    SignalLogic,
    StrategySpec,
    StrategyStatus,
)

from .base import Agent


class StrategyArchitectAgent(Agent):
    name = "strategy_architect_agent"

    def generate_variants(
        self, hypothesis: Hypothesis, market: str, timeframe: str, signal_col: str, threshold: float,
        max_variants: int | None = None,
    ) -> list[StrategySpec]:
        if hypothesis.status != HypothesisStatus.SUPPORTED:
            raise ValueError(
                f"Refusing to build a strategy on hypothesis {hypothesis.hypothesis_id} "
                f"with status={hypothesis.status.value} (must be SUPPORTED)."
            )
        settings = get_settings()
        max_variants = max_variants or settings.max_strategy_variants_per_hypothesis

        baseline_id = new_id("strat")
        baseline = StrategySpec(
            strategy_id=baseline_id,
            name=f"{hypothesis.statement[:60]} — baseline",
            hypothesis_id=hypothesis.hypothesis_id,
            market=market,
            timeframe=timeframe,
            features=[
                FeatureSpec(name="vol20", expression="rolling_std_20", lookback=20),
                FeatureSpec(name="vol20_rank60", expression="vol20:rolling_rank_60", lookback=60),
            ],
            signal_logic=SignalLogic(all_of=[f"{signal_col} > {threshold}"]),
            entry_logic=SignalLogic(all_of=[f"{signal_col} > {threshold}"]),
            exit_logic=SignalLogic(all_of=[f"{signal_col} < {-threshold}"]),
            risk_model=RiskModel(time_stop_bars=20, max_position_size=1, max_risk_per_trade_pct=1.0),
            execution_model=ExecutionModel(),
            parameters={"side": 1, "threshold": threshold},
            status=StrategyStatus.PROTOTYPE,
        )

        variants: list[StrategySpec] = [baseline]

        def _variant(name_suffix: str, reason: str, expected_effect: str, **overrides) -> StrategySpec:
            spec = baseline.model_copy(deep=True)
            spec.strategy_id = new_id("strat")
            spec.name = f"{baseline.name} [{name_suffix}]"
            spec.parent_strategy_id = baseline_id
            spec.mutation_reason = reason
            spec.expected_effect = expected_effect
            for key, value in overrides.items():
                setattr(spec, key, value)
            return spec

        candidate_variants = [
            _variant(
                "tighter threshold", "Test whether a stricter signal threshold improves precision at the cost of frequency.",
                "Fewer, higher-conviction trades; expect similar or better Sharpe, lower trade count.",
                signal_logic=SignalLogic(all_of=[f"{signal_col} > {threshold * 1.5}"]),
                entry_logic=SignalLogic(all_of=[f"{signal_col} > {threshold * 1.5}"]),
                parameters={"side": 1, "threshold": threshold * 1.5},
            ),
            _variant(
                "volatility filter", "Hypothesis #2 predicts the signal is stronger in high-volatility regimes.",
                "Higher win rate when combined with a volatility regime filter, at reduced trade count.",
                signal_logic=SignalLogic(all_of=[f"{signal_col} > {threshold}", "vol20_rank60 > 0.7"]),
                entry_logic=SignalLogic(all_of=[f"{signal_col} > {threshold}", "vol20_rank60 > 0.7"]),
            ),
            _variant(
                "session filter (RTH only)", "Hypothesis #3 predicts the signal concentrates near session open.",
                "Fewer trades restricted to session open hours; expect improved win rate if microstructure-driven.",
                signal_logic=SignalLogic(all_of=[f"{signal_col} > {threshold}", "hour >= 13", "hour < 15"]),
                entry_logic=SignalLogic(all_of=[f"{signal_col} > {threshold}", "hour >= 13", "hour < 15"]),
                constraints=Constraints(session_filters=["session_open"]),
            ),
            _variant(
                "stop/take-profit exit instead of signal reversal",
                "Test whether a fixed risk/reward exit outperforms waiting for signal reversal.",
                "Expect shorter average holding period and different drawdown profile.",
                exit_logic=SignalLogic(),
                risk_model=RiskModel(stop_loss="1.5*vol20*close", take_profit="2.5*vol20*close",
                                      time_stop_bars=20, max_position_size=1),
            ),
        ]

        variants.extend(candidate_variants[: max(0, max_variants - 1)])
        return variants
