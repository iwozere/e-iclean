"""All tunables live here, with sane defaults, overridable via environment variables."""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_app_data_dir() -> Path:
    return Path(os.environ.get("APPDATA", ".")) / "EiClean"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EICLEAN_")

    # Storage locations
    APP_DATA_DIR: Path = _default_app_data_dir()
    DB_FILENAME: str = "state.db"

    # Device discovery / pairing
    DEVICE_POLL_INTERVAL_SECONDS: float = 1.5
    TRUST_PROMPT_TIMEOUT_SECONDS: float = 60.0
    # Bounds a single is_paired/request_pairing attempt inside the trust-wait loop.
    # Distinct from TRUST_PROMPT_TIMEOUT_SECONDS: that's the user-facing "waiting for
    # you to tap Trust" budget; this is how long one usbmux/lockdown round-trip may
    # take before we treat it as failed and retry. Without this, a missing/broken
    # Apple Mobile Device Support driver makes the very first connection attempt hang
    # for 20+ seconds with no IPC response at all (found via real PyInstaller-frozen
    # exe testing on a machine with no driver installed).
    PAIRING_ATTEMPT_TIMEOUT_SECONDS: float = 10.0

    # Transfer engine
    TRANSFER_CHUNK_SIZE_BYTES: int = 4 * 1024 * 1024
    TRANSFER_CONCURRENCY: int = 1  # experimental beyond 1, see spec §5.4
    DB_PROGRESS_FLUSH_EVERY_N_CHUNKS: int = 5
    # A single fread() on an otherwise-healthy AFC/lockdown session can fail
    # transiently (observed against a real ~141GB/12k-item library: the device never
    # leaves usbmux, only the in-flight read errors out) - without a retry here, that
    # looks identical to a real cable-pull to the rest of the app, but a real
    # disconnect only gets detected (and the transfer resumed) via a fresh usbmux
    # device_connected event, which never fires because the device never actually
    # went away. See docs/DEVELOPMENT.md.
    AFC_READ_RETRY_ATTEMPTS: int = 3
    AFC_READ_RETRY_DELAY_SECONDS: float = 1.0

    # Delete workflow
    DELETE_BATCH_SIZE: int = 50

    # Verification
    CHECKSUM_ALGORITHM: str = "sha256"

    # Library Cleanup module (spec §11) - fully independent of the iPhone-transfer
    # settings above, operates on arbitrary local folders.
    LIBRARY_IMAGE_EXTENSIONS: set[str] = {
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".heif",
        ".bmp",
        ".tiff",
        ".tif",
        ".gif",
        ".webp",
    }
    # Hamming distance (out of a 64-bit perceptual hash) under which two images are
    # considered near-duplicates (spec FR-L2). Conservative default - favors missing
    # a few true near-duplicates over flagging visually-different images, since
    # nothing here is auto-deleted (spec §11.0's review-and-confirm constraint), but a
    # wrong grouping is still confusing/untrustworthy noise for the user to review.
    LIBRARY_NEAR_DUPLICATE_HAMMING_THRESHOLD: int = 5
    # Sibling folder suffix for safe-delete moves (spec FR-L7), e.g. "MyPictures" ->
    # "MyPictures-delete".
    LIBRARY_DELETE_FOLDER_SUFFIX: str = "-delete"
    # Hashing is CPU-bound (image decode + perceptual hash); bounds how many files are
    # hashed concurrently via a thread pool (spec §11.4). Distinct from
    # TRANSFER_CONCURRENCY above, which is 1 by AFC protocol necessity - local disk
    # reads have no such constraint, so this defaults higher.
    LIBRARY_SCAN_WORKER_CONCURRENCY: int = 8
    # Mirrors DB_PROGRESS_FLUSH_EVERY_N_CHUNKS's reasoning: emitting a progress event
    # per file would flood the IPC channel on a many-thousand-file scan.
    LIBRARY_SCAN_PROGRESS_EVERY_N_FILES: int = 10

    @property
    def db_path(self) -> Path:
        return self.APP_DATA_DIR / self.DB_FILENAME


settings = Settings()
