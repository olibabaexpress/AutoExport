"""
Popup detection and handling (OCR + window-diff based).

Everything here deals with detecting and dismissing Ableton Live dialogs
and popups that appear during automation: ASIO/recover/invalid-file popups
on startup, the "Specify..."/Save-As/decoding popups during Collect All and
Save, side-menu tick detection, the Qubiq Engine popup, and the Save Live
Set As / Save Audio File As dialog helpers.

Most detectors use a short-lived OCR cache (_ocr_cache/_should_ocr/_store_ocr)
to avoid re-running Tesseract OCR more than once every OCR_COOLDOWN seconds
when a caller polls in a tight loop. Some callers outside this module (e.g.
collect_all_and_save in export_automation.py) intentionally reset entries in
_ocr_cache directly to force a fresh OCR read - that coupling is expected.
"""

import os
import re
import time
import difflib
import logging
from datetime import datetime

import numpy as np
import pyautogui
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

from PIL import ImageChops
from pywinauto import Application, Desktop

import naming_settings
import ableton_state
from window_utils import (
    win_exists,
    _list_windows_by_class_win32,
    _find_all_window_elements,
    win_activate,
    ensure_caps_lock_off,
)
from project_and_file_naming import append_version_to_filename

logger = logging.getLogger("AutoExport")

# OCR 
OCR_COOLDOWN = 2.0  # seconds
_ocr_cache = {
    "no_asio": (0.0, False),
    "save_popup": (0.0, False),
    "decoding": (0.0, False),
    "specify": (0.0, False),
    "save_changes_to_popup": (0.0, False),
    "invalid_popup": (0.0, False),
    "recover_popup": (0.0, False),
}

def _get_window_rect_via_win32(hwnd):
    """Get actual window rectangle using Win32 API (more reliable than pywinauto)."""
    import ctypes
    from ctypes import wintypes
    
    GetWindowRect = ctypes.windll.user32.GetWindowRect
    rect = wintypes.RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top

def _untitled_live_windows():
    """Get all untitled Ableton windows using Win32 API for accurate bounds."""
    wins = []
    all_windows = list(Desktop(backend="win32").windows(class_name="Ableton Live Window Class"))
    
    
    for idx, w in enumerate(all_windows):
        try:
            title = w.window_text()
            
            
            if title:
                
                continue
            
            # Get actual window bounds using Win32 API (more reliable)
            left, top, width, height = _get_window_rect_via_win32(w.handle)
            
            
            # Skip windows with obviously invalid bounds
            if width < 100 or height < 100:
                
                continue
            
            # Create a simple bounds object to match the old interface
            class Bounds:
                pass
            r = Bounds()
            r.left = left
            r.top = top
            # Use default arguments to capture values at definition time, not reference
            r.width = lambda w=width: w
            r.height = lambda h=height: h
            
            
            wins.append((w, r))
        except Exception as e:
            
            continue
    
    logger.debug(f"[WIN_DEBUG] Final result: {len(wins)} windows after filtering")
    return wins


def _should_ocr(kind):
    now = time.time()
    last_t, last_res = _ocr_cache[kind]
    if now - last_t < OCR_COOLDOWN:
        return False, last_res
    return True, None

def _store_ocr(kind, result):
    _ocr_cache[kind] = (time.time(), result)
    return result

#tick detection
def find_highlighted_item_bounds(before_screenshot, after_screenshot):
    """
    Find the bounding box of the highlighted item by comparing before/after.
    Returns (left, top, right, bottom) of the highlighted item.
    """
    try:
        diff = ImageChops.difference(before_screenshot, after_screenshot)
        diff_gray = diff.convert("L")
        pixels = np.array(diff_gray)
        
        # Use more aggressive threshold to find just highlighted row
        dark_rows = np.where(np.any(pixels > 30, axis=1))[0]
        dark_cols = np.where(np.any(pixels > 30, axis=0))[0]
        
        if len(dark_rows) == 0 or len(dark_cols) == 0:
            return None
        
        item_left = int(dark_cols[0])
        item_right = int(dark_cols[-1])
        item_top = int(dark_rows[0])
        item_bottom = int(dark_rows[-1])
        
        height = item_bottom - item_top
        # Only return if bounds look like a menu item (height 15-100 pixels)
        if 15 < height < 100:
            return (item_left, item_top, item_right, item_bottom)
        else:
            return None
        
    except Exception as e:
        return None

def detect_checkmark_in_box(before_screenshot, box_bounds, debug_name=None):
    """
    Detect if a menu item has a checkmark based on darkness in left region.
    Returns True if checkmark detected, False otherwise.
    If debug_name is provided, saves a screenshot of the tick region.
    """
    try:
        left, top, right, bottom = box_bounds
        item_crop_before = before_screenshot.crop((left, top, right, bottom))
        
        width, height = item_crop_before.size
        left_region_width = min(20, width)
        left_region = item_crop_before.crop((0, 0, left_region_width, height))
        
        left_array = np.array(left_region.convert("L"))
        dark_pixels = np.sum(left_array < 100)
        total_pixels = left_array.size
        dark_ratio = dark_pixels / total_pixels if total_pixels > 0 else 0
        
        logger.debug(f"{debug_name} - dark_ratio = {dark_ratio:.4f} (threshold: 0.005)")
        
        # Save debug screenshot if requested
        if debug_name:
            debug_dir = "tick_detection_debug"
            if not os.path.exists(debug_dir):
                os.makedirs(debug_dir)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            debug_filename = os.path.join(debug_dir, f"{timestamp}_{debug_name}_tick.png")
            left_region.save(debug_filename)
            logger.debug(f"  Saved debug screenshot: {debug_filename}")
        
        # Threshold tuning FIXED:
        # - 0.05 (5%) was way too strict
        # - 0.02 (2%) still not sensitive enough (was causing all False detections)
        # - 0.005 (0.5%) = very sensitive - detects ANY dark pixels
        # Since we have white background, even a few dark pixels from tick marks count
        # This handles cropped ticks and partial visibility properly
        return dark_ratio > 0.005
        
    except Exception as e:
        logger.debug(f"ERROR in detect_checkmark_in_box: {e}")
        return False

def check_menu_items():
    """
    Check Help View, Device View, and Browser menu items for ticks.
    Returns dict with results: {"Help View": bool, "Device View": bool, "Browser": bool}
    """
    
    # Open View menu
    pyautogui.hotkey('alt', 'v')
    time.sleep(0.3)  # Increased from 0.01 to allow menu to fully render
    
    # Take baseline screenshot
    menu_open_screenshot = pyautogui.screenshot()
    time.sleep(0.2)
    
    # ===== HELP VIEW (4 down) =====
    for i in range(4):
        pyautogui.press('down')
        time.sleep(0.05)  # Increased from 0.001
    time.sleep(0.2)
    
    help_view_screenshot = pyautogui.screenshot()
    help_view_bounds = find_highlighted_item_bounds(menu_open_screenshot, help_view_screenshot)
    help_view_has_tick = detect_checkmark_in_box(menu_open_screenshot, help_view_bounds, "Help_View") if help_view_bounds else False
    
    # ===== CLIP VIEW (4 more down) =====
    for i in range(4):
        pyautogui.press('down')
        time.sleep(0.05)  # Increased from 0.001
    time.sleep(0.2)
    
    clip_view_screenshot = pyautogui.screenshot()
    clip_view_bounds = find_highlighted_item_bounds(menu_open_screenshot, clip_view_screenshot)
    clip_view_has_tick = detect_checkmark_in_box(menu_open_screenshot, clip_view_bounds, "Clip_View") if clip_view_bounds else False


    # ===== DEVICE VIEW (1 more down) 
    for i in range(1):
        pyautogui.press('down')
        time.sleep(0.05)  # Increased from 0.001
    time.sleep(0.2)
    
    device_view_screenshot = pyautogui.screenshot()
    device_view_bounds = find_highlighted_item_bounds(menu_open_screenshot, device_view_screenshot)
    device_view_has_tick = detect_checkmark_in_box(menu_open_screenshot, device_view_bounds, "Device_View") if device_view_bounds else False
    
    # ===== BROWSER (10 more down, total 19) =====
    for i in range(10):
        pyautogui.press('down')
        time.sleep(0.05)  # Increased from 0.001
    time.sleep(0.2)
    
    browser_screenshot = pyautogui.screenshot()
    browser_bounds = find_highlighted_item_bounds(menu_open_screenshot, browser_screenshot)
    browser_has_tick = detect_checkmark_in_box(menu_open_screenshot, browser_bounds, "Browser") if browser_bounds else False
    
    # Close menu
    pyautogui.press('esc')
    time.sleep(0.2)  # Increased from 0.1
    
    return {
        "Help View": help_view_has_tick,
        "Clip View": clip_view_has_tick,
        "Device View": device_view_has_tick,
        "Browser": browser_has_tick
    
    }


## popup detection and handling functions
def detect_no_asio(threshold=0.8):  # returns True/False
    ok, cached = _should_ocr("no_asio")
    if not ok:
        return cached
    
    trigger_phrase = (
        "audio is disabled. please choose an audio output device in audio settings."
    )

    for w, r in _untitled_live_windows():
        try:
            screenshot = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()
            similarity = difflib.SequenceMatcher(None, normalized_text, trigger_phrase).ratio()
            if similarity >= threshold:
                logger.debug(f"Detected no ASIO popup! Similarity: {similarity:.2f}")
                logger.debug(f"Detected text: {normalized_text}")
                return _store_ocr("no_asio", True)
        except Exception as e:
            logger.debug(f"Error in detect_no_asio: {e}")
            continue
    logger.debug("No ASIO popup detected")
    return _store_ocr("no_asio", False)

def dismiss_asio_popup():
    """Dismiss the 'audio is disabled' popup if present."""
    try:
        if detect_no_asio():
            logger.debug("ASIO popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.press('enter')
            time.sleep(0.2)
            return True
    except Exception as e:
        logger.debug(f"Failed to dismiss ASIO popup: {e}")
    return False

def detect_recover_popup(threshold=0.8, verbose=False):  # returns True/False
    ok, cached = _should_ocr("recover_popup")
    if not ok:
        return cached

    # The quoted .als filename in the middle varies per project, so match the
    # static head/tail of the sentence with a sliding window (same approach as
    # fuzzy_phrase_in_text) instead of a whole-window ratio that the filename
    # length can drag below threshold.
    head = "live unexpectedly quit while you were working on the live set"
    tail = "would you like to recover your work"

    for idx, (w, r) in enumerate(_untitled_live_windows()):
        try:
            width, height = r.width(), r.height()
            screenshot = pyautogui.screenshot(region=(r.left, r.top, width, height))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()
            if verbose or len(normalized_text) < 40:
                # Short/garbage OCR (e.g. a half-drawn window or the Ableton
                # splash) can never match - log it so a missed popup leaves a
                # trace of what OCR actually saw.
                logger.debug(f"[RECOVER_CHECK] window {idx} {width}x{height} OCR: {repr(normalized_text[:200])}")
            matched_head, sim_head = fuzzy_phrase_in_text(normalized_text, head, threshold)
            matched_tail, sim_tail = fuzzy_phrase_in_text(normalized_text, tail, threshold)
            if verbose:
                logger.debug(
                    f"[RECOVER_CHECK] window {idx} {width}x{height} "
                    f"head={sim_head:.2f} tail={sim_tail:.2f} vs {threshold:.2f} "
                    f"OCR: {repr(normalized_text[:200])}"
                )
            if matched_head and matched_tail:
                logger.debug(f"Detected recover popup! head={sim_head:.2f} tail={sim_tail:.2f}")
                logger.debug(f"Detected text: {normalized_text}")
                return _store_ocr("recover_popup", True)
        except Exception as e:
            logger.debug(f"Error in detect_recover_popup: {e}")
            continue
    logger.debug("No recover popup detected")
    return _store_ocr("recover_popup", False)

def dismiss_recover_popup(verbose=False):
    """Dismiss the 'recover' popup if present."""
    try:
        if detect_recover_popup(verbose=verbose):
            logger.debug("Recover popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.press('enter')
            time.sleep(0.2)
            return True
    except Exception as e:
        logger.debug(f"Failed to dismiss recover popup: {e}")
    return False






def detect_invalid_popup(threshold=0.75):  # returns True/False
    """
    Detect 'invalid audio file' popup with improved accuracy.
    Pattern: The audio file "{filename}" cannot be read. [This File does not appear to be a valid {filetype} file.]
    
    We extract filename and filetype, then match only the static parts.
    """
    ok, cached = _should_ocr("invalid_popup")
    if not ok:
        return cached
    
    # Static parts of the message (the parts that don't change)
    static_trigger = "cannot be read this file does not appear to be a valid"

    for w, r in _untitled_live_windows():
        try:
            screenshot = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()
            
            # Remove quoted filenames and file extensions - they vary
            # Pattern: "..." or single/double quoted content
            normalized_text = re.sub(r'["\']([^"\']*)["\']', ' ', normalized_text)
            
            # Remove file type words (wav, mp3, aif, flac, etc)
            normalized_text = re.sub(r'\b(wav|mp3|aif|aiff|flac|m4a|ogg|wma|dsd)\b', ' ', normalized_text, flags=re.IGNORECASE)
            
            # Clean up extra whitespace
            normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
            
            similarity = difflib.SequenceMatcher(None, normalized_text, static_trigger).ratio()
            
            if similarity >= threshold:
                logger.debug(f"Detected invalid audio file popup! Similarity: {similarity:.2f}")
                logger.debug(f"Detected text: {normalized_text}")
                return _store_ocr("invalid_popup", True)
        except Exception as e:
            logger.error(f"Error in detect_invalid_popup: {e}")
            continue
    
    logger.debug("No invalid popup detected")
    return _store_ocr("invalid_popup", False)

def dismiss_invalid_popup():
    """Dismiss the 'invalid audio file' popup by pressing Enter."""
    try:
        if detect_invalid_popup():
            logger.debug("Invalid audio file popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press('enter')
            logger.debug("Dismissed invalid popup")
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.debug(f"Failed to dismiss invalid popup: {e}")
    return False

def detect_media_decoding_popup(threshold=0.7):
    """Detect 'file could not be decoded' popup on project load (e.g. Kontakt 7 / MediaFoundation)."""
    trigger_phrases = (
        "could not be decoded",
        "could not be decoded using mediafoundation",
        "mediafoundation",
        "error code",
    )

    for w in Desktop(backend="win32").windows(class_name="Ableton Live Window Class"):
        try:
            title = w.window_text()
            if title:
                # Skip titled windows (Ableton main window, untitled Live window)
                continue

            r = w.rectangle()
            width = r.width()
            height = r.height()

            if width <= 0 or height <= 0:
                continue

            screenshot = pyautogui.screenshot(region=(r.left, r.top, width, height))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()

            for phrase in trigger_phrases:
                if phrase in normalized_text:
                    logger.debug(f"Detected media decoding popup! Text: {normalized_text[:200]}")
                    return True

        except Exception:
            continue

    return False


def detect_codec_popup(threshold=0.7):
    """Detect 'file could not be decoded using MediaFoundation' popup on project load.

    Static trigger: "could not be decoded using mediafoundation error code : N"
    The filename varies, so we match only the static part after filename cleanup.
    """
    ok, cached = _should_ocr("decoding")
    if not ok:
        return cached

    trigger_parts = (
        "could not be decoded",
        "mediafoundation",
        "error code",
    )

    for w, r in _untitled_live_windows():
        try:
            screenshot = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()

            # Remove quoted filenames (they vary)
            normalized_text = re.sub(r"['\"]", " ", normalized_text)

            # Remove file type words
            normalized_text = re.sub(r'\b(mov|mp4|m4v|avi|mkv|webm)\b', ' ', normalized_text, flags=re.IGNORECASE)
            pass  # channel-strip removed: it erased the trigger word

            normalized_text = re.sub(r"\s+", " ", normalized_text).strip()

            if all(part in normalized_text for part in trigger_parts):
                logger.debug(f"Detected codec popup! Text: {normalized_text[:200]}")
                return _store_ocr("decoding", True)

        except Exception as e:
            logger.debug(f"Error in detect_codec_popup: {e}")
            continue

    logger.debug("No codec popup detected")
    return _store_ocr("decoding", False)


def dismiss_codec_popup():
    """Dismiss the 'file could not be decoded using MediaFoundation' popup by pressing Enter."""
    try:
        if detect_codec_popup():
            logger.debug("Codec (MediaFoundation) popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press("enter")
            logger.debug("Dismissed codec popup")
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.debug(f"Failed to dismiss codec popup: {e}")
    return False


# ---------------------------------------------------------------------------
# Plugin popups (Kontakt 7, Qubiq Engine, ...): handled by window handle,
# not OCR.
#
# Ableton's own popups are untitled "Ableton Live Window Class" windows
# (OCR'd by the detectors above). Plugins raise something different: a
# *named* standard Win32 dialog (class #32770) owned by the Live process -
# e.g. the "Kontakt 7" recall warning that blocked a project open while
# detect_save_changes_to_popup OCR'd its pixels through the untitled Ableton
# window behind it (0.45 similarity vs 0.80 threshold). Matching by
# handle/class/title means we never OCR a plugin's messagebox needlessly, we
# know exactly which popup is up, and every popup seen is logged with its
# handle so unfamiliar ones can be filtered in later.
# ---------------------------------------------------------------------------

# Named Ableton-native dialogs that must never get a blind Enter: each has a
# dedicated handler (file dialogs, confirmations, export window). Mirrors
# ableton_state.KNOWN_BLOCKING_TITLES minus "Qubiq Engine", which - like
# every other plugin messagebox - is answered with Enter.
_ABLETON_NATIVE_DIALOG_PREFIXES = (
    "Open Document:",
    "Save Audio File As:",
    "Save Live Set As:",
    "Confirm Save As",
    "Export Audio/Video",
)

# Handles already logged once, so poll loops don't re-report every window.
_reported_popup_hwnds = set()


def _live_named_popup_windows():
    """Live-owned, titled, popup-sized windows that are not Ableton's own.

    Returns ableton_state.WindowInfo entries; describe() carries
    hwnd/pid/class/title, so state dumps and the dismissal logs report the
    handle of every popup. Each candidate is logged (once per handle) under
    [POPUP_HANDLE] - that report is how an unfamiliar popup gets identified
    and filtered in later.
    """
    windows = []
    seen = set()
    for w in ableton_state.list_ableton_windows():
        if w.class_name in (ableton_state.ABLETON_WINDOW_CLASS,
                            ableton_state.GHOST_WINDOW_CLASS):
            # Ableton's main window and its own themed popups are handled by
            # the OCR detectors; the Ghost stand-in is not a popup.
            continue
        if not w.title:
            continue
        if w.width < ableton_state.MIN_POPUP_SIZE or w.height < ableton_state.MIN_POPUP_SIZE:
            continue
        if any(w.title.startswith(t) for t in _ABLETON_NATIVE_DIALOG_PREFIXES):
            continue
        windows.append(w)
        seen.add(w.hwnd)
        if w.hwnd not in _reported_popup_hwnds:
            _reported_popup_hwnds.add(w.hwnd)
            logger.debug(f"[POPUP_HANDLE] Live popup candidate: {w.describe()}")
    _reported_popup_hwnds.intersection_update(seen)
    return windows


def find_plugin_messagebox_popups(title_contains=None):
    """Standard Win32 dialogs (#32770) Live has open: plugin messageboxes.

    Any plugin can raise one while a set loads ("Kontakt 7", "Qubiq
    Engine", ...); they all share the default messagebox style and are
    answered with Enter. Filtered by handle/class/title - no OCR.
    `title_contains` narrows the result case-insensitively.
    """
    popups = [
        w for w in _live_named_popup_windows()
        if w.class_name == ableton_state.STANDARD_DIALOG_CLASS
    ]
    if title_contains:
        needle = title_contains.lower()
        popups = [w for w in popups if needle in w.title.lower()]
    return popups


def dismiss_plugin_messagebox_popups(title_contains=None, max_rounds=5, poll=0.4):
    """Enter-dismiss plugin messageboxes. True if at least one was pressed.

    Every popup is logged with hwnd/pid/class/title before the Enter press
    and any that survive are logged as warnings - handles included, so an
    unknown popup can be identified from the log alone. Each handle gets at
    most one Enter per call; wait_for_clean_state's polling retries the rest.
    """
    dismissed = 0
    pressed_hwnds = set()
    try:
        for _ in range(max_rounds):
            fresh = [
                w for w in find_plugin_messagebox_popups(title_contains)
                if w.hwnd not in pressed_hwnds
            ]
            if not fresh:
                break
            for w in fresh:
                logger.info(f"[POPUP_HANDLE] Dismissing plugin messagebox: {w.describe()}")
                pressed_hwnds.add(w.hwnd)
                try:
                    win_activate(w.title)
                except Exception as e:
                    logger.debug(f"Could not focus popup '{w.title}' before Enter: {e}")
                pyautogui.press("enter")
                dismissed += 1
                time.sleep(poll)
        for w in find_plugin_messagebox_popups(title_contains):
            if w.hwnd in pressed_hwnds:
                logger.warning(
                    f"[POPUP_HANDLE] Plugin messagebox survived Enter: {w.describe()}"
                )
    except Exception as e:
        logger.debug(f"Failed to dismiss plugin messagebox popups: {e}")
    return dismissed > 0


def detect_kontakt_popup(threshold=0.7):
    """Detect the 'Kontakt 7' popup by window handle (no OCR).

    `threshold` is kept for call-signature compatibility and ignored: the
    popup is a named #32770 dialog, so handle/title matching is exact where
    OCR over Ableton's windows both missed it (wrong window class) and
    polluted the save-changes check's screenshots.
    """
    found = find_plugin_messagebox_popups(title_contains="kontakt")
    for w in found:
        logger.debug(f"[POPUP_HANDLE] Kontakt popup detected: {w.describe()}")
    return bool(found)


def dismiss_kontakt_popup():
    """Dismiss the 'Kontakt 7' popup with Enter (handle-based, no OCR)."""
    try:
        return dismiss_plugin_messagebox_popups(title_contains="kontakt")
    except Exception as e:
        logger.debug(f"Failed to dismiss Kontakt popup: {e}")
    return False


def wait_for_recover_or_asio(timeout=90, poll=1.0):
    """
    Wait for Ableton to fully start up, handling popups in order:
    1. Recover popup (if Live crashed previously) - comes FIRST
    2. ASIO popup (audio disabled) - comes SECOND if recover not present
    3. Main Ableton window - final state
    
    Args:
        timeout: Maximum time to wait (seconds)
        poll: Check interval (seconds)
    
    Returns:
        "recover" if recover popup was dismissed
        "asio" if ASIO popup was dismissed
        "live" if main window appeared without popups
        "timeout" if none of the above within timeout
    """
    start = time.time()
    
    while time.time() - start < timeout:
        # Priority 1: Check for recover popup FIRST (comes before ASIO)
        if dismiss_recover_popup():
            logger.debug("Recover popup dismissed, continuing...")
            return "recover"
        
        # Priority 2: Check for ASIO popup (comes after recover)
        if dismiss_asio_popup():
            logger.debug("ASIO popup dismissed, continuing...")
            return "asio"
        
        # Priority 3: Check for main Ableton window
        try:
            for w in Desktop(backend="win32").windows():
                title = w.window_text()
                if title and "Ableton Live" in title:
                    logger.debug(f"Main Ableton window detected: {title}")
                    return "live"
        except Exception as e:
            logger.debug(f"Window enumeration error: {e}")
        
        time.sleep(poll)
    
    logger.warning(f"Timeout waiting for Ableton startup after {timeout}s")
    return "timeout"


def detect_specify_window(threshold=0.75):
    ok, cached = _should_ocr("specify")
    if not ok:
        logger.debug(f"Specify window detection on cooldown, returning cached: {cached}")
        return cached

    logger.debug("Running fresh OCR for specify window detection...")
    trigger_phrase = "specify which used media files are to be copied into the project"
    
    # Crop fractions (resolution-independent)
    # Calculated from 534x290 reference window
    CROP_LEFT = 115 / 534      # 0.2154
    CROP_RIGHT = 21 / 534      # 0.0394
    CROP_TOP = 50 / 290        # 0.1724
    CROP_BOTTOM = 180 / 290    # 0.6207
    
    windows_checked = 0

    for w, r in _untitled_live_windows():
        try:
            # Filter by aspect ratio - specify window is ~534x290 (ratio ~1.84)
            # Allow 1.7-2.0 range for different screen resolutions
            width = r.width()
            height = r.height()
            aspect_ratio = width / height if height > 0 else 0
            if aspect_ratio < 1.7 or aspect_ratio > 2.0:
                logger.debug(f"Skipping window with aspect ratio {aspect_ratio:.2f} (expected 1.7-2.0 for specify)")
                continue
            
            windows_checked += 1
            logger.debug(f"[SPECIFY_CHECK] Checking window {windows_checked}: {width}x{height} (ratio {aspect_ratio:.2f})")
            
            # Wait for window to fully render (semi-transparent initially)
            time.sleep(0.5)
            
            # Take screenshot
            screenshot = pyautogui.screenshot(region=(r.left, r.top, width, height))
            
            # Crop to remove junk text (using fractions for resolution independence)
            crop_left = int(width * CROP_LEFT)
            crop_right = int(width * CROP_RIGHT)
            crop_top = int(height * CROP_TOP)
            crop_bottom = int(height * CROP_BOTTOM)
            
            cropped = screenshot.crop((
                crop_left,
                crop_top,
                width - crop_right,
                height - crop_bottom
            ))
            
            # Run OCR on cropped image
            text = pytesseract.image_to_string(cropped)
            logger.debug(f"[SPECIFY_CHECK] OCR text raw: {repr(text[:100])}")
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()
            logger.debug(f"[SPECIFY_CHECK] OCR text normalized: {repr(normalized_text[:100])}")
            
            # Use fuzzy matching to handle OCR variations
            similarity = difflib.SequenceMatcher(None, normalized_text, trigger_phrase).ratio()
            logger.debug(f"[SPECIFY_CHECK] Similarity: {similarity:.2f} vs threshold {threshold:.2f}")
            
            if similarity >= threshold:
                logger.debug("Detected specify window (Collect All & Save).")
                return _store_ocr("specify", True)
        except Exception as e:
            logger.debug(f"Error checking window for specify: {e}")
            continue

    logger.debug(f"[SPECIFY_CHECK] No specify window found (checked {windows_checked} windows)")
    return _store_ocr("specify", False)

def fuzzy_phrase_in_text(text, phrase, threshold=0.8):
    """
    Check whether a phrase appears anywhere within OCR text,
    allowing for OCR errors.
    """
    text = re.sub(r"\s+", " ", text.lower()).strip()
    phrase = re.sub(r"\s+", " ", phrase.lower()).strip()

    # Exact substring match
    if phrase in text:
        return True, 1.0

    phrase_words = phrase.split()
    text_words = text.split()

    if len(text_words) < len(phrase_words):
        return False, 0.0

    best_similarity = 0.0

    for i in range(len(text_words) - len(phrase_words) + 1):
        candidate = " ".join(
            text_words[i:i + len(phrase_words)]
        )

        similarity = difflib.SequenceMatcher(
            None,
            candidate,
            phrase
        ).ratio()

        best_similarity = max(best_similarity, similarity)

    return best_similarity >= threshold, best_similarity




def detect_save_popup(threshold=0.75):

    ok, cached = _should_ocr("save_popup")
    if not ok:
        logger.debug(f"Save popup detection on cooldown, returning cached: {cached}")
        return cached

    logger.debug("Running fresh OCR for save popup detection...")

    trigger_phrase = (
        "the live set you are trying to save was created with a version "
        "of live that is either older or has fewer features to avoid "
        "incompatibilities please save your set as a new file"
    )

    # Crop fractions (resolution-independent)
    # Calculated from 534x205 reference window
    CROP_LEFT = 100 / 534
    CROP_RIGHT = 24 / 534
    CROP_TOP = 35 / 205
    CROP_BOTTOM = 55 / 205

    windows_checked = 0

    for w, r in _untitled_live_windows():
        try:
            # Filter by aspect ratio - save popup is ~534x205 (ratio ~2.60)
            # Allow 2.4-2.8 range for different screen resolutions
            width = r.width()
            height = r.height()

            aspect_ratio = width / height if height > 0 else 0

            if aspect_ratio < 2.4 or aspect_ratio > 2.8:
                logger.debug(
                    f"Skipping window with aspect ratio "
                    f"{aspect_ratio:.2f} "
                    f"(expected 2.4-2.8 for save)"
                )
                continue

            windows_checked += 1

            logger.debug(
                f"[SAVE_CHECK] Checking window {windows_checked}: "
                f"{width}x{height} (ratio {aspect_ratio:.2f})"
            )

            # Wait for window to fully render (semi-transparent initially)
            time.sleep(0.5)

            screenshot = pyautogui.screenshot(
                region=(r.left, r.top, width, height)
            )

            screenshot.save(
                f"debug/debug_save_popup_screenshot_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )

            # Crop to remove junk text
            # Using fractions for resolution independence
            crop_left = int(width * CROP_LEFT)
            crop_right = int(width * CROP_RIGHT)
            crop_top = int(height * CROP_TOP)
            crop_bottom = int(height * CROP_BOTTOM)

            cropped = screenshot.crop((
                crop_left,
                crop_top,
                width - crop_right,
                height - crop_bottom
            ))

            # Run OCR on cropped image
            text = pytesseract.image_to_string(cropped)

            logger.debug(
                f"[SAVE_CHECK] OCR text raw: "
                f"{repr(text[:200])}"
            )

            normalized_text = re.sub(
                r"\s+",
                " ",
                text.lower()
            ).strip()

            logger.debug(
                f"[SAVE_CHECK] OCR text normalized: "
                f"{repr(normalized_text[:200])}"
            )

            # Use the shared fuzzy phrase matcher.
            # It first checks for an exact phrase anywhere in the OCR
            # and then performs fuzzy matching against word-sized windows.
            matched, similarity = fuzzy_phrase_in_text(
                normalized_text,
                trigger_phrase,
                threshold
            )

            logger.debug(
                f"[SAVE_CHECK] Best phrase similarity: "
                f"{similarity:.2f} vs threshold {threshold:.2f}"
            )

            if matched:
                logger.debug(
                    "Detected save popup (Collect All & Save). "
                    f"Similarity: {similarity:.2f}"
                )
                return _store_ocr("save_popup", True)

        except Exception as e:
            logger.debug(
                f"Error checking window for save popup: {e}"
            )
            continue

    logger.debug(
        f"[SAVE_CHECK] No save popup found "
        f"(checked {windows_checked} windows)"
    )

    return _store_ocr("save_popup", False)


def detect_save_changes_to_popup(threshold=0.8, current_project=None):
    ok, cached = _should_ocr("save_changes_to_popup")
    if not ok:
        return cached

    trigger_phrase = "save changes to before closing"
    # Anchored fallback: the fixed head + tail with anything (the quoted set
    # name, OCR junk) in between. fuzzy_phrase_in_text slides a window exactly
    # trigger-length, so an inserted project name can never match - this regex
    # covers that case.
    anchor_re = re.compile(
        r"save\s+changes\s+to\b.*\bbe\s*fore\s+closing\?", re.IGNORECASE | re.DOTALL
    )
    windows_found = 0

    for w, r in _untitled_live_windows():
        windows_found += 1

        try:
            screenshot = pyautogui.screenshot(
                region=(r.left, r.top, r.width(), r.height())
            )

            text = pytesseract.image_to_string(screenshot)

            # Remove quoted project names
            text = re.sub(r'"[^"]*"', ' ', text)

            # Strip the currently-open set name if we know it (OCR usually
            # keeps it unquoted, e.g. "save changes to  arp with kontakt ...").
            if current_project:
                stripped = re.sub(
                    re.escape(current_project), ' ', text, flags=re.IGNORECASE
                )
                # Also try without the .als extension / trailing "*" dirty flag.
                base = re.sub(r"\.als\*?$", "", current_project.strip(), flags=re.IGNORECASE)
                if base and base.lower() != current_project.lower():
                    stripped = re.sub(re.escape(base), ' ', stripped, flags=re.IGNORECASE)
                text = stripped

            # Remove button text
            text = re.sub(
                r'\b(yes|no|cancel)\b',
                ' ',
                text,
                flags=re.IGNORECASE
            )

            text = re.sub(r'[|()]+', ' ', text)
            text = re.sub(r'-', ' ', text)

            normalized_text = re.sub(
                r"\s+",
                " ",
                text.lower()
            ).strip()

            logger.debug(
                f"[SAVE_CHANGES_CHECK] OCR text normalized: "
                f"{repr(normalized_text[:200])}"
            )

            matched, similarity = fuzzy_phrase_in_text(
                normalized_text,
                trigger_phrase,
                threshold
            )

            anchor_matched = bool(anchor_re.search(normalized_text))

            logger.debug(
                f"[SAVE_CHANGES_CHECK] Best phrase similarity: "
                f"{similarity:.2f} vs threshold {threshold:.2f}"
                f" anchor_match={anchor_matched}"
            )

            if matched or anchor_matched:
                logger.debug(
                    f"Detected save-changes popup! "
                    f"Similarity: {similarity:.2f}"
                )
                return _store_ocr(
                    "save_changes_to_popup",
                    True
                )

        except Exception as e:
            logger.debug(f"Exception in window loop: {e}")
            import traceback
            traceback.print_exc()
            continue

    logger.debug(
        f"No save-changes popup matched "
        f"({windows_found} untitled windows checked)"
    )

    return _store_ocr("save_changes_to_popup", False)

   

def handle_save_changes_to():
    time.sleep(1)
    pyautogui.press('tab')
    time.sleep(0.5)
    pyautogui.press('enter')
    logger.debug("Handled save changes popup")



def detect_decoding_popup(threshold=0.75):
    """Detect 'samples not ready' popup during Collect All and Save."""
    trigger_phrase = "some samples are not ready yet, as they are still decoding, loading or downloading. operation cancelled."

    for w in Desktop(backend="win32").windows(class_name="Ableton Live Window Class"):
        try:
            if w.window_text():  # skip titled windows
                continue

            r = w.rectangle()
            width = r.width()
            height = r.height()

            if width <= 0 or height <= 0:
                continue

            screenshot = pyautogui.screenshot(region=(r.left, r.top, width, height))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()

            # Fuzzy match using difflib
            similarity = difflib.SequenceMatcher(None, normalized_text, trigger_phrase).ratio()
            if similarity >= threshold:
                logger.debug(f"Detected decoding popup! Similarity: {similarity:.2f}")
                return True

        except Exception:
            continue

    return False


def detect_media_decoding_popup(threshold=0.7):
    """Detect 'file could not be decoded' popup on project load (e.g. Kontakt 7 / MediaFoundation)."""
    trigger_phrases = (
        "could not be decoded",
        "could not be decoded using mediafoundation",
        "mediafoundation",
        "error code",
    )

    for w in Desktop(backend="win32").windows(class_name="Ableton Live Window Class"):
        try:
            title = w.window_text()
            if title:
                # Skip titled windows (Ableton main window, untitled Live window)
                continue

            r = w.rectangle()
            width = r.width()
            height = r.height()

            if width <= 0 or height <= 0:
                continue

            screenshot = pyautogui.screenshot(region=(r.left, r.top, width, height))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()

            for phrase in trigger_phrases:
                if phrase in normalized_text:
                    logger.debug(f"Detected media decoding popup! Text: {normalized_text[:200]}")
                    return True

        except Exception:
            continue

    return False


def detect_codec_popup(threshold=0.7):
    """Detect 'file could not be decoded using MediaFoundation' popup on project load.

    Static trigger: "could not be decoded using mediafoundation error code : N"
    The filename varies, so we match only the static part after filename cleanup.
    """
    ok, cached = _should_ocr("decoding")
    if not ok:
        return cached

    trigger_parts = (
        "could not be decoded",
        "mediafoundation",
        "error code",
    )

    for w, r in _untitled_live_windows():
        try:
            screenshot = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()

            # Remove quoted filenames (they vary)
            normalized_text = re.sub(r"['\"]", " ", normalized_text)

            # Remove file type words
            normalized_text = re.sub(r'\b(mov|mp4|m4v|avi|mkv|webm)\b', ' ', normalized_text, flags=re.IGNORECASE)
            pass  # channel-strip removed: it erased the trigger word

            normalized_text = re.sub(r"\s+", " ", normalized_text).strip()

            if all(part in normalized_text for part in trigger_parts):
                logger.debug(f"Detected codec popup! Text: {normalized_text[:200]}")
                return _store_ocr("decoding", True)

        except Exception as e:
            logger.debug(f"Error in detect_codec_popup: {e}")
            continue

    logger.debug("No codec popup detected")
    return _store_ocr("decoding", False)


def dismiss_codec_popup():
    """Dismiss the 'file could not be decoded using MediaFoundation' popup by pressing Enter."""
    try:
        if detect_codec_popup():
            logger.debug("Codec (MediaFoundation) popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press("enter")
            logger.debug("Dismissed codec popup")
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.debug(f"Failed to dismiss codec popup: {e}")
    return False


def _confirm_save_as_overwrite():
    """Dismiss a visible 'Confirm Save As' prompt.

    Prefers clicking the dialog's own Yes button over a blind Alt+Y: the
    prompt grabs focus from the Save dialog the caller just typed into, so
    the accelerator only lands if that prompt happens to own focus. The
    button caption carries its accelerator as '&Yes', so match on the
    stripped text rather than the literal 'Yes'.
    """
    for confirm_win in _find_all_window_elements(r".*Confirm Save As.*", backend="win32"):
        try:
            for child in confirm_win.children():
                try:
                    caption = child.window_text().replace("&", "").strip().lower()
                    is_button = child.class_name() == "Button"
                except Exception:
                    continue
                if is_button and caption == "yes":
                    child.click()
                    logger.debug("Overwrite confirmed (Yes button)")
                    return True
        except Exception as e:
            logger.debug(f"Could not click Yes on 'Confirm Save As': {e}")
    pyautogui.hotkey("alt", "y")
    logger.debug("Overwrite confirmed via Alt+Y fallback")
    return True


def handle_overwrite_confirmation(save_dialog_title="Save Audio File As:", timeout=5):
    """Handle the 'Confirm Save As' overwrite prompt.

    Polls rather than checking once: the prompt only appears once Ableton has
    written the file and stat'd the destination, which on a slow disk (HDD
    export folder) is well after the caller's Alt+S. A single check that fires
    before the prompt exists returns "no overwrite needed" and leaves the
    prompt on screen, where it blocks the save dialog indefinitely.

    The wait ends as soon as either the prompt appears or the save dialog
    closes (meaning no overwrite was needed), so the common no-prompt case
    still returns as soon as the dialog is gone.
    """
    deadline = time.monotonic() + timeout
    while not win_exists("Confirm Save As"):
        if not win_exists(save_dialog_title) or time.monotonic() >= deadline:
            logger.debug("No overwrite prompt")
            return True
        time.sleep(0.2)

    logger.debug("'Confirm Save As' window exists")
    try:
        return _confirm_save_as_overwrite()
    except Exception as e:
        logger.debug(f"Failed to handle overwrite: {e}")
        return False

def handle_decoding_popup(retry_delay=2):
    attempt = 0
    while True:
        attempt += 1
        if detect_decoding_popup():
            logger.debug(f"Decoding popup detected (attempt {attempt}), dismissing...")
            pyautogui.press('enter')
            time.sleep(retry_delay)
            
            logger.debug(f"Retrying export window (attempt {attempt})...")
            pyautogui.hotkey('ctrl', 'shift', 'r')
            time.sleep(0.2)
            logger.debug("Opened export window")
            time.sleep(0.2)
        else:
            logger.debug("No decoding popup detected, buffering complete - proceeding with export")
            return True

def _find_dialog_by_ratio_progress_bar(class_name, min_ratio, max_ratio):
    """Find a top-level window by class whose height/width ratio is within bounds."""
    for w in _list_windows_by_class_win32(class_name):
        try:
            rect = w.rectangle()
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width <= 0:
                continue
            ratio = round(height / width, 4)
            if min_ratio <= ratio <= max_ratio:
                return w, ratio, (width, height)
        except Exception:
            continue
    return None, None, None
    
def handle_progress_bar_wait(timeout=120, appear_timeout=0):
    """Wait while the progress bar dialog is present (ratio ~0.47-0.50).

    `appear_timeout` keeps polling for that long for the bar to show up at
    all - without it, a bar that opens a moment after this is called (slow
    disk) is reported as "never appeared" and left running behind the caller.
    """
    start = time.time()
    seen = False
    while time.time() - start < timeout:
        w, ratio, wh = _find_dialog_by_ratio_progress_bar("Ableton Live Window Class", 0.47, 0.50)
        if w:
            if not seen:
                logger.debug(f"Progress bar detected (ratio={ratio}, size={wh})")
                seen = True
            time.sleep(0.2)
        elif not seen and time.time() - start < appear_timeout:
            time.sleep(0.2)
        else:
            if seen:
                logger.debug("Progress bar closed")
            return seen
    logger.debug("Progress bar still present after timeout")
    return False

def handle_sidemenus():
    """Ensure Help View and Browser side menus are CLOSED."""

    view_ticks = check_menu_items()
    logger.debug(f"Help View: {view_ticks['Help View']}")
    logger.debug(f"Clip View: {view_ticks['Clip View']}")
    logger.debug(f"Device View: {view_ticks['Device View']}")
    logger.debug(f"Browser: {view_ticks['Browser']}")

    if view_ticks['Help View']:
        pyautogui.hotkey('ctrl', 'alt', '7')
    time.sleep(0.1)
    

    if view_ticks['Clip View']:
        pyautogui.hotkey('ctrl', 'alt', '3')
    time.sleep(0.1)

    if view_ticks['Browser']:
        pyautogui.hotkey('ctrl', 'alt', '5')
    time.sleep(0.1)

    if view_ticks['Device View']:
        pyautogui.hotkey('ctrl', 'alt', '4')
    time.sleep(0.1)

    

def handle_qubiq_popup(timeout=60):
    """
    Handle Qubiq Engine popup(s) continuously until all instances are dismissed.
    The popup may appear multiple times (once per problematic plugin instance).
    
    Args:
        timeout: Maximum time to spend handling popups (seconds)
    
    Returns:
        True when all popups are handled or no popup found
        False if timeout exceeded
    """
    start_time = time.time()
    popup_count = 0
    
    while time.time() - start_time < timeout:
        if not win_exists("Qubiq Engine"):
            if popup_count > 0:
                logger.debug(f"All {popup_count} Qubiq Engine popup(s) handled")
            else:
                logger.debug("No Qubiq Engine popup")
            return True
        
        # Popup exists - dismiss it
        popup_count += 1
        logger.debug(f"Qubiq Engine popup #{popup_count} detected")
        
        try:
            pyautogui.press('enter')
            logger.debug(f"Dismissed popup #{popup_count}")
            time.sleep(0.3)
        except Exception as e:
            logger.debug(f"Failed to dismiss Qubiq Engine popup: {e}")
            return False
    
    logger.error(f"Qubiq Engine popups not fully handled within {timeout}s (handled {popup_count})")
    return False


# save dialog getters and setters
def get_save_live_set_as(timeout=30):
    """Wait for and return the 'Save Live Set As:' dialog window."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            for w in Desktop(backend="win32").windows():
                title = w.window_text()
                if title == "Save Live Set As:":
                    logger.debug(f"Found 'Save Live Set As:' dialog")
                    return w
        except Exception as e   :
            logger.debug(f"Error in get_save_live_set_as: {e}")
        time.sleep(0.3)
    
    logger.debug(f"'Save Live Set As:' dialog not found within {timeout} seconds")
    return None

def get_save_audio_file_as(timeout=30):
    """Wait for and return the 'Save Audio File As:' dialog window."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            for w in Desktop(backend="win32").windows():
                title = w.window_text()
                if title == "Save Audio File As:":
                    logger.debug(f"Found 'Save Audio File As:' dialog")
                    return w
        except Exception as e:
            logger.debug(f"Error in get_save_audio_file_as: {e}")
        time.sleep(0.3)
    
    logger.debug(f"'Save Audio File As:' dialog not found within {timeout} seconds")
    return None


def get_filename_from_dialog(dlg):
    """Extract the current filename from the edit field in the save dialog."""
    if dlg is None:
        logger.error("Dialog object is None")
        return ""
    
    try:
        children = dlg.children()
        logger.debug(f"Dialog has {len(children)} children")
        
        for i, child in enumerate(children):
            class_name = child.class_name()
            text = child.window_text()
            
            if class_name == "Edit" and text:
                logger.debug(f"Found Edit control [{i}]: '{text}'")
                
                # Strip any Ableton version suffix like (AL12), (AL11.2), (AL12.1), etc.
                # Pattern: (AL followed by digits and optional dots)
                cleaned_text = re.sub(r'\s*\(AL[\d.]+\)', '', text)
                
                if cleaned_text != text:
                    logger.debug(f"Stripped Ableton version suffix: '{text}' -> '{cleaned_text}'")
                
                return cleaned_text
        
        logger.error("No Edit control with text found")
        return ""
    except Exception as e:
        logger.error(f"ERROR in get_filename_from_dialog: {e}")
        import traceback
        traceback.print_exc()
        return ""

def set_text_on_edit_control(dlg, filename):
    """Set filename in a Save dialog. Try control (Win32/UIA) first, then keyboard."""
    if dlg is None:
        return False

    try:
        dlg.set_focus()
    except Exception:
        pass
    try:
        ensure_caps_lock_off()
        pyautogui.hotkey('alt', 'n')  # focus name box
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        # Clipboard paste is immune to keyboard state (Caps Lock, dead Shift
        # keys from rapid hotkeys); fall back to raw typing if unavailable.
        pasted = False
        try:
            import pyperclip
            previous_clipboard = pyperclip.paste()
            pyperclip.copy(filename)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.2)
            pasted = True
            # The dialog has consumed the text; put the old clipboard back.
            pyperclip.copy(previous_clipboard)
        except Exception as clip_error:
            logger.debug(f"Clipboard paste unavailable ({clip_error}) - typing instead")
        if not pasted:
            pyautogui.write(filename, interval=0.02)
        logger.debug(f"Set filename via keyboard: {filename}")
        return True
    except Exception as e:
        logger.debug(f"Keyboard set failed: {e}")
        return False

def set_export_path_in_dialog(export_path):
    """Set export path in a Save dialog via Alt+D (directory field)."""
    try:
        ensure_caps_lock_off()
        pyautogui.hotkey('alt', 'd')
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.write(export_path, interval=0.02)
        time.sleep(0.1)
        pyautogui.press('enter')
        time.sleep(0.1)
        logger.debug(f"Set export path to: {export_path}")
        return True
    except Exception as e:
        logger.debug(f"Failed to set export path via keyboard: {e}")
        return False

def handle_save_live_set_as(ableton_version):
    dlg = get_save_live_set_as(timeout=30)
    if dlg is None:
        logger.error("'Save Live Set As' dialog not found")
        return False
    
    current_filename = get_filename_from_dialog(dlg)
    if not current_filename:
        logger.error("Could not get filename from dialog")
        return False
    
    
    naming = naming_settings.load_settings()
    if naming["include_ableton_version"]:
        new_filename = append_version_to_filename(current_filename, ableton_version)

    else:
        new_filename = current_filename
    

    if not set_text_on_edit_control(dlg, new_filename):
        return False
        
    
    time.sleep(0.2)
                    
    # Save  
    pyautogui.hotkey('alt', 's')
    logger.debug("Sent Alt+S to save")
    time.sleep(0.2)

    # Poll until the dialog closes, confirming the overwrite prompt whenever it
    # shows up. It must be handled *inside* this loop: on a slow disk (HDD
    # export folder) Ableton only raises the prompt after writing the .als and
    # stat'ing the destination, well after the 0.2s above. Checking once before
    # waiting misses it entirely, and the prompt then blocks this dialog
    # forever - the main window, Save dialog and prompt all go 'disabled'.
    end_wait = time.time() + 30
    confirmed = False
    while time.time() < end_wait:
        if not confirmed and win_exists("Confirm Save As"):
            confirmed = True
            try:
                _confirm_save_as_overwrite()
            except Exception as e:
                logger.debug(f"Failed to handle overwrite confirmation: {e}")
        if not win_exists("Save Live Set As") and not win_exists("Save Live Set As:"):
            logger.debug("Save Live Set As closed")
            return True
        time.sleep(0.2)
        
    logger.debug("Save Live Set As did not close in time")
    return False
