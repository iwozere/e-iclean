"""Process-wide mutable state for the running sidecar.

One sidecar process per app instance, single Tauri-owned client — no multi-tenant
concerns, so a simple module-level singleton is appropriate (not a premature
abstraction to avoid: see AGENTS.md).
"""
from dataclasses import dataclass, field

from app.device.afc_client import AfcClient
from app.services.transfer_engine import TransferEngine


@dataclass
class AppState:
    afc_clients: dict[str, AfcClient] = field(default_factory=dict)
    engines: dict[int, TransferEngine] = field(default_factory=dict)
    session_udid_by_id: dict[int, str] = field(default_factory=dict)


state = AppState()
