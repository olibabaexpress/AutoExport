"""
Standalone resilience test for export_window_controls.py.

Run this with the "Export Audio/Video" dialog already open in Ableton. It
activates the Ableton window, prints a BEFORE snapshot of every toggle plus
the render target, then calls export_automation.prepare_export_dialog() -
the exact same function the real pipeline calls - so whatever real-world
render-target-selection / toggle / export-button bug shows up in production
shows up here too, instead of the test script's own diverging sequence
papering over it.

IMPORTANT: this WILL commit a real export. prepare_export_dialog() ends by
clicking the independently verified Export button, exactly like the production
pipeline. There is no dry-run mode because it exercises the real commit path.

It prints, for both the BEFORE and AFTER captures:
  - Every raw OCR line pytesseract read (text + bounding box), so an OCR
    misread on the Rendered Track dropdown (or anywhere else) can be seen
    directly rather than inferred from a downstream failure.
  - The absolute screen coordinates of every toggle, the Rendered Track
    dropdown, and the Export button, i.e. exactly the points the automation
    would click.

Edit DESIRED_RENDER_TARGET and DESIRED_TOGGLES below to change what gets
tested.
"""

import json
import logging
import time
from datetime import datetime

from window_utils import win_activate_ableton
from export_automation import prepare_export_dialog
from export_window_controls import (
    RENDER_TARGET_MASTER,
    RENDER_TARGET_ALL_INDIVIDUAL_TRACKS,
    TOGGLE_LABELS,
    RENDERED_TRACK_LABEL,
    capture_export_window,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


import os
from PIL import Image
# ---------------------------------------------------------------------------
# Edit these to change what the test drives the dialog to.
# ---------------------------------------------------------------------------

# "main" (Master) or "individual" (All Individual Tracks). Set to None to
# leave the "Rendered Track" dropdown untouched.
DESIRED_RENDER_TARGET = "individual"

# True/False forces the toggle, None leaves it alone. Only
# include_return_and_main_effects and normalize are actually passed through
# to prepare_export_dialog() below - that mirrors the real pipeline, which
# never touches the other toggles from this call (create_analysis_file is
# always forced True by prepare_export_dialog itself). Left here so the
# printed BEFORE/AFTER tables show what was asked for vs what every toggle
# ended up as.
DESIRED_TOGGLES = {
    "include_return_and_main_effects": True,
    "normalize": True,
}

_RENDER_TARGETS = {
    "main": (RENDER_TARGET_MASTER, "Main"),
    "master": (RENDER_TARGET_MASTER, "Main"),
    "individual": (RENDER_TARGET_ALL_INDIVIDUAL_TRACKS, "All Individual Tracks"),
    "all_individual_tracks": (RENDER_TARGET_ALL_INDIVIDUAL_TRACKS, "All Individual Tracks"),
}


def _toggle_state_str(value):
    return "On" if value else "Off" if value is False else "?"


def _print_toggle_table(title, state):
    print(f"\n{title}")
    if state is None:
        print("  (dialog could not be read)")
        return
    for key, label in TOGGLE_LABELS.items():
        print(f"  {label:<32} {_toggle_state_str(state.toggles.get(key))}")
    print(f"  {'Rendered Track':<32} {state.rendered_track!r}")


def _print_raw_ocr(state):
    """Dump every OCR line pytesseract read, with its bounding box, verbatim."""
    print("\nRAW OCR LINES (window-relative coords)")
    if state is None or not state.lines:
        print("  (none - OCR found no text, or the dialog could not be read)")
        return
    for line in state.lines:
        print(
            f"  x0={line['x0']:>4} x1={line['x1']:>4} y0={line['y0']:>4} y1={line['y1']:>4}"
            f"  text={line['text']!r}"
        )


def _print_control_locations(state):
    """Print the absolute screen coordinates the automation would click for
    every toggle, the Rendered Track dropdown, and the Export button."""
    print("\nCONTROL LOCATIONS (absolute screen coords)")
    if state is None:
        print("  (dialog could not be read)")
        return

    for key, label in TOGGLE_LABELS.items():
        point = state.toggle_point(key)
        found = "found" if state.rows.get(key) is not None else "NOT FOUND"
        print(f"  {label:<32} {str(point):<16} ({found})")

    dropdown_point = state.rendered_track_point()
    print(f"  {RENDERED_TRACK_LABEL:<32} {str(dropdown_point):<16} "
          f"({'found' if dropdown_point else 'NOT FOUND'}, read as {state.rendered_track!r})")

    export_point = state.button_point("Export")
    print(f"  {'Export button':<32} {str(export_point):<16} "
          f"({'found' if export_point else 'NOT FOUND'} - broad OCR diagnostic; "
          f"commit uses the button's separate OCR crop)")
    verified_point = state.export_button_point()
    print(f"  {'Verified Export button':<32} {str(verified_point):<16} "
          f"({'found' if verified_point else 'NOT FOUND'} - used to commit)")

    bg_point = state.background_point()
    print(f"  {'Background reset point':<32} {str(bg_point):<16} "
          f"({'found' if bg_point else 'NOT FOUND'})")


def save_rendered_track_ocr_debug(state, stamp):
    """
    Save the exact crop used by export_window_controls.py
    to OCR the Rendered Track dropdown.

    Also saves the full dialog with the detected crop outlined.
    Does not click anything or commit an export.
    """

    if state is None:
        print("Cannot save OCR debug: export dialog was not captured.")
        return

    row = getattr(state, "rendered_track_row", None)

    if row is None:
        print("Cannot save OCR debug: Rendered Track row was not found.")
        return

    # Use the same crop-detection function as the production module.
    box = state._find_value_box(row)

    if box is None:
        print("Could not detect the Rendered Track dropdown box.")

        # Still save the full dialog for inspection.
        full_path = os.path.join(
            "export_window_debug",
            f"{stamp}_rendered_track_full.png"
        )

        os.makedirs("export_window_debug", exist_ok=True)
        state.image.save(full_path)

        print(f"Saved full dialog: {full_path}")
        return

    bx0, bx1, by0, by1 = box

    # IMPORTANT: Use the exact same slicing as the production OCR.
    crop = state.rgb[by0:by1, bx0:bx1]

    os.makedirs("debug/export_window_debug", exist_ok=True)

    crop_path = os.path.join(
        "debug/export_window_debug",
        f"{stamp}_rendered_track_crop.png"
    )

    annotated_path = os.path.join(
        "debug/export_window_debug",
        f"{stamp}_rendered_track_annotated.png"
    )

    # Save the actual OCR crop.
    if crop.size:
        Image.fromarray(crop).save(crop_path)

        print(f"\nSaved Rendered Track OCR crop: {crop_path}")
        print(f"Crop coordinates: {box}")
        print(
            f"Crop dimensions: "
            f"{crop.shape[1]} x {crop.shape[0]} pixels"
        )
    else:
        print("WARNING: Detected crop is empty.")

    # Save the full dialog with the crop outlined.
    annotated = state.rgb.copy()

    import cv2

    cv2.rectangle(
        annotated,
        (bx0, by0),
        (bx1, by1),
        (255, 0, 0),
        2
    )

    Image.fromarray(annotated).save(annotated_path)

    print(f"Saved annotated dialog: {annotated_path}")

    # Print what Tesseract reads from the saved crop.
    import pytesseract

    if crop.size:
        text = pytesseract.image_to_string(
            crop,
            config="--psm 7"
        ).strip()

        print(f"OCR result from crop: {text!r}")

def run_test():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("Activating Ableton window...")
    if win_activate_ableton() is None:
        print("Could not activate the Ableton window - is it running?")
        return
    time.sleep(0.3)

    before = capture_export_window(debug_name=f"{stamp}_before")
    save_rendered_track_ocr_debug(before, stamp)
    _print_toggle_table("BEFORE", before)
    _print_raw_ocr(before)
    _print_control_locations(before)
    if before is None:
        print("\nCould not find/read the Export dialog - make sure it is open and try again.")
        return

    target = _RENDER_TARGETS.get((DESIRED_RENDER_TARGET or "individual").lower())
    if target is None:
        print(f"\nUnknown DESIRED_RENDER_TARGET {DESIRED_RENDER_TARGET!r} - defaulting to All Individual Tracks")
        target = _RENDER_TARGETS["individual"]
    index, label = target

    print(f"\nCalling prepare_export_dialog(render_target_index={index}, render_target_label={label!r}, "
          f"include_return_and_main_effects={DESIRED_TOGGLES.get('include_return_and_main_effects')}, "
          f"normalize={DESIRED_TOGGLES.get('normalize')}) - THIS WILL COMMIT A REAL EXPORT")
    committed = prepare_export_dialog(
        index,
        label,
        include_return_and_main_effects=DESIRED_TOGGLES.get("include_return_and_main_effects"),
        normalize=DESIRED_TOGGLES.get("normalize"),
        debug_name=f"{stamp}_prepare",
    )
    print(f"prepare_export_dialog returned: {committed}")

    # The Save Audio File As dialog takes focus once committed. The AFTER
    # state is whatever was captured internally by
    # prepare_export_dialog (saved under export_window_debug/{stamp}_prepare*
    # thanks to debug_name), not a fresh capture here.
    print("\nSUMMARY")
    print(f"  Render target requested   : {DESIRED_RENDER_TARGET!r} -> {label}")
    print(f"  Toggles requested         : {DESIRED_TOGGLES}")
    print(f"  prepare_export_dialog() ok: {committed}")
    print("  (the export dialog opens Save Audio File As inside prepare_export_dialog - see "
          f"export_window_debug/{stamp}_prepare*.png for the last captures before commit)")

    report = {
        "timestamp": stamp,
        "render_target_requested": DESIRED_RENDER_TARGET,
        "render_target_resolved": label,
        "desired_toggles": DESIRED_TOGGLES,
        "before_toggles": {k: before.toggles.get(k) if before else None for k in TOGGLE_LABELS},
        "prepare_export_dialog_result": committed,
    }
    report_path = f"debug/export_window_debug/{stamp}_report.json"
    try:
        import os
        os.makedirs("debug/export_window_debug", exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport + screenshots saved under debug/export_window_debug/ (prefix {stamp})")
    except Exception as e:
        print(f"\nCould not save report: {e}")


if __name__ == "__main__":
    run_test()
