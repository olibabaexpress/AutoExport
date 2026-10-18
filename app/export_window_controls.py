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
from PIL import Image
from pywinauto import Desktop

from popup_automation import _get_window_rect_via_win32
from paths import DEBUG_DIR
import system_deps as system_deps

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

# Tokens that are just the dropdown-arrow glyph misread by OCR. These are
# stripped from the trailing end of an OCR'd dropdown value.
_DROPDOWN_ARROW_TOKENS = {"v", "vv", "w", "y", "¥", "\\", "/", "|"}


def _strip_dropdown_arrow(text):
    """Remove trailing dropdown-arrow glyphs from an OCR'd dropdown value.

    OCR frequently reads the little ▼ triangle as ``v``, ``Vv``, ``¥`` etc.
    We drop whole trailing whitespace-delimited tokens that are nothing but
    arrow artifacts, so a genuine value ending in a letter (e.g. ``WAV``) is
    never harmed -- only a standalone arrow token is removed.
    """
    tokens = text.split()
    while tokens and tokens[-1].lower() in _DROPDOWN_ARROW_TOKENS:
        tokens.pop()
    return " ".join(tokens) if tokens else text


def _match_dropdown_option(ocr_text, options):
    """Return the option from `options` that best matches `ocr_text`, or None.

    Comparison is case-insensitive and ignores non-alphanumeric characters, so
    "POW-r 1" matches an OCR read of "POWr1"/"pow-r 1" etc. Returns None when
    nothing in `options` is found in `ocr_text` (or ocr_text is empty).
    """
    if not ocr_text:
        return None
    norm_ocr = "".join(ch for ch in ocr_text.lower() if ch.isalnum())
    if not norm_ocr:
        return None
    for opt in options:
        norm_opt = "".join(ch for ch in opt.lower() if ch.isalnum())
        if norm_opt and norm_opt in norm_ocr:
            return opt
    return None


def _ocr_value_pixels(gray, debug_crops=False, tag=None):
    """OCR a grayscale crop of dropdown value text and return the best line.

    Ableton draws dropdown values as light text on a dark fill, so the
    inverted / Otsu-inverted variants are the ones that actually read. To keep
    this fast we try only the two working variants first and short-circuit on
    the first confident non-empty read (normally 1 OCR pass); the full variant
    sweep is reserved for `debug_crops` mode, which also dumps the crops.
    """
    variants = {}
    variants["inv"] = cv2.bitwise_not(gray)
    try:
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants["otsu_inv"] = cv2.bitwise_not(otsu)
    except Exception:
        pass
    if debug_crops:
        # Extra variants purely for diagnosing a crop that won't read.
        variants["gray"] = gray
        try:
            variants["otsu"] = otsu
        except NameError:
            pass
        _, thr = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY)
        variants["thr"] = thr
        variants["thr_inv"] = cv2.bitwise_not(thr)

    scaled = {
        name: cv2.resize(img, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for name, img in variants.items()
    }

    if debug_crops and tag:
        try:
            debug_dir = os.path.join("debug", "export_window_debug")
            os.makedirs(debug_dir, exist_ok=True)
            for name, img in scaled.items():
                cv2.imwrite(os.path.join(debug_dir, f"{tag}_{name}.png"), img)
        except Exception:
            pass

    # Non-debug: single line mode is enough and short-circuits fast. Debug tries
    # every PSM so a crop that only reads under a sparse/segmentation mode is
    # still captured.
    psm_modes = ("--psm 7",) if not debug_crops else ("--psm 7", "--psm 8", "--psm 6", "--psm 13")
    for name, img in scaled.items():
        rgb = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        for psm in psm_modes:
            try:
                data = pytesseract.image_to_data(
                    Image.fromarray(rgb),
                    output_type=pytesseract.Output.DICT,
                    config=psm,
                )
            except Exception:
                continue
            words, confs = [], []
            for i in range(len(data["text"])):
                text = (data["text"][i] or "").strip()
                if text:
                    try:
                        conf = float(data["conf"][i])
                    except (TypeError, ValueError):
                        conf = -1.0
                    words.append(text)
                    confs.append(max(conf, 0.0))
            if not words:
                continue
            avg_conf = sum(confs) / len(confs)
            line = _strip_dropdown_arrow(" ".join(words))
            # First confident read wins; only keep hunting if it was garbage.
            if line and avg_conf >= 40:
                return line
    return None


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

# The dialog's dropdown menus (e.g. Sample Rate, Bit Depth, File Type, Dither),
# keyed by the name callers use and mapped to the label text that identifies
# their row on screen.
DROPDOWN_LABELS = {
    "sample_rate": "Sample Rate",
    "bit_depth": "Bit Depth",
    "file_type": "File Type",
    "dither": "Dither Options",
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
            # Per-word (x0, x1, text) kept so callers can work out where the
            # LABEL portion of a grouped line ends (e.g. "Sample Rate 22050 v"),
            # separately from the trailing value/arrow words.
            "words": [(w[0], w[0] + w[2], w[4]) for w in words],
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


def _label_right_edge(line, label):
    """X-coordinate where the `label` text ends within `line`.

    When OCR groups the label and its value onto one line ("Sample Rate 22050
    v"), the line's overall x1 extends past the label to cover the value. To
    scan for the dropdown box (which sits immediately right of the LABEL, not
    right of the value) we need the label's own right edge. This matches the
    label's words against the line's per-word boxes and returns the right edge
    of the last word that belongs to the label. Falls back to the line x0 +
    a proportional estimate, then to the line x1, if per-word data is missing.
    """
    words = line.get("words") or []
    if not words:
        # No per-word data: estimate by character fraction of the label vs line.
        raw = line.get("text", "")
        total = max(1, len(raw))
        frac = min(1.0, len(label) / total)
        return int(line["x0"] + frac * (line["x1"] - line["x0"]))

    label_words = label.lower().split()
    if not label_words:
        return line["x0"]

    # Walk the line's words left-to-right, consuming label words in order. The
    # right edge of the last consumed word is where the label ends.
    last_right = line["x0"]
    li = 0
    for wx0, wx1, wtext in words:
        if li >= len(label_words):
            break
        wn = wtext.lower()
        # Skip pure arrow/punctuation tokens when advancing, but if we hit the
        # value before finishing the label, stop at the last matched word.
        if not wn:
            continue
        if label_words[li] in wn or wn in label_words[li]:
            last_right = wx1
            li += 1
    return int(last_right)


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

    def __init__(self, rect, image, lines, debug_crops=False):
        self.rect = rect                      # (left, top, width, height) on screen
        self.image = image                    # PIL screenshot of the dialog
        self.lines = lines
        self.debug_crops = debug_crops        # dump preprocessed dropdown crops?
        self.rgb = np.array(image.convert("RGB"))
        self.height, self.width = self.rgb.shape[:2]
        self.row_half = max(4, int(self._row_half_height()))
        self.band = _toggle_band(lines, self.width)
        self.rows = {}                        # toggle key -> matched OCR line
        self.toggles = {}                     # toggle key -> True / False / None
        self.saturations = {}                 # toggle key -> measured mean saturation
        self.dropdowns = {}                   # dropdown key -> matched OCR line
        self.dropdown_value_boxes = {}        # dropdown key -> (x0, x1, y0, y1) box
        self.dropdown_values = {}             # dropdown key -> OCR'd label text
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

        for key, label in DROPDOWN_LABELS.items():
            row, value_box, value_text = self.decode_dropdown(key, label)
            if row is None:
                logger.warning(
                    f"Dropdown '{key}' ('{label}'): no OCR row found for label"
                )
                # Diagnostic: surface everything pytesseract read, so we can see
                # whether the label is being OCR'd but filtered out, or not OCR'd
                # at all.
                other = [ln["text"] for ln in self.lines]
                logger.warning(f"  OCR lines in this dialog: {other}")
                self.dropdowns[key] = None
                continue
            self.dropdowns[key] = row
            if value_box is not None:
                self.dropdown_value_boxes[key] = value_box
            if value_text:
                logger.debug(
                    f"Dropdown '{key}' ('{label}'): value box OCR'd as {value_text!r}"
                )

        self.rendered_track_row = None   # OCR line for the "Rendered Track" label
        self.rendered_track_value_box = None  # (x0, x1, y0, y1) of the value/dropdown text, if read
        self.rendered_track = self._decode_rendered_track()

    def _find_dropdown_value_box(self, row, x0_ratio=0.53, x1_ratio=0.92,
                                  min_height_ratio=0.015):
        """Fallback box helper (used by the Rendered Track decoder)."""
        x0 = int(x0_ratio * self.width)
        x1 = int(x1_ratio * self.width)
        half_height = max(12, int(min_height_ratio * self.height))
        y0 = max(0, row["yc"] - half_height)
        y1 = min(self.height, row["yc"] + half_height)
        return x0, x1, y0, y1

    def decode_dropdown(self, key, label, min_score=0.72):
        """Find a dropdown's label row via OCR, then locate the dropdown value
        box to the right of that label.

        Returns (row, value_box, value_text) where:
          - row        is the matched OCR line (or None if the label wasn't found)
          - value_box  is (x0, x1, y0, y1) in image-relative pixels (or None)
          - value_text is the OCR'd text inside the value box (or None)

        Two sources of the value are tried:
          1. Extract the value from the same OCR line as the label (label and
             value are often grouped into one line, e.g. "Sample Rate 44100 Hz")
          2. Crop the region to the right of the label row and OCR it with
             pytesseract.
        """
        row = _find_label_row(self.lines, label, self.width, min_score=min_score)
        if row is None:
            return None, None, None

        self.dropdowns[key] = row

        # ALWAYS locate the dropdown box (the darker region right of the label).
        # We need its coordinates to click the dropdown, so it must be found
        # regardless of whether the value can be read off the same OCR line -
        # otherwise a readable same-line value would leave us with no box to
        # click. Scan from the LABEL's right edge, not the whole line's x1
        # (which now reaches past the box because OCR groups "Sample Rate
        # 22050 v" into one line).
        scan_start_x = _label_right_edge(row, label)
        box = self._find_dropdown_box_by_darkness(row, scan_start_x=scan_start_x)
        if box is not None:
            self.dropdown_value_boxes[key] = box

        # 1) Prefer reading the value from the same OCR line as the label (label
        #    and value are often grouped into one line, e.g. "Sample Rate 44100").
        value_text = None
        remainder = re.sub(re.escape(label), "", row["text"], count=1).strip()
        if remainder:
            cleaned = re.sub(r"[^\w\s/()\-]+", " ", remainder)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                value_text = cleaned

        # 2) Fall back to OCRing the box's contents if the same-line read failed.
        if not value_text and box is not None:
            bx0, bx1, by0, by1 = box
            ocr = self._ocr_box_value(bx0, bx1, by0, by1)
            if ocr:
                value_text = ocr

        if value_text:
            self.dropdown_values[key] = value_text

        return row, box, value_text

    def _ocr_box_value(self, x0, x1, y0, y1):
        """OCR the text inside a dropdown box (thin wrapper over _ocr_value_pixels).

        Crops the box region from the stored screenshot and delegates to the
        shared OCR helper, which short-circuits on the first confident read.
        Debug-crop dumping is preserved via the `tag` argument.
        """
        pad = 3
        ph, pw = self.rgb.shape[:2]
        py0 = max(0, y0 - pad)
        py1 = min(ph - 1, y1 + pad)
        px0 = max(0, x0 - pad)
        px1 = min(pw - 1, x1 + pad)
        crop = self.rgb[py0:py1 + 1, px0:px1 + 1]
        if crop.size == 0:
            return None

        gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        tag = f"dd_{int(x0)}_{int(y0)}_{int(x1)}_{int(y1)}" if self.debug_crops else None
        return _ocr_value_pixels(gray, debug_crops=self.debug_crops, tag=tag)

    def _find_dropdown_box_by_darkness(
        self,
        row,
        x_offset_px=6,
        scan_width_pct=0.95,
        bg_ratio=0.80,
        scan_start_x=None,
    ):
        """Locate a dropdown to the right of its OCR-detected label.

        `scan_start_x` overrides where the rightward dark-region scan begins.
        When the value is grouped onto the label's OCR line, the label's own
        right edge (not the line's x1) is the correct place to start scanning,
        otherwise the box is skipped past.
        """

        rgb = self.rgb
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        value_channel = hsv[:, :, 2].astype(float)

        # Use the dialog background as the brightness reference.
        margin = value_channel[
            :,
            int(0.02 * self.width):int(0.06 * self.width)
        ]
        bg_value = float(np.mean(margin)) if margin.size else 0.0

        # Dropdown interiors and borders should be darker than the background.
        threshold = bg_value * bg_ratio

        # Search the remainder of the row, including the far-right controls.
        if scan_start_x is not None:
            x0 = max(0, int(scan_start_x) + x_offset_px)
        else:
            x0 = max(0, row["x1"] + x_offset_px)
        x1 = min(self.width, int(self.width * scan_width_pct))

        half_height = max(12, int(0.025 * self.height))
        y0 = max(0, row["yc"] - half_height)
        y1 = min(self.height, row["yc"] + half_height)

        if x1 <= x0 or y1 <= y0:
            return None

        band = value_channel[y0:y1, x0:x1]

        if band.size == 0:
            return None

        dark_mask = (band < threshold).astype(np.uint8) * 255

        # Close small gaps in the dropdown's border.
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
        dark_mask = cv2.morphologyEx(
            dark_mask, cv2.MORPH_CLOSE, kernel
        )

        num_labels, labels, stats, _ = (
            cv2.connectedComponentsWithStats(dark_mask, connectivity=8)
        )

        candidates = []

        for label_id in range(1, num_labels):
            left = stats[label_id, cv2.CC_STAT_LEFT]
            top = stats[label_id, cv2.CC_STAT_TOP]
            width = stats[label_id, cv2.CC_STAT_WIDTH]
            height = stats[label_id, cv2.CC_STAT_HEIGHT]
            area = stats[label_id, cv2.CC_STAT_AREA]

            # Reject tiny components and components that cannot be controls.
            if width < 35 or height < 12 or area < 100:
                continue

            # Prefer a rectangular control aligned with the label row.
            box_yc = y0 + top + height / 2
            if abs(box_yc - row["yc"]) > half_height:
                continue

            candidates.append(
                (area, x0 + left, y0 + top,
                x0 + left + width - 1, y0 + top + height - 1)
            )

        if not candidates:
            return None

        # Prefer the widest plausible control, not merely the darkest component.
        _, bx0, by0, bx1, by1 = max(
            candidates,
            key=lambda item: item[3] - item[1]
        )

        return bx0, bx1, by0, by1


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
        box = self._find_dropdown_value_box(row)

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

    def dropdown_point(self, key):
        """Absolute screen coordinates to click a named dropdown, or None if unreadable.

        `key` is one of the DROPDOWN_LABELS keys (sample_rate, bit_depth,
        file_type, dither).
        """
        if key not in self.dropdown_value_boxes:
            return None
        x0, x1, y0, y1 = self.dropdown_value_boxes[key]
        left, top = self.rect[0], self.rect[1]
        return int(left + (x0 + x1) / 2), int(top + (y0 + y1) / 2)

    def dropdown_box(self, key):
        """Image-relative (x0, x1, y0, y1) box of a dropdown, or None."""
        return self.dropdown_value_boxes.get(key)

    def dropdown_option_point(self, key, option_index, num_options):
        """Screen coordinate to click option `option_index` in an OPEN dropdown menu.

        Once the dropdown is clicked, its menu pops open directly below the
        dropdown box. The menu is the same width as the box and its height is
        box_height * num_options, with the options stacked top-to-bottom in the
        caller's known display order (e.g. the TARGETS order). So option i is
        centred vertically at box_bottom + (i + 0.5) * box_height and
        horizontally at the box's centre.

        Returns (screen_x, screen_y) or None if the box is unknown / the
        requested index is out of range.
        """
        box = self.dropdown_value_boxes.get(key)
        if box is None or num_options <= 0:
            return None
        if option_index < 0 or option_index >= num_options:
            return None
        x0, x1, y0, y1 = box
        box_height = (y1 - y0) + 1
        left, top = self.rect[0], self.rect[1]
        cx = left + (x0 + x1) / 2.0
        # Menu opens below the box; each option slot is one box-height tall.
        cy = top + y1 + 1 + (option_index + 0.5) * box_height
        return int(cx), int(cy)

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
        for key in DROPDOWN_LABELS:
            value = self.dropdowns.get(key)
            parts.append(f"{key}={value if value is not None else '?'}")
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


def capture_ocr_log(debug_name):
    """Capture the export dialog and save OCR data to a text file, for
    diagnosing which labels are (or aren't) being read, and at what
    confidence.

    This is the first diagnostic step when a dropdown (e.g. Sample Rate,
    Bit Depth) isn't being detected: it shows the raw pytesseract output,
    including the confidence score that `_ocr_lines` uses to filter out
    low-confidence words (currently `conf < 40`).
    """
    state = capture_export_window(debug_name=debug_name)
    if state is None:
        return None
    lines_path = os.path.join(_DEBUG_DIR, f"{debug_name}_ocr_lines.txt")
    try:
        with open(lines_path, "w") as f:
            f.write(f"# Full OCR data from pytesseract.image_to_data()\n")
            f.write(f"# Filtered to keep only lines (label + value, grouped by block/par/line)\n")
            f.write(f"# Filter threshold: confidence >= 40\n")
            f.write(f"# Required columns: block, par, line, left, top, width, height, conf, text\n")
            for line in state.lines:
                f.write(
                    f"block=0 par=0 line=0 left={line['x0']:4d} top={line['y0']:3d} "
                    f"width={line['x1']-line['x0']:3d} height={line['y1']-line['y0']:3d} "
                    f"conf=100 (grouped) text={line['text']!r}\n"
                )
            f.write(f"# Total OCR lines: {len(state.lines)}\n")
        logger.info(f"Saved OCR lines: {lines_path}")
    except Exception as e:
        logger.debug(f"Could not save OCR lines: {e}")
    return state


def capture_dropdown_debug(debug_name):
    """Capture the export dialog and save debug images focused on the dropdown
    controls (Sample Rate, Bit Depth, File Type, Dither).

    Saves two images under export_window_debug/:
      - {debug_name}_dropdowns.png        : a crop of just the top dropdown row(s)
      - {debug_name}_annotated.png        : the full dialog with each dropdown
                                            boxed in yellow (plus toggles + render
                                            target, same as a normal capture)

    Returns an ExportWindowState or None.
    """
    state = capture_export_window(debug_name=debug_name)
    if state is None:
        return None

    # Crop the top of the dialog where the dropdown rows live, giving a larger
    # view of each dropdown control. The range below stops short of the toggle
    # column and the Export/Cancel buttons at the bottom.
    crop_top = 0
    crop_bottom = int(0.30 * state.height)
    crop = state.image.crop((0, crop_top, state.width, crop_bottom))
    dropdown_crop_path = os.path.join(_DEBUG_DIR, f"{debug_name}_dropdowns.png")
    try:
        crop.save(dropdown_crop_path)
        logger.info(f"Saved dropdown crop: {dropdown_crop_path}")
    except Exception as e:
        logger.debug(f"Could not save dropdown crop: {e}")

    # Extra annotated image so each dropdown location is highlighted.
    _save_debug_image(state, f"{debug_name}_annotated")
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
        for key, box in state.dropdown_value_boxes.items():
            # Yellow rectangle around each dropdown value region.
            cv2.rectangle(annotated, (box[0], box[2]), (box[1], box[3]),
                          (0, 255, 255), 2)
            cv2.putText(annotated, key, (box[0] + 2, box[3] - 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
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
            logger.debug(
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
        logger.debug(f"Verified render target is '{state.rendered_track}' (wanted {label})")
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
    logger.debug("Clicked the Export button")
    return True


def _find_open_menu_option(state, key, target_option, options):
    """Locate `target_option` in an OPEN dropdown menu by OCRing the menu region.

    Robust to the menu opening upward or downward and to any per-row spacing,
    unlike the fixed downward geometry in dropdown_option_point. Screenshots a
    tall region around the dropdown box (covering both directions), OCRs it,
    and returns the screen (x, y) of the row whose text best matches
    `target_option`, or None if it can't be read.
    """
    from PIL import Image

    box = state.dropdown_box(key)
    if box is None:
        return None
    x0, x1, y0, y1 = (int(v) for v in box)
    left, top = int(state.rect[0]), int(state.rect[1])
    box_h = (y1 - y0) + 1
    menu_h = box_h * len(options)

    # Region big enough to contain the menu whether it opens up or down, plus a
    # little slack. Menus match the box's width, so keep the same x-range.
    pad = 6
    rx0 = max(0, left + x0 - pad)
    rx1 = left + x1 + pad
    ry0 = max(0, top + y0 - menu_h - box_h)
    ry1 = top + y1 + menu_h + box_h
    region = (int(rx0), int(ry0), int(rx1 - rx0), int(ry1 - ry0))

    try:
        shot = pyautogui.screenshot(region=region)
    except Exception as e:
        logger.debug(f"Dropdown '{key}': could not screenshot menu region: {e}")
        return None

    arr = np.array(shot.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    # 3x upscale so small menu text OCRs reliably.
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    # Menu rows are usually light text on a darker highlight; try both polarities.
    best = None  # (score, screen_x, screen_y, text)
    norm_target = _normalize(target_option)
    for variant in (gray, cv2.bitwise_not(gray)):
        try:
            data = pytesseract.image_to_data(
                Image.fromarray(cv2.cvtColor(variant, cv2.COLOR_GRAY2RGB)),
                output_type=pytesseract.Output.DICT,
                config="--psm 6",
            )
        except Exception:
            continue
        rows = {}
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
            rkey = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            l, t, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            r = rows.setdefault(
                rkey, {"x0": l, "x1": l + w, "y0": t, "y1": t + h, "conf": [], "words": []}
            )
            r["x0"] = min(r["x0"], l)
            r["x1"] = max(r["x1"], l + w)
            r["y0"] = min(r["y0"], t)
            r["y1"] = max(r["y1"], t + h)
            r["conf"].append(conf)
            r["words"].append(text)

        for r in rows.values():
            line_text = " ".join(r["words"])
            norm = _normalize(line_text)
            if not norm:
                continue
            avg_conf = sum(r["conf"]) / len(r["conf"])
            if norm == norm_target:
                score = avg_conf + 100  # exact match wins
            elif norm_target in norm:
                score = avg_conf + 50   # substring match next
            else:
                continue
            # Undo the 3x upscale and map region-relative -> screen coords.
            cx_region = (r["x0"] + r["x1"]) / 2 / 3.0
            cy_region = (r["y0"] + r["y1"]) / 2 / 3.0
            screen_x = int(rx0 + cx_region)
            screen_y = int(ry0 + cy_region)
            if best is None or score > best[0]:
                best = (score, screen_x, screen_y, line_text)
        # Short-circuit: an exact match in this polarity is authoritative, so
        # skip OCRing the inverted variant (halves the menu-scan cost).
        if best is not None and best[0] >= 100:
            break

    if best is None:
        logger.debug(f"Dropdown '{key}': OCR could not find {target_option!r} in the open menu")
        return None
    logger.debug(
        f"Dropdown '{key}': OCR located {target_option!r} in the menu as "
        f"{best[3]!r} at ({best[1]}, {best[2]})"
    )
    return (best[1], best[2])


def _read_dropdown_box_value(state, key):
    """Screenshot just one dropdown's box and OCR its value (no full recapture).

    This is the fast verification path: after changing a dropdown we only need
    to re-read *that* box, not re-screenshot and re-OCR the whole dialog. Maps
    the stored box's image-relative coords to absolute screen coords, screens
    that small region, and OCRs it with the shared helper.
    """
    box = state.dropdown_box(key)
    if box is None:
        return None
    x0, x1, y0, y1 = (int(v) for v in box)
    left, top = int(state.rect[0]), int(state.rect[1])
    region = (left + x0, top + y0, (x1 - x0) + 1, (y1 - y0) + 1)
    try:
        shot = pyautogui.screenshot(region=region)
    except Exception as e:
        logger.debug(f"Dropdown '{key}': could not screenshot box for verify: {e}")
        return None
    gray = cv2.cvtColor(np.array(shot.convert("RGB")), cv2.COLOR_RGB2GRAY)
    return _ocr_value_pixels(gray)


def set_dropdown(key, target_option, options, debug_name=None, state=None):
    """Force one dropdown to `target_option` by clicking it and picking from its menu.

    `options` is the ordered list of choices the menu shows (top-to-bottom), in
    the same order the caller knows them (e.g. the TARGETS list). `target_option`
    must be a member of that list.

    Algorithm:
      1. Read the current value off the (already-open) dialog.
      2. If it already matches `target_option`, do nothing and return True.
      3. Otherwise click the dropdown box to open the menu, wait for it to
         appear, then click the slot at the target option's index (the menu's
         slots are one box-height each, stacked below the box in `options` order).
      4. Re-read the value and confirm it now matches; retry a couple of times.

    Returns True if the dropdown was verified to show `target_option`, False
    otherwise. `None` means the dialog / box could not be read at all.
    """
    if target_option not in options:
        logger.error(f"Dropdown '{key}': target {target_option!r} not in options {options}")
        return False

    if state is None:
        state = capture_export_window(debug_name=debug_name)
    if state is None:
        return None

    num_options = len(options)
    result = {"key": key, "target": target_option, "changed": False, "ok": False, "attempts": 0}

    for attempt in range(2):
        result["attempts"] = attempt + 1
        current = _match_dropdown_option(state.dropdown_values.get(key, ""), options)
        if current == target_option:
            result["ok"] = True
            logger.debug(f"Dropdown '{key}' already shows {target_option!r}")
            return result

        box = state.dropdown_box(key)
        if box is None:
            logger.warning(f"Dropdown '{key}': no value box found, cannot click it")
            return result

        # 1) Click the dropdown box to open the menu.
        open_point = state.dropdown_point(key)
        pyautogui.click(open_point)
        time.sleep(0.25)  # let the menu animate open

        # 2) Find the target option in the now-open menu and click it. OCR is
        #    preferred because it's direction- and spacing-agnostic: a dropdown
        #    near the bottom of the dialog opens its menu UPWARD, which the fixed
        #    downward geometry gets wrong by a whole slot (that's why sample_rate
        #    at index 3 worked but dither at index 3 landed on the item above it).
        #    Fall back to the geometric slot only if OCR can't read the menu.
        slot = _find_open_menu_option(state, key, target_option, options)
        if slot is None:
            option_index = options.index(target_option)
            slot = state.dropdown_option_point(key, option_index, num_options)
        if slot is None:
            logger.error(f"Dropdown '{key}': could not compute slot for {target_option!r}")
            pyautogui.press("escape")  # don't leave the menu open
            return result
        pyautogui.click(slot)
        result["changed"] = True
        time.sleep(0.3)  # let the selection apply and the menu close

        # 3) Verify by re-reading ONLY this dropdown's box (fast) instead of a
        #    full dialog re-screenshot + full OCR. If that read is unreadable
        #    (None), fall back to a full recapture so we don't miss a real change.
        verified_raw = _read_dropdown_box_value(state, key)
        if verified_raw is None:
            refreshed = capture_export_window(
                debug_name=f"{debug_name}_dd_{key}_v{attempt}" if debug_name else None
            )
            if refreshed is None:
                break
            state = refreshed
            verified_raw = state.dropdown_values.get(key, "")
        else:
            state.dropdown_values[key] = verified_raw

    current = _match_dropdown_option(state.dropdown_values.get(key, ""), options)
    result["ok"] = (current == target_option)
    if not result["ok"]:
        raw_read = state.dropdown_values.get(key, "")
        logger.error(
            f"Dropdown '{key}': wanted {target_option!r} but verification read "
            f"matched={current!r} (raw value OCR'd as {raw_read!r})"
        )
    else:
        logger.debug(f"Dropdown '{key}' set to {target_option!r}")
    return result

