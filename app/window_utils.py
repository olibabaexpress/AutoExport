import time
import re
import ctypes
import logging
from ctypes import wintypes
from pywinauto import Application, Desktop

logger = logging.getLogger("AutoExport")

_user32 = ctypes.windll.user32
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]

VK_CAPITAL = 0x14


def caps_lock_is_on():
    """Read the toggled state of Caps Lock via the Win32 API."""
    try:
        return bool(_user32.GetKeyState(VK_CAPITAL) & 0x0001)
    except Exception:
        return False


def ensure_caps_lock_off():
    """Turn Caps Lock off if it is on.

    pyautogui.write() types uppercase letters as Shift+letter, which under an
    active Caps Lock produces lowercase (and plain keys produce uppercase),
    so every letter of a typed filename gets its case inverted. That saved
    mangled WAV names ("bONTEMPI mIXDOWN...") and then the filename checks
    reported the render as missing. Call this before any pyautogui.write()
    that types a path, filename, or any text where case matters.
    """
    if not caps_lock_is_on():
        return
    logger.warning("Caps Lock is on - turning it off before typing")
    try:
        _user32.keybd_event(VK_CAPITAL, 0, 0, 0)
        _user32.keybd_event(VK_CAPITAL, 0, 0x0002, 0)  # KEYEVENTF_KEYUP
        time.sleep(0.05)  # let the toggle settle before keystrokes land
    except Exception as e:
        logger.error(f"Could not turn Caps Lock off: {e}")


def _raw_window_title(hwnd):
    """Window title via GetWindowTextW, bypassing the owning process.

    pywinauto's window_text() goes through SendMessageTimeout with
    SMTO_ABORTIFHUNG, which returns '' when the owning thread has stopped
    pumping messages - exactly what Ableton does while writing a large .als
    to a slow (HDD) export folder. Every title match then fails and a popup
    that is really on screen goes undetected. GetWindowTextW reads the title
    straight out of the window record and works either way.
    """
    length = _user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _raw_matching_hwnds(title_re_pattern, visible_only=True):
    """Top-level hwnds whose title matches the regex, read via GetWindowTextW."""
    try:
        regex = re.compile(title_re_pattern, re.IGNORECASE)
    except re.error as e:
        logger.error(f"Invalid window title regex {title_re_pattern!r}: {e}")
        return []

    matches = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _collect(hwnd, _lparam):
        if visible_only and not _user32.IsWindowVisible(hwnd):
            return True
        if regex.search(_raw_window_title(hwnd)):
            matches.append(hwnd)
        return True

    try:
        _user32.EnumWindows(_collect, 0)
    except Exception as e:
        logger.debug(f"EnumWindows failed for {title_re_pattern!r}: {e}")
    return matches


def _find_all_window_elements(title_re_pattern, backend="uia"):
    """All visible top-level windows matching a title regex, or [] on failure."""
    hwnds = _raw_matching_hwnds(title_re_pattern)
    if not hwnds:
        return []
    # Bug 2 fallout: wrapping every hwnd in one list comprehension means a
    # single dying COM object ("An event was unable to invoke any of the
    # subscribers" while the Export dialog tears down) discards ALL matches.
    # Wrap per-handle so one bad window can't hide the rest.
    wrapped = []
    try:
        desktop = Desktop(backend=backend)
    except Exception as e:
        logger.debug(f"Could not open Desktop({backend}) for {title_re_pattern!r}: {e}")
        return []
    for h in hwnds:
        try:
            wrapped.append(desktop.windows(handle=h)[0])
        except Exception as e:
            logger.debug(f"Could not wrap window {h} for {title_re_pattern!r}: {e}")
            continue
    return wrapped


def _pick_best_window(candidates):
    """Prefer the first visible+enabled candidate (skips minimized/ghost
    windows left behind by a crash or unclean close); falls back to the
    first candidate of any kind."""
    for w in candidates:
        try:
            if w.is_visible() and w.is_enabled():
                return w
        except Exception:
            continue
    return candidates[0] if candidates else None


def _find_window_element(title_re_pattern, backend="uia", timeout=2):
    """
    Resolve a single window by title regex, or None if it never appears.

    Enumerates Desktop.windows() rather than Application.connect():
    connect() forces visible_only=False, so it happily returns a hidden/ghost
    window left behind by a crash, and it only reports ambiguity when *several*
    windows match - a lone ghost is returned without any warning at all, and
    set_focus() on it then fails or focuses nothing. Desktop() defaults to
    visible_only=True and returns every match, so _pick_best_window can choose
    the real window whenever there is a choice.

    Polls for the full timeout (as connect(timeout=) did) but returns as soon
    as a match turns up, instead of blocking for the whole timeout on a miss.
    """
    start_time = time.time()
    while True:
        window = _pick_best_window(_find_all_window_elements(title_re_pattern, backend))
        if window is not None:
            return window
        if time.time() - start_time >= timeout:
            return None
        time.sleep(0.1)


## window management functions
def wait_for_window(title_part, timeout=60):
    """Wait for window with partial title match to exist"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            app = Application(backend="uia").connect(title_re=f".*{re.escape(title_part)}.*", timeout=1)
            return app
        except:
            time.sleep(0.2)
    return None

def find_window(title_part):
    """Find window by partial title, return pywinauto Application or None"""
    try:
        return Application(backend="uia").connect(title_re=f".*{re.escape(title_part)}.*", timeout=2)
    except:
        return None

def find_window_in_children(parent_window, title):
    """Find child window by partial title match"""
    try:
        children = parent_window.children()
        for child in children:
            try:
                if title.lower() in child.window_text().lower():
                    return child
            except:
                pass
    except:
        pass
    return None

def find_window_by_title_win32(title):
    """Find window by title using win32 backend (better for standard dialogs)"""
    try:
        app = Application(backend="win32").connect(title_re=f".*{title}.*", timeout=1)
        return app
    except:
        return None

def win_exists(title):
    """Check if window with title exists (checks UIA top-level, UIA child windows, and Win32)"""
    pattern = f".*{title}.*"

    # First try UIA top-level window
    if _find_all_window_elements(pattern, backend="uia"):
        return True

    # Try Win32 backend (better for standard dialogs like Save dialogs)
    if _find_all_window_elements(pattern, backend="win32"):
        return True
    
    # Check if it's a child window of Ableton (UIA)
    ableton_window = _find_window_element(".*Ableton Live.*", backend="uia")
    if ableton_window is not None:
        try:
            child = find_window_in_children(ableton_window, title)
            if child:
                return True
        except Exception:
            pass
    
    return False

def win_activate_ableton():
    """Activate the Ableton Live window."""
    window = _find_window_element(".*Ableton Live.*", backend="uia")
    if window is None:
        logger.debug("Could not activate Ableton window: no matching window found")
        return None
    try:
        window.set_focus()
        return window
    except Exception as e:
        logger.debug(f"Could not activate Ableton window: {e}")
        return None


def win_activate(title):
    """Activate window with partial title match (checks UIA top-level, UIA child windows, and Win32)"""
    pattern = f".*{title}.*"

    # Try UIA top-level window first
    window = _find_window_element(pattern, backend="uia")
    if window is not None:
        try:
            window.set_focus()
            return window
        except Exception:
            pass
    
    # Try Win32 backend (better for standard dialogs)
    window = _find_window_element(pattern, backend="win32")
    if window is not None:
        try:
            window.set_focus()
            return window
        except Exception:
            pass
    
    # Check Ableton child windows
    ableton_window = _find_window_element(".*Ableton Live.*", backend="uia")
    if ableton_window is not None:
        try:
            child = find_window_in_children(ableton_window, title)
            if child:
                child.set_focus()
                return child
        except Exception:
            pass
    
    return None

def win_close(title):
    """Close window with partial title match (checks UIA top-level, UIA child windows, and Win32).

    Closes every top-level window matching the title (not just one) - a
    lingering/ghost window left behind by a crash or unclean close would
    otherwise both make the match ambiguous and stay open after this call.
    """
    pattern = f".*{title}.*"

    for backend in ("uia", "win32"):
        matches = _find_all_window_elements(pattern, backend=backend)
        if not matches:
            continue
        closed_any = False
        for window in matches:
            try:
                window.close()
                closed_any = True
            except Exception as e:
                logger.debug(f"Could not close a matched '{title}' window ({backend}): {e}")
        if closed_any:
            return True
    
    # Check Ableton child windows
    ableton_window = _find_window_element(".*Ableton Live.*", backend="uia")
    if ableton_window is not None:
        try:
            child = find_window_in_children(ableton_window, title)
            if child:
                child.close()
                return True
        except Exception:
            pass
    
    return False

def win_exists_by_class(class_name):
    """Check if window with specific class name exists"""
    try:
        Application(backend="uia").connect(class_name=class_name, timeout=0.5)
        return True
    except:
        return False

def win_close_by_class(class_name):
    """Close window with specific class name"""
    try:
        app = Application(backend="uia").connect(class_name=class_name, timeout=2)
        window = app.window(class_name=class_name)
        window.close()
        return True
    except:
        return False

def _list_windows_by_class_win32(class_name):
    """Return top-level windows by class using Win32 backend."""
    try:
        return Desktop(backend="win32").windows(class_name=class_name)
    except Exception:
        return []