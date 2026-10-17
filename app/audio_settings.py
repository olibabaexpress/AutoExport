"""
Audio settings: the single source of truth for the Export dialog's audio
format options (Sample Rate, Bit Depth, File Type, Dither).

These option lists are shared by three consumers that must never disagree:

  * the GUI "Audio Settings" window, which renders them as dropdowns,
  * export_window_controls / export_automation, which drive Ableton's Export
    dialog dropdowns to a chosen option by its index in these lists,
  * size_estimator, which converts a chosen Sample Rate / Bit Depth into a
    per-second byte cost for the size estimates.

The user's current choices are persisted to config.json under the "audio"
key via app_config (get_audio / set_audio), exactly like the other
preferences. load() returns the saved choices merged over the defaults;
save() writes them back.
"""

from dataclasses import dataclass

import app_config

# ---------------------------------------------------------------------------
# Option lists
#
# The ORDER here is the display order in Ableton's dropdowns, which is what
# makes index-based navigation in export_window_controls correct. Sample Rate
# values are strings (they include "44100" etc.); Bit Depth likewise.
# ---------------------------------------------------------------------------

SAMPLE_RATES = ["22050", "32000", "44100", "48000", "88200", "96000", "176400", "192000"]
BIT_DEPTHS = ["16", "24", "32"]
FILE_TYPES = ["WAV", "AIFF", "FLAC"]
# Dither is only meaningful for integer bit depths < 32; Ableton greys the
# Dither dropdown out when Bit Depth is 32 (no dither applied). The options
# are shown in Ableton's order.
DITHER_OPTIONS = ["No Dither", "Rectangular", "Triangular", "POW-r 1", "POW-r 2", "POW-r 3"]

# Number of audio channels rendered. Ableton's Export dialog has no channel
# dropdown (stereo is fixed), so this is not user-selectable here; it exists
# so the size estimator has a channel count to work with.
CHANNELS = 2

# Human-facing defaults, applied on first run before anything is saved.
DEFAULTS = {
    "sample_rate": "44100",
    "bit_depth": "16",
    "file_type": "WAV",
    "dither": "Triangular",
}

# The four dropdown keys, in the order they should be shown / driven.
DROPDOWN_KEYS = ["sample_rate", "bit_depth", "file_type", "dither"]

# Ordered option lists keyed by the same names export_window_controls uses.
OPTIONS = {
    "sample_rate": SAMPLE_RATES,
    "bit_depth": BIT_DEPTHS,
    "file_type": FILE_TYPES,
    "dither": DITHER_OPTIONS,
}


@dataclass(frozen=True)
class AudioSettings:
    """An immutable snapshot of the four chosen audio options."""

    sample_rate: str
    bit_depth: str
    file_type: str
    dither: str

    @property
    def sample_rate_int(self) -> int:
        try:
            return int(str(self.sample_rate).strip())
        except (TypeError, ValueError):
            return int(DEFAULTS["sample_rate"])

    @property
    def bit_depth_int(self) -> int:
        try:
            return int(str(self.bit_depth).strip())
        except (TypeError, ValueError):
            return int(DEFAULTS["bit_depth"])

    def dither_enabled(self) -> bool:
        """Dither only applies at integer bit depths below 32.

        Ableton disables the Dither dropdown at 32-bit, so callers should not
        attempt to set it there regardless of the stored choice.
        """
        return self.bit_depth_int != 32

    def render_settings(self) -> dict:
        """The {sample_rate, bit_depth, channels} shape size_estimator wants."""
        return {
            "sample_rate": self.sample_rate_int,
            "bit_depth": self.bit_depth_int,
            "channels": CHANNELS,
        }


def load() -> AudioSettings:
    """Load the saved audio settings, merged over the defaults.

    Any saved value that isn't in its option list is ignored in favour of the
    default, so a stale config (or a hand-edited one) can't feed an invalid
    option into the export dialog.
    """
    stored = app_config.get_audio() or {}

    def pick(key):
        value = stored.get(key, DEFAULTS[key])
        options = OPTIONS[key]
        return value if value in options else DEFAULTS[key]

    return AudioSettings(
        sample_rate=pick("sample_rate"),
        bit_depth=pick("bit_depth"),
        file_type=pick("file_type"),
        dither=pick("dither"),
    )


def save(settings) -> bool:
    """Persist an AudioSettings (or a plain dict) to config.json. Returns True on success."""
    if isinstance(settings, AudioSettings):
        values = {
            "sample_rate": settings.sample_rate,
            "bit_depth": settings.bit_depth,
            "file_type": settings.file_type,
            "dither": settings.dither,
        }
    else:
        values = dict(settings or {})
    return app_config.set_audio(values)


def option_index(key, value) -> int:
    """Index of `value` within the option list for `key`, or -1 if absent."""
    options = OPTIONS.get(key, [])
    try:
        return options.index(value)
    except ValueError:
        return -1
