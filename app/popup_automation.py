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
from PIL import ImageChops
from pywinauto import Application, Desktop

import naming_settings
from paths import DEBUG_DIR
from window_utils import win_exists, _list_windows_by_class_win32
from project_and_file_naming import append_version_to_filename
from ableton_state import _window_text

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
            # GetWindowTextW, not window_text(): the latter can read '' while
            # Ableton is busy, which would misreport the main window as a popup.
            title = _window_text(w.handle)
            
            
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
    
    logger.info(f"[WIN_DEBUG] Final result: {len(wins)} windows after filtering")
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
            logger.info(f"  Saved debug screenshot: {debug_filename}")
        
        # Threshold tuning FIXED:
        # - 0.05 (5%) was way too strict
        # - 0.02 (2%) still not sensitive enough (was causing all False detections)
        # - 0.005 (0.5%) = very sensitive - detects ANY dark pixels
        # Since we have white background, even a few dark pixels from tick marks count
        # This handles cropped ticks and partial visibility properly
        return dark_ratio > 0.005
        
    except Exception as e:
        logger.info(f"ERROR in detect_checkmark_in_box: {e}")
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
                logger.info(f"Detected no ASIO popup! Similarity: {similarity:.2f}")
                logger.info(f"Detected text: {normalized_text}")
                return _store_ocr("no_asio", True)
        except Exception as e:
            logger.info(f"Error in detect_no_asio: {e}")
            continue
    logger.info("No ASIO popup detected")
    return _store_ocr("no_asio", False)

def dismiss_asio_popup():
    """Dismiss the 'audio is disabled' popup if present."""
    try:
        if detect_no_asio():
            logger.info("ASIO popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.press('enter')
            time.sleep(0.2)
            return True
    except Exception as e:
        logger.info(f"Failed to dismiss ASIO popup: {e}")
    return False

def detect_recover_popup(threshold=0.8):  # returns True/False
    ok, cached = _should_ocr("recover_popup")
    if not ok:
        return cached
    
    trigger_phrase = (
        "live unexpectedly quit while you were working on the Live Set Would you like to recover your work?"
    )

    for w, r in _untitled_live_windows():
        try:
            screenshot = pyautogui.screenshot(region=(r.left, r.top, r.width(), r.height()))
            text = pytesseract.image_to_string(screenshot)
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()
            similarity = difflib.SequenceMatcher(None, normalized_text, trigger_phrase).ratio()
            if similarity >= threshold:
                logger.info(f"Detected recover popup! Similarity: {similarity:.2f}")
                logger.info(f"Detected text: {normalized_text}")
                return _store_ocr("recover_popup", True)
        except Exception as e:
            logger.info(f"Error in detect_recover_popup: {e}")
            continue
    logger.info("No recover popup detected")
    return _store_ocr("recover_popup", False)

def dismiss_recover_popup():
    """Dismiss the 'recover' popup if present."""
    try:
        if detect_recover_popup():
            logger.info("Recover popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.press('enter')
            time.sleep(0.2)
            return True
    except Exception as e:
        logger.info(f"Failed to dismiss recover popup: {e}")
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
                logger.info(f"Detected invalid audio file popup! Similarity: {similarity:.2f}")
                logger.info(f"Detected text: {normalized_text}")
                return _store_ocr("invalid_popup", True)
        except Exception as e:
            logger.error(f"Error in detect_invalid_popup: {e}")
            continue
    
    logger.info("No invalid popup detected")
    return _store_ocr("invalid_popup", False)

def dismiss_invalid_popup():
    """Dismiss the 'invalid audio file' popup by pressing Enter."""
    try:
        if detect_invalid_popup():
            logger.info("Invalid audio file popup detected, dismissing...")
            time.sleep(0.2)
            pyautogui.press('enter')
            logger.info("Dismissed invalid popup")
            time.sleep(0.3)
            return True
    except Exception as e:
        logger.info(f"Failed to dismiss invalid popup: {e}")
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
            logger.info("Recover popup dismissed, continuing...")
            return "recover"
        
        # Priority 2: Check for ASIO popup (comes after recover)
        if dismiss_asio_popup():
            logger.info("ASIO popup dismissed, continuing...")
            return "asio"
        
        # Priority 3: Check for main Ableton window
        try:
            for w in Desktop(backend="win32").windows():
                title = w.window_text()
                if title and "Ableton Live" in title:
                    logger.info(f"Main Ableton window detected: {title}")
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
            logger.info(f"[SPECIFY_CHECK] Checking window {windows_checked}: {width}x{height} (ratio {aspect_ratio:.2f})")
            
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
            logger.info(f"[SPECIFY_CHECK] OCR text raw: {repr(text[:100])}")
            normalized_text = re.sub(r"\s+", " ", text.lower()).strip()
            logger.info(f"[SPECIFY_CHECK] OCR text normalized: {repr(normalized_text[:100])}")
            
            # Use fuzzy matching to handle OCR variations
            similarity = difflib.SequenceMatcher(None, normalized_text, trigger_phrase).ratio()
            logger.info(f"[SPECIFY_CHECK] Similarity: {similarity:.2f} vs threshold {threshold:.2f}")
            
            if similarity >= threshold:
                logger.info("Detected specify window (Collect All & Save).")
                return _store_ocr("specify", True)
        except Exception as e:
            logger.debug(f"Error checking window for specify: {e}")
            continue

    logger.info(f"[SPECIFY_CHECK] No specify window found (checked {windows_checked} windows)")
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

            screenshot.save(os.path.join(
                DEBUG_DIR,
                f"debug_save_popup_screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            ))

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


def detect_save_changes_to_popup(threshold=0.8):
    ok, cached = _should_ocr("save_changes_to_popup")
    if not ok:
        return cached

    trigger_phrase = "save changes to before closing"
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

            logger.debug(
                f"[SAVE_CHANGES_CHECK] Best phrase similarity: "
                f"{similarity:.2f} vs threshold {threshold:.2f}"
            )

            if matched:
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
    """Answer "Save changes to <set> before closing?" with No.

    Focus starts on Yes; Tab moves it to No. Never saving here is what keeps
    a wet+dry export's switched-off effects out of the project file.
    """
    time.sleep(1)
    pyautogui.press('tab')
    time.sleep(0.5)
    pyautogui.press('enter')
    logger.info("Handled save changes popup (answered No)")



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
                logger.info(f"Detected decoding popup! Similarity: {similarity:.2f}")
                return True

        except Exception:
            continue

    return False

def handle_overwrite_confirmation(save_dialog_title="Save Audio File As:", timeout=5):
    """Handle 'Confirm Save As' overwrite prompt using Alt+Y.

    Polls rather than checking once: on a slow machine the prompt can appear
    well after the caller's Alt+S. The wait ends as soon as either the prompt
    appears or the save dialog closes (meaning no overwrite was needed).
    """
    deadline = time.monotonic() + timeout
    while not win_exists("Confirm Save As"):
        if not win_exists(save_dialog_title) or time.monotonic() >= deadline:
            logger.info("No overwrite prompt")
            return True
        time.sleep(0.2)

    logger.info("'Confirm Save As' window exists")
    try:
        pyautogui.hotkey('alt', 'y')
        logger.info("Sent Alt+Y as overwrite confirmation")
        time.sleep(0.5)
        return True
    except Exception as e:
        logger.info(f"Failed to handle overwrite: {e}")
        return False

def handle_decoding_popup(retry_delay=2):
    attempt = 0
    while True:
        attempt += 1
        if detect_decoding_popup():
            logger.info(f"Decoding popup detected (attempt {attempt}), dismissing...")
            pyautogui.press('enter')
            time.sleep(retry_delay)
            
            logger.info(f"Retrying export window (attempt {attempt})...")
            pyautogui.hotkey('ctrl', 'shift', 'r')
            time.sleep(0.2)
            logger.info("Opened export window")
            time.sleep(0.2)
        else:
            logger.info("No decoding popup detected, buffering complete - proceeding with export")
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
                logger.info(f"Progress bar detected (ratio={ratio}, size={wh})")
                seen = True
            time.sleep(0.2)
        elif not seen and time.time() - start < appear_timeout:
            time.sleep(0.2)
        else:
            if seen:
                logger.info("Progress bar closed")
            return seen
    logger.info("Progress bar still present after timeout")
    return False

def handle_sidemenus():
    """Ensure Help View and Browser side menus are CLOSED."""

    view_ticks = check_menu_items()
    logger.info(f"Help View: {view_ticks['Help View']}")
    logger.info(f"Clip View: {view_ticks['Clip View']}")
    logger.info(f"Device View: {view_ticks['Device View']}")
    logger.info(f"Browser: {view_ticks['Browser']}")

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
                logger.info(f"All {popup_count} Qubiq Engine popup(s) handled")
            else:
                logger.info("No Qubiq Engine popup")
            return True
        
        # Popup exists - dismiss it
        popup_count += 1
        logger.info(f"Qubiq Engine popup #{popup_count} detected")
        
        try:
            pyautogui.press('enter')
            logger.info(f"Dismissed popup #{popup_count}")
            time.sleep(0.3)
        except Exception as e:
            logger.info(f"Failed to dismiss Qubiq Engine popup: {e}")
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
                    logger.info(f"Found 'Save Live Set As:' dialog")
                    return w
        except Exception as e   :
            logger.info(f"Error in get_save_live_set_as: {e}")
        time.sleep(0.3)
    
    logger.info(f"'Save Live Set As:' dialog not found within {timeout} seconds")
    return None

def get_save_audio_file_as(timeout=30):
    """Wait for and return the 'Save Audio File As:' dialog window."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            for w in Desktop(backend="win32").windows():
                title = w.window_text()
                if title == "Save Audio File As:":
                    logger.info(f"Found 'Save Audio File As:' dialog")
                    return w
        except Exception as e:
            logger.info(f"Error in get_save_audio_file_as: {e}")
        time.sleep(0.3)
    
    logger.info(f"'Save Audio File As:' dialog not found within {timeout} seconds")
    return None


def get_filename_from_dialog(dlg):
    """Extract the current filename from the edit field in the save dialog."""
    if dlg is None:
        logger.error("Dialog object is None")
        return ""
    
    try:
        children = dlg.children()
        logger.info(f"Dialog has {len(children)} children")
        
        for i, child in enumerate(children):
            class_name = child.class_name()
            text = child.window_text()
            
            if class_name == "Edit" and text:
                logger.info(f"Found Edit control [{i}]: '{text}'")
                
                # Strip any Ableton version suffix like (AL12), (AL11.2), (AL12.1), etc.
                # Pattern: (AL followed by digits and optional dots)
                cleaned_text = re.sub(r'\s*\(AL[\d.]+\)', '', text)
                
                if cleaned_text != text:
                    logger.info(f"Stripped Ableton version suffix: '{text}' -> '{cleaned_text}'")
                
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
        pyautogui.hotkey('alt', 'n')  # focus name box
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.write(filename, interval=0.02)
        logger.info(f"Set filename via keyboard: {filename}")
        return True
    except Exception as e:
        logger.info(f"Keyboard set failed: {e}")
        return False

def set_export_path_in_dialog(export_path):
    """Set export path in a Save dialog via Alt+D (directory field)."""
    try:
        pyautogui.hotkey('alt', 'd')
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.1)
        pyautogui.write(export_path, interval=0.02)
        time.sleep(0.1)
        pyautogui.press('enter')
        time.sleep(0.1)
        logger.info(f"Set export path to: {export_path}")
        return True
    except Exception as e:
        logger.info(f"Failed to set export path via keyboard: {e}")
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
    logger.info("Sent Alt+S to save")
    time.sleep(0.2)

    # Poll until the dialog closes, confirming the overwrite prompt whenever it
    # shows up - on a slow disk it can appear well after the 0.2s above.
    end_wait = time.time() + 30
    confirmed = False
    while time.time() < end_wait:
        if not confirmed and win_exists("Confirm Save As"):
            confirmed = True
            try:
                confirm_app = Application(backend="win32").connect(title_re=".*Confirm Save As.*", timeout=5)
                confirm_win = confirm_app.window(title_re=".*Confirm Save As.*")
                try:
                    confirm_win.child_window(title="Yes", class_name="Button").click()
                    logger.info("Overwrite confirmed (Yes)")
                except Exception:
                    pyautogui.hotkey('alt', 'y')
                    logger.info("Overwrite confirmed via Alt+Y fallback")
            except Exception as e:
                logger.info(f"Failed to handle overwrite confirmation: {e}")
        if not win_exists("Save Live Set As") and not win_exists("Save Live Set As:"):
            logger.info("Save Live Set As closed")
            return True
        time.sleep(0.2)

    logger.error("Save Live Set As did not close in time")
    return False
