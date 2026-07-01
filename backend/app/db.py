"""SQLite engine / session helpers.

For MVP, schema is created directly from app.models at first run — there is no prior
schema to migrate from. If the schema changes after the first release, introduce
Alembic at that point (see AGENTS.md §9).
"""
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

_engine = None


def get_engine():
    """
    Return the process-wide SQLite engine, creating it (and the DB file's parent
    directory) on first call.

    Returns:
        A SQLAlchemy `Engine` bound to `settings.db_path`.
    """
    global _engine
    if _engine is None:
        settings.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})
    return _engine


def init_db() -> None:
    """Create all tables that don't already exist."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Session:
    """
    Return a new `Session` bound to the process-wide engine.

    Returns:
        A new SQLModel `Session`. Caller is responsible for closing it
        (use as a context manager).
    """
    return Session(get_engine())
