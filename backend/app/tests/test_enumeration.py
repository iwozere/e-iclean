"""Library enumeration: recursive /DCIM walk + idempotent re-runs (FR-3, FR-11)."""
from app.device.afc_client import MockAfcClient
from app.services.enumeration import enumerate_library


def test_enumerate_walks_nested_dcim_subfolders():
    afc = MockAfcClient(
        {
            "/DCIM/100APPLE/IMG_0001.HEIC": b"a" * 100,
            "/DCIM/100APPLE/IMG_0002.MOV": b"b" * 200,
            "/DCIM/101APPLE/IMG_0003.HEIC": b"c" * 50,
        }
    )

    total_items, total_bytes = enumerate_library("udid-1", afc)

    assert total_items == 3
    assert total_bytes == 350


def test_enumerate_is_idempotent_on_rerun():
    """Re-running against the same library must not duplicate rows (FR-11)."""
    afc = MockAfcClient({"/DCIM/100APPLE/IMG_0001.HEIC": b"a" * 100})

    enumerate_library("udid-1", afc)
    total_items, total_bytes = enumerate_library("udid-1", afc)

    assert total_items == 1
    assert total_bytes == 100
