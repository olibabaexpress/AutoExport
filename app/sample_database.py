"""
Sample database management for the abletoolz-based missing-media resolver.

Wraps abletoolz's sample_databaser module with:
- A persisted, GUI-editable list of directories to scan for samples.
- A JSON-backed sample database stored alongside this script (not in a hidden
  user-config folder), so it's easy to find, back up, or delete.

The directory list is meant to grow over time (e.g. when an external drive
with more samples is plugged in), so it is user-editable configuration
("sample_directories" in data/config.json) rather than hardcoded.
"""

import logging
import time
from threading import Event
from datetime import datetime
from pathlib import Path
import create_db_patch

import app_config

logger = logging.getLogger(__name__)

from paths import data_file
CONFIG_KEY = "sample_directories"
DB_PATH = Path(data_file("abletoolz_sample_db.json"))

# Used until the user edits the list: the machine's Ableton User Library and
# Core Library samples.
def _default_sample_dirs():
    dirs = []
    try:
        import ableton_build
        user_library = ableton_build.user_library_path()
        if user_library:
            dirs.append(user_library)
        exe = ableton_build.get_build()["exe"]
        if exe:
            core = Path(exe).parent.parent / "Resources" / "Core Library" / "Samples"
            if core.is_dir():
                dirs.append(str(core))
    except Exception as e:
        logger.warning(f"Could not derive default sample directories: {e}")
    return dirs


def load_sample_dirs():
    """Return the configured list of sample directories."""
    dirs = app_config.get(CONFIG_KEY)
    return dirs if isinstance(dirs, list) else _default_sample_dirs()


def save_sample_dirs(directories):
    """Persist the directory list."""
    return app_config.set(CONFIG_KEY, list(directories))


def add_sample_dir(path):
    """Add a directory to the configured list (no-op if already present)."""
    dirs = load_sample_dirs()
    if path not in dirs:
        dirs.append(path)
        save_sample_dirs(dirs)
    return dirs


def remove_sample_dir(path):
    """Remove a directory from the configured list."""
    dirs = load_sample_dirs()
    if path in dirs:
        dirs.remove(path)
        save_sample_dirs(dirs)
    return dirs


def automatic_sample_dirs():
    """Folders always indexed: everything under %APPDATA%\\Ableton (all Live
    versions' preferences, including Live Recordings temp projects)."""
    import os

    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    roaming = Path(appdata) / "Ableton"
    return [str(roaming)] if roaming.is_dir() else []


def _with_automatic_dirs(directories):
    import os

    def norm(path):
        return os.path.normcase(os.path.abspath(str(path)))

    combined = list(directories)
    covered = [norm(path) for path in combined]
    for extra in automatic_sample_dirs():
        target = norm(extra)
        if not any(
            target == root or target.startswith(root.rstrip("\\") + "\\")
            for root in covered
        ):
            combined.append(extra)
            covered.append(target)
    return combined


def build_or_update_index(
    directories=None,
    progress_callback=None,
    pause_event: Event | None = None,
    cancel_event: Event | None = None,
):
    """
    Scan the configured (or given) directories and build/update the sample database.

    Args:
    directories: Optional list of directory paths. Defaults to load_sample_dirs().
    progress_callback: Optional callable(stage, current, total) used for progress updates.
    pause_event: Optional set event that allows indexing to proceed. Clearing it
        pauses at the next file or directory boundary.

    Returns:
        (db_path, entry_count)

    Raises:
        ValueError: if none of the configured directories currently exist.
    """
    if directories is None:
        directories = load_sample_dirs()
    directories = _with_automatic_dirs(directories)

    started = time.monotonic()
    logger.info(
        "Sample index build requested for %d configured director%s",
        len(directories), "y" if len(directories) == 1 else "ies",
    )

    valid_dirs = [d for d in directories if Path(d).exists()]
    missing_dirs = [d for d in directories if not Path(d).exists()]

    #if missing_dirs and progress_callback:
    #    progress_callback(f"Skipping {len(missing_dirs)} configured directory(ies) that don't currently exist (e.g. unplugged drive).")
    if missing_dirs:
        logger.warning(f"Skipping directories that don't exist: {missing_dirs}")

    if not valid_dirs:
        raise ValueError("No valid sample directories configured/found - nothing to index.")

    #if progress_callback:
    #    progress_callback(f"Scanning {len(valid_dirs)} directory(ies) for audio files (this can take a while on first run)...")

    db_path = create_db_patch.create_or_update_db(
        valid_dirs,
        db_path=DB_PATH,
        progress_callback=progress_callback,
        pause_event=pause_event,
        cancel_event=cancel_event,
    )
    db = create_db_patch.load_db(db_path)

    #if progress_callback:
    #    progress_callback(f"Done. Sample database now has {len(db)} indexed file(s).")

    logger.info(
        "Sample index build finished: %d entries in %.1fs -> %s",
        len(db), time.monotonic() - started, db_path,
    )
    return db_path, len(db)


def load_index():
    """Load the sample database if it exists, else return None."""
    if not DB_PATH.exists():
        return None
    try:
        return create_db_patch.load_db(DB_PATH)
    except Exception as e:
        logger.error(f"Could not load sample database: {e}")
        return None


def get_index_status():
    """Return a small status dict for display in the GUI."""
    if not DB_PATH.exists():
        return {"exists": False, "count": 0, "updated_at": None}
    try:
        db = create_db_patch.load_db(DB_PATH)
        mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime).isoformat(timespec="seconds")
        return {"exists": True, "count": len(db), "updated_at": mtime}
    except Exception as e:
        return {"exists": False, "count": 0, "updated_at": None, "error": str(e)}
