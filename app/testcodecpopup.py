



import pyautogui
import re
import pytesseract
import time

from popup_automation import _should_ocr, _untitled_live_windows, _store_ocr




trigger_parts = (
    "could not be decoded",
    "mediafoundation",
    "error code",
)


screenshot = "c:\\Users\\olito\\OneDrive\\Desktop\\20261013_180828_40_project_open_calypso_als.png"
text = pytesseract.image_to_string(screenshot)
normalized_text = re.sub(r"\s+", " ", text.lower()).strip()

# Remove quoted filenames (they vary)
normalized_text = re.sub(r"['\"]", " ", normalized_text)

# Remove file type words
normalized_text = re.sub(r'\b(mov|mp4|m4v|avi|mkv|webm)\b', ' ', normalized_text, flags=re.IGNORECASE)
pass  # channel-strip removed: it erased the trigger word

normalized_text = re.sub(r"\s+", " ", normalized_text).strip()

if all(part in normalized_text for part in trigger_parts):
    print(f"Detected codec popup! Text: {normalized_text[:200]}")
    
else:
    print(f"No codec popup detected. Text: {normalized_text[:200]}")


