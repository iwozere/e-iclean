"""settings.get / settings.set over the IPC dispatcher (docs/ipc_protocol.md) - the
frontend's actual path for remembering the destination folder across restarts."""
from app.ipc import handlers  # noqa: F401  (registers settings.get / settings.set)
from app.ipc.dispatcher import dispatch


async def test_settings_set_then_get_round_trips_destination_default():
    await dispatch("settings.set", {"values": {"destination_default": "D:\\Photos"}})

    result = await dispatch("settings.get", {})

    assert result["values"]["destination_default"] == "D:\\Photos"


async def test_settings_get_before_any_set_has_no_destination_default():
    result = await dispatch("settings.get", {})

    assert "destination_default" not in result["values"]
