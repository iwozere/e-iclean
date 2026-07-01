"""Settings persistence (spec §5.7 points 3, 8): destination folder default must
survive an app restart, not just live in frontend/process memory."""
from app.services.settings_service import get_all_settings, get_setting, set_setting


def test_set_then_get_persists_value():
    assert get_setting("destination_default") is None

    set_setting("destination_default", "C:\\Photos")

    assert get_setting("destination_default") == "C:\\Photos"
    assert get_all_settings() == {"destination_default": "C:\\Photos"}


def test_set_overwrites_existing_value():
    set_setting("destination_default", "C:\\Photos")
    set_setting("destination_default", "D:\\Backups\\Photos")

    assert get_setting("destination_default") == "D:\\Backups\\Photos"
