"""Deployment endpoints — this is where the human-approval gate (spec
section 28) actually lives operationally: `POST /deployments/{id}/approve`
is the only way a `Deployment` row gets `approved_by`/`approved_at` set,
which `agents.live_execution_agent.LiveExecutionAgent` requires before it
will submit a live order in `HUMAN_APPROVAL`/`AUTO_PAPER` mode.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import get_db
from database.models import Deployment

router = APIRouter(prefix="/deployments", tags=["deployments"])


class ApprovalRequest(BaseModel):
    approved_by: str


@router.get("")
def list_deployments(session: Session = Depends(get_db), status: str | None = None):
    stmt = select(Deployment).order_by(Deployment.created_at.desc())
    if status:
        stmt = stmt.where(Deployment.status == status)
    return [d.__dict__ for d in session.execute(stmt).scalars().all()]


@router.post("/{deployment_id}/approve")
def approve_deployment(deployment_id: str, request: ApprovalRequest, session: Session = Depends(get_db)):
    deployment = session.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    deployment.approved_by = request.approved_by
    deployment.approved_at = datetime.utcnow()
    deployment.status = "APPROVED"
    session.flush()
    return {"deployment_id": deployment_id, "status": "APPROVED", "approved_by": request.approved_by}


@router.post("/{deployment_id}/reject")
def reject_deployment(deployment_id: str, session: Session = Depends(get_db)):
    deployment = session.get(Deployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    deployment.status = "REJECTED"
    session.flush()
    return {"deployment_id": deployment_id, "status": "REJECTED"}
