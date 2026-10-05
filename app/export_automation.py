"""
Export automation for the AutoExport pipeline.

Contains the three main export "operations" (Collect All and Save, export
stems, export main mix) plus the small set of helper functions that only
those operations use (main-mix export folder resolution, silent-file
detection/cleanup, and moving/renaming the main mix file). Those helpers
were moved here alongside the export operations - rather than left in the
main script - purely to avoid a circular import, since the main script's
filename cannot be imported as a normal Python module and these helpers
have no other callers anywhere in the app.

When an arrangement has multiple render clusters, stem WAVs and their group
folders live under the project's "Cluster N" directories. Standalone main
mixes stay in MAIN and use "Cluster N" in their filenames.
"""

import os
import shutil
import time
import logging

import numpy as np
import pyautogui
from export_path_config import get_export_path
from pylive_commands import (
    unmute_all_tracks, unsolo_all_tracks, disable_external_devices, get_tempo,
    get_main_devices, enable_main_devices_named, get_enabled_main_effects,
)
from window_management import get_ableton_window_center
from project_and_file_naming import (
    process_project_name,
    process_project_name_main,
    build_main_mix_filename,
    build_stems_folder_name,
    shorten_project_name,
    sanitize_filename,
    resolve_directory_collision,
)
import naming_settings
from window_utils import win_exists, win_activate, win_activate_ableton
from popup_automation_pcpatch import (
    _ocr_cache,
    _untitled_live_windows,
    detect_specify_window,
    detect_save_popup,
    handle_save_live_set_as,
    handle_progress_bar_wait,
    handle_decoding_popup,
    handle_overwrite_confirmation,
    get_save_audio_file_as,
    get_filename_from_dialog,
    set_text_on_edit_control,
    set_export_path_in_dialog,
)
from popup_automation import _find_dialog_by_ratio_progress_bar
import ableton_state
from organize_stems_by_group import StemsOrganizerByGroup
from export_window_controls import (
    RENDER_TARGET_MASTER,
    RENDER_TARGET_ALL_INDIVIDUAL_TRACKS,
    apply_toggles,
    capture_export_window,
)
from find_clusters import project_render_ranges, render_position, render_length
from rendered_files import wait_for_finished_files, move_rendered_file

logger = logging.getLogger("AutoExport")

# Collect All and Save timing. Generous on purpose - these are ceilings for
# slow (HDD) machines; fast machines move on as soon as the dialog appears.
SPECIFY_APPEAR_TIMEOUT = 20
SAVE_POPUP_APPEAR_TIMEOUT = 20
COLLECT_PROGRESS_TIMEOUT = 600
IDLE_SETTLE_SECONDS = 4


def click_centre_and_zoom_out():
    # Click center
    center = get_ableton_window_center()
    if center:
        x, y = center
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.click(x, y)
    else:
        logger.info("Could not get Ableton window center to click")

    # Zoom out
    for _ in range(3):
        pyautogui.press('-')
        time.sleep(0.05)

def open_export_window():
    """Open the export dialog from arrangement focus and confirm it appeared."""
    if win_activate_ableton() is None:
        logger.error("Cannot open export window: Ableton could not be activated")
        return False
    center = get_ableton_window_center()
    if center is None:
        logger.error("Cannot open export window: Ableton window center not found")
        return False
    pyautogui.click(center)
    time.sleep(0.2)
    pyautogui.hotkey('ctrl', 'shift', 'r')
    time.sleep(0.2)
    handle_decoding_popup()

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if win_exists("Export Audio/Video"):
            if win_activate("Export Audio/Video") is None:
                logger.error("Export dialog appeared but could not be activated")
                return False
            logger.info("Export window opened and activated")
            return True
        time.sleep(0.2)
    logger.error("Export window did not appear within 30 seconds")
    return False


def get_main_mix_export_folder():
    return os.path.join(get_export_path(), "MAIN")


def get_mp3_export_folder():
    return os.path.join(get_export_path(), "MAINmp3")


def _file_signature(path):
    try:
        stat = os.stat(path)
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def move_main_mp3(export_path, base_name, previous=None, timeout=60):
    """Move the MP3 Ableton encoded alongside a main mix ("<base_name>.mp3")
    into MAINmp3, replacing an older copy as the WAV overwrite does. Waits for
    a fresh file whose size has stopped changing. Returns True if moved."""
    src = os.path.join(export_path, f"{base_name}.mp3")
    deadline = time.monotonic() + timeout
    last = None
    while True:
        sig = _file_signature(src)
        if sig is not None and sig != previous and sig[1] > 0 and sig == last:
            break
        if time.monotonic() >= deadline:
            logger.warning(f"No finished MP3 for '{base_name}' after {timeout}s - "
                           "is 'Encode MP3' on in the export dialog?")
            return False
        last = sig
        time.sleep(0.5)

    folder = get_mp3_export_folder()
    try:
        os.makedirs(folder, exist_ok=True)
        dst = os.path.join(folder, f"{base_name}.mp3")
        os.replace(src, dst)
        asd = f"{src}.asd"
        if os.path.exists(asd):
            os.replace(asd, f"{dst}.asd")
        logger.info(f"Moved MP3 to: {dst}")
        return True
    except OSError as e:
        logger.warning(f"Could not move MP3 '{src}' to {folder}: {e}")
        return False


# Wet+dry exports: stems go to Wet/ and Dry/ under the project's stems folder;
# dry stems get DRY_SUFFIX. Main mixes both go to MAIN, the wet one suffixed
# with its enabled main-track effects.
WET_FOLDER = "Wet"
DRY_FOLDER = "Dry"
DRY_SUFFIX = " (dry)"
# Main devices that are always switched on for a wet main export.
FORCE_ON_MAIN_DEVICE_TEXT = "Ozone"
MAIN_EFFECTS_SUFFIX_MAX_LEN = 100


def main_effects_suffix(effect_names):
    """'(Pro-L+Limiter+Ozone 12)' for the wet main mix filename."""
    parts = [sanitize_filename(str(n)).strip() for n in effect_names]
    joined = "+".join(p for p in parts if p)
    if len(joined) > MAIN_EFFECTS_SUFFIX_MAX_LEN:
        joined = joined[:MAIN_EFFECTS_SUFFIX_MAX_LEN].rstrip("+ ")
    return f"({joined})" if joined else ""


def prepare_main_wet_dry(set):
    """Get the main track ready for the wet pass of a wet+dry main export.

    Switches on any disabled main-track device with "Ozone" in its name, then
    reads which effects are on. Returns (ok, suffix): suffix is the wet
    filename suffix, or None when there is nothing on the main track to
    compare against (no devices, or none switched on) - a plain export then
    replaces the wet+dry pair. ok is False if the main track couldn't be read.
    """
    devices = get_main_devices(set)
    if devices is None:
        return False, None
    if not devices:
        logger.info("Wet+Dry: main track has no devices - doing a normal main export")
        return True, None

    enabled = enable_main_devices_named(set, FORCE_ON_MAIN_DEVICE_TEXT)
    if enabled is None:
        return False, None
    if enabled:
        ableton_state.mark_unsaveable_changes()
        logger.info(f"Wet+Dry: switched on {enabled} for the wet main export")

    effects = get_enabled_main_effects(set)
    if effects is None:
        return False, None
    if not effects:
        logger.info("Wet+Dry: no main track effects are switched on - doing a normal main export")
        return True, None

    suffix = main_effects_suffix(effects)
    logger.info(f"Wet+Dry: main effects on for the wet export: {effects} -> suffix '{suffix}'")
    return True, suffix


def close_export_window_if_open(timeout=10):
    """Esc out of the Export Audio/Video dialog if it is still open (it
    normally closes itself once a render finishes). True once it's gone."""
    deadline = time.monotonic() + timeout
    while win_exists("Export Audio/Video"):
        if time.monotonic() >= deadline:
            logger.error(f"Export window still open after {timeout}s of Esc")
            return False
        win_activate("Export Audio/Video")
        pyautogui.press('esc')
        logger.info("Sent Esc to close the export window")
        time.sleep(0.5)
    return True


def stems_cluster_path(export_path, cluster_index):
    if cluster_index is None:
        return export_path
    cluster_path = os.path.join(export_path, f"Cluster {cluster_index}")
    os.makedirs(cluster_path, exist_ok=True)
    return cluster_path

def _reset_export_dialog_focus(state):
    """
    Click the dialog's blank background to reset keyboard focus to a known,
    reproducible state.

    A fixed Tab count is only ever valid starting from a known-reset focus
    state, never from whatever control was last clicked - that mismatch is
    what previously mis-set the Sample Rate combo (a stale Tab count was
    reused after toggles had already been clicked with the mouse). Returns
    True if a background point was found and clicked.
    """
    point = state.background_point() if state is not None else None
    if point is None:
        return False
    pyautogui.click(point)
    time.sleep(0.15)
    return True


def select_render_target(index, label, state=None):
    """
    Set the Export dialog's "Rendered Track" dropdown to a fixed entry.

    The dropdown opens with the highlight on whatever Ableton rendered last and
    wraps around at the ends, so pressing 'down' on its own is relative and
    drifts between projects (Master -> All Individual Tracks -> Selected Tracks
    Only -> back to Master). Pressing 'home' first re-anchors the highlight on
    the top entry, which makes the 'down' presses that follow absolute.

    Focusing the dropdown itself with a fixed two-Tab-press count is only
    reliable from a known-reset focus state. Passing an already-captured
    `state` resets focus first (a background click), which makes this safe
    to retry after other controls have already been clicked with the mouse.

    Leaves focus wherever the dropdown's own Enter-to-confirm lands it -
    callers should not assume focus ends up on Export; reach it via
    click_verified_export_button() instead.
    """
    if state is not None:
        _reset_export_dialog_focus(state)

    for _ in range(2):
        pyautogui.press('tab')
        time.sleep(0.1)

    pyautogui.press('enter')
    time.sleep(0.3)
    pyautogui.press('home')
    time.sleep(0.2)
    for _ in range(index):
        pyautogui.press('down')
        time.sleep(0.15)
    pyautogui.press('enter')
    time.sleep(0.2)
    logger.info(f"Selected render target: {label}")


def click_verified_export_button():
    """
    Commit via the independently verified Export button, not blind tab order.
    """
    if win_activate("Export Audio/Video") is None:
        logger.error("Cannot commit export: could not activate export dialog")
        return False
    time.sleep(0.2)
    current = capture_export_window()
    if current is None:
        logger.error("Cannot commit export: export dialog is not visible")
        return False
    point = current.export_button_point()
    if point is None:
        logger.error("Cannot commit export: Export button could not be verified")
        return False
    pyautogui.click(point)
    deadline = time.monotonic() + 30
    while not win_exists("Save Audio File As:"):
        if time.monotonic() >= deadline:
            logger.error("Save Audio File As did not appear after clicking Export")
            return False
        time.sleep(0.2)
    logger.info("Clicked verified Export button; Save Audio File As appeared")
    return True


def wait_for_save_audio_dialog(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if win_exists("Save Audio File As:"):
            win_activate("Save Audio File As:")
            time.sleep(0.2)
            return True
        time.sleep(0.2)
    logger.error("Save Audio File As dialog did not open within %s seconds", timeout)
    return False


def set_render_range(state, render_range):
    if state is None:
        logger.error("Cannot set render range without a readable export dialog")
        return False
    expected = {
        "Render Start": render_position(render_range[0]),
        "Render Length": render_length(render_range[1] - render_range[0]),
    }
    for label, parts in expected.items():
        point = state.render_time_point(label)
        if point is None:
            logger.error("Cannot locate %s in export dialog", label)
            return False
        pyautogui.click(point)
        for index, part in enumerate(parts):
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.write(str(part))
            if index < 2:
                pyautogui.press('tab')
        pyautogui.press('enter')

    logger.info("Set render start %s and length %s",
                expected["Render Start"], expected["Render Length"])
    return _reset_export_dialog_focus(state)


def render_file_snapshot(export_path, expected_base, individual_tracks):
    if not os.path.isdir(export_path):
        return {}
    files = {}
    with os.scandir(export_path) as entries:
        for entry in entries:
            if (entry.is_file() and entry.name.lower().endswith(".wav")
                    and ((individual_tracks and entry.name.startswith(expected_base + " "))
                         or (not individual_tracks and entry.name == expected_base + ".wav"))):
                stat = entry.stat()
                files[entry.name] = (stat.st_mtime_ns, stat.st_size)
    return files


def wait_for_render(export_path, expected_base, individual_tracks, previous_files):
    def has_fresh_output():
        return any(
            previous_files.get(name) != details
            for name, details in render_file_snapshot(
                export_path, expected_base, individual_tracks
            ).items()
        )

    start = time.monotonic()
    while not win_exists("Export Audio...") and not has_fresh_output():
        if time.monotonic() - start > 30:
            logger.error("Render did not start or create output for %s", expected_base)
            return False
        time.sleep(0.2)
    while win_exists("Export Audio..."):
        time.sleep(1)
    start = time.monotonic()
    while not has_fresh_output():
        if time.monotonic() - start > 15:
            logger.error("Render finished without expected WAV for %s", expected_base)
            return False
        time.sleep(0.2)
    return wait_for_finished_files(
        lambda: render_file_snapshot(export_path, expected_base, individual_tracks),
        export_path,
    )


def prepare_export_dialog(render_target_index, render_target_label,
                          include_return_and_main_effects=None, render_range=None,
                          normalize=None, debug_name=None, commit=True, encode_mp3=None):
    """
    Select the render target, visually confirm the open Export dialog matches
    what was asked for, force the required toggles, then commit the dialog.

    Keyboard navigation alone is blind - it cannot tell whether the "Rendered
    Track" dropdown actually moved - so the dialog is screenshotted and read
    back before anything is rendered. If the render target is definitely wrong
    the export is abandoned rather than silently producing the wrong files.

    `commit=False` runs every step up to (but not including) reaching/pressing
    Export, and returns the post-toggle state instead of True/False - used by
    test_export_window_toggles.py to exercise exactly this code path without
    actually triggering a real render/file-save each run.

    Returns True if the dialog was confirmed and rendering should proceed
    (or, with commit=False, the ExportDialogState after toggles were applied,
    or None/False the same way the committing path would have aborted).
    """
    if render_target_index == RENDER_TARGET_MASTER and include_return_and_main_effects:
        # Ableton always includes return/main effects when rendering the
        # Master and disables the toggle accordingly, so trying to force it
        # on here just clicks a control that can never change and logs a
        # spurious failure.
        logger.info(
            "Rendering Master - 'Include Return and Main Effects' is implicit "
            "and can't be toggled, skipping it"
        )
        include_return_and_main_effects = None

    select_render_target(render_target_index, render_target_label)

    state = capture_export_window(debug_name=debug_name)
    if state is None:
        logger.error("Aborting export: cannot read the export dialog")
        return False

    match = state.matches_render_target(render_target_index)
    if match is False:
        logger.error(
            f"Rendered Track reads '{state.rendered_track}' but should be "
            f"'{render_target_label}' - re-selecting and re-checking"
        )
        select_render_target(render_target_index, render_target_label, state=state)
        state = capture_export_window(debug_name=f"{debug_name}_retry" if debug_name else None)
        if state is None:
            logger.error("Aborting export: cannot read dialog after render-target retry")
            return False
        if state.matches_render_target(render_target_index) is False:
            logger.error(
                f"Aborting export - Rendered Track is still '{state.rendered_track}' "
                f"instead of '{render_target_label}'"
            )
            return False
    elif match is None:
        logger.warning(f"Could not read the Rendered Track dropdown (wanted {render_target_label})")
    else:
        logger.info(f"Verified render target is '{state.rendered_track}'")

    result = apply_toggles(
        {
            # Always required downstream, so it is forced rather than exposed.
            "create_analysis_file": True,
            "include_return_and_main_effects": include_return_and_main_effects,
            "normalize": normalize,
            "encode_mp3": encode_mp3,
        },
        state=state,
        debug_name=debug_name,
    )
    if result and "encode_mp3" in result["failed"]:
        logger.warning(f"Could not confirm 'Encode MP3' is {'On' if encode_mp3 else 'Off'}")

    final_state = result["state"] if result else state

    if render_range is not None and not set_render_range(final_state, render_range):
        return False

    if not commit:
        return final_state

    return click_verified_export_button()



def is_silent_audio(file_path, threshold_db=-60):
    """
    Check if a WAV file is completely silent (entire file below -60dB).
    Returns True only if 100% of the file is silent.
    Supports all WAV formats including IEEE float.
    """
    try:
        import soundfile as sf
        
        # Read audio file
        audio_array, samplerate = sf.read(file_path, dtype='float32')
        
        if len(audio_array) == 0:
            return True
        
        # Handle mono vs stereo
        if audio_array.ndim == 1:
            audio_array = audio_array.reshape(-1, 1)
        
        # Calculate RMS for each frame (100ms chunks)
        chunk_size = int(samplerate * 0.1)
        silent_frames = 0
        total_frames = 0
        
        for i in range(0, len(audio_array), chunk_size):
            chunk = audio_array[i:i+chunk_size]
            if len(chunk) == 0:
                continue
            
            total_frames += 1
            rms = np.sqrt(np.mean(chunk ** 2))
            
            if rms > 0:
                db = 20 * np.log10(rms)
            else:
                db = -np.inf
            
            if db < threshold_db:
                silent_frames += 1
        
        if total_frames == 0:
            return True
        
        silence_percentage = (silent_frames / total_frames) * 100
        logger.info(f"  Audio silence: {silence_percentage:.1f}%")
        
        return silence_percentage >= 100
        
    except Exception as e:
        logger.info(f"Error analyzing audio file {file_path}: {e}")
        return False
            
def delete_silent_files_in_folder(folder_path):
    """
    Scan folder for WAV files and delete those that are completely silent (100%).
    """
    if not os.path.exists(folder_path):
        logger.info(f"Folder does not exist: {folder_path}")
        return 0
    
    deleted_count = 0
    wav_files = [f for f in os.listdir(folder_path) if f.endswith('.wav')]
    
    if not wav_files:
        logger.info(f"No WAV files found in: {folder_path}")
        return 0
    
    logger.info(f"Checking {len(wav_files)} WAV file(s) for complete silence...")
    
    for wav_file in wav_files:
        file_path = os.path.join(folder_path, wav_file)
        logger.info(f"Analyzing: {wav_file}")
        
        if is_silent_audio(file_path):
            try:
                os.remove(file_path)
                logger.info(f" Deleted completely silent file: {wav_file}")
                deleted_count += 1
                
                # Also delete associated ASD file if it exists
                asd_file = f"{file_path}.asd"
                if os.path.exists(asd_file):
                    os.remove(asd_file)
                    logger.info(f"Deleted analysis file: {os.path.basename(asd_file)}")
            except Exception as e:
                logger.info(f"Error deleting {wav_file}: {e}")
        else:
            logger.info(f"Kept (has audio): {wav_file}")
    
    if deleted_count > 0:
        logger.info(f"Deleted {deleted_count} completely silent file(s)")
    else:
        logger.info("No completely silent files found")
    
    return deleted_count

def move_and_rename_main_mix(export_path, original_name, tempo, project_date, expected_base=None,
                              include_bpm=True, include_date=True, cluster_index=None,
                              name_suffix=""):
    r"""
    Move the main mix WAV and ASD files to Main Mix folder,
    renaming them with the original name, date, and tempo.

    ``expected_base`` is the filename Ableton was given in the save dialog. A
    Master render writes exactly "<expected_base>.wav", while an individual
    tracks render writes "<expected_base> <track name>.wav" for every track and
    no bare file. Matching that exact name is therefore the only safe way to
    tell a main mix from a stem; without it the shortest stem filename gets
    moved out of the stems folder and misfiled as a main mix.
    """
    try:
        # Create the main mix folder if it doesn't exist
        main_mix_folder = get_main_mix_export_folder()
        if not os.path.exists(main_mix_folder):
            os.makedirs(main_mix_folder)
            logger.info(f"Created main mix folder: {main_mix_folder}")
        
        wav_files = [f for f in os.listdir(export_path) if f.endswith('.wav')]
        
        if not wav_files:
            logger.info(f"No WAV files found in: {export_path}")
            return False
        
        if expected_base:
            main_mix_file = f"{expected_base}.wav"
            if main_mix_file not in wav_files:
                logger.info(
                    f"No main mix file named '{main_mix_file}' in {export_path} - "
                    f"leaving all {len(wav_files)} rendered file(s) in place"
                )
                return False
        else:
            # Fall back to the shortest filename (main mix has no track suffix)
            main_mix_file = min(wav_files, key=len)

        old_name_base = os.path.splitext(main_mix_file)[0]
        
        old_wav_path = os.path.join(export_path, main_mix_file)
        asd_file = f"{old_name_base}.wav.asd"
        old_asd_path = os.path.join(export_path, asd_file)

        new_base = build_main_mix_filename(original_name, tempo, project_date, include_bpm, include_date)
        if cluster_index is not None:
            new_base = f"{new_base} Cluster {cluster_index}"
        if os.path.exists(os.path.join(main_mix_folder, f"{new_base}{name_suffix}.wav")):
            new_base = f"{new_base} Stems"
        new_base = f"{new_base}{name_suffix}"
        unique_base = new_base
        counter = 2
        while os.path.exists(os.path.join(main_mix_folder, f"{unique_base}.wav")):
            unique_base = f"{new_base} {counter}"
            counter += 1
        new_base = unique_base
        new_wav_filename = f"{new_base}.wav"
        new_asd_filename = f"{new_base}.wav.asd"
        
        final_wav_path = os.path.join(main_mix_folder, new_wav_filename)
        move_rendered_file(old_wav_path, final_wav_path)
        logger.info(f"Moved main mix to: {final_wav_path}")
        
        if os.path.exists(old_asd_path):
            final_asd_path = os.path.join(main_mix_folder, new_asd_filename)
            move_rendered_file(old_asd_path, final_asd_path)
            logger.info(f"Moved analysis file to: {final_asd_path}")
        
        return True
        
    except Exception as e:
        logger.info(f"Error moving/renaming main mix file: {e}")
        return False


def collect_all_and_save(ableton_version):
    """Trigger Ableton's Collect All and Save and handle related dialogs."""
    # Ensure Ableton is focused
    win_activate_ableton()
    time.sleep(0.2)

    # Open File menu and navigate to Collect All and Save
    pyautogui.hotkey('alt', 'f')
    time.sleep(0.3)
    for _ in range(11):
        pyautogui.press('down')
        time.sleep(0.05)
    pyautogui.press('enter')
    logger.info("Invoked Collect All and Save from menu")
    time.sleep(0.2)

    # Handle specify dialog - press enter to dismiss it. Polled rather than
    # checked once: on a slow disk it can take several seconds to open, and a
    # missed specify dialog stays open and blocks everything after it.
    logger.info(">>> Waiting for Specify window...")
    start_time = time.time()
    specify_detected = False
    while time.time() - start_time < SPECIFY_APPEAR_TIMEOUT:
        if detect_specify_window():
            specify_detected = True
            break
        time.sleep(0.3)
    if not specify_detected:
        logger.error(
            f"Specify window did not appear within {SPECIFY_APPEAR_TIMEOUT}s - "
            "Collect All and Save was probably not invoked"
        )
        return False
    if specify_detected:
        logger.info("Specify window detected, dismissing...")
        pyautogui.press('enter')
        time.sleep(0.5)
        
        # Now wait for specify window to actually disappear
        # Use fresh checks without cooldown by calling _untitled_live_windows directly
        specify_gone = False
        timeout = 10  # Max 10 seconds to wait for window to disappear
        wait_start = time.time()
        while time.time() - wait_start < timeout:
            # Check if specify window (534x290, ratio ~1.84) is still present
            found = False
            for w, r in _untitled_live_windows():
                width = r.width()
                height = r.height()
                aspect_ratio = width / height if height > 0 else 0
                if 1.7 <= aspect_ratio <= 2.0:  # Specify window ratio
                    found = True
                    break
            
            if not found:
                specify_gone = True
                break
            
            time.sleep(0.2)
        
        elapsed = time.time() - start_time
        if specify_gone:
            logger.info(f"Specify window closed (took {elapsed:.1f}s)")
        else:
            logger.warning(f"Specify window did not close after {elapsed:.1f}s (timeout)")
    
    # Clear both specify and save_popup caches so fresh OCR can run
    _ocr_cache["specify"] = (0.0, False)
    _ocr_cache["save_popup"] = (0.0, False)
    
    # After specify closes, Ableton either shows the "created with an older
    # version - save as a new file" popup, starts copying (progress bar), or -
    # with nothing to collect and an up-to-date set - just goes idle. Poll for
    # whichever happens instead of sleeping a fixed 2s and checking once.
    logger.info(">>> Waiting for Save popup / progress bar / idle...")
    start_time = time.time()
    detected = False
    clean_since = None
    while time.time() - start_time < SAVE_POPUP_APPEAR_TIMEOUT:
        if detect_save_popup():
            detected = True
            logger.info(f"Save popup detected (appeared after {time.time() - start_time:.1f}s)")
            break
        if _find_dialog_by_ratio_progress_bar("Ableton Live Window Class", 0.47, 0.50)[0]:
            logger.info("Progress bar appeared (no save popup)")
            break
        if ableton_state.get_state()["clean"]:
            clean_since = clean_since or time.time()
            if time.time() - clean_since >= IDLE_SETTLE_SECONDS:
                logger.info("Ableton went idle without a save popup or progress bar")
                break
        else:
            clean_since = None
        time.sleep(0.3)
    
    if detected:
        pyautogui.press('enter')
        logger.info("Pressed Enter on save popup")
        time.sleep(0.3)
        if not _wait_for_save_popup_to_close():
            return False
        time.sleep(0.3)
        if not handle_save_live_set_as(ableton_version):
            logger.error("Collect All and Save: 'Save Live Set As' step failed")
            return False

    time.sleep(0.3)

    if handle_progress_bar_wait(timeout=COLLECT_PROGRESS_TIMEOUT, appear_timeout=5):
        logger.info("Collect all progress bar finished")

    # Success is judged by the end state, not by which dialogs were seen:
    # whatever path Ableton took, it must end up idle on its main window.
    late_popup_handled = []

    def _handle_late_save_popup(_state):
        # Very slow machines can go quiet for longer than the idle window
        # before the save popup opens; handle it here rather than time out.
        if late_popup_handled or not detect_save_popup():
            return
        late_popup_handled.append(True)
        logger.info("Late save popup detected - handling")
        pyautogui.press('enter')
        time.sleep(0.3)
        if _wait_for_save_popup_to_close():
            time.sleep(0.3)
            handle_save_live_set_as(ableton_version)
            handle_progress_bar_wait(timeout=COLLECT_PROGRESS_TIMEOUT, appear_timeout=5)

    if not ableton_state.wait_for_clean_state(
        timeout=60, settle=IDLE_SETTLE_SECONDS, on_blocked=_handle_late_save_popup,
        label="after Collect All and Save"
    ):
        return False
    logger.info("Collect All and Save completed")
    return True


def _wait_for_save_popup_to_close(timeout=15):
    deadline = time.time() + timeout
    while detect_save_popup():
        if time.time() >= deadline:
            logger.error(f"'Save as new file' popup did not close within {timeout}s")
            return False
        time.sleep(0.1)
    return True

def export_stems(export_path, project_path, project_date, ableton_version, set,
                 include_return_and_main_effects=True, normalize=False, render_ranges=None,
                 pass_folder=None, name_suffix=""):
    """Render stems for every cluster. Returns the project's stems folder
    (which may have been renamed to include the BPM), or False on failure.

    `pass_folder` ("Wet"/"Dry" for a wet+dry export) renders into that
    subfolder of the stems folder, so the two passes never collide;
    `name_suffix` is appended to every organised stem filename.
    """
    ranges = render_ranges if render_ranges is not None else project_render_ranges(project_path)
    for index, render_range in enumerate(ranges, 1):
        result = _export_stems_range(
            export_path, project_path, project_date, ableton_version, set,
            include_return_and_main_effects, normalize, render_range,
            index if len(ranges) > 1 else None,
            pass_folder=pass_folder, name_suffix=name_suffix,
        )
        if not result:
            return False
        export_path = result
    return export_path


def _export_stems_range(export_path, project_path, project_date, ableton_version, set,
                        include_return_and_main_effects, normalize, render_range, cluster_index,
                        pass_folder=None, name_suffix=""):
    logger.info(">>> EXPORT_STEMS START")
    

    #pylive operations
    set = set
    
    
    # Debug: Test basic OSC connection with retry loop
    # On first project, numeric property queries may timeout initially
    # Attempt tempo query but don't block on it - we've already verified scan works
    max_retries = 5
    retry_delay = 1.0
    tempo = None
    
    for attempt in range(max_retries):
        try:
            tempo = set.tempo
            logger.info(f"OSC Connection verified - Queried tempo: {tempo} BPM")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"OSC query attempt {attempt + 1}/{max_retries} failed, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                # On first project, this may happen. Log warning but continue since scan succeeded
                logger.warning(f"OSC tempo query failed after {max_retries} attempts (first project may need longer warmup): {str(e)[:100]}")
                tempo = None  # Will not be available for naming but export can continue
    
    # Debug: Check scan mode and project structure
    logger.info(f"  Scanned: {set.scanned}")
    logger.info(f"  Total tracks: {len(set.tracks)}")
    logger.info(f"  Total groups: {len(set.groups)}")
    if set.groups:
        logger.info(f"  Group names: {[g.name for g in set.groups]}")

    # The stems folder was created (in create_export_folders) before Ableton
    # opened, so it couldn't include a BPM segment - tempo is now known, so
    # rename it in place if the "include bpm" setting calls for it.
    naming = naming_settings.load_settings()
    if naming["stems_folder_include_bpm"] and tempo and export_path and os.path.isdir(export_path):
        sanitized_project_name = sanitize_filename(os.path.splitext(os.path.basename(project_path))[0])
        new_folder_name = build_stems_folder_name(
            sanitized_project_name, tempo, project_date,
            include_bpm=True, include_date=naming["stems_folder_include_date"],
        )
        current_folder_name = os.path.basename(export_path)
        if new_folder_name != current_folder_name:
            new_export_path = os.path.join(os.path.dirname(export_path), new_folder_name)
            new_export_path = resolve_directory_collision(new_export_path)
            try:
                os.rename(export_path, new_export_path)
                logger.info(f"Renamed stems folder to include BPM: '{current_folder_name}' -> '{os.path.basename(new_export_path)}'")
                export_path = new_export_path
            except Exception as e:
                logger.warning(f"Could not rename stems folder to include BPM: {e}")

    unmute_all_tracks(set)
    disable_external_devices(set)
    click_centre_and_zoom_out()
    time.sleep(0.1)

    pyautogui.press('f10')
    time.sleep(0.2)

    if not open_export_window():
        return False
   
    if not prepare_export_dialog(
        RENDER_TARGET_ALL_INDIVIDUAL_TRACKS,
        "All Individual Tracks",
        include_return_and_main_effects=include_return_and_main_effects,
        normalize=normalize,
        render_range=render_range,
        debug_name="export_stems",
        encode_mp3=False,
    ):
        return False
    time.sleep(0.1)

    if not wait_for_save_audio_dialog():
        return False

    # Read filename
    save_dlg = get_save_audio_file_as(timeout=10)
    if not save_dlg:
        logger.info("Could not get Save Audio dialog")
        return False
    
    current_text = get_filename_from_dialog(save_dlg)
    if not current_text:
        logger.info("Could not get current filename from Save Audio dialog")
        return False

    tempo = get_tempo(set)
    logger.debug(f"Current tempo: {tempo}")
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    final_text = process_project_name(project_name, tempo, ableton_version)
    if cluster_index is not None:
        final_text = f"{final_text} Cluster {cluster_index}"

    # Set filename
    if not set_text_on_edit_control(save_dlg, final_text):
        logger.info("Failed to set filename")
        return False

    time.sleep(0.2)

    pass_root = os.path.join(export_path, pass_folder) if pass_folder else export_path
    os.makedirs(pass_root, exist_ok=True)
    cluster_export_path = stems_cluster_path(pass_root, cluster_index)
    if not set_export_path_in_dialog(cluster_export_path):
        logger.error("Failed to set stems export path: %s", cluster_export_path)
        return False
    time.sleep(0.1)

    previous_files = render_file_snapshot(cluster_export_path, final_text, True)

    # Save
    try:
        pyautogui.hotkey('alt', 's')
        logger.debug("Sent Alt+S to save")
    except Exception as e:
        logger.info(f"Failed to send Alt+S: {e}")

    time.sleep(0.2)

    # Overwrite prompt

    handle_overwrite_confirmation()
    time.sleep(0.2)

    if not wait_for_render(cluster_export_path, final_text, True, previous_files):
        return False

    time.sleep(0.2)

    # Move and rename main mix file
    move_and_rename_main_mix(
        cluster_export_path, project_name, tempo, project_date,
        expected_base=final_text,
        include_bpm=naming["main_mix_include_bpm"],
        include_date=naming["main_mix_include_date"],
        cluster_index=cluster_index,
        name_suffix=name_suffix,
    )

    remaining_wavs = [f for f in os.listdir(cluster_export_path) if f.endswith('.wav')] \
        if os.path.exists(cluster_export_path) else []
    if not remaining_wavs:
        logger.error(
            f"Stems export produced no stem files in {cluster_export_path} - Ableton "
            "rendered the Master only, so the 'Rendered Track' dropdown was not "
            "set to 'All Individual Tracks'."
        )
        return False

    #delete silent audio files
    delete_silent_files_in_folder(cluster_export_path)

    #organise stems by group, matching the project's nested group/subgroup
    #structure, and rename each file per the naming settings above
    display_name = shorten_project_name(project_name)
    organizer = StemsOrganizerByGroup(
        stems_dir=cluster_export_path,
        ableton_working_prefix=final_text,
        project_path=project_path,
        tempo=tempo,
        display_name=display_name,
        include_bpm=naming["stem_files_include_bpm"],
        include_project_name=naming["stem_files_include_project_name"],
        name_suffix=name_suffix,
    )
    organizer.organize()

    # Don't shutdown OSC here - let Ableton cleanup handle it
    # Explicit shutdown can cause port binding issues for next project
    logger.debug("OSC server will be cleaned up when Ableton closes")

    return export_path

def export_main(project_date, ableton_version, set,
                include_return_and_main_effects=True, normalize=False,
                project_path=None, render_ranges=None, name_suffix=""):
    """Render the main mix for every cluster into MAIN. `name_suffix` is
    appended to each filename (the wet pass of a wet+dry export)."""
    if project_path is None:
        raise ValueError("project_path is required for stable main mix naming")
    if render_ranges is None:
        render_ranges = project_render_ranges(project_path)
    for index, render_range in enumerate(render_ranges, 1):
        if not _export_main_range(
            project_date, ableton_version, set, include_return_and_main_effects,
            normalize, render_range, index if len(render_ranges) > 1 else None,
            project_path, name_suffix=name_suffix,
        ):
            return False
    return True


def _export_main_range(project_date, ableton_version, set,
                       include_return_and_main_effects, normalize, render_range, cluster_index,
                       project_path, name_suffix=""):
    export_path = get_main_mix_export_folder()
    if not os.path.exists(export_path):
        os.makedirs(export_path)
        logger.info(f"Created export path: {export_path}")



    pyautogui.press('f10')
    time.sleep(0.2)

    #pylive operations
    set = set

    tempo = set.tempo
    print(f"Debugging: Queried Current tempo: {tempo}")
    
    unsolo_all_tracks(set)
    disable_external_devices(set)
    click_centre_and_zoom_out()

    if not open_export_window():
        return False

    # Ableton remembers the last render target, so without this an export that
    # follows a stems export would render individual tracks instead of the mix.
    if not prepare_export_dialog(
        RENDER_TARGET_MASTER,
        "Master",
        include_return_and_main_effects=include_return_and_main_effects,
        normalize=normalize,
        render_range=render_range,
        debug_name="export_main",
        encode_mp3=True,
    ):
        return False
    time.sleep(0.1)

    if not wait_for_save_audio_dialog():
        return False

    # Read filename
    save_dlg = get_save_audio_file_as(timeout=10)
    if not save_dlg:
        logger.info("Could not get Save Audio dialog")
        return False
    
    current_text = get_filename_from_dialog(save_dlg)
    if not current_text:
        logger.info("Could not get current filename from Save Audio dialog")
        return False

    tempo = get_tempo(set)
    logger.debug(f"Current tempo: {tempo}")
    naming = naming_settings.load_settings()
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    final_text = process_project_name_main(
        project_name, tempo, ableton_version, project_date,
        include_bpm=naming["main_mix_include_bpm"],
        include_date=naming["main_mix_include_date"],
    )
    if cluster_index is not None:
        final_text = f"{final_text} Cluster {cluster_index}"
    final_text = f"{final_text}{name_suffix}"

    # Set filename
    if not set_text_on_edit_control(save_dlg, final_text):
        logger.info("Failed to set filename")
        return False


    # Set export path
    set_export_path_in_dialog(export_path)
    time.sleep(0.1)

    previous_files = render_file_snapshot(export_path, final_text, False)
    mp3_previous = _file_signature(os.path.join(export_path, f"{final_text}.mp3"))

    # Save
    try:
        pyautogui.hotkey('alt', 's')
        logger.debug("Sent Alt+S to save")
    except Exception as e:
        logger.info(f"Failed to send Alt+S: {e}")

    time.sleep(2)

    # Overwrite prompt
    handle_overwrite_confirmation()
    time.sleep(0.2)

    if not wait_for_render(export_path, final_text, False, previous_files):
        return False

    move_main_mp3(export_path, final_text, mp3_previous)

    time.sleep(1)

    # Don't shutdown OSC here - let Ableton cleanup handle it
    # Explicit shutdown can cause port binding issues for next project
    logger.debug("OSC server will be cleaned up when Ableton closes")

    return True
