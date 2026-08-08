"""
backend/database.py
SQLite + SQLAlchemy foundation for M2 (auth, writing notepad, documents).

The PRD specifies PostgreSQL 15 + Alembic. This solo build uses SQLite with
create_all() instead — same schema shape, far less ceremony, no server to run.
Postgres-specific bits (gen_random_uuid(), native UUID/JSONB) are replaced with
portable equivalents so the models stay readable.
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

# DB file sits next to the backend package: backend/leximind.db
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "leximind.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH}")

# check_same_thread=False: FastAPI serves requests from a thread pool, and a
# SQLite connection is otherwise pinned to the thread that created it.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite ignores FK constraints unless enabled per-connection."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create any missing tables. Safe to call on every startup."""
    from backend import models  # noqa: F401 — registers models on Base.metadata

    Base.metadata.create_all(bind=engine)
