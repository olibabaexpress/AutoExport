import time
import re
import logging
from pywinauto import Application, Desktop
from pywinauto.findwindows import ElementAmbiguousError
logger = logging.getLogger("AutoExport")


def _find_all_window_elements(title_re_pattern, backend="uia"):
    """All top-level windows matching a title regex, or [] on any failure."""
    try:
        return Desktop(backend=backend).windows(title_re=title_re_pattern)
    except Exception:
        return []


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
    Resolve a single window by title regex, without letting pywinauto's
    ElementAmbiguousError bubble up as an unhandled failure when more than
    one window matches (a lingering/ghost Ableton window is common after a
    crash or an unclean close). Returns a window element or None.
    """
    try:
        app = Application(backend=backend).connect(title_re=title_re_pattern, timeout=timeout)
        return app.window(title_re=title_re_pattern)
    except ElementAmbiguousError:
        return _pick_best_window(_find_all_window_elements(title_re_pattern, backend))
    except Exception:
        return None


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
        logger.info("Could not activate Ableton window: no matching window found")
        return None
    try:
        window.set_focus()
        return window
    except Exception as e:
        logger.info(f"Could not activate Ableton window: {e}")
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