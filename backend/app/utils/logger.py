"""Shared logger setup: rotating local file, never transmitted anywhere."""
import logging
import logging.handlers
import os
from pathlib import Path

_LOG_DIR_ENV = "EICLEAN_LOG_DIR"
_DEFAULT_LOG_DIR = Path(os.environ.get("APPDATA", ".")) / "EiClean" / "logs"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_configured = False


def _log_dir() -> Path:
    return Path(os.environ.get(_LOG_DIR_ENV, str(_DEFAULT_LOG_DIR)))


def _configure_root() -> None:
    global _configured
    if _configured:
        return

    log_dir = _log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_dir / "backend.log",
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    _configured = True


def setup_logger(name: str) -> logging.Logger:
    """
    Return a module-scoped logger writing to the shared rotating log file.

    Args:
        name: Usually `__name__` of the calling module.

    Returns:
        A configured `logging.Logger` instance.
    """
    _configure_root()
    return logging.getLogger(name)
