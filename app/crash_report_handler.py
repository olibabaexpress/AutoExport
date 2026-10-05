"""
Crash Report Handler Module

Provides detection and closing of Ableton crash report dialogs via template matching.

This module uses cv2 template matching to detect crash reports in Ableton Live
and close them by clicking the X button. Requires sample images:
- report_a_crash.png: Full crash report dialog
- crash_report_banner.png: Banner showing X button location

Usage (imported):
    from crash_report_handler import detect_crash_report_via_template, close_crash_report
    
    if detect_crash_report():
        close_crash_report(timeout=30)

Usage (standalone test):
    python crash_report_handler.py
"""

import cv2
import numpy as np
import pyautogui
from pathlib import Path
import os
import time
import logging
import ctypes
import struct

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _get_window_rect_via_win32(window_title="Ableton Live"):
    """
    Get window rectangle using Windows API (more reliable than pywinauto).
    Returns (x, y, width, height) or None if not found.
    """
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, None)
        
        while hwnd:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                if window_title in title.value:
                    rect = ctypes.wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    x = rect.left
                    y = rect.top
                    width = rect.right - rect.left
                    height = rect.bottom - rect.top
                    return (x, y, width, height)
            hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
        return None
    except Exception as e:
        logger.error(f"Error getting window bounds via Win32: {e}")
        return None


def get_ableton_window_bounds():
    """
    Get the bounding box of the Ableton Live window.
    Uses Windows API for reliability.
    
    Returns:
        tuple: (x, y, width, height) of Ableton window, or None if not found
    """
    bounds = _get_window_rect_via_win32("Ableton Live")
    if bounds:
        return bounds
    
    # Fallback to pyautogui
    try:
        windows = pyautogui.getWindowsWithTitle('Ableton Live')
        if windows:
            window = windows[0]
            return (window.left, window.top, window.width, window.height)
    except Exception as e:
        logger.error(f"Error getting Ableton window bounds: {e}")
    
    return None


def get_crash_report_search_region():
    """
    Get the region to search for crash report (right half of Ableton window).
    Optimized for speed - crash reports appear on the right side.
    
    Returns:
        tuple: (x, y, width, height) of search region
    """
    bounds = get_ableton_window_bounds()
    if not bounds:
        logger.warning("Could not get Ableton window bounds")
        return (0, 0, 1920, 1080)
    
    x, y, width, height = bounds
    # Search right half of window
    region_x = x + width // 2
    region_y = y
    region_w = width // 2
    region_h = height
    
    return (region_x, region_y, region_w, region_h)


def detect_crash_report(use_rightmost_third=True):
    """
    Detect if crash report is visible using template matching.
    Much faster than OCR - just looks for the report_a_crash.png image.
    
    Args:
        use_rightmost_third (bool): If True, search rightmost third of Ableton window
        
    Returns:
        bool: True if crash report detected, False otherwise
    """
    try:
        # Get script directory for template images
        try:
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        except NameError:
            script_dir = Path(os.getcwd())
        
        crash_image_path = script_dir / "assets" / "report_a_crash.png"
        
        if not crash_image_path.exists():
            logger.info(f"Crash report image not found ({crash_image_path}). Skipping detection.")
            return False
        
        # Load template
        template = cv2.imread(str(crash_image_path), cv2.IMREAD_GRAYSCALE)
        if template is None:
            logger.error("Could not read crash report template")
            return False
        
        if use_rightmost_third:
            # Only search right half of Ableton window for speed
            region = get_crash_report_search_region()
            x, y, w, h = region
            screenshot = pyautogui.screenshot(region=(x, y, w, h))
        else:
            screenshot = pyautogui.screenshot()
        
        screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        screenshot_gray = cv2.cvtColor(screenshot_cv, cv2.COLOR_BGR2GRAY)
        
        # Match template - use lower threshold for detection (just checking if it exists)
        result = cv2.matchTemplate(screenshot_gray, template, cv2.TM_CCOEFF)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        
        # Detection threshold - raised to 25M to avoid false positives
        # False positives detected at ~15M
        # Real crash reports typically score 35M+
        DETECTION_THRESHOLD = 25000000
        
        if max_val >= DETECTION_THRESHOLD:
            logger.info(f"Crash report detected with confidence {max_val:.0f} (threshold: {DETECTION_THRESHOLD})")
            return True
        else:
            logger.debug(f"No crash report (max_val={max_val:.0f}, threshold={DETECTION_THRESHOLD})")
            return False
        
    except Exception as e:
        logger.info(f"Error in template-based detection: {e}")
        return False


def close_crash_report(timeout=30, debug=False):
    """
    Close crash report by finding and clicking X button via template matching.
    Uses banner sample image to find the exact "Report a Crash" panel location.
    Only searches rightmost third of Ableton window for speed.
    Detection uses template matching instead of OCR for speed (~3x faster).

    Requires sample images:
    - crash_report_banner.png: Banner showing X button + "Report a Crash" text
    - report_a_crash.png: Full crash report image (for detection)
    
    Args:
        timeout (int): Maximum time to attempt closing (seconds)
        debug (bool): Save debug screenshots to crash_report_debug/
        
    Returns:
        bool: True if crash report was closed or not found, False if timeout
    """
    # Handle both notebook (__file__ undefined) and script contexts
    try:
        script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        # Running in Jupyter notebook
        script_dir = Path(os.getcwd())
    
    banner_path = script_dir / "assets" / "crash_report_banner.png"
    
    if not banner_path.exists():
        logger.warning(f"Banner sample not found ({banner_path}). Skipping crash report handling.")
        return True
    
    # First check: Does crash report even exist? (using fast template matching)
    if not detect_crash_report(use_rightmost_third=True):
        logger.info("No crash report detected. Proceeding...")
        return True
    
    logger.info("Crash report detected. Attempting to close...")
    
    start_time = time.time()
    banner_template = cv2.imread(str(banner_path), cv2.IMREAD_GRAYSCALE)
    
    if banner_template is None:
        logger.error("Could not load banner template image")
        return False
    
    banner_h, banner_w = banner_template.shape
    logger.info(f"Banner template size: {banner_w}x{banner_h}")
    
    # Confidence threshold: only click matches with very high confidence
    # Real crash report score: ~37,906,932
    # False positives score: ~13,107,155
    # Use threshold of 20,000,000 to filter out low-confidence matches
    CONFIDENCE_THRESHOLD = 20000000
    
    # Get search region (right half of Ableton window)
    search_region = get_crash_report_search_region()
    region_x, region_y, region_w, region_h = search_region
    
    iteration = 0
    while time.time() - start_time < timeout:
        iteration += 1
        
        # Check if crash report still exists (using fast template matching)
        if not detect_crash_report(use_rightmost_third=True):
            logger.info("Crash report closed successfully")
            return True
        
        try:
            # Take screenshot of ONLY the search region
            screenshot = pyautogui.screenshot(region=search_region)
            screenshot_cv = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
            screenshot_gray = cv2.cvtColor(screenshot_cv, cv2.COLOR_BGR2GRAY)
            
            # Match banner template
            result = cv2.matchTemplate(screenshot_gray, banner_template, cv2.TM_CCOEFF)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            logger.info(f"Match iteration {iteration}: score={max_val:.0f}, location={max_loc}")
            
            # If confidence too low, skip this match (false positive)
            if max_val < CONFIDENCE_THRESHOLD:
                logger.info(f"Low confidence match (threshold: {CONFIDENCE_THRESHOLD}). Skipping...")
                time.sleep(0.3)
                continue
            
            # Found high-confidence banner! X button is in the TOP-LEFT
            # Convert from region-relative coordinates back to screen coordinates
            x_offset = 12  # Pixels from left edge of matched banner
            y_offset = 10  # Pixels from top edge of matched banner
            
            click_x = region_x + max_loc[0] + x_offset
            click_y = region_y + max_loc[1] + y_offset
            
            logger.info(f"Found HIGH-CONFIDENCE banner at ({region_x + max_loc[0]}, {region_y + max_loc[1]}). X button at ({click_x}, {click_y})...")
            
            if debug or iteration == 1:  # Always save debug on first iteration
                result_display = screenshot_cv.copy()
                # Draw banner box (full matched area)
                cv2.rectangle(result_display, max_loc, 
                             (max_loc[0] + banner_w, max_loc[1] + banner_h), 
                             (0, 255, 0), 3)
                # Draw calculated X position
                relative_click_x = x_offset
                relative_click_y = y_offset
                cv2.circle(result_display, (relative_click_x, relative_click_y), 15, (0, 0, 255), 2)
                cv2.circle(result_display, (relative_click_x, relative_click_y), 3, (0, 0, 255), -1)
                
                debug_dir = script_dir / "crash_report_debug"
                debug_dir.mkdir(exist_ok=True)
                debug_file = debug_dir / f"iteration_{iteration:02d}_banner_match.png"
                cv2.imwrite(str(debug_file), result_display)
                logger.info(f"  Debug image: {debug_file}")
            
            # Click the button (use converted screen coordinates)
            logger.info(f"  Clicking at ({click_x}, {click_y})")
            pyautogui.click(click_x, click_y)
            time.sleep(0.5)
            
            # IMMEDIATELY check if crash report closed after click (using fast template matching)
            if not detect_crash_report(use_rightmost_third=True):
                logger.info("Crash report closed successfully on first try!")
                return True
            else:
                logger.info("Crash report still present after click, will retry...")
            
        except Exception as e:
            logger.info(f"Error during crash report closing: {e}")
            time.sleep(0.5)
            continue
    
    logger.error(f"Crash report still present after {timeout}s")
    return False


# Standalone test mode
if __name__ == "__main__":
    print("\n" + "="*70)
    print("CRASH REPORT HANDLER - STANDALONE TEST")
    print("="*70)
    
    # Try to detect and close crash report
    print("\nAttempting to detect crash report...\n")
    
    if detect_crash_report():
        print("\n[OK] Crash report detected!")
        print("\nAttempting to close...\n")
        
        if close_crash_report(timeout=10, debug=True):
            print("\n[OK] Crash report closed successfully!")
        else:
            print("\n[ERROR] Failed to close crash report")
    else:
        print("\n[ERROR] No crash report detected")
        print("(Create a crash in Ableton to test this functionality)")
    
    print("\n" + "="*70 + "\n")
