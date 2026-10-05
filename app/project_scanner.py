"""
Ableton project discovery for the bulk missing-media fixer.

Separate from EXPORT_TASKS.json (the export queue): this scans configured
root directories directly for .als files, so "fix missing media on everything"
can run over your whole project library, not just whatever is currently
queued for export.
"""

import logging
from pathlib import Path

import app_config

logger = logging.getLogger(__name__)

CONFIG_KEY = "project_directories"

# Used until the user adds a folder.
DEFAULT_PROJECT_DIRS = []

# Folders that should never be scanned for "live" project files.
SKIP_DIR_NAMES = {"backup", "abletoolz_backup"}


def load_project_dirs():
    """Return the configured list of project root directories."""
    dirs = app_config.get(CONFIG_KEY)
    return dirs if isinstance(dirs, list) else list(DEFAULT_PROJECT_DIRS)


def save_project_dirs(directories):
    """Persist the project root directory list."""
    return app_config.set(CONFIG_KEY, list(directories))


def find_all_projects(directories=None):
    """
    Recursively find every .als file under the configured (or given) root
    directories, skipping Backup/abletoolz_backup folders.

    Returns:
        List of absolute .als file path strings, de-duplicated and sorted.
    """
    if directories is None:
        directories = load_project_dirs()

    found = set()
    for root_dir in directories:
        root_path = Path(root_dir)
        if not root_path.exists():
            logger.warning(f"Project directory not found, skipping: {root_dir}")
            continue
        for als_path in root_path.rglob("*.als"):
            if any(part.lower() in SKIP_DIR_NAMES for part in als_path.parts):
                continue
            found.add(str(als_path))

    return sorted(found)
