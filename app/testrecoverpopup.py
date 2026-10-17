import argparse
import live
import time
import json
import traceback  
import cv2
from pathlib import Path
import ableton_build
import ableton_state
import system_deps
import project_structure
import naming_settings
from ableton_readiness import AbletonReadinessMonitor, wait_for_ableton_ready
from project_and_file_naming import sanitize_filename, get_project_modification_date, build_stems_folder_name
from window_management import launch_ableton_from_menu, maximize_ableton_window, close_ableton_cleanly
from popup_automation import (
    detect_recover_popup, wait_for_recover_or_asio, detect_save_changes_to_popup, handle_save_changes_to,
    dismiss_invalid_popup, dismiss_recover_popup, dismiss_asio_popup, handle_qubiq_popup,
    dismiss_codec_popup, dismiss_kontakt_popup, dismiss_plugin_messagebox_popups,
)
from export_automation import (
    collect_all_and_save, export_stems, export_main,
    prepare_main_wet_dry, close_export_window_if_open,
    WET_FOLDER, DRY_FOLDER, DRY_SUFFIX,
)
from pylive_commands import disable_audio_effects, disable_main_effects
from find_clusters import project_render_ranges
from window_utils import wait_for_window, find_window, find_window_in_children, find_window_by_title_win32, win_exists, win_activate_ableton, win_activate, win_close, win_exists_by_class, win_close_by_class, _list_windows_by_class_win32
from export_path_config import get_export_path
# abletoolz-based missing media resolution (runs before Ableton is even launched)
from sample_database import load_index as load_sample_db
from missing_media_resolver import resolve_missing_media_bulk




try:
    import pyautogui
    
    from pywinauto import Application
    from pywinauto import Desktop
    import psutil
except ImportError as e:
    print(f"Missing required library: {e}")
    print("Install with: pip install pyautogui pywinauto psutil")
    

if not system_deps.configure_tesseract():
    print("Tesseract OCR not found - export dialog and popup detection will fail")

# Configure pyautogui safety features   
pyautogui.FAILSAFE = True  # Move mouse to corner to abort
pyautogui.PAUSE = 0.1

time.sleep(1)  # Allow time for any previous operations to settle
dismiss_recover_popup()  # Ensure no recover popup is blocking automation at start