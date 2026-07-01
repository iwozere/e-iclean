"""Persisted key/value app settings (spec §5.7 points 3, 8), backed by the `settings`
table (app.models.Setting). Values are stored as plain strings; callers own any type
conversion, matching the loosely-typed `values: dict` shape of the settings.* IPC
methods (docs/ipc_protocol.md)."""
from typing import Optional

from sqlmodel import select

from app.db import get_session
from app.models import Setting


def get_all_settings() -> dict[str, str]:
    with get_session() as session:
        rows = session.exec(select(Setting)).all()
        return {row.key: row.value for row in rows}


def get_setting(key: str) -> Optional[str]:
    with get_session() as session:
        row = session.get(Setting, key)
        return row.value if row else None


def set_setting(key: str, value: str) -> None:
    with get_session() as session:
        row = session.get(Setting, key)
        if row is None:
            row = Setting(key=key, value=value)
        else:
            row.value = value
        session.add(row)
        session.commit()
