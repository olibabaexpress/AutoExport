
import os
import re
import time
import difflib
import logging
from datetime import datetime
from venv import logger

import numpy as np
import pyautogui
import pytesseract

from popup_automation import _untitled_live_windows, fuzzy_phrase_in_text
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

from PIL import ImageChops
from pywinauto import Application, Desktop

import naming_settings
import ableton_state
from window_utils import (
    win_activate_ableton,
    win_exists,
    _list_windows_by_class_win32,
    _find_all_window_elements,
    win_activate,
    ensure_caps_lock_off,
)
from project_and_file_naming import append_version_to_filename

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

def _should_ocr(kind):
    now = time.time()
    last_t, last_res = _ocr_cache[kind]
    if now - last_t < OCR_COOLDOWN:
        return False, last_res
    return True, None

def _store_ocr(kind, result):
    _ocr_cache[kind] = (time.time(), result)
    return result

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
                    f" OCR text: {repr(text[:200])}"
                )
                return _store_ocr(
                    "save_changes_to_popup",
                    True
                )

        except Exception as e:
            print(f"Exception in window loop: {e}")
            import traceback
            traceback.print_exc()
            continue

    print(
        f"No save-changes popup matched "
        f"({windows_found} untitled windows checked)"
    )

    return _store_ocr("save_changes_to_popup", False)

win_activate_ableton()
detect_save_changes_to_popup()