from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from config import get_settings
from database.models import RiskEvent, SystemEvent

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/config")
def config_summary():
    settings = get_settings()
    return {
        "mode": settings.mode.value,
        "allow_full_auto_live": settings.allow_full_auto_live,
        "has_llm": settings.has_llm,
        "max_daily_experiments": settings.max_daily_experiments,
        "max_research_papers_per_day": settings.max_research_papers_per_day,
    }


@router.get("/decision-log")
def decision_log(session: Session = Depends(get_db), limit: int = 100, agent: str | None = None):
    stmt = select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
    if agent:
        stmt = stmt.where(SystemEvent.agent == agent)
    events = session.execute(stmt).scalars().all()
    return [
        {"agent": e.agent, "event_type": e.event_type, "decision_log": e.decision_log, "created_at": str(e.created_at)}
        for e in events
    ]


@router.get("/risk-events")
def risk_events(session: Session = Depends(get_db), limit: int = 100):
    events = session.execute(
        select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
    ).scalars().all()
    return [
        {"strategy_id": e.strategy_id, "severity": e.severity, "rule": e.rule, "message": e.message,
         "action_taken": e.action_taken, "created_at": str(e.created_at)}
        for e in events
    ]
