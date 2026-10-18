from popup_automation import _should_ocr, _store_ocr, _untitled_live_windows, detect_save_changes_to_popup
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
from window_utils import win_activate_ableton, win_exists, _list_windows_by_class_win32
from project_and_file_naming import append_version_to_filename

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
        print(f"Save popup detection on cooldown, returning cached: {cached}")
        return cached

    print("Running fresh OCR for save popup detection...")

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
                print(
                    f"Skipping window with aspect ratio "
                    f"{aspect_ratio:.2f} "
                    f"(expected 2.4-2.8 for save)"
                )
                continue

            windows_checked += 1

            print(
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

            print(
                f"[SAVE_CHECK] OCR text raw: "
                f"{repr(text[:200])}"
            )

            normalized_text = re.sub(
                r"\s+",
                " ",
                text.lower()
            ).strip()

            print(
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

            print(
                f"[SAVE_CHECK] Best phrase similarity: "
                f"{similarity:.2f} vs threshold {threshold:.2f}"
            )

            if matched:
                print(
                    "Detected save popup (Collect All & Save). "
                    f"Similarity: {similarity:.2f}"
                )
                return _store_ocr("save_popup", True)

        except Exception as e:
            print(
                f"Error checking window for save popup: {e}"
            )
            continue

    print(
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

            print(
                f"[SAVE_CHANGES_CHECK] OCR text normalized: "
                f"{repr(normalized_text[:200])}"
            )

            matched, similarity = fuzzy_phrase_in_text(
                normalized_text,
                trigger_phrase,
                threshold
            )

            print(
                f"[SAVE_CHANGES_CHECK] Best phrase similarity: "
                f"{similarity:.2f} vs threshold {threshold:.2f}"
            )

            if matched:
                print(
                    f"Detected save-changes popup! "
                    f"Similarity: {similarity:.2f}"
                )
                return _store_ocr(
                    "save_changes_to_popup",
                    True
                )

        except Exception as e:
            print(f"DEBUG: Exception in window loop: {e}")
            import traceback
            traceback.print_exc()
            continue

    print(
        f"No save-changes popup matched "
        f"({windows_found} untitled windows checked)"
    )

    return _store_ocr("save_changes_to_popup", False)


win_activate_ableton()
detect_save_popup(threshold=0.75)
detect_save_changes_to_popup(threshold=0.8)