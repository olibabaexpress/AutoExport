"""
Naming settings for exported files and folders.

Centralizes every user-adjustable piece of the export naming conventions
(main mix filenames, stems live-set folder names, individual stem
filenames) so the launcher GUI's "Naming Settings" window and the export
pipeline always agree, and so choices persist between runs ("naming" in
data/config.json).
"""

import logging

import app_config

logger = logging.getLogger("AutoExport")

CONFIG_KEY = "naming"

DEFAULTS = {
    # Main mix export filename: "<name>_<tempo>bpm_<dd-mm-yy>.wav"
    "main_mix_include_bpm": True,
    "main_mix_include_date": True,

    # Per-live-set folder name inside a stems project folder:
    # "<name>_<dd-mm-yy>" / "<name>_<tempo>bpm_<dd-mm-yy>"
    "stems_folder_include_bpm": True,
    "stems_folder_include_date": True,

    # Individual stem filenames: off by default because the analysis file
    # already carries this information, and the project name repeated on
    # every single stem is rarely wanted.
    "stem_files_include_bpm": False,
    "stem_files_include_project_name": False,

    # Ableton version suffix for exported files
    "include_ableton_version": False,
}


def load_settings():
    """Read persisted naming settings, filling in any missing keys with defaults."""
    settings = dict(DEFAULTS)
    stored = app_config.get(CONFIG_KEY) or {}
    if isinstance(stored, dict):
        for key in DEFAULTS:
            if key in stored:
                settings[key] = bool(stored[key])
    return settings


def save_settings(settings):
    """Persist naming settings (only known keys are kept). Returns True on success."""
    merged = dict(DEFAULTS)
    merged.update({k: bool(v) for k, v in settings.items() if k in DEFAULTS})
    return app_config.set(CONFIG_KEY, merged)
