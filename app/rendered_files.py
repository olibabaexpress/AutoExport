"""Wait for Windows render handles and safely rename finished output."""

import logging
import os
import time

import pywintypes
import win32con
import win32file

logger = logging.getLogger("AutoExport")
SHARING_ERRORS = (32, 33)


def file_is_released(path):
    """Probe exclusive read access without changing the file."""
    try:
        handle = win32file.CreateFile(
            os.fspath(path), win32con.GENERIC_READ, 0, None,
            win32con.OPEN_EXISTING, win32con.FILE_ATTRIBUTE_NORMAL, None,
        )
    except pywintypes.error as error:
        if error.winerror in SHARING_ERRORS:
            return False
        raise
    handle.Close()
    return True


def wait_for_finished_files(snapshot, folder, timeout=30, stable_seconds=1,
                            abort_check=None):
    """Require an unchanged set of nonempty WAVs with no outstanding handles."""
    deadline = time.monotonic() + timeout
    previous = None
    unchanged_since = time.monotonic()
    blocked = []
    while True:
        now = time.monotonic()
        if abort_check is not None and not abort_check():
            return False
        current = snapshot()
        if current != previous:
            previous = current
            unchanged_since = now
        if current and all(size > 0 for _, size in current.values()):
            blocked = [
                name for name in current
                if not file_is_released(os.path.join(folder, name))
            ]
            if not blocked and now - unchanged_since >= stable_seconds:
                return True
        if now >= deadline:
            logger.error(
                "Rendered WAVs did not become stable and unlocked within %ss in %s; locked files: %s",
                timeout, folder, ", ".join(blocked) or "none (output still changing or empty)",
            )
            return False
        time.sleep(0.2)


def move_rendered_file(source, destination, timeout=15):
    """Atomically rename same-volume output; never copy a locked source."""
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(destination)):
        return
    deadline = time.monotonic() + timeout
    warned = False
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in SHARING_ERRORS:
                raise
            if time.monotonic() >= deadline:
                logger.error("Timed out renaming locked render: %s -> %s", source, destination)
                raise
            if not warned:
                logger.warning("Render still locked; waiting up to %ss to rename: %s", timeout, source)
                warned = True
            time.sleep(0.2)
