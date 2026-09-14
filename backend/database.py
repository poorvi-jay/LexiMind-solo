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
#
# timeout=30: pysqlite gives up after 5s by default and raises "database is
# locked", which surfaces as a 500. One writer plus a thread pool of readers
# makes that reachable on a deployed instance in a way it never was locally,
# so wait for the lock instead of failing.
engine = create_engine(
    DATABASE_URL,
    connect_args=(
        {"check_same_thread": False, "timeout": 30}
        if DATABASE_URL.startswith("sqlite")
        else {}
    ),
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Per-connection SQLite settings. Both are off by default.

    foreign_keys  SQLite ignores FK constraints unless this is enabled.
    journal_mode  The default rollback journal takes an exclusive lock for
                  every write, so a read concurrent with a write blocks.
                  WAL lets them overlap, which matters once more than one
                  person is using a deployed instance. It is persistent —
                  set on the file, not the connection — so re-running it is
                  a no-op, and it creates the -wal and -shm sidecar files
                  already covered by .gitignore.
    """
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_db():
    """FastAPI dependency — yields a session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added to existing tables after the first release. create_all() creates
# missing *tables* but never missing *columns*, so without this an existing
# leximind.db would raise "no such column" on every query touching the new field.
# The PRD's Alembic would own this; with SQLite and one developer, an explicit
# ADD COLUMN on startup is the honest small version of the same job.
_ADDED_COLUMNS = {
    "users": {
        # Backfilled to the row's creation time: any token older than that
        # cannot exist, so existing sessions survive the upgrade.
        "password_changed_at": "DATETIME",
    },
}


def _add_missing_columns():
    from sqlalchemy import text

    with engine.begin() as connection:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))
            }
            if not existing:
                continue  # table isn't there yet; create_all just made it correctly
            for column, ddl in columns.items():
                if column in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
                if table == "users" and column == "password_changed_at":
                    connection.execute(
                        text("UPDATE users SET password_changed_at = created_at")
                    )


def init_db():
    """Create any missing tables and columns. Safe to call on every startup."""
    from backend import models  # noqa: F401 — registers models on Base.metadata

    Base.metadata.create_all(bind=engine)
    if DATABASE_URL.startswith("sqlite"):
        _add_missing_columns()
