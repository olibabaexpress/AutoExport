"""
Where AutoExport keeps everything that isn't code.

All per-machine state (queue, caches, settings, sample database), logs and
debug captures live at the project root, next to app/, so the app folder
can be replaced or updated without touching a machine's data:

    <root>/app     code
    <root>/data    settings, queue, caches
    <root>/LOGS    run logs (+ failures/ snapshots)
    <root>/debug   OCR / screenshot debug output

Set AUTOEXPORT_HOME to keep the data somewhere else (e.g. if the code is
installed somewhere read-only).
"""

import os
import shutil
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(APP_DIR)
ROOT_DIR = os.path.abspath(os.environ.get("AUTOEXPORT_HOME") or INSTALL_DIR)
RESOURCE_DIR = getattr(sys, "_MEIPASS", INSTALL_DIR)

DATA_DIR = os.path.join(ROOT_DIR, "data")
LOG_DIR = os.path.join(ROOT_DIR, "LOGS")
DEBUG_DIR = os.path.join(ROOT_DIR, "debug")
VENDOR_DIR = os.path.join(RESOURCE_DIR, "vendor")

# Data used to live in app/data; move it across once.
_LEGACY_DATA_DIR = os.path.join(APP_DIR, "data")


def _migrate_legacy_data():
    if not os.path.isdir(_LEGACY_DATA_DIR) or os.path.normcase(_LEGACY_DATA_DIR) == os.path.normcase(DATA_DIR):
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    for name in os.listdir(_LEGACY_DATA_DIR):
        src = os.path.join(_LEGACY_DATA_DIR, name)
        dst = os.path.join(DATA_DIR, name)
        if os.path.exists(dst):
            continue
        try:
            shutil.move(src, dst)
        except OSError:
            pass  # e.g. the other process is migrating at the same moment
    try:
        os.rmdir(_LEGACY_DATA_DIR)  # only succeeds once it's empty
    except OSError:
        pass


if not getattr(sys, "frozen", False):
    _migrate_legacy_data()
for _d in (DATA_DIR, LOG_DIR, DEBUG_DIR):
    os.makedirs(_d, exist_ok=True)


def data_file(name):
    return os.path.join(DATA_DIR, name)


def debug_dir(*parts):
    path = os.path.join(DEBUG_DIR, *parts)
    os.makedirs(path, exist_ok=True)
    return path
