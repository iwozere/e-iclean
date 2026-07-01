"""All tunables live here, with sane defaults, overridable via environment variables."""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_app_data_dir() -> Path:
    return Path(os.environ.get("APPDATA", ".")) / "EFileTrans"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EFILETRANS_")

    # Storage locations
    APP_DATA_DIR: Path = _default_app_data_dir()
    DB_FILENAME: str = "state.db"

    # Device discovery / pairing
    DEVICE_POLL_INTERVAL_SECONDS: float = 1.5
    TRUST_PROMPT_TIMEOUT_SECONDS: float = 60.0

    # Transfer engine
    TRANSFER_CHUNK_SIZE_BYTES: int = 4 * 1024 * 1024
    TRANSFER_CONCURRENCY: int = 1  # experimental beyond 1, see spec §5.4
    DB_PROGRESS_FLUSH_EVERY_N_CHUNKS: int = 5

    # Delete workflow
    DELETE_BATCH_SIZE: int = 50

    # Verification
    CHECKSUM_ALGORITHM: str = "sha256"

    @property
    def db_path(self) -> Path:
        return self.APP_DATA_DIR / self.DB_FILENAME


settings = Settings()
