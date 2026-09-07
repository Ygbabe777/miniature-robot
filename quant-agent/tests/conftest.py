import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("QUANT_AGENT_MODE", "HUMAN_APPROVAL")

import pytest

from database import get_sessionmaker, init_db
from database.session import get_engine


@pytest.fixture()
def db_session():
    init_db()
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        # Reset in-memory schema between tests since sqlite ":memory:" is
        # per-connection; using a shared engine keeps state across the test.
        from database.models import Base
        Base.metadata.drop_all(bind=get_engine())
        Base.metadata.create_all(bind=get_engine())
