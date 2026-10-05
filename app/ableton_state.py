"""
Ableton UI state inspection and hard recovery.

The automation is a sequence of "do X, expect window Y" steps. When a step's
expected window doesn't show up (or shows up late on a slow disk), whatever
dialog *is* open blocks every later step - most visibly Ctrl+O, which can't
open "Open Document:" while any modal is up. This module answers the one
question every step needs before acting: "is Ableton sitting idle on its main
window, or is something in the way?" - and provides the escape hatch when the
answer stays "something is in the way": kill the Live process outright, which
needs no popup handling at all (the "Live unexpectedly quit - recover?" popup
on the next launch is already handled by wait_for_recover_or_asio).

Window titles are read with GetWindowTextW rather than pywinauto's
window_text(): the latter can come back empty while Ableton is busy (e.g.
loading a set from an HDD), which makes the main window look like an untitled
popup and makes a loaded project look like it never opened.
"""

import ctypes
import logging
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime

import psutil
import pyautogui

import ableton_build
from paths import LOG_DIR

logger = logging.getLogger("AutoExport")

_user32 = ctypes.windll.user32

ABLETON_WINDOW_CLASS = "Ableton Live Window Class"
STANDARD_DIALOG_CLASS = "#32770"
GHOST_WINDOW_CLASS = "Ghost"

# Titled dialogs that always mean "a modal is open". Untitled Ableton-class
# windows (Ableton's own popups) and standard Win32 dialogs are caught
# generically, so this list only needs titled windows of other classes.
KNOWN_BLOCKING_TITLES = (
    "Open Document:",
    "Save Audio File As:",
    "Save Live Set As:",
    "Confirm Save As",
    "Export Audio/Video",
    "Qubiq Engine",
)

# Ableton keeps tiny hidden helper windows around; real popups are larger.
MIN_POPUP_SIZE = 100

FAILURE_SNAPSHOT_DIR = os.path.join(
    LOG_DIR, "failures"
)

# Set once effects have been switched on/off for a wet+dry export: the open
# set must then never be saved (close_ableton_cleanly skips its Ctrl+S, and
# the "save changes?" prompt is always answered No).
_unsaveable_changes = False


def mark_unsaveable_changes():
    global _unsaveable_changes
    _unsaveable_changes = True


def clear_unsaveable_changes():
    global _unsaveable_changes
    _unsaveable_changes = False


def has_unsaveable_changes():
    return _unsaveable_changes


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    left: int
    top: int
    width: int
    height: int
    enabled: bool
    hung: bool

    def describe(self):
        flags = []
        if not self.enabled:
            flags.append("disabled")
        if self.hung:
            flags.append("not responding")
        flag_text = f" [{', '.join(flags)}]" if flags else ""
        return (
            f"'{self.title}' class={self.class_name} "
            f"{self.width}x{self.height} @({self.left},{self.top}){flag_text}"
        )


def _window_text(hwnd):
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _class_name(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _window_pid(hwnd):
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _visible_top_level_hwnds():
    hwnds = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _collect(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True

    _user32.EnumWindows(_collect, 0)
    return hwnds


def ableton_pids():
    """PIDs of the Live executable for the selected build (not its helpers
    like 'Ableton Index', and not the other build if both are installed)."""
    exe = ableton_build.get_build()["exe"]
    if not exe:
        return set()
    exe_name = os.path.basename(exe).lower()
    pids = set()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == exe_name:
                pids.add(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def _window_info(hwnd, pid, hung=None):
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return WindowInfo(
        hwnd=hwnd,
        pid=pid,
        title=_window_text(hwnd),
        class_name=_class_name(hwnd),
        left=rect.left,
        top=rect.top,
        width=rect.right - rect.left,
        height=rect.bottom - rect.top,
        enabled=bool(_user32.IsWindowEnabled(hwnd)),
        hung=bool(_user32.IsHungAppWindow(hwnd)) if hung is None else hung,
    )


def list_ableton_windows():
    """All visible top-level windows owned by the selected Live process.

    When Live stops pumping messages for ~5s, Windows hides its main window
    and shows a "Ghost" window ("... (Not Responding)") owned by dwm.exe
    instead. That stand-in is included (flagged hung) so a frozen Live reads
    as "not responding" rather than "main window not found".
    """
    pids = ableton_pids()
    if not pids:
        return []
    title_fragment = ableton_build.get_build()["window_title"]
    windows = []
    for hwnd in _visible_top_level_hwnds():
        try:
            pid = _window_pid(hwnd)
            if pid in pids:
                windows.append(_window_info(hwnd, pid))
            elif _class_name(hwnd) == GHOST_WINDOW_CLASS and title_fragment in _window_text(hwnd):
                windows.append(_window_info(hwnd, pid, hung=True))
        except Exception as e:
            logger.debug(f"Could not inspect window {hwnd}: {e}")
    return windows


def main_window(windows=None):
    """The Live main window (titled '<set> - Ableton Live 12 ...'), or None.
    May be the hung-app Ghost stand-in, which is always flagged hung."""
    title_fragment = ableton_build.get_build()["window_title"]
    windows = list_ableton_windows() if windows is None else windows
    candidates = [
        w for w in windows
        if w.class_name in (ABLETON_WINDOW_CLASS, GHOST_WINDOW_CLASS) and title_fragment in w.title
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda w: w.width * w.height)


def blocking_windows(windows=None):
    """Visible Ableton-owned windows that indicate a modal/popup is open."""
    windows = list_ableton_windows() if windows is None else windows
    main = main_window(windows)
    blockers = []
    for w in windows:
        if main is not None and w.hwnd == main.hwnd:
            continue
        if w.width < MIN_POPUP_SIZE or w.height < MIN_POPUP_SIZE:
            continue
        if (
            w.class_name == STANDARD_DIALOG_CLASS
            or (w.class_name == ABLETON_WINDOW_CLASS and not w.title)
            or any(w.title.startswith(t) for t in KNOWN_BLOCKING_TITLES)
        ):
            blockers.append(w)
    return blockers


def is_running():
    return bool(ableton_pids())


def get_state():
    """Snapshot of Ableton's UI state as a dict.

    'clean' means: Live is running, its main window exists, is enabled (a
    modal disables its owner), is responding, and no popup/dialog is open.
    """
    windows = list_ableton_windows()
    main = main_window(windows)
    blockers = blocking_windows(windows)
    clean = (
        main is not None
        and main.enabled
        and not main.hung
        and not blockers
    )
    return {
        "running": is_running(),
        "main": main,
        "blockers": blockers,
        "all_windows": windows,
        "clean": clean,
    }


def describe_state(state=None):
    state = state or get_state()
    if not state["running"]:
        return "Ableton is not running"
    lines = []
    main = state["main"]
    lines.append(f"main window: {main.describe() if main else 'NOT FOUND'}")
    if state["blockers"]:
        for b in state["blockers"]:
            lines.append(f"blocking: {b.describe()}")
    else:
        lines.append("blocking: none")
    others = [
        w for w in state["all_windows"]
        if w not in state["blockers"] and (main is None or w.hwnd != main.hwnd)
    ]
    for w in others:
        lines.append(f"other: {w.describe()}")
    return "\n    ".join(lines)


def wait_for_clean_state(timeout, settle=1.0, poll=0.3, on_blocked=None, label=""):
    """
    Wait until Ableton has been continuously clean for `settle` seconds.

    The settle period matters because several flows chain popups with a short
    gap between them (e.g. Collect All: "Specify..." closes, then ~1-2s later
    the "save as new file" warning opens) - a single instantaneous check
    would happily report "clean" in that gap.

    `on_blocked(state)` is called while something is blocking; return True
    from it if it dismissed something (resets the settle timer).

    Returns True once clean, False on timeout (logging what was open).
    """
    deadline = time.monotonic() + timeout
    clean_since = None
    last_state = None
    while time.monotonic() < deadline:
        state = get_state()
        last_state = state
        if state["clean"]:
            if clean_since is None:
                clean_since = time.monotonic()
            if time.monotonic() - clean_since >= settle:
                return True
        else:
            clean_since = None
            if on_blocked is not None and state["blockers"]:
                try:
                    on_blocked(state)
                except Exception as e:
                    logger.debug(f"on_blocked handler raised: {e}")
        time.sleep(poll)

    suffix = f" ({label})" if label else ""
    logger.error(
        f"Ableton did not reach a clean state within {timeout}s{suffix}:\n    "
        f"{describe_state(last_state) if last_state else 'no state read'}"
    )
    return False


def capture_failure_snapshot(label):
    """Log every Ableton window and save a full-screen screenshot.

    Returns the screenshot path (or None). Called the moment a step fails so
    the evidence reflects the state that caused the failure, not whatever
    later recovery did to it.
    """
    try:
        logger.error(f"Ableton state at failure ({label}):\n    {describe_state()}")
    except Exception as e:
        logger.error(f"Could not read Ableton state at failure: {e}")

    try:
        os.makedirs(FAILURE_SNAPSHOT_DIR, exist_ok=True)
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:80]
        path = os.path.join(
            FAILURE_SNAPSHOT_DIR,
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_label}.png",
        )
        pyautogui.screenshot(path)
        logger.error(f"Failure screenshot saved: {path}")
        return path
    except Exception as e:
        logger.error(f"Could not save failure screenshot: {e}")
        return None


def kill_ableton(timeout=30):
    """Force-terminate the selected Live build (and its child processes).

    Deliberately bypasses the UI: whatever popup or half-finished dialog is
    open can't block a process kill. Returns True once no Live process is left.
    """
    pids = ableton_pids()
    if not pids:
        logger.info("Ableton is not running - nothing to kill")
        clear_unsaveable_changes()
        return True

    procs = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            procs.extend(proc.children(recursive=True))
            procs.append(proc)
        except psutil.NoSuchProcess:
            continue

    for proc in procs:
        try:
            logger.warning(f"Killing {proc.name()} (pid {proc.pid})")
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            logger.debug(f"Could not kill pid {proc.pid}: {e}")

    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    if alive:
        logger.error(f"Ableton processes still alive after kill: {[p.pid for p in alive]}")
        return False

    # Give the audio driver / OSC port a moment to be released before relaunch.
    time.sleep(3)
    clear_unsaveable_changes()
    logger.info("Ableton process terminated")
    return True
