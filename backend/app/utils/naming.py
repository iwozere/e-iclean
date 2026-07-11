"""Shared filename-collision disambiguation convention.

Used by both the transfer engine (backend/app/services/transfer_engine.py, spec
§5.4) and the library-cleanup delete service (backend/app/services/library_delete.py,
spec §11.5) - two different modules that each independently need to answer "two
things want the same destination filename, what do I call the second one" and should
share one naming scheme rather than inventing it twice.
"""
from pathlib import Path, PurePosixPath


def disambiguated_name(file_name: str, source_path: str) -> str:
    """Fold `source_path`'s parent folder name into `file_name` to disambiguate a
    collision, e.g. `IMG_0005.HEIC` sourced from `/DCIM/100APPLE/IMG_0005.HEIC`
    becomes `IMG_0005 (100APPLE).HEIC`.

    `source_path` may use either `/` or `\\` separators - `PurePosixPath` is used
    deliberately (not `Path`, which would be platform-dependent) since callers pass
    both AFC remote paths (always `/`-separated) and local filesystem paths.
    """
    parent = PurePosixPath(source_path.replace("\\", "/")).parent.name
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    return f"{stem} ({parent}){suffix}"
