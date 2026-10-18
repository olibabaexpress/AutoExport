"""Base export folder ("export_path" in data/config.json)."""

from pathlib import Path

import app_config as app_config

# Blank until chosen (GUI File > Set Export Path, or the installer).
DEFAULT_EXPORT_PATH = ""


def get_export_path():
    """Return the currently configured base export folder ('' if unset)."""
    return app_config.get("export_path") or DEFAULT_EXPORT_PATH


def set_export_path(path):
    """Set and persist the base export folder."""
    path = str(Path(path))
    if not app_config.set("export_path", path):
        raise OSError(f"Could not save export path to {app_config.CONFIG_FILE}")
    return path
