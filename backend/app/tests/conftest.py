"""Shared test fixtures. Every test gets an isolated in-memory-equivalent SQLite DB —
tests must never touch the real %APPDATA%/EFileTrans state (AGENTS.md §12)."""
from pathlib import Path

import pytest

import app.db as db_module
from app.config import settings


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "APP_DATA_DIR", tmp_path)
    monkeypatch.setattr(db_module, "_engine", None)
    db_module.init_db()
    yield
    monkeypatch.setattr(db_module, "_engine", None)


@pytest.fixture
def destination(tmp_path) -> Path:
    dest = tmp_path / "destination"
    dest.mkdir()
    return dest


class EventCollector:
    """Collects (event, data) tuples emitted via an async on_event callback."""

    def __init__(self) -> None:
        self.collected: list[tuple[str, object]] = []

    async def __call__(self, event: str, data: object) -> None:
        self.collected.append((event, data))


@pytest.fixture
def events() -> EventCollector:
    return EventCollector()
