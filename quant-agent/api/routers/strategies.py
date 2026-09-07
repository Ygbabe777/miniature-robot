from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from api.deps import get_db
from memory.strategy_memory import trace_lineage
from strategies.registry import StrategyRegistry

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("/leaderboard")
def leaderboard(session: Session = Depends(get_db)):
    registry = StrategyRegistry(session)
    return [row.__dict__ for row in registry.leaderboard()]


@router.get("/{strategy_id}/lineage")
def lineage(strategy_id: str, session: Session = Depends(get_db)):
    try:
        return trace_lineage(session, strategy_id)
    except ValueError as exc:
        return {"error": str(exc)}
