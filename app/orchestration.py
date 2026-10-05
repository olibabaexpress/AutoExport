import shutil
import time
import os
import re
import subprocess
import sys
from datetime import datetime
import logging
import logging.handlers

# ============= LOGGING SETUP (EARLY) =============
from paths import LOG_DIR, data_file

log_filename = os.path.join(LOG_DIR, f"AutoExport_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# Create logger
logger = logging.getLogger("AutoExport")
logger.setLevel(logging.DEBUG)

# Prevent duplicate handlers if script is imported multiple times
if not logger.handlers:
    # File handler (all messages)
    file_handler = logging.handlers.RotatingFileHandler(
        log_filename, maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)

    # Console handler (info and above)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.stream.reconfigure(encoding='utf-8', errors='replace')  # Handle Unicode gracefully on Windows

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# Prevent propagation to root logger (which might have its own handler)
logger.propagate = False

logger.info("="*70)
logger.info("AutoExport Script Started")
logger.info("="*70)

import argparse
import live
import time
import nltk
import json
import traceback  
import cv2
from pathlib import Path
import ableton_build
import ableton_state
import project_structure
import naming_settings
from ableton_readiness import AbletonReadinessMonitor, wait_for_ableton_ready
from project_and_file_naming import sanitize_filename, get_project_modification_date, build_stems_folder_name
from window_management import launch_ableton_from_menu, maximize_ableton_window, close_ableton_cleanly
from popup_automation import (
    wait_for_recover_or_asio, detect_save_changes_to_popup, handle_save_changes_to,
    dismiss_invalid_popup, dismiss_recover_popup, dismiss_asio_popup, handle_qubiq_popup,
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

# Import crash report handling functions from module
from crash_report_handler import close_crash_report


try:
    import pyautogui
    
    from pywinauto import Application
    from pywinauto import Desktop
    import psutil
except ImportError as e:
    logger.error(f"Missing required library: {e}")
    logger.error("Install with: pip install pyautogui pywinauto psutil")
    sys.exit(1) 

try:
    nltk.data.find('corpora/stopwords')
except LookupError:
    nltk.download('stopwords')

# Configure pyautogui safety features   
pyautogui.FAILSAFE = True  # Move mouse to corner to abort
pyautogui.PAUSE = 0.1

# ============= CONFIGURATION =============
# Which Ableton build to drive (shipping release vs. public beta) is chosen
# in the launcher GUI and persisted by ableton_build. Resolved once at
# import time so a single run never straddles two builds.
ABLETON_BUILD = ableton_build.get_build()
ABLETON_PATH = ABLETON_BUILD["exe"]
ABLETON_WINDOW_TITLE = ABLETON_BUILD["window_title"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_EXPORT_PATH = get_export_path()
STEMS_EXPORT_PATH = os.path.join(BASE_EXPORT_PATH, "STEMS")

# What to do when a step doesn't reach the state the next step needs.
#   stop:    halt the run immediately and leave Ableton exactly as it is so
#            the stuck dialog can be inspected.
#   recover: flag the project in the task file, force-close Ableton, and
#            continue with the next task from a fresh launch.
# Override per run with --on-failure or the AUTOEXPORT_ON_FAILURE env var.
ON_FAILURE_STOP = "stop"
ON_FAILURE_RECOVER = "recover"
DEFAULT_ON_FAILURE = ON_FAILURE_RECOVER
MAX_CONSECUTIVE_FAILURES = 3

# Timeouts are ceilings sized for slow (HDD) machines; every wait returns as
# soon as its condition is met, so fast machines don't pay for them.
STARTUP_TIMEOUT = 180
PROJECT_LOAD_TIMEOUT = 240
POST_LOAD_CLEAN_TIMEOUT = 180
POST_STEP_CLEAN_TIMEOUT = 60
PREFLIGHT_TIMEOUT = 30



## receiving task list from JSON file
def load_task_list_from_file(task_file):
    """Load task list from JSON file"""
    try:
        with open(task_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Could not load task list: {e}")
        return None

def _update_task(task_file, project_path, update):
    """Apply `update(task)` to the matching task in the JSON file."""
    try:
        tasks = load_task_list_from_file(task_file)
        if tasks:
            for task in tasks:
                if task.get("project_path", "").strip() == project_path.strip():
                    update(task)
                    break

            with open(task_file, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, indent=2)
            return True
    except Exception as e:
        logger.warning(f"Could not update task file: {e}")
    return False


def mark_task_done(task_file, project_path):
    """Mark a task as done in the JSON file (clearing any earlier failure)."""
    def update(task):
        task["done"] = True
        task.pop("failed", None)
        task.pop("failure", None)

    if _update_task(task_file, project_path, update):
        logger.info(f"[QUEUE] Marked project as done in JSON: {os.path.basename(project_path)}")
        return True
    return False


def mark_task_failed(task_file, project_path, step, detail, screenshot=None):
    """Flag a task as failed. It stays not-done, so a rerun retries it."""
    def update(task):
        task["failed"] = True
        task["failure"] = {
            "step": step,
            "detail": detail,
            "at": datetime.now().isoformat(timespec="seconds"),
            "screenshot": screenshot,
            "log": log_filename,
        }

    if _update_task(task_file, project_path, update):
        logger.info(f"[QUEUE] Flagged project as failed in JSON: {os.path.basename(project_path)}")
        return True
    return False

def check_pause_flag():
    """Check if pause has been requested via PAUSE_FLAG.txt"""
    pause_file = data_file("PAUSE_FLAG.txt")
    try:
        return os.path.exists(pause_file)
    except Exception:
        return False

def wait_for_resume():
    """Wait in a loop until pause flag is removed (resume is requested)"""
    logger.info(">>> PAUSED - Waiting for resume signal...")
    check_count = 0
    while check_pause_flag():
        time.sleep(0.5)  # Check every 500ms
        check_count += 1
        if check_count % 120 == 0:  # Every 60 seconds
            logger.info(f">>> Still paused... (checked {check_count} times)")
    logger.info(">>> RESUMED - Continuing with next task...")

## system monitoring functions
def get_cpu_load():
    """Get current CPU load percentage.

    A short sampling window is plenty here - the reading only gates a
    coarse "is the machine thrashing?" check, so a full second of blocking
    per project is wasted time.
    """
    return psutil.cpu_percent(interval=0.2)

def check_free_space(drive):
    """Check free space on drive in MB"""
    try:
        usage = psutil.disk_usage(drive)
        return usage.free // (1024 * 1024)
    except:
        return 0


# helper functions for Ableton launching and window management
def launch_ableton(project_path):
    """Launch Ableton Live with the specified project."""
    try:
        cmd = f'"{ABLETON_PATH}" "{project_path}"'
        subprocess.Popen(cmd, shell=True)
        logger.info(f"Launched Ableton Live with project: {project_path}")
        return True
    except Exception as e:
        logger.info(f"Error launching Ableton: {e}")
        return False


# file and folder naming functions
def get_ableton_version():
    """Detect the Ableton Live version string used for file naming.

    Returns '12.3' for the shipping build or '12.4 BETA' for the beta,
    matching whichever build this run is driving. See ableton_build for
    the release-channel filtering.
    """
    return ableton_build.detect_version(ABLETON_BUILD["key"])


# folder and file management functions
def delete_empty_folders(root_path):
    """Recursively delete all empty folders in the given path."""
    deleted_count = 0
    
    # Walk bottom-up so we delete deepest folders first
    for dirpath, dirnames, filenames in os.walk(root_path, topdown=False):
        # Skip the root folder itself
        if dirpath == root_path:
            continue
            
        try:
            # Check if folder is empty (no files and no subdirectories)
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                logger.info(f"Deleted empty folder: {dirpath}")
                deleted_count += 1
        except Exception as e:
            logger.info(f"Could not delete folder {dirpath}: {e}")
    
    if deleted_count > 0:
        logger.info(f"Deleted {deleted_count} empty folder(s)")
    else:
        logger.info("No empty folders found")
    
    return deleted_count

def create_export_folders(tasks):
    
    
    

    """Create export folders only for projects that need stem export."""
    project_groups = {}
    naming = naming_settings.load_settings()
    logger.info("Mapping export folders for stem tasks")
    logger.info(f"Project groups initialized: {'Empty' if not project_groups else f'Contains {len(project_groups)} items'}")

    # Filter to only tasks with export_stems enabled
    stem_tasks = [t for t in tasks if t.get("export_stems", False)]
    logger.info(f"Found {len(stem_tasks)} tasks requiring stem export")

    for task in stem_tasks:
        project_path = task["project_path"].strip()

        if not project_path:
            logger.info("Skipping empty project path")
            continue

        logger.info(f"Processing project: {project_path}")

        # Extract group folder and project name
        project_dir = os.path.dirname(project_path)
        project_base_name = os.path.splitext(os.path.basename(project_path))[0]

        if not project_dir:
            logger.info(f"Project directory is empty, skipping project: {project_path}")
            continue

        group_folder = os.path.basename(project_dir)
        # Ableton's own project folder is named "<name> Project" - drop that
        # suffix so our stems folders are just "<name>".
        group_folder = re.sub(r'\s+Project$', '', group_folder, flags=re.IGNORECASE)
        group_folder = sanitize_filename(group_folder)
        logger.info(f"Detected group folder: {group_folder}")

        # Create export folder for the group
        group_export_path = os.path.join(STEMS_EXPORT_PATH, group_folder)
        if not os.path.exists(group_export_path):
            os.makedirs(group_export_path)
            logger.info(f"Created group export folder: {group_export_path}")

        # Get project modification date
        project_date = get_project_modification_date(project_path)
        logger.info(f"Project modification date: {project_date}")

        # Create export folder for individual project with date subfolder.
        # The BPM segment (if enabled) can't be added here - tempo isn't
        # known until Ableton has the project open - so export_stems()
        # renames this folder in place once the tempo query succeeds.
        sanitized_project_name = sanitize_filename(project_base_name)
        folder_name = build_stems_folder_name(
            sanitized_project_name, tempo=None, project_date=project_date,
            include_bpm=naming["stems_folder_include_bpm"],
            include_date=naming["stems_folder_include_date"],
        )
        project_export_path = os.path.join(group_export_path, folder_name)
        logger.info(f"Created Project Export Path: {project_export_path}")
        logger.info(f"Sanitized project name: {sanitized_project_name}")
        logger.info(f"Group Export Path: {group_export_path}")

        if not os.path.exists(project_export_path):
            logger.info(f"Attempting to create directory: {project_export_path}")
            os.makedirs(project_export_path)
            logger.info(f"Created project export folder: {project_export_path}")

        logger.info(f"Project export path set to: {project_export_path}")

        # Store export path in dictionary
        project_groups[project_path] = project_export_path

    return project_groups


#core loops

class ProjectStepError(Exception):
    """A workflow step did not reach the state the next step depends on."""

    def __init__(self, step, detail):
        super().__init__(f"[{step}] {detail}")
        self.step = step
        self.detail = detail


class RunAborted(Exception):
    """The whole run must stop (stop policy, or too many failures in a row)."""


def dismiss_known_popups(_state=None):
    """Try every popup handler we know how to answer. True if one was handled.

    Used as the `on_blocked` callback while waiting for a clean state, so any
    popup we recognise is answered no matter which step it appears after. A
    popup none of these recognise (e.g. the recursive-project warning) is
    left open, the wait times out, and the project is flagged.
    """
    if detect_save_changes_to_popup(threshold=0.8):
        logger.info("Save changes popup detected - answering")
        handle_save_changes_to()
        return True
    if dismiss_recover_popup():
        return True
    if dismiss_asio_popup():
        return True
    if dismiss_invalid_popup():
        logger.info("Invalid audio file popup dismissed, proceeding with project")
        return True
    if win_exists("Qubiq Engine"):
        handle_qubiq_popup()
        return True
    return False


def ensure_clean(label, timeout=POST_STEP_CLEAN_TIMEOUT, settle=2.0):
    """Raise ProjectStepError unless Ableton settles on its main window."""
    if not ableton_state.wait_for_clean_state(
        timeout=timeout, settle=settle, on_blocked=dismiss_known_popups, label=label
    ):
        raise ProjectStepError(label, f"Ableton did not return to a clean state within {timeout}s")


def _title_shows_project(title, project_name):
    if not title.startswith(project_name):
        return False
    # Guard against "Song" matching "Song 2 - Ableton Live ..."; allow the
    # unsaved-changes '*' and Live's " [Project Folder]" suffix.
    rest = title[len(project_name):].lstrip("*")
    return rest == "" or rest.startswith(" - ") or rest.startswith(" [")


def wait_for_project_open(project_path, timeout):
    """Wait for the Live main window title to show `project_path`'s set.

    Answers known popups (save changes, invalid audio, ASIO, ...) while
    waiting. Reads the title through ableton_state (GetWindowTextW), which
    stays readable while Ableton is busy loading.
    """
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    logger.info(f">>> Waiting up to {timeout}s for '{project_name}' to open...")
    start = time.time()
    last_title = None
    while time.time() - start < timeout:
        state = ableton_state.get_state()
        if not state["running"]:
            raise ProjectStepError("project_open", "Ableton exited while opening the project")
        main = state["main"]
        title = main.title if main else ""
        if title != last_title:
            logger.debug(f"Main window title: {title!r}")
            last_title = title
        if _title_shows_project(title, project_name):
            logger.info(f">>> '{project_name}' open after {time.time() - start:.1f}s")
            return
        if state["blockers"]:
            dismiss_known_popups(state)
        time.sleep(0.5)
    raise ProjectStepError(
        "project_open",
        f"'{project_name}' did not appear in the Ableton title within {timeout}s "
        f"(title was {last_title!r})",
    )


def wait_for_main_window_responsive(timeout):
    start = time.time()
    hung_logged = False
    while time.time() - start < timeout:
        main = ableton_state.main_window()
        if main is not None and not main.hung:
            if hung_logged:
                logger.info(f"Ableton responding again after {time.time() - start:.1f}s")
            return
        if main is not None and not hung_logged:
            logger.info("Ableton is not responding (still loading?) - waiting...")
            hung_logged = True
        time.sleep(1)
    raise ProjectStepError("responsive", f"Ableton did not respond within {timeout}s")


def process_project(project_path, export_path, project_date, task, ableton_version, fresh_launch=False, abletoolz_result=None):
    """Run one task. Raises ProjectStepError when a step fails.

    `fresh_launch` means Ableton isn't running and is started with this
    project on the command line; otherwise the project is opened via Ctrl+O
    in the already-running (and verified clean) instance.
    """
    recovery_popup_occurred = False

    if fresh_launch:
        if not launch_ableton(project_path):
            raise ProjectStepError("launch", "Could not start the Ableton process")

        state = wait_for_recover_or_asio(timeout=STARTUP_TIMEOUT, poll=1.0)
        if state == "recover":
            recovery_popup_occurred = True
        elif state == "asio":
            logger.info("ASIO popup was handled, continuing...")
        elif state == "live":
            logger.info("Ableton main window detected, continuing...")
        else:
            raise ProjectStepError("startup", f"Ableton did not start within {STARTUP_TIMEOUT}s")
    else:
        if not launch_ableton_from_menu(project_path):
            raise ProjectStepError("open_dialog", "Could not open the project through the Open Document dialog")

    wait_for_project_open(project_path, timeout=PROJECT_LOAD_TIMEOUT)
    # The previous set (if any) is closed now, so its wet+dry changes are gone.
    ableton_state.clear_unsaveable_changes()

    logger.info(">>> Waiting for Ableton to respond...")
    wait_for_main_window_responsive(timeout=PROJECT_LOAD_TIMEOUT)

    # Memory-stabilisation heuristic; a timeout here is not proof of a
    # problem, so it only warns - the clean-state check below is the gate.
    success, elapsed = wait_for_ableton_ready(timeout=120, verbose=True)
    if success:
        logger.info(f"[OK] Ableton is ready ({elapsed:.1f}s after window detection)")
    else:
        logger.warning(f"Ableton memory did not stabilise within {elapsed:.1f}s - continuing to clean-state check")

    # Any popup left after loading that we can't answer (missing plugin
    # notices are answered; the recursive-project warning is not yet) fails
    # the project here instead of derailing the export steps.
    ensure_clean("after project load", timeout=POST_LOAD_CLEAN_TIMEOUT)

    cpu_load = get_cpu_load()
    if cpu_load > 80:
        logger.info(f"System is buffering, CPU load is high: {cpu_load}%")
        time.sleep(5)
    else:
        logger.info(f"System is not buffering, CPU load is: {cpu_load}%")

    win_activate_ableton()
    time.sleep(0.2)
    logger.info("Ableton window is active and ready")

    maximize_ableton_window()

    # Wait for the OSC daemon to actually become responsive rather than
    # sleeping for a fixed, deliberately-pessimistic period. We poll a cheap
    # tempo query (the same probe pylive itself uses to decide a set is
    # running) and continue the moment it answers. The old fixed waits are
    # kept as timeout ceilings so a slow-loading project degrades to exactly
    # the previous behaviour instead of failing early.
    osc_timeout = 11 if fresh_launch else 7
    logger.info(f">>> Waiting for Ableton OSC daemon (up to {osc_timeout}s)...")

    try:
        set = live.Set(scan=False)
        logger.info(">>> Set object created successfully")
    except Exception as e:
        raise ProjectStepError("osc", f"Failed to create live.Set(): {e}")

    # Per-attempt timeout: AbletonOSC reliably answers this same query (it's
    # what pylive's own set.tempo / get_tempo() use) but the round trip can
    # take noticeably longer than the 0.4s this loop used to allow, which made
    # every attempt "time out" even though the daemon was never actually
    # unresponsive. 1.5s matches real-world round trip time while still
    # leaving room for several attempts inside the overall osc_timeout budget
    osc_query_timeout = 1.5
    osc_deadline = time.time() + osc_timeout
    osc_started = time.time()
    osc_ready = False
    while time.time() < osc_deadline:
        try:
            set.live.query("/live/song/get/tempo", timeout=osc_query_timeout)
            osc_ready = True
            break
        except Exception:
            time.sleep(0.1)

    if osc_ready:
        logger.info(f">>> OSC daemon responded after {time.time() - osc_started:.1f}s")
    else:
        logger.warning(
            f">>> OSC daemon did not respond within {osc_timeout}s - continuing anyway "
            "(the scan below may still succeed)."
        )

    try:
        set.scan(mode="auto")
        logger.info(">>> Network scan complete")
    except Exception as e:
        logger.warning(f">>> Network scan failed: {e}")

    # Check for missing media. Abletoolz already resolved whatever it could
    # during the pre-pass in main() before Ableton was launched (see
    # missing_media_resolver.resolve_missing_media_bulk). There is no more
    # in-app OCR-based fallback - anything abletoolz couldn't fix (e.g.
    # samples missing from every scanned drive) needs manual relinking.
    logger.info(">>> Checking for missing media files...")
    missing_media_was_handled = False
    if abletoolz_result:
        if abletoolz_result.get('success') and abletoolz_result.get('missing_after', 0) == 0:
            logger.info("Abletoolz already resolved all missing media before launch.")
        else:
            still_missing = abletoolz_result.get('missing_after', '?')
            logger.warning(
                f"Abletoolz could not resolve all missing media for this project "
                f"({still_missing} sample(s) still missing) - these will need manual relinking."
            )
    else:
        logger.info("No abletoolz pre-pass result available for this project.")

    time.sleep(0.2)

    export_on = task.get("export_main", False) or task.get("export_stems", False)

    if export_on:
        # Arrangement view
        pyautogui.hotkey('alt', '2')
        time.sleep(0.5)
        logger.info("Moved focus to the arrangement view")

        if recovery_popup_occurred:
            logger.info("Attempting to close crash report (recovery popup was present)...")
            close_crash_report(timeout=10, debug=True)
            time.sleep(0.3)
        else:
            logger.info("Skipping crash report check (no recovery popup occurred)")

    should_collect_and_save = task.get("collect_all_and_save", False) or missing_media_was_handled

    if should_collect_and_save:
        if not collect_all_and_save(ableton_version):
            raise ProjectStepError("collect_all_and_save", "Collect All and Save did not complete")
        time.sleep(0.3)
        logger.info("Collect All and Save operation completed")

    include_return_fx = task.get("include_return_and_main_effects", True)
    normalize = task.get("normalize", False)
    wet_dry = task.get("wet_dry", False)
    render_ranges = project_render_ranges(project_path) if export_on else None

    if task.get("export_main", False):
        wet_suffix = None
        if wet_dry:
            ok, wet_suffix = prepare_main_wet_dry(set)
            if not ok:
                raise ProjectStepError(
                    "wet_dry_main",
                    "Could not read or switch the main track devices - is the patched "
                    "AbletonOSC (with /live/master_track handlers) installed and Live restarted?",
                )
        if not export_main(project_date, ableton_version, set,
                    include_return_and_main_effects=include_return_fx,
                    normalize=normalize, project_path=project_path,
                    render_ranges=render_ranges, name_suffix=wet_suffix or ""):
            raise ProjectStepError("export_main", "Main mix export failed")
        ensure_clean("after export main")
        if wet_suffix and _switch_to_dry("main", disable_main_effects, set):
            if not export_main(project_date, ableton_version, set,
                        include_return_and_main_effects=include_return_fx,
                        normalize=normalize, project_path=project_path,
                        render_ranges=render_ranges):
                raise ProjectStepError("export_main_dry", "Dry main mix export failed")
            ensure_clean("after dry export main")
        logger.info("Export Main operation completed")

    if task.get("export_stems", False):
        stems_path = export_stems(export_path, project_path, project_date, ableton_version, set,
                     include_return_and_main_effects=include_return_fx,
                     normalize=normalize, render_ranges=render_ranges,
                     pass_folder=WET_FOLDER if wet_dry else None)
        if not stems_path:
            raise ProjectStepError("export_stems", "Stem export failed")
        ensure_clean("after export stems")
        if wet_dry and _switch_to_dry("stems", disable_audio_effects, set):
            if not export_stems(stems_path, project_path, project_date, ableton_version, set,
                         include_return_and_main_effects=include_return_fx,
                         normalize=normalize, render_ranges=render_ranges,
                         pass_folder=DRY_FOLDER, name_suffix=DRY_SUFFIX):
                raise ProjectStepError("export_stems_dry", "Dry stem export failed")
            ensure_clean("after dry export stems")
        logger.info("Export Stems operation completed")

    ensure_clean("end of project")


def _switch_to_dry(kind, disable_effects, set):
    """Between the wet and dry passes: make sure the export window is closed,
    then switch the effects off and wait until Live reports them off.

    From here on the set must never be saved. Returns False when there was
    nothing to switch off (the dry render would equal the wet one), so the
    dry pass is skipped.
    """
    if not close_export_window_if_open():
        raise ProjectStepError(f"wet_dry_{kind}", "Export window would not close after the wet export")
    ensure_clean(f"before disabling {kind} effects")
    ableton_state.mark_unsaveable_changes()
    count = disable_effects(set)
    if count is None:
        raise ProjectStepError(
            f"wet_dry_{kind}",
            f"Could not switch off the {kind} effects (or Live did not report them as off)",
        )
    if count == 0:
        logger.info(f"Wet+Dry: no {kind} effects were on - dry export would match the wet one, skipping it")
        return False
    logger.info(f"Wet+Dry: switched off {count} {kind} effect(s) - exporting dry (set will NOT be saved)")
    ensure_clean(f"after disabling {kind} effects")
    return True


def _handle_failure(on_failure, label, consecutive_failures):
    """Apply the failure policy after evidence has been captured.

    stop: raise RunAborted and leave Ableton exactly as it is.
    recover: kill Ableton so the next task starts from a fresh launch.
    """
    if on_failure == ON_FAILURE_STOP:
        raise RunAborted(
            f"{label} - stopping (on-failure=stop). Ableton has been left as-is for debugging."
        )
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        raise RunAborted(
            f"{consecutive_failures} failures in a row (last: {label}) - something is "
            "wrong beyond a single project, stopping."
        )
    logger.warning(f"{label} - recovering: force-closing Ableton, next task starts from a fresh launch")
    if not ableton_state.kill_ableton():
        raise RunAborted("Could not kill Ableton during recovery")


def main_processing_loop(tasks, project_groups, ableton_version, task_file, on_failure, abletoolz_results=None):
    """Iterate tasks and run the workflow. Returns the list of failed tasks."""
    abletoolz_results = abletoolz_results or {}
    failures = []
    consecutive_failures = 0

    for idx, task in enumerate(tasks, 1):
        project_path = task.get("project_path", "").strip()
        if not project_path:
            continue

        if task.get("done", False):
            logger.info(f"\n[{idx}/{len(tasks)}] SKIPPING (already done): {os.path.basename(project_path)}")
            continue

        # Recursive projects (a project folder inside another) trigger a Live
        # warning modal on Collect All and Save. Never open them; the GUI
        # offers a fix on the flagged queue entry.
        nesting = project_structure.find_recursive_nesting(project_path)
        if nesting:
            inner, outer = nesting
            detail = (f"Project folder '{inner}' is inside another Ableton project "
                      f"'{outer}'. Move it out (double-click the queue entry) and rerun.")
            logger.error(f"[SKIPPED] Recursive project: {detail}")
            mark_task_failed(task_file, project_path, "recursive_project", detail)
            failures.append({"project_path": project_path, "step": "recursive_project", "detail": detail})
            continue

        logger.info(f"\n[{idx}/{len(tasks)}] Processing project: {project_path}")
        logger.info(f"  Collect All and Save: {task.get('collect_all_and_save', False)}")
        logger.info(f"  Export Stems: {task.get('export_stems', False)}")
        logger.info(f"  Export Main: {task.get('export_main', False)}")
        logger.info(f"  Include Return and Main Effects: {task.get('include_return_and_main_effects', True)}")
        logger.info(f"  Normalize: {task.get('normalize', False)}")
        logger.info(f"  Wet+Dry: {task.get('wet_dry', False)}")

        export_path = None
        if task.get("export_stems", False):
            if project_path not in project_groups:
                logger.error(f"Project path not found in projectGroups, skipping: {project_path}")
                continue
            export_path = project_groups[project_path]

        # Preflight: the previous task must have left Ableton idle on its main
        # window (Ctrl+O does nothing while any dialog is open). Not this
        # project's fault if it isn't, so it isn't flagged.
        fresh_launch = not ableton_state.is_running()
        if not fresh_launch and not ableton_state.wait_for_clean_state(
            timeout=PREFLIGHT_TIMEOUT, settle=1.0, on_blocked=dismiss_known_popups,
            label="preflight before opening next project",
        ):
            ableton_state.capture_failure_snapshot(f"preflight_{idx}")
            consecutive_failures += 1
            _handle_failure(on_failure, "Ableton not in a clean state before opening the next project",
                            consecutive_failures)
            fresh_launch = True
        if fresh_launch:
            logger.info("Ableton is not running - this project will start a fresh instance")

        project_date = get_project_modification_date(project_path)

        try:
            process_project(project_path, export_path, project_date, task, ableton_version,
                            fresh_launch=fresh_launch,
                            abletoolz_result=abletoolz_results.get(project_path))
        except pyautogui.FailSafeException:
            raise
        except Exception as e:
            if isinstance(e, ProjectStepError):
                step, detail = e.step, e.detail
            else:
                step, detail = "unexpected_error", f"{type(e).__name__}: {e}"
                logger.error(traceback.format_exc())
            name = os.path.basename(project_path)
            logger.error(f"[FAILED] {name} at step '{step}': {detail}")
            screenshot = ableton_state.capture_failure_snapshot(f"{idx}_{step}_{name}")
            mark_task_failed(task_file, project_path, step, detail, screenshot)
            failures.append({"project_path": project_path, "step": step, "detail": detail})
            consecutive_failures += 1
            _handle_failure(on_failure, f"{name} failed at '{step}'", consecutive_failures)
            continue

        consecutive_failures = 0
        mark_task_done(task_file, project_path)

    return failures


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="AutoExport Ableton automation")
    parser.add_argument(
        "task_file", nargs="?", default=data_file("EXPORT_TASKS.json"),
        help="Task list JSON (default: data/EXPORT_TASKS.json)",
    )
    default_policy = os.environ.get("AUTOEXPORT_ON_FAILURE", DEFAULT_ON_FAILURE).strip().lower()
    if default_policy not in (ON_FAILURE_STOP, ON_FAILURE_RECOVER):
        default_policy = DEFAULT_ON_FAILURE
    parser.add_argument(
        "--on-failure", choices=(ON_FAILURE_STOP, ON_FAILURE_RECOVER), default=default_policy,
        help="stop: halt and leave Ableton as-is for debugging; recover: flag the "
             "project, force-close Ableton and continue with the next task from a "
             "fresh launch (default: AUTOEXPORT_ON_FAILURE env var, else "
             f"'{DEFAULT_ON_FAILURE}')",
    )
    return parser.parse_args(argv)


def main():
    """Main script execution. Returns the process exit code."""
    # Force line-buffered stdout so logs flush immediately
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    args = _parse_args()

    logger.info("="*50)
    logger.info("AutoExport Python Script Started")
    logger.info(f"Failure policy: {args.on_failure}")
    logger.info(f"Ableton: {ABLETON_PATH or 'NOT CONFIGURED'} (window title '{ABLETON_WINDOW_TITLE}')")
    logger.info("="*50)

    if not ABLETON_PATH:
        logger.error(
            "No Ableton Live 12 executable is configured. Set it in the GUI "
            "(File > Set Ableton Live Exe...) and try again."
        )
        return 2

    if not BASE_EXPORT_PATH:
        logger.error("No export folder is set. Set it in the GUI (File > Set Export Path...) and try again.")
        return 2

    # Write state file to signal GUI that script is running
    state_file = data_file("SCRIPT_STATE.json")
    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump({"status": "running", "started_at": datetime.now().isoformat()}, f)
    except Exception as e:
        logger.warning(f"Could not write state file: {e}")

    try:
        return _run(args.task_file, args.on_failure)
    finally:
        # Always clear the "running" marker, or the GUI thinks a crashed or
        # stopped run is still going.
        try:
            if os.path.exists(state_file):
                os.remove(state_file)
        except Exception as e:
            logger.warning(f"Could not remove state file: {e}")


def _run(task_file, on_failure):
    logger.info(f"Loading task list from: {task_file}")
    tasks = load_task_list_from_file(task_file)
    if not tasks:
        logger.info("No tasks loaded, exiting")
        return 0

    for i, t in enumerate(tasks, 1):
        logger.debug(
            f"Task {i}: {t.get('project_path')} "
            f"collect={t.get('collect_all_and_save')} "
            f"stems={t.get('export_stems')} "
            f"main={t.get('export_main')}"
        )

    ableton_version = get_ableton_version()
    naming = naming_settings.load_settings()
    append_version = naming["include_ableton_version"]
    logger.info(f"DEBUG: append version setting: {append_version}")

    if append_version:
        rename_suffix = f" (AL{ableton_version})"
        logger.info("Ableton version will be appended to filenames")
        logger.info(f"Using rename suffix: {rename_suffix}")
    else:
        logger.info("Ableton version will NOT be appended to filenames, will overwrite original to current ableton version")

    # The pipeline launches Ableton fresh for the first project, so a
    # leftover instance from a manual session or a crashed run is closed
    # first. If it won't close cleanly it is killed - there is no automation
    # state to preserve for debugging at this point.
    if ableton_state.is_running():
        logger.warning("Ableton is already open - closing it before starting the export run")
        if not close_ableton_cleanly(ableton_version) or ableton_state.is_running():
            logger.warning("Ableton did not close cleanly - force-closing it")
            if not ableton_state.kill_ableton():
                logger.error("Could not close Ableton before starting - aborting")
                return 2
    else:
        logger.info("Ableton is not currently running - starting fresh")

    project_groups = create_export_folders(tasks)

    # Pre-pass: resolve missing media via abletoolz + local sample database
    # directly on the .als files, before Ableton is launched for any project.
    # Projects abletoolz can't fully resolve are just reported as still
    # missing during process_project (no more OCR-based fallback).
    abletoolz_results = {}
    try:
        sample_db = load_sample_db()
        if sample_db is None:
            logger.warning(
                "No sample database found - skipping abletoolz pre-pass. "
                "Build one from the launcher GUI's 'Sample Index' button to enable it."
            )
        else:
            pending_paths = [
                task["project_path"].strip()
                for task in tasks
                if task.get("project_path", "").strip() and not task.get("done", False)
            ]
            abletoolz_results = resolve_missing_media_bulk(pending_paths, sample_db, progress_callback=logger.info)
    except Exception as e:
        logger.warning(f"Abletoolz pre-pass failed, continuing without missing-media resolution: {e}")

    try:
        failures = main_processing_loop(tasks, project_groups, ableton_version, task_file,
                                        on_failure, abletoolz_results=abletoolz_results)
    except RunAborted as e:
        logger.error("="*50)
        logger.error(f"RUN STOPPED: {e}")
        logger.error(f"Failure screenshots: {ableton_state.FAILURE_SNAPSHOT_DIR}")
        logger.error(f"Full log: {log_filename}")
        logger.error("="*50)
        return 2

    delete_empty_folders(STEMS_EXPORT_PATH)

    logger.info("\n" + "="*50)
    logger.info("Processed Projects:\n" + "\n".join(t["project_path"] for t in tasks))
    if failures:
        logger.warning(f"{len(failures)} project(s) FAILED and were flagged in {task_file}:")
        for f in failures:
            logger.warning(f"  - {f['project_path']} (step '{f['step']}': {f['detail']})")
    logger.info("="*50)

    if ableton_state.is_running():
        if close_ableton_cleanly(ableton_version):
            logger.info("Ableton closed successfully after processing all projects")
        else:
            logger.warning("Ableton may still be open after processing all projects")

    logger.info("\nScript finished" + (" with failures." if failures else " successfully!"))
    return 1 if failures else 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except KeyboardInterrupt:
        logger.info("\nScript interrupted by user (Ctrl+C)")
    except pyautogui.FailSafeException:
        logger.error("\nScript aborted by pyautogui fail-safe (mouse moved to a screen corner)")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        logger.error(traceback.format_exc())
    sys.exit(exit_code)
