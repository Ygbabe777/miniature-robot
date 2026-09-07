"""Strategy registry: versioning, state-machine transitions, leaderboard.

Backed by the relational schema in `database/models.py`. Every write here
enforces the same `is_transition_allowed` state machine used everywhere
else in the system — there is no back door that lets an agent move a
strategy straight from BACKTESTING to LIVE.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Backtest, Experiment, RobustnessScoreRow, Strategy, StrategyVersion
from strategies.schemas.strategy import StrategySpec, StrategyStatus, is_transition_allowed


class IllegalStateTransition(RuntimeError):
    pass


@dataclass
class LeaderboardRow:
    strategy_id: str
    name: str
    market: str
    timeframe: str
    status: str
    latest_sharpe: float | None
    latest_oos_sharpe: float | None
    robustness_score: float | None


class StrategyRegistry:
    def __init__(self, session: Session):
        self.session = session

    def register(self, spec: StrategySpec) -> Strategy:
        strategy = Strategy(
            strategy_id=spec.strategy_id,
            hypothesis_id=spec.hypothesis_id,
            parent_strategy_id=spec.parent_strategy_id,
            mutation_reason=spec.mutation_reason,
            name=spec.name,
            market=spec.market,
            timeframe=spec.timeframe,
            status=spec.status.value,
        )
        self.session.add(strategy)
        version = StrategyVersion(strategy_id=spec.strategy_id, version=spec.version, spec=spec.model_dump(mode="json"))
        self.session.add(version)
        self.session.flush()
        return strategy

    def new_version(self, spec: StrategySpec, generated_code: str | None = None, git_commit: str | None = None) -> StrategyVersion:
        version = StrategyVersion(
            strategy_id=spec.strategy_id,
            version=spec.version,
            spec=spec.model_dump(mode="json"),
            generated_code=generated_code,
            git_commit=git_commit,
        )
        self.session.add(version)
        self.session.flush()
        return version

    def get(self, strategy_id: str) -> Strategy | None:
        return self.session.get(Strategy, strategy_id)

    def transition(self, strategy_id: str, target: StrategyStatus, reason: str) -> Strategy:
        strategy = self.get(strategy_id)
        if strategy is None:
            raise ValueError(f"Unknown strategy_id {strategy_id}")
        current = StrategyStatus(strategy.status)
        if not is_transition_allowed(current, target):
            raise IllegalStateTransition(
                f"Cannot move strategy {strategy_id} from {current.value} to {target.value}. Reason given: {reason}"
            )
        strategy.status = target.value
        self.session.flush()
        return strategy

    def leaderboard(self) -> list[LeaderboardRow]:
        rows = []
        strategies = self.session.execute(select(Strategy)).scalars().all()
        for s in strategies:
            latest_experiment = self.session.execute(
                select(Experiment)
                .where(Experiment.strategy_id == s.strategy_id)
                .order_by(Experiment.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

            latest_sharpe = latest_oos_sharpe = robustness = None
            if latest_experiment is not None:
                backtests = self.session.execute(
                    select(Backtest).where(Backtest.experiment_id == latest_experiment.experiment_id)
                ).scalars().all()
                for bt in backtests:
                    if bt.split == "TEST":
                        latest_sharpe = bt.metrics.get("sharpe")
                    if bt.split == "FINAL_OOS":
                        latest_oos_sharpe = bt.metrics.get("sharpe")
                rob = self.session.execute(
                    select(RobustnessScoreRow).where(RobustnessScoreRow.experiment_id == latest_experiment.experiment_id)
                ).scalar_one_or_none()
                if rob is not None:
                    robustness = rob.score

            rows.append(
                LeaderboardRow(
                    strategy_id=s.strategy_id,
                    name=s.name,
                    market=s.market,
                    timeframe=s.timeframe,
                    status=s.status,
                    latest_sharpe=latest_sharpe,
                    latest_oos_sharpe=latest_oos_sharpe,
                    robustness_score=robustness,
                )
            )
        rows.sort(key=lambda r: (r.robustness_score or -1), reverse=True)
        return rows
