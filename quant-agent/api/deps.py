"""FastAPI dependency injection helpers."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from database.session import get_sessionmaker


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    finally:
        session.close()
