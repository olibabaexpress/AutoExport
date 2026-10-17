"""
Standalone test harness for the Export Audio/Video dialog preparation.

This file keeps its OWN copy of prepare_export_dialog() so you can edit the
function body below, without touching app/export_automation.py. It then drives
that copy against a live Ableton export dialog using hard-coded dummy values
for every argument.

Run this with the "Export Audio/Video" dialog already open in Ableton. It
activates Ableton, runs the function, and prints the dummy values used plus
what the function returned.

IMPORTANT: with commit=True (the default) this clicks the verified Export
button and commits a real export. Use commit=False to run the whole flow up to
(but not including) the Export click, which returns the pre-commit state.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime

from window_utils import win_activate_ableton
from export_window_controls_testcopy import (
    RENDER_TARGET_MASTER,
    RENDER_TARGET_ALL_INDIVIDUAL_TRACKS,
    DROPDOWN_LABELS,
    TOGGLE_LABELS,
    apply_toggles,
    capture_export_window,
    set_dropdown,
)
from export_automation import (
    click_verified_export_button,
    select_render_target,
    set_render_range,
)

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Dummy values for EVERY argument that prepare_export_dialog can be called
# with. Edit these to try different combinations without touching the function
# body below.
# ---------------------------------------------------------------------------

# ---- render target (see app/export_window_controls_testcopy.py for the real values) ----
# RENDER_TARGET_MASTER  -> "Main"
# RENDER_TARGET_ALL_INDIVIDUAL_TRACKS -> "All Individual Tracks"
DUMMY_RENDER_TARGET_INDEX = RENDER_TARGET_ALL_INDIVIDUAL_TRACKS
DUMMY_RENDER_TARGET_LABEL = "All Individual Tracks"

# ---- toggles ----
# None = leave the toggle alone; True/False = force it on/off.
DUMMY_INCLUDE_RETURN_AND_MAIN_EFFECTS = None  # Master can't toggle this at all
DUMMY_NORMALIZE = True                        # True / False / None
DUMMY_ENCODE_MP3 = False                      # True / False / None

# ---- render range (None = don't touch the range) ----
DUMMY_RENDER_RANGE = None

# ---- debug / commit flags ----
DUMMY_DEBUG_NAME = "testexportwindow"
DUMMY_COMMIT = True      # True = click the verified Export button

# ---------------------------------------------------------------------------
# Dummy dropdown targets.
#
# DROPDOWN_OPTIONS is the FULL ordered list of choices each dropdown menu shows
# (top-to-bottom) - keep this in sync with Ableton. The DUMMY_*_TARGET values
# below are what we want to drive each dropdown to (must be a member of that
# dropdown's option list). Set a target to None to leave that dropdown alone.
#
# Note: Dither Options is greyed out when Bit Depth is 32. When we drive
# Bit Depth to 32 we therefore skip adjusting Dither.
# ---------------------------------------------------------------------------

DROPDOWN_OPTIONS = {
    "sample_rate": ["22050", "32000", "44100", "48000", "88200", "96000", "176400", "192000"],
    "file_type": ["WAV", "AIFF", "FLAC"],
    "bit_depth": ["16", "24", "32"],
    "dither": ["No Dither", "Rectangular", "Triangular", "POW-r 1", "POW-r 2", "POW-r 3"],
}

DUMMY_SAMPLE_RATE_TARGET = "44100"    # options: see DROPDOWN_OPTIONS["sample_rate"]
DUMMY_FILE_TYPE_TARGET = "WAV"        # options: WAV, AIFF, FLAC
DUMMY_BIT_DEPTH_TARGET = "16"         # options: 16, 24, 32
DUMMY_DITHER_TARGET = "POW-r 1"       # options: No Dither, Rectangular, Triangular, POW-r 1/2/3

# Map each dropdown key to its (dummy) target above.
DUMMY_DROPDOWN_TARGETS = {
    "sample_rate": DUMMY_SAMPLE_RATE_TARGET,
    "file_type": DUMMY_FILE_TYPE_TARGET,
    "bit_depth": DUMMY_BIT_DEPTH_TARGET,
    "dither": DUMMY_DITHER_TARGET,
}

# ---------------------------------------------------------------------------
# prepare_export_dialog
#
# The current pipeline implementation of app/export_automation.py's
# prepare_export_dialog(), copied into this file so it can be edited here
# without touching the original module. It uses the dummy constants above, so
# any call exercises exactly these values.
#
# The helper functions referenced here (select_render_target, capture_export_window,
# apply_toggles, set_render_range, click_verified_export_button) are the real
# pipeline helpers, imported from their modules so the dialog still gets driven
# through the real UI automation.
# ---------------------------------------------------------------------------
def prepare_export_dialog(
    render_target_index,
    render_target_label,
    include_return_and_main_effects=None,
    render_range=None,
    normalize=None,
    debug_name=None,
    commit=True,
    encode_mp3=None,
):
    if render_target_index == RENDER_TARGET_MASTER and include_return_and_main_effects:
        # Ableton always includes return/main effects when rendering the
        # Master and disables the toggle accordingly, so trying to force it
        # on here just clicks a control that can never change and logs a
        # spurious failure.
        logger.debug(
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
        state = capture_export_window(
            debug_name=f"{debug_name}_retry" if debug_name else None
        )
        if state is None:
            logger.error("Aborting export: cannot read dialog after render-target retry")
            return False
        if state.matches_render_target(render_target_index) is False:
            logger.error(
                f"Aborting export - Rendered Track is still "
                f"'{state.rendered_track}' instead of '{render_target_label}'"
            )
            return False
    elif match is None:
        logger.warning(
            f"Could not read the Rendered Track dropdown (wanted {render_target_label})"
        )
    else:
        logger.debug(f"Verified render target is '{state.rendered_track}'")

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
        logger.warning(
            f"Could not confirm 'Encode MP3' is {'On' if encode_mp3 else 'Off'}"
        )

    final_state = result["state"] if result else state

    if render_range is not None and not set_render_range(final_state, render_range):
        return False

    if not commit:
        return final_state

    return click_verified_export_button()

def run_test():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("Activating Ableton window...")
    if win_activate_ableton() is None:
        print("Could not activate the Ableton window - is it running?")
        return
    time.sleep(0.5)

    print("\n--- Dummy values being driven into prepare_export_dialog ---")
    print(f"  render_target_index         = {DUMMY_RENDER_TARGET_INDEX}")
    print(f"  render_target_label         = {DUMMY_RENDER_TARGET_LABEL!r}")
    print(
        f"  include_return_and_main_effects = "
        f"{DUMMY_INCLUDE_RETURN_AND_MAIN_EFFECTS!r}"
    )
    print(f"  render_range                = {DUMMY_RENDER_RANGE!r}")
    print(f"  normalize                   = {DUMMY_NORMALIZE!r}")
    print(f"  encode_mp3                  = {DUMMY_ENCODE_MP3!r}")
    print(f"  debug_name                  = {DUMMY_DEBUG_NAME!r}")
    print(f"  commit                      = {DUMMY_COMMIT!r}")
    print("  (commit=True will click the verified Export button - a real export)")
    print("-------------------------------------------------------------------\n")

    result = prepare_export_dialog(
        DUMMY_RENDER_TARGET_INDEX,
        DUMMY_RENDER_TARGET_LABEL,
        include_return_and_main_effects=DUMMY_INCLUDE_RETURN_AND_MAIN_EFFECTS,
        render_range=DUMMY_RENDER_RANGE,
        normalize=DUMMY_NORMALIZE,
        debug_name=DUMMY_DEBUG_NAME,
        commit=DUMMY_COMMIT,
        encode_mp3=DUMMY_ENCODE_MP3,
    )

    print(f"\nprepare_export_dialog returned: {result}")

    # Save a small report next to the export_window_debug screenshots so a
    # failure can be diagnosed later.
    report = {
        "timestamp": stamp,
        "render_target_index": DUMMY_RENDER_TARGET_INDEX,
        "render_target_label": DUMMY_RENDER_TARGET_LABEL,
        "include_return_and_main_effects": DUMMY_INCLUDE_RETURN_AND_MAIN_EFFECTS,
        "render_range": DUMMY_RENDER_RANGE,
        "normalize": DUMMY_NORMALIZE,
        "encode_mp3": DUMMY_ENCODE_MP3,
        "debug_name": DUMMY_DEBUG_NAME,
        "commit": DUMMY_COMMIT,
        "result": result,
    }
    try:
        os.makedirs("debug/export_window_debug", exist_ok=True)
        report_path = f"debug/export_window_debug/{stamp}_testexportwindow.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport saved to {report_path}")
    except Exception as e:  # pragma: no cover
        print(f"\nCould not save report: {e}")


if __name__ == "__main__":
    # 1. Open the export dialog.
    print("Activating Ableton window...")
    if win_activate_ableton() is None:
        print("Could not activate Ableton window - is it running?")
        sys.exit(1)
    time.sleep(0.5)
    print("Press F10 to open the Export Audio/Video dialog...")
    time.sleep(0.5)

    # 2. Capture the export window ONCE (single screenshot).
    from export_window_controls_testcopy import capture_export_window
    state = capture_export_window("testexportwindow")
    if state is None:
        print("Could not capture the export dialog - is it open in Ableton?")
        sys.exit(1)

    # 3. Read every dropdown AND toggle off that SAME screenshot.
    print("\n=== Dropdowns (single export window screenshot) ===")
    for key in DROPDOWN_LABELS:
        row = state.dropdowns.get(key)
        box = state.dropdown_value_boxes.get(key)
        value = state.dropdown_values.get(key, "<not read>")
        print(f"  {key:<20} label={row['text'] if row else None!r:<20} "
              f"box={box} value_raw={value!r}")

    print("\n=== Toggles (single export window screenshot) ===")
    for key in TOGGLE_LABELS:
        print(f"  {key:<25} {state.toggles.get(key)!r}")

    print("\n=== Rendered Track ===")
    print(f"  {state.rendered_track!r}")

    # ---- Full ordered option lists for each dropdown (menu order, top->bottom) ----
    TARGETS = DROPDOWN_OPTIONS

    # 4. Match the current dropdown values against the options (for reporting).
    def match_target(ocr_text, options):
        """Return the first option that appears in ocr_text (case-insensitive,
        ignoring non-alphanumeric chars), or None if no match."""
        if not ocr_text:
            return None
        norm_ocr = "".join(ch for ch in ocr_text.lower() if ch.isalnum())
        for opt in options:
            if "".join(ch for ch in opt.lower() if ch.isalnum()) in norm_ocr:
                return opt
        return None

    print("\n=== Current dropdown values (before) ===")
    matched = {}
    for key in DROPDOWN_LABELS:
        raw = state.dropdown_values.get(key, "")
        opt = match_target(raw, TARGETS[key])
        matched[key] = opt
        target = DUMMY_DROPDOWN_TARGETS.get(key)
        flag = "" if opt == target else "  <-- will change"
        print(f"  {key:<20} raw={raw!r:<20} matched={opt!r:<15} target={target!r}{flag}")

    # 5. Drive each dropdown to its dummy target (click + pick from the menu).
    #    Dither Options is greyed out when Bit Depth is 32, so only touch Dither
    #    when the (target) bit depth leaves it selectable.
    print("\n=== Setting dropdowns to targets ===")
    final_bit_depth = DUMMY_DROPDOWN_TARGETS.get("bit_depth")
    dropdown_results = {}
    for key in DROPDOWN_LABELS:
        target = DUMMY_DROPDOWN_TARGETS.get(key)
        if target is None:
            print(f"  {key:<20} target=None -> leaving alone")
            continue
        if key == "dither" and final_bit_depth == "32":
            print(f"  {key:<20} skipped (bit depth 32 -> dither greyed out)")
            continue
        res = set_dropdown(key, target, TARGETS[key], debug_name=DUMMY_DEBUG_NAME, state=state)
        dropdown_results[key] = res
        ok = bool(res) and res.get("ok")
        print(f"  {key:<20} -> {target!r:<12} changed={bool(res) and res.get('changed')!s:<6} ok={ok}")

    # 6. Re-capture and report the final state of everything.
    print("\n=== Final dropdown values (after) ===")
    state = capture_export_window(DUMMY_DEBUG_NAME)
    if state is not None:
        for key in DROPDOWN_LABELS:
            raw = state.dropdown_values.get(key, "")
            opt = match_target(raw, TARGETS[key])
            target = DUMMY_DROPDOWN_TARGETS.get(key)
            print(f"  {key:<20} raw={raw!r:<20} matched={opt!r:<15} target={target!r}")

    # Dither is greyed out when bit depth is 32.
    if final_bit_depth == "32":
        print("\n  NOTE: bit depth target=32 -> dither options are greyed out and were not set")

    # 7. Press the export button (commits a real export).
    print("\n=== Export button ===")
    result = click_verified_export_button()
    print(f"Export button pressed: {result}")


