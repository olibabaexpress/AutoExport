"""
Ableton window management helpers.

Everything here is about finding, verifying, waiting on, or manipulating
the Ableton Live application window (opening a project via the File > Open
menu dialog, confirming the right project loaded, waiting for
responsiveness, maximizing, getting window bounds). launch_ableton()
itself (which uses subprocess.Popen to start the Ableton process) stays in
the main script since it isn't window management.
"""

import os
import re
import time
import logging

import pyautogui
from pywinauto import Desktop

from window_utils import win_exists, win_close, win_activate_ableton, _find_window_element, ensure_caps_lock_off
from popup_automation import (
    detect_save_popup,
    detect_save_changes_to_popup,
    handle_save_changes_to,
    handle_save_live_set_as,
    handle_qubiq_popup,
    dismiss_plugin_messagebox_popups,
)
import ableton_build as ableton_build
import ableton_state as ableton_state

logger = logging.getLogger("AutoExport")


def _title_re():
    """pywinauto title_re fragment for the currently selected Ableton build."""
    return f".*{re.escape(ableton_build.get_build()['window_title'])}.*"


def launch_ableton_from_menu(project_path):
    pyautogui.hotkey('ctrl', 'o')
    time.sleep(0.5)
    if get_open_document():
        handle_open_document_dialog(project_path)

        #wait for open document to close
        start_time = time.time()
        timeout = 30
        while time.time() - start_time < timeout:
            if not win_exists("Open Document:"):
                logger.debug("Open Document dialog closed successfully")
                return True
            time.sleep(0.5)

        logger.warning(f"Open Document dialog did not close within {timeout}s")
        return False
    else: 
        logger.warning("Open Document dialog did not appear")
        return False


def get_open_document(timeout=30):
    """Wait for and return the 'Open Document:' dialog window."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            for w in Desktop(backend="win32").windows():
                title = w.window_text()
                if title == "Open Document:":
                    logger.debug(f"Found 'Open Document:' dialog")
                    return w
        except Exception as e:
            logger.debug(f"Error in get_open_document: {e}")
        time.sleep(0.3)
    
    logger.warning(f"'Open Document:' dialog not found within {timeout} seconds")
    return None

def handle_open_document_dialog(project_path):
    """Navigate to project directory and open the file in Open Document dialog."""
    try:
        # Get directory and filename
        project_dir = os.path.dirname(project_path)
        project_filename = os.path.basename(project_path)
        
        logger.debug(f"Opening: {project_filename} from {project_dir}")
        
        # Focus the address/path bar and enter directory
        ensure_caps_lock_off()
        pyautogui.hotkey('alt', 'd')
        time.sleep(0.3)

        
        # Type path using write() instead of typewrite() for backslash support
        pyautogui.write(project_dir, interval=0.01)
        time.sleep(0.3)
        
        # Press enter to navigate to directory
        pyautogui.press('enter')
        time.sleep(1)  # Wait for directory to load

        #focus on edit control to type filename
        pyautogui.hotkey('alt', 'n')
        time.sleep(0.2)

        # Type filename
        pyautogui.write(project_filename, interval=0.01)
        time.sleep(0.3)


              
        # Press enter or Alt+O to open
        pyautogui.hotkey('alt', 'o')
        time.sleep(0.5)
        
        logger.debug(f"Opened project via dialog: {project_filename}")
        return True
        
    except Exception as e:
        logger.error(f"Error handling Open Document dialog: {e}")
        return False


def get_ableton_window_title():
    """Get the title of the currently active Ableton Live window."""
    window = _find_window_element(".*Ableton Live.*", backend="win32", timeout=2)
    if window is None:
        logger.warning("Could not get Ableton window title: no matching window found")
        return None
    try:
        return window.window_text()
    except Exception as e:
        logger.warning(f"Could not get Ableton window title: {e}")
        return None

def verify_project_loaded(project_path, timeout=30):
    """
    Verify that the correct project is loaded in Ableton.
    
    Args:
        project_path: Full path to expected project file
        timeout: Maximum seconds to wait
    
    Returns:
        True if correct project is loaded, False otherwise
    """
    import time
    
    project_name = os.path.basename(project_path)
    # Remove .als extension for matching
    if project_name.endswith('.als'):
        project_name = project_name[:-4]
    
    logger.debug(f"Verifying project: '{project_name}'")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            # Try Win32 backend first (more reliable for Ableton)
            for w in Desktop(backend="win32").windows():
                try:
                    window_title = w.window_text()
                    if not window_title:
                        continue
                    
                    
                    
                    # Check if project name is in the window title
                    if project_name in window_title:
                        logger.debug(f"Verified: '{project_name}' is loaded in window: {window_title}")
                        return True
                except Exception:
                    continue
        
        except Exception as e:
            logger.debug(f"Error during window enumeration: {e}")
        
        time.sleep(0.5)
    
    # Timeout reached - log what we saw
    logger.error(f"Verification failed: '{project_name}' not found in any window title after {timeout}s")
    logger.error(f"Expected project path: {project_path}")
    return False


def wait_for_ableton_to_respond(window_title, timeout=60):
    """
    Wait for Ableton to stop showing "(not responding)" in the window title.
    Logs when it goes unresponsive and when it recovers.
    
    Args:
        window_title: Expected text in window title (for logging)
        timeout: Maximum time to wait (seconds)
    
    Returns:
        True if Ableton became responsive, False if timeout
    """
    import time
    
    start_time = time.time()
    not_responding_logged = False
    
    while time.time() - start_time < timeout:
        current_title = get_ableton_window_title()
        
        if not current_title:
            time.sleep(1)
            continue
        
        if "(not responding)" in current_title.lower():
            if not not_responding_logged:
                logger.warning(f"Ableton is not responding, waiting for recovery...")
                not_responding_logged = True
            time.sleep(1)
            continue
        
        # If we were previously not responding and now it's back
        if not_responding_logged:
            logger.debug("Ableton is responding again")
        
        return True
    
    logger.error(f"Timeout waiting for Ableton to respond after {timeout}s")
    return False



def maximize_ableton_window():
    window = _find_window_element(_title_re(), backend="win32", timeout=2)
    if window is None:
        logger.debug("Could not maximize window: no matching window found")
        return
    try:
        window.set_focus()
        time.sleep(0.1)
        window.maximize()
        time.sleep(0.1)
        is_max = bool(window.is_maximized())
        logger.debug(f"is_maximized (Win32): {is_max}")
    except Exception as e:
        logger.debug(f"Could not maximize window: {e}")

def close_ableton_cleanly(ableton_version=None, save_changes_timeout=15, close_confirm_timeout=20):
    """
    Save the current set and close Ableton, handling whatever save dialog
    that triggers.

    A bare ``pyautogui.hotkey("ctrl", "q")`` is unreliable here: Quit isn't
    guaranteed to be focus-independent, and even when it lands it can leave
    a "Save changes...?" or "this set was created with a different Live
    version" dialog open behind it, which then blocks everything after it
    (including the next project launch). This instead:

      1. Activates Ableton and sends Ctrl+S, so the set is already saved
         and closing shouldn't need to ask about unsaved changes at all.
      2. Handles the "different Live version - save as a new file" warning
         if Ctrl+S triggered it (same dialog Collect All and Save can hit).
      3. Closes the main window directly via its handle (win_close), which
         is equivalent to clicking the title bar's close button rather than
         relying on a keyboard shortcut actually reaching Ableton.
      4. Handles a lingering "Save changes to <project> before closing?"
         prompt as a last resort, then confirms the window is actually gone.

    Returns True once the Ableton window is confirmed closed (or was never
    open to begin with), False if it's still there after the timeout.
    """
    window_title = ableton_build.get_build().get("window_title", "Ableton Live")

    if not win_exists(window_title):
        logger.debug("Ableton is not open - nothing to close")
        return True

    window = win_activate_ableton()
    if window is None:
        logger.warning("Could not activate Ableton window before closing - trying to close anyway")
    time.sleep(0.3)

    if ableton_state.has_unsaveable_changes():
        # Effects were switched for a wet+dry export: closing must discard
        # that, so no Ctrl+S - the "save changes?" prompt below is answered No.
        logger.debug("Set has wet+dry effect changes - closing WITHOUT saving")
    else:
        pyautogui.hotkey('ctrl', 's')
        logger.debug("Sent Ctrl+S before closing Ableton")
        time.sleep(0.5)

    # Saving can re-trigger plugin validation and surface messageboxes
    # ("Qubiq Engine", "Kontakt 7", ...) - clear those out before they
    # block the rest of the close sequence. Handle-based, no OCR.
    dismiss_plugin_messagebox_popups()
    handle_qubiq_popup()

    if not ableton_state.has_unsaveable_changes() and detect_save_popup():
        logger.debug("Detected 'different Live version' save warning while closing - handling it")
        pyautogui.press('enter')
        time.sleep(0.3)
        popup_deadline = time.time() + 15
        while detect_save_popup() and time.time() < popup_deadline:
            time.sleep(0.1)
        time.sleep(0.3)
        handle_save_live_set_as(ableton_version or ableton_build.detect_version())

    time.sleep(0.5)

    win_close(window_title)
    logger.debug(f"Sent close request to '{window_title}'")

    # Closing itself can also re-trigger plugin validation.
    dismiss_plugin_messagebox_popups()
    handle_qubiq_popup()

    # A stray "Save changes to <project> before closing?" prompt can still
    # appear (e.g. Ctrl+S was a no-op because nothing was dirty, but closing
    # itself surfaces a different unsaved-state warning).
    deadline = time.time() + save_changes_timeout
    while time.time() < deadline:
        if not win_exists(window_title):
            break
        if detect_save_changes_to_popup(threshold=0.75):
            logger.debug("Detected 'save changes before closing' popup - dismissing")
            handle_save_changes_to()
            break
        time.sleep(0.3)

    deadline = time.time() + close_confirm_timeout
    while time.time() < deadline:
        if not win_exists(window_title):
            logger.debug("Ableton window closed successfully")
            ableton_state.clear_unsaveable_changes()
            return True
        time.sleep(0.3)

    logger.warning(f"Ableton window ('{window_title}') still present after {close_confirm_timeout}s")
    return False


def get_ableton_window_center():
    """Get the center coordinates of the Ableton Live window.
    Returns tuple (center_x, center_y) or None if window not found."""
    window = _find_window_element(_title_re(), backend="uia", timeout=2)
    if window is None:
        logger.debug("Could not get Ableton window center: no matching window found")
        return None
    try:
        rect = window.rectangle()
        center_x = (rect.left + rect.right) // 2
        center_y = (rect.top + rect.bottom) // 2
        return (center_x, center_y)
    except Exception as e:
        logger.debug(f"Could not get Ableton window center: {e}")
        return None
