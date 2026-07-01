"""Device watcher: connect/disconnect event emission from a polled udid set (FR-1)."""
from app.device.discovery import DeviceWatcher


async def test_emits_connected_then_disconnected(events):
    udid_sets = [{"udid-1"}, {"udid-1"}, set()]

    async def list_udids():
        return udid_sets.pop(0)

    watcher = DeviceWatcher(on_event=events, list_connected_udids=list_udids)

    await watcher.poll_once()  # nothing known -> udid-1 connects
    await watcher.poll_once()  # steady state -> no new events
    await watcher.poll_once()  # udid-1 disconnects

    event_names = [event for event, _ in events.collected]
    assert event_names == ["device_connected", "device_disconnected"]
