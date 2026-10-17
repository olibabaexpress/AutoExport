"""
User configuration: one file, data/config.json.

    {
      "export_path": "F:\\Bounces\\AUTOEXPORT",
      "project_directories": ["F:\\ABLETON PROJECTS\\2023"],
      "sample_directories": ["F:\\SAMPLES"],
      "naming": {"main_mix_include_bpm": true, ...},
      "ableton": {"build": "release", "release_exe": "...", "beta_exe": "..."}
    }

Every key is optional; each owning module supplies its own defaults. This is
the file a setup script / installer writes. Large or frequently rewritten
state (task queue, caches, sample database) lives in separate files in data/.
"""

import json
import logging
import os
import threading

from paths import DATA_DIR, data_file

# Default audio settings used when config.json has no "audio" section. These
# mirror the dropdown option lists in audio_settings.py; kept here so config
# has a sensible initial value before audio_settings is ever imported.
_DEFAULT_AUDIO = {
    "sample_rate": "44100",
    "bit_depth": "16",
    "file_type": "WAV",
    "dither": "Triangular",
}

logger = logging.getLogger("AutoExport")

CONFIG_FILE = data_file("config.json")
_lock = threading.Lock()

# Old one-file-per-setting layout, migrated into config.json once.
_LEGACY_FILES = {
    "export_config.json": lambda d: {"export_path": d.get("export_path")},
    "project_directories.json": lambda d: {"project_directories": d.get("directories")},
    "sample_directories.json": lambda d: {"sample_directories": d.get("directories")},
    "naming_settings.json": lambda d: {"naming": d},
    "ableton_build.json": lambda d: {"ableton": d},
}
_LEGACY_BACKUP_DIR = os.path.join(DATA_DIR, "old_config")


def _read():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"Could not read {CONFIG_FILE}: {e}")
        return {}


def _write(data):
    # Write-then-rename so a crash (or the other process reading) never
    # sees a half-written file.
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    tmp = f"{CONFIG_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _migrate_legacy():
    found = [n for n in _LEGACY_FILES if os.path.isfile(os.path.join(DATA_DIR, n))]
    if not found:
        return
    with _lock:
        data = _read()
        for name in found:
            path = os.path.join(DATA_DIR, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    legacy = json.load(f)
                for key, value in _LEGACY_FILES[name](legacy).items():
                    if value is not None and key not in data:
                        data[key] = value
            except Exception as e:
                logger.warning(f"Could not migrate {name}: {e}")
                continue
        try:
            _write(data)
        except OSError as e:
            logger.warning(f"Could not write {CONFIG_FILE}: {e}")
            return
        os.makedirs(_LEGACY_BACKUP_DIR, exist_ok=True)
        for name in found:
            try:
                os.replace(os.path.join(DATA_DIR, name), os.path.join(_LEGACY_BACKUP_DIR, name))
            except OSError:
                pass


def get(key, default=None):
    """A top-level config value (a copy for dicts/lists), or default."""
    value = _read().get(key, default)
    if isinstance(value, (dict, list)):
        return json.loads(json.dumps(value))
    return value


def set(key, value):
    """Set one top-level config value. Returns True on success."""
    return update({key: value})


def update(values):
    """Set several top-level values in one read-modify-write. Returns True on success."""
    with _lock:
        data = _read()
        data.update(values)
        try:
            _write(data)
            return True
        except OSError as e:
            logger.warning(f"Could not save {CONFIG_FILE}: {e}")
            return False


def get_audio():
    """The saved "audio" settings dict, merged over the defaults.

    Returns a copy so callers can mutate it freely. Any keys the user has
    saved override the defaults; missing keys fall back to the default so the
    dict always has sample_rate / bit_depth / file_type / dither present.
    """
    stored = get("audio", {}) or {}
    merged = dict(_DEFAULT_AUDIO)
    if isinstance(stored, dict):
        merged.update({k: v for k, v in stored.items() if v is not None})
    return merged


def set_audio(values):
    """Merge `values` into the saved "audio" settings. Returns True on success.

    Only the provided keys are changed; the rest of the existing audio config
    is preserved. Keys are validated against the known set so a typo can't
    silently write junk into config.json.
    """
    allowed = _DEFAULT_AUDIO.keys()
    clean = {k: v for k, v in (values or {}).items() if k in allowed}
    return update({"audio": {**get_audio(), **clean}})


_migrate_legacy()
