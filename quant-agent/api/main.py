"""FastAPI application — dashboard API (spec section 35).

Serves the read-only dashboard endpoints plus the one *write* endpoint
that matters for safety: deployment approval/rejection (spec section 28).
Nothing here can place a trade — that only ever happens through
`agents.live_execution_agent`, which this API does not call.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database import init_db

from .routers import deployments, papers, strategies, system

app = FastAPI(title="Quant Agent API", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


app.include_router(papers.router)
app.include_router(strategies.router)
app.include_router(deployments.router)
app.include_router(system.router)


@app.get("/health")
def health():
    return {"status": "ok"}


try:
    app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")
except RuntimeError:
    pass  # dashboard/ directory not present in some deployment contexts
