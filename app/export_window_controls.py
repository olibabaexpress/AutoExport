"""
Visual verification and control of Ableton's "Export Audio/Video" dialog.

The export dialog is driven entirely by keystrokes elsewhere in the pipeline,
which is fast but blind: there is no feedback about what actually ended up
selected. That blindness caused a production bug where the "Rendered Track"
dropdown silently drifted between projects and whole batches of stems were
never rendered.

This module closes that loop. It screenshots the dialog, OCRs it once to
locate each labelled row, and then reads the state of the On/Off toggles and
the "Rendered Track" dropdown directly off the pixels. Callers can use it to
assert the render target really is what they asked for, and to force
individual toggles (Include Return and Main Effects, Normalize, Create
Analysis File) on or off. Render Start and Render Length are located by their
labelled rows before entering the requested range.

Theme independence
------------------
Ableton ships many themes, so toggle state is never matched against a
hardcoded colour. Every theme draws an "On" toggle filled with its saturated
accent colour and an "Off" toggle in a near-neutral dark/light fill, so state
is decided by comparing the toggle's mean HSV saturation against the
saturation of the window's own background. On the default theme that
separation is roughly 2.7x background versus 1.1x background, which leaves a
wide margin either side of the threshold. This is the same relative-saturation
trick the missing-media engine uses to read Ableton's YES/NO buttons.
"""

import os
import re
import time
import difflib
import logging

import cv2
import numpy as np
import pyautogui
import pytesseract
from pywinauto import Desktop

from popup_automation_pcpatch import _get_window_rect_via_win32
from paths import DEBUG_DIR
import system_deps

system_deps.configure_tesseract()

logger = logging.getLogger(__name__)

EXPORT_WINDOW_TITLE = "Export Audio/Video"
_EXPORT_TITLE_RE = re.compile(r"export\s+audio", re.I)

# Position of each entry in the Export dialog's "Rendered Track" dropdown,
# together with the text that entry shows once selected. Kept here rather than
# in export_automation so the keyboard navigation and the visual verification
# that checks it can never disagree about what index means what.
RENDER_TARGET_MASTER = 0
RENDER_TARGET_ALL_INDIVIDUAL_TRACKS = 1

RENDER_TARGET_NAMES = {
    RENDER_TARGET_MASTER: ("main", "master"),
    RENDER_TARGET_ALL_INDIVIDUAL_TRACKS: ("allindividualtracks",),
}

# The dialog's On/Off toggles, keyed by the name callers use and mapped to the
# label text that identifies their row on screen.
TOGGLE_LABELS = {
    "include_return_and_main_effects": "Include Return and Main Effects",
    "render_as_loop": "Render as Loop",
    "convert_to_mono": "Convert to Mono",
    "normalize": "Normalize",
    "create_analysis_file": "Create Analysis File",
    "encode_pcm": "Encode PCM",
    "encode_mp3": "Encode MP3",
    "create_video": "Create Video",
}

RENDERED_TRACK_LABEL = "Rendered Track"
_RENDERED_TRACK_LABEL_RE = re.compile(r"rendered\s*track", re.I)

# A toggle counts as On when its fill is this many times more saturated than
# the window background. The absolute floor stops a perfectly neutral theme
# background (saturation ~0) from making the ratio meaningless.
_ON_SATURATION_RATIO = 1.8
_ON_SATURATION_FLOOR = 40.0

# Toggle half-width as a fraction of window width, measured from the dialog
# (88px wide in a 571px-wide window). Only used to size the sample region.
_TOGGLE_HALF_WIDTH_RATIO = 0.077
_ROW_HALF_HEIGHT_RATIO = 0.008

_DEBUG_DIR = os.path.join(DEBUG_DIR, "export_window_debug")


def _normalize(text):
    """Lowercase and strip everything but letters and digits, for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find_export_window():
    """Return (hwnd, (left, top, width, height)) for the export dialog, or None."""
    try:
        windows = Desktop(backend="win32").windows()
    except Exception as e:
        logger.debug(f"Could not enumerate windows: {e}")
        return None

    for win in windows:
        try:
            title = win.window_text()
        except Exception:
            continue
        if title and _EXPORT_TITLE_RE.search(title):
            try:
                return win.handle, _get_window_rect_via_win32(win.handle)
            except Exception as e:
                logger.debug(f"Could not read export window rect: {e}")
                return None
    return None


def _ocr_lines(image):
    """OCR the image once and return one entry per text line with its bounding box."""
    try:
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception as e:
        logger.warning(f"OCR of export window failed: {e}")
        return []

    grouped = {}
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf < 40:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append(
            (data["left"][i], data["top"][i], data["width"][i], data["height"][i], text)
        )

    lines = []
    for words in grouped.values():
        words.sort(key=lambda w: w[0])
        x0 = min(w[0] for w in words)
        x1 = max(w[0] + w[2] for w in words)
        y0 = min(w[1] for w in words)
        y1 = max(w[1] + w[3] for w in words)
        lines.append({
            "text": " ".join(w[4] for w in words),
            "x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "yc": (y0 + y1) // 2,
        })
    lines.sort(key=lambda ln: ln["yc"])
    return lines


def _find_label_row(lines, label, width, min_score=0.72):
    """Find the OCR line in the left-hand label column that best matches `label`."""
    target = _normalize(label)
    best, best_score = None, 0.0
    for line in lines:
        if line["x0"] > 0.5 * width:
            continue  # labels are left-aligned; values/toggles live on the right
        norm = _normalize(line["text"])
        if not norm:
            continue
        score = 1.0 if target in norm else difflib.SequenceMatcher(None, target, norm).ratio()
        if score > best_score:
            best, best_score = line, score
    return best if best_score >= min_score else None


def _toggle_band(lines, width):
    """
    Work out the horizontal span of the toggle column.

    Every toggle draws its "On"/"Off" caption centred in the box, so when at
    least two of those captions are readable the column is derived from them -
    which self-calibrates for window size and DPI. The ratio-based fallback is
    only used when OCR reads none of them (e.g. every toggle is On and the dark
    caption on the accent fill is too low-contrast to register).
    """
    centres = [
        (line["x0"] + line["x1"]) / 2.0
        for line in lines
        if _normalize(line["text"]) in ("on", "off") and line["x0"] > 0.5 * width
    ]
    half = _TOGGLE_HALF_WIDTH_RATIO * width
    if len(centres) >= 2:
        centre = sum(centres) / len(centres)
    else:
        centre = 0.84 * width
    return int(round(centre - half)), int(round(centre + half))


class ExportWindowState:
    """A single snapshot of the export dialog: its pixels, its OCR and its decoded controls."""

    def __init__(self, rect, image, lines):
        self.rect = rect                      # (left, top, width, height) on screen
        self.image = image                    # PIL screenshot of the dialog
        self.lines = lines
        self.rgb = np.array(image.convert("RGB"))
        self.height, self.width = self.rgb.shape[:2]
        self.row_half = max(4, int(self._row_half_height()))
        self.band = _toggle_band(lines, self.width)
        self.rows = {}                        # toggle key -> matched OCR line
        self.toggles = {}                     # toggle key -> True / False / None
        self.saturations = {}                 # toggle key -> measured mean saturation
        self.background_saturation = 0.0
        self.rendered_track = None
        self._decode()

    def _row_half_height(self):
        return _ROW_HALF_HEIGHT_RATIO * self.height

    def _decode(self):
        hsv = cv2.cvtColor(self.rgb, cv2.COLOR_RGB2HSV)
        saturation = hsv[:, :, 1].astype(float)

        # Neutral reference taken from the dialog's own left margin, which is
        # always plain background regardless of theme.
        margin = saturation[:, int(0.02 * self.width):int(0.06 * self.width)]
        self.background_saturation = float(np.mean(margin)) if margin.size else 0.0
        threshold = max(self.background_saturation * _ON_SATURATION_RATIO, _ON_SATURATION_FLOOR)

        x0, x1 = self.band
        x0 = max(0, x0)
        x1 = min(self.width, x1)

        for key, label in TOGGLE_LABELS.items():
            row = _find_label_row(self.lines, label, self.width)
            if row is None:
                self.toggles[key] = None
                continue
            self.rows[key] = row
            y0 = max(0, row["yc"] - self.row_half)
            y1 = min(self.height, row["yc"] + self.row_half)
            box = saturation[y0:y1, x0:x1]
            if box.size == 0:
                self.toggles[key] = None
                continue
            mean_sat = float(np.mean(box))
            self.saturations[key] = mean_sat
            self.toggles[key] = mean_sat >= threshold

        self.rendered_track_row = None   # OCR line for the "Rendered Track" label
        self.rendered_track_value_box = None  # (x0, x1, y0, y1) of the value/dropdown text, if read
        self.rendered_track = self._decode_rendered_track()

    def _find_value_box(self, row, min_col_density=0.6):
        """
        Locate the Rendered Track dropdown using its known
        position within Ableton's Export Audio/Video dialog.

        Returns (x0, x1, y0, y1) in image-relative pixels.
        """

        # Dropdown occupies the right-hand portion of the
        # Rendered Track row.
        x0 = int(0.53 * self.width)
        x1 = int(0.92 * self.width)

        # Centre the crop vertically on the detected label row.
        half_height = max(12, int(0.015 * self.height))

        y0 = max(0, row["yc"] - half_height)
        y1 = min(self.height, row["yc"] + half_height)

        return x0, x1, y0, y1
    
    def _decode_rendered_track(self):
        row = _find_label_row(
            self.lines,
            RENDERED_TRACK_LABEL,
            self.width
        )

        if row is None:
            return None

        self.rendered_track_row = row

        # First, try the darker dropdown box.
        # However, do not trust arbitrary OCR output from this crop.
        box = self._find_value_box(row)

        if box is not None:
            bx0, bx1, by0, by1 = box

            # Include the right and bottom boundaries.
            crop = self.rgb[by0:by1 + 1, bx0:bx1 + 1]

            if crop.size:
                try:
                    boxed_text = pytesseract.image_to_string(
                        crop,
                        config="--psm 7"
                    ).strip()
                except Exception:
                    boxed_text = ""

                boxed_text = re.sub(
                    r"[^\w\s/()\-]+",
                    " ",
                    boxed_text
                )

                boxed_text = re.sub(
                    r"\s+",
                    " ",
                    boxed_text
                ).strip()

                # Only accept the crop if it actually resembles
                # one of Ableton's known render-target labels.
                norm = _normalize(boxed_text)

                known_target = any(
                    name in norm
                    for names in RENDER_TARGET_NAMES.values()
                    for name in names
                )

                if known_target:
                    self.rendered_track_value_box = box
                    return boxed_text

                # Otherwise, discard the crop result and fall
                # through to the original full-row OCR.
                logger.debug(
                    "Ignoring unrecognised Rendered Track crop "
                    f"OCR: {boxed_text!r}; "
                    f"original row: {row['text']!r}"
                )


        # Ableton's label and its current value are often close enough
        # together that pytesseract groups them into a single OCR line (e.g.
        # "Rendered Track Main"), rather than the label and value each being
        # their own line. Try stripping the label text back out of the row
        # first - if that leaves something behind, that's the value.
        remainder = _RENDERED_TRACK_LABEL_RE.sub("", row["text"], count=1).strip()
        if remainder:
            self.rendered_track_value_box = (row["x0"], row["x1"], row["y0"], row["y1"])
            text = re.sub(r"[^\w\s/()\-]+", " ", remainder)
            return re.sub(r"\s+", " ", text).strip()

        # Label and value were read as separate OCR lines - look for the
        # value on the same row, to the right of the label.
        tolerance = max(12, (row["y1"] - row["y0"]))
        values = [
            line for line in self.lines
            if line is not row
            and line["x0"] > 0.45 * self.width
            and abs(line["yc"] - row["yc"]) <= tolerance
        ]
        if not values:
            return None
        values.sort(key=lambda ln: ln["x0"])
        self.rendered_track_value_box = (
            min(ln["x0"] for ln in values),
            max(ln["x1"] for ln in values),
            min(ln["y0"] for ln in values),
            max(ln["y1"] for ln in values),
        )
        text = " ".join(ln["text"] for ln in values)
        # Drop the dropdown's arrow glyph, which OCR renders as a stray symbol.
        text = re.sub(r"[^\w\s/()\-]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def background_point(self):
        """
        Absolute screen coordinates of a point on the dialog that is always
        blank background, regardless of theme or which controls are present.

        Clicking here resets keyboard focus to the dialog itself, which is
        what makes a fixed Tab count afterwards reliable - a fixed Tab count
        is only ever valid starting from a known-reset focus state, never
        from whatever control was last clicked (that's what previously
        mis-set the Sample Rate combo when a stale Tab count was used after
        toggles had already been clicked with the mouse).
        """
        left, top = self.rect[0], self.rect[1]
        # Same left-margin column already used as the "definitely background"
        # sample region for toggle colour detection.
        x = int(0.03 * self.width)
        y = int(0.5 * self.height)
        return int(left + x), int(top + y)

    def render_time_point(self, label):
        row = _find_label_row(self.lines, label, self.width)
        if row is None:
            return None
        return self.rect[0] + int(0.77 * self.width), self.rect[1] + row["yc"]

    def rendered_track_point(self):
        """
        Absolute screen coordinates to click the "Rendered Track" dropdown, or
        None if its row/value could not be read.

        Anchoring the dropdown by clicking its actual on-screen position (as
        opposed to blindly pressing Tab a fixed number of times from whatever
        control currently has focus) is what makes re-selecting it safe to
        retry after other controls have already been clicked - a fixed Tab
        count is only valid from the dialog's pristine just-opened focus.
        """
        left, top = self.rect[0], self.rect[1]
        if self.rendered_track_value_box is not None:
            x0, x1, y0, y1 = self.rendered_track_value_box
            return int(left + (x0 + x1) / 2), int(top + (y0 + y1) / 2)
        if self.rendered_track_row is not None:
            # Value text wasn't read (e.g. blank/unrecognised), but the label
            # was - the dropdown itself sits to the right of it at the same
            # row height, in the same column the On/Off toggles occupy.
            row = self.rendered_track_row
            x0, _x1 = self.band
            return int(left + x0), int(top + row["yc"])
        return None

    def toggle_point(self, key):
        """Absolute screen coordinates to click for a toggle, or None if its row wasn't found."""
        row = self.rows.get(key)
        if row is None:
            return None
        left, top = self.rect[0], self.rect[1]
        x0, x1 = self.band
        return int(left + (x0 + x1) / 2), int(top + row["yc"])

    def export_button_point(self):
        """Locate the Export button in its own crop, excluding adjacent Cancel."""
        x0, x1 = int(0.26 * self.width), int(0.50 * self.width)
        y0, y1 = int(0.924 * self.height), int(0.966 * self.height)
        crop = self.image.crop((x0, y0, x1, y1))
        text = pytesseract.image_to_string(crop, config="--psm 7")
        # cv2.imwrite("debug/export_window_debug/export_button_crop.png", cv2.cvtColor(np.array(crop), cv2.COLOR_RGB2BGR))

        if _normalize(text) != "export":
            return None
        return (self.rect[0] + int(0.38 * self.width),
                self.rect[1] + int(0.946 * self.height))

    def button_point(self, name="Export"):
        """Absolute screen coordinates of a bottom-row dialog button, or None.

        The broad OCR line may merge Export and Cancel, so this is only
        retained for diagnostics. Use export_button_point to commit.
        """
        target = _normalize(name)
        left, top = self.rect[0], self.rect[1]
        fallback = None
        for line in self.lines:
            if line["yc"] < 0.85 * self.height:
                continue
            norm = _normalize(line["text"])
            if norm == target:
                return int(left + (line["x0"] + line["x1"]) / 2), int(top + line["yc"])
            if fallback is None and target in norm:
                raw = line["text"] or ""
                idx = raw.lower().find(name.lower())
                if idx == -1:
                    continue
                width_px = line["x1"] - line["x0"]
                frac_start = idx / max(1, len(raw))
                frac_end = (idx + len(name)) / max(1, len(raw))
                x0 = line["x0"] + frac_start * width_px
                x1 = line["x0"] + frac_end * width_px
                fallback = (int(left + (x0 + x1) / 2), int(top + line["yc"]))
        return fallback

    def matches_render_target(self, index):
        """True/False if the dropdown text was read, None if it could not be read.

        Match is fuzzy: OCR sometimes garbles or drops a character or two, but
        "Master" and "All Individual Tracks" (and the other render targets)
        are different enough in length/content that a generous fuzzy ratio
        still can't confuse them, so an exact substring match is tried first
        and a fuzzy best-match-wins fallback covers near-misses.
        """
        if not self.rendered_track:
            return None
        norm = _normalize(self.rendered_track)
        if not norm:
            return None
        if any(name in norm for name in RENDER_TARGET_NAMES.get(index, ())):
            return True
        best_index, best_score = None, 0.0
        for candidate_index, names in RENDER_TARGET_NAMES.items():
            for name in names:
                score = difflib.SequenceMatcher(None, name, norm).ratio()
                if score > best_score:
                    best_index, best_score = candidate_index, score
        if best_score >= 0.6:
            return best_index == index
        return False

    def describe(self):
        parts = [f"Rendered Track={self.rendered_track!r}"]
        for key in TOGGLE_LABELS:
            state = self.toggles.get(key)
            parts.append(f"{key}={'On' if state else 'Off' if state is False else '?'}")
        return ", ".join(parts)


def capture_export_window(debug_name=None):
    """Screenshot and decode the export dialog. Returns an ExportWindowState or None."""
    found = find_export_window()
    if not found:
        logger.warning("Export window not found - skipping visual verification")
        return None

    _hwnd, (left, top, width, height) = found
    screen_w, screen_h = pyautogui.size()
    clamped_left, clamped_top = max(0, left), max(0, top)
    clamped_w = min(width - (clamped_left - left), screen_w - clamped_left)
    clamped_h = min(height - (clamped_top - top), screen_h - clamped_top)
    if clamped_w < 100 or clamped_h < 100:
        logger.warning(f"Export window bounds look wrong ({width}x{height}) - skipping visual verification")
        return None

    try:
        image = pyautogui.screenshot(region=(clamped_left, clamped_top, clamped_w, clamped_h))
    except Exception as e:
        logger.warning(f"Could not screenshot export window: {e}")
        return None

    state = ExportWindowState((clamped_left, clamped_top, clamped_w, clamped_h), image, _ocr_lines(image))
    if debug_name:
        _save_debug_image(state, debug_name)
    return state


def _save_debug_image(state, debug_name):
    """Write an annotated copy of the screenshot so failures can be eyeballed later."""
    try:
        os.makedirs(_DEBUG_DIR, exist_ok=True)
        annotated = cv2.cvtColor(state.rgb, cv2.COLOR_RGB2BGR).copy()
        x0, x1 = state.band
        for key, row in state.rows.items():
            on = state.toggles.get(key)
            colour = (0, 200, 0) if on else (0, 0, 220) if on is False else (0, 200, 200)
            cv2.rectangle(annotated, (x0, row["yc"] - state.row_half),
                          (x1, row["yc"] + state.row_half), colour, 1)
            cv2.putText(annotated, f"{key}={on}", (6, row["yc"] + 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, colour, 1)
        cv2.imwrite(os.path.join(_DEBUG_DIR, f"{debug_name}.png"), annotated)
    except Exception as e:
        logger.debug(f"Could not save export window debug image: {e}")


def apply_toggles(desired, attempts=2, debug_name=None, state=None):
    """
    Force the given toggles to the requested states by clicking them.

    `desired` maps TOGGLE_LABELS keys to True/False; entries whose value is
    None are ignored, so callers can pass "leave this alone" straight through.
    A caller that has already captured the dialog can pass that snapshot in as
    `state` to avoid a second OCR pass.

    Returns a result dict, or None if the dialog could not be read at all (in
    which case the caller should carry on with its blind keyboard behaviour
    rather than abort the export).
    """
    wanted = {k: v for k, v in (desired or {}).items() if v is not None}

    if state is None:
        state = capture_export_window(debug_name=debug_name)
    if state is None:
        return None

    changed, failed = [], []
    for attempt in range(max(1, attempts)):
        pending = [k for k, v in wanted.items() if state.toggles.get(k) is not v]
        pending = [k for k in pending if state.rows.get(k) is not None]
        if not pending:
            break

        for key in pending:
            point = state.toggle_point(key)
            if point is None:
                continue
            logger.info(
                f"Export toggle '{TOGGLE_LABELS[key]}' is "
                f"{'On' if state.toggles.get(key) else 'Off'}, want {'On' if wanted[key] else 'Off'} - clicking"
            )
            pyautogui.click(point)
            changed.append(key)
            time.sleep(0.15)

        time.sleep(0.3)
        refreshed = capture_export_window(debug_name=f"{debug_name}_retry{attempt}" if debug_name else None)
        if refreshed is None:
            break
        state = refreshed

    for key, value in wanted.items():
        actual = state.toggles.get(key)
        if actual is None:
            logger.warning(f"Could not read export toggle '{TOGGLE_LABELS[key]}' - leaving it as-is")
            failed.append(key)
        elif actual is not value:
            logger.error(
                f"Export toggle '{TOGGLE_LABELS[key]}' is still "
                f"{'On' if actual else 'Off'} after {attempts} attempt(s), wanted {'On' if value else 'Off'}"
            )
            failed.append(key)

    return {
        "state": state,
        "changed": sorted(set(changed)),
        "failed": failed,
        "ok": not failed,
    }


def verify_render_target(index, label, debug_name=None):
    """
    Check the "Rendered Track" dropdown really shows `index`.

    Returns True (confirmed), False (definitely wrong) or None (could not be
    read - the caller should treat that as inconclusive rather than a failure).
    """
    state = capture_export_window(debug_name=debug_name)
    if state is None:
        return None
    result = state.matches_render_target(index)
    if result is True:
        logger.info(f"Verified render target is '{state.rendered_track}' (wanted {label})")
    elif result is False:
        logger.error(f"Render target reads '{state.rendered_track}' but should be '{label}'")
    else:
        logger.warning(f"Could not read the Rendered Track dropdown (wanted {label})")
    return result


def click_export_button(state=None):
    """Click the dialog's Export button. Returns True if it was found and clicked."""
    if state is None:
        state = capture_export_window()
    if state is None:
        return False
    point = state.button_point("Export")
    if point is None:
        logger.warning("Could not locate the Export button")
        return False
    pyautogui.click(point)
    logger.info("Clicked the Export button")
    return True
