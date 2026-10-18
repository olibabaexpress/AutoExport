
import json
import logging
from pathlib import Path

from abletoolz.live_set import AbletonSet
from find_clusters import render_ranges, TICKS_PER_BEAT, TAIL_BARS
import os
from paths import data_file
import audio_settings as audio_settings
TASK_FILE = data_file("EXPORT_TASKS.json")
logger = logging.getLogger(__name__)



# ============================================================
# DEFAULT WAV SETTINGS
# ============================================================

# Fallbacks used only if audio_settings (config.json) can't be read. The
# user's saved audio preferences are the real source of truth; these mirror
# audio_settings.DEFAULTS so a fresh install still estimates sensibly.
DEFAULT_SAMPLE_RATE = 44100
DEFAULT_BIT_DEPTH = 16
DEFAULT_CHANNELS = 2


def _resolve_render_settings(sample_rate, bit_depth, channels):
    """Fill any None sample_rate/bit_depth/channels from the saved audio settings.

    The functions below used to hardcode DEFAULT_* as their default arguments,
    which are frozen at import time and ignore the user's Audio Settings. Now
    they accept None and resolve here at call time, so the size estimate tracks
    whatever Sample Rate / Bit Depth the user has chosen. Any argument the
    caller passed explicitly always wins; only the None ones are filled.
    """
    saved = audio_settings.load().render_settings()
    return (
        saved["sample_rate"] if sample_rate is None else sample_rate,
        saved["bit_depth"] if bit_depth is None else bit_depth,
        saved["channels"] if channels is None else channels,
    )


# ============================================================
# FORMATTING
# ============================================================

def format_bytes(num_bytes):
    """
    Convert bytes to a human-readable size.

    Uses binary units:
        KB = 1024 bytes
        MB = 1024 KB
        GB = 1024 MB
        etc.
    """

    value = float(num_bytes)

    units = [
        "B",
        "KB",
        "MB",
        "GB",
        "TB",
        "PB",
    ]

    for unit in units:
        if value < 1024:
            return f"{value:,.2f} {unit}"

        value /= 1024

    return f"{value:,.2f} PB"


def format_duration(seconds):
    """
    Convert seconds to HH:MM:SS.
    """

    seconds = int(round(seconds))

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


# ============================================================
# WAV SIZE
# ============================================================

def calculate_bytes_per_second(
    sample_rate=DEFAULT_SAMPLE_RATE,
    bit_depth=DEFAULT_BIT_DEPTH,
    channels=DEFAULT_CHANNELS,
):
    """
    Calculate the uncompressed PCM data rate.
    """

    return (
        sample_rate
        * channels
        * (bit_depth / 8)
    )


def calculate_track_size(
    duration_seconds,
    sample_rate=None,
    bit_depth=None,
    channels=None,
):
    sample_rate, bit_depth, channels = _resolve_render_settings(sample_rate, bit_depth, channels)
    """
    Estimate the size of one WAV rendered for the specified duration.

    44 bytes are added for the basic PCM WAV header.
    """

    audio_bytes = (
        duration_seconds
        * calculate_bytes_per_second(
            sample_rate,
            bit_depth,
            channels,
        )
    )

    wav_header = 44

    return int(audio_bytes + wav_header)


def rescale_estimate(result, sample_rate, bit_depth, channels):
    """Recalculate size from cached render ranges without reopening the set."""
    ranges = result["render_ranges"]
    range_count = len(ranges)
    if not range_count:
        raise ValueError("Cannot rescale an estimate with no render ranges")

    wav_count = result["wav_count"]
    if wav_count % range_count:
        raise ValueError("Cached WAV count does not match its render ranges")
    tracks_per_range = wav_count // range_count

    bytes_per_track = sum(
        calculate_track_size(
            (render_range["end"] - render_range["start"])
            / TICKS_PER_BEAT * 60 / result["bpm"],
            sample_rate,
            bit_depth,
            channels,
        )
        for render_range in ranges
    )
    total_bytes = bytes_per_track * tracks_per_range

    updated = dict(result)
    updated["bytes_per_wav"] = bytes_per_track
    updated["total_bytes"] = total_bytes
    updated["total_size"] = format_bytes(total_bytes)
    return updated


# ============================================================
# ABLETON PROJECT LENGTH
# ============================================================

def get_project_duration(live_set):
    """
    Get the estimated arrangement duration from abletoolz.

    abletoolz's transport.length() represents the longest clip /
    furthest arrangement position.

    It returns a duration containing:
        - bpm
        - bars

    abletoolz documents this calculation as valid for 4/4.

    Returns:
        duration_seconds
        bars
        bpm
    """

    length = live_set.transport.length()

    bpm = float(length.bpm)
    bars = float(length.bars)

    if bpm <= 0:
        raise ValueError(
            f"Invalid BPM returned by abletoolz: {bpm}"
        )

    if bars < 0:
        raise ValueError(
            f"Invalid bar length returned by abletoolz: {bars}"
        )

    # 4 beats per bar.
    beats = bars * 4

    seconds_per_beat = 60 / bpm

    duration_seconds = (
        beats * seconds_per_beat
    )

    return (
        duration_seconds,
        bars,
        bpm,
    )


# ============================================================
# TRACK IDENTIFICATION
# ============================================================

def get_track_type(track):
    """
    Safely obtain the abletoolz track type.
    """

    try:
        return str(track.type)
    except Exception:
        return type(track).__name__


def get_track_name(track):
    """
    Safely obtain the track name.
    """

    try:
        name = track.name

        if name:
            return str(name)

    except Exception:
        pass

    return "(unnamed)"


def is_return_track(track):
    """
    Determine whether an abletoolz track is a Return Track.
    """

    track_type = get_track_type(track).lower()

    return "returntrack" in track_type


# ============================================================
# TRACK COUNTING
# ============================================================

def count_stem_tracks(
    live_set,
    include_return_tracks,
):
    """
    Count tracks for an "All Individual Tracks" export.

    Counts:

        Audio tracks
        MIDI tracks
        Group tracks
        Group children
        Nested group tracks
        Return tracks (if requested)
        Main/Master track

    The Main/Master track is explicitly added separately.

    This is intentional: Live 12 calls it MainTrack and it may not
    be included in live_set.tracks.load() in the same way as the
    ordinary arrangement tracks.

    Returns:

        track_count
        track_details
    """

    tracks = list(
        live_set.tracks.load()
    )

    track_details = []

    for track in tracks:

        track_type = get_track_type(track)
        track_name = get_track_name(track)

        if is_return_track(track):

            if include_return_tracks:

                track_details.append({
                    "name": track_name,
                    "type": track_type,
                    "category": "return",
                })

            continue

        # Every other track returned by abletoolz counts.
        #
        # This intentionally includes Group Tracks.
        #
        # Example:
        #
        #   DRUMS [Group]
        #       Kick
        #       Snare
        #
        # = 3 WAVs
        track_details.append({
            "name": track_name,
            "type": track_type,
            "category": "track",
        })

    # --------------------------------------------------------
    # Main / Master
    # --------------------------------------------------------
    #
    # We explicitly count this as one stereo WAV.
    #
    # This was the missing track in the previous estimator.
    #

    track_details.append({
        "name": "Main / Master",
        "type": "MainTrack",
        "category": "main",
    })

    return (
        len(track_details),
        track_details,
    )


# ============================================================
# SINGLE PROJECT
# ============================================================

def estimate_project(
    project_path,
    export_stems,
    export_main,
    include_return_and_main_effects,
    sample_rate=None,
    bit_depth=None,
    channels=None,
):
    sample_rate, bit_depth, channels = _resolve_render_settings(sample_rate, bit_depth, channels)
    """
    Estimate the WAV export size for one project.

    Parameters
    ----------
    project_path:
        Path to the .als file.

    export_stems:
        True:
            Export all individual tracks.

        False:
            Do not export individual tracks.

    export_main:
        True:
            Export the Main/Master stereo channel.

        False:
            Do not export the Main/Master channel as a standalone
            export.

    include_return_and_main_effects:
        True:
            Return tracks are NOT exported separately.

        False:
            Return tracks ARE exported separately.

    Returns
    -------
    dict
    """

    project_path = Path(project_path)

    # --------------------------------------------------------
    # Basic validation
    # --------------------------------------------------------

    if not project_path.exists():

        raise FileNotFoundError(
            f"Project does not exist: {project_path}"
        )

    if not project_path.is_file():

        raise ValueError(
            f"Project path is not a file: {project_path}"
        )

    # --------------------------------------------------------
    # Parse Ableton project
    # --------------------------------------------------------

    live_set = AbletonSet(project_path)

    live_set.parse()

    # --------------------------------------------------------
    # Determine the actual export ranges (not the furthest stray clip).
    # --------------------------------------------------------

    _, _, bpm = get_project_duration(live_set)
    ranges = render_ranges(live_set)
    duration_seconds = sum(
        (end - start) / TICKS_PER_BEAT * 60 / bpm
        for start, end in ranges
    )
    bars = sum((end - start) / (TICKS_PER_BEAT * 4) for start, end in ranges)

    # --------------------------------------------------------
    # Determine WAV count
    # --------------------------------------------------------

    track_details = []

    if export_stems:

        # When true, return tracks are NOT individually rendered.
        #
        # When false, return tracks ARE individually rendered.
        include_return_tracks = (
            not include_return_and_main_effects
        )

        (
            wav_count,
            track_details,
        ) = count_stem_tracks(
            live_set,
            include_return_tracks,
        )

    else:
        wav_count = 0

    if export_main:

        # Only the Main/Master stereo export.
        wav_count += 1

        track_details.append({
            "name": "Main / Master",
            "type": "MainTrack",
            "category": "main",
        })

    # --------------------------------------------------------
    # Calculate size
    # --------------------------------------------------------

    bytes_per_track = sum(
        calculate_track_size(
            (end - start) / TICKS_PER_BEAT * 60 / bpm,
            sample_rate, bit_depth, channels,
        )
        for start, end in ranges
    )

    total_bytes = (
        bytes_per_track
        * wav_count
    )

    # --------------------------------------------------------
    # Return project result
    # --------------------------------------------------------

    return {
        "project_path": str(project_path),

        "duration_seconds": duration_seconds,
        "duration": format_duration(duration_seconds),

        "bars": bars,
        "bpm": bpm,

        "wav_count": wav_count * len(ranges),
        "render_ranges": [
            {"start": start, "end": end} for start, end in ranges
        ],
        "tail_padding_bars": TAIL_BARS,

        "bytes_per_wav": bytes_per_track,

        "total_bytes": total_bytes,
        "total_size": format_bytes(total_bytes),

        "export_stems": bool(export_stems),
        "export_main": bool(export_main),
        "include_return_and_main_effects": bool(
            include_return_and_main_effects
        ),

        "tracks": track_details,
    }


# ============================================================
# BACKGROUND WORKER ENTRY POINTS
# ============================================================

def lower_worker_priority():
    """Process-pool initializer: estimates are background work, so worker
    processes run at idle priority and yield CPU to the GUI and Ableton."""
    try:
        import psutil
    except ImportError as exc:
        logger.warning("Could not lower size-estimator worker priority: %s", exc)
        return
    try:
        psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS if os.name == "nt" else 19)
    except (psutil.Error, OSError) as exc:
        logger.warning("Could not lower size-estimator worker priority: %s", exc)


def estimate_task(task, render_settings):
    """Process-pool entry point for one queue task. Never raises, so a single
    unreadable set reports an error instead of breaking the pool."""
    try:
        return estimate_project(
            project_path=task["project_path"],
            export_stems=bool(task.get("export_stems", False)),
            export_main=bool(task.get("export_main", False)),
            include_return_and_main_effects=bool(
                task.get("include_return_and_main_effects", False)
            ),
            **render_settings,
        )
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# QUEUE PROCESSING
# ============================================================

def estimate_queue(
    json_path,
    sample_rate=None,
    bit_depth=None,
    channels=None,
):
    sample_rate, bit_depth, channels = _resolve_render_settings(sample_rate, bit_depth, channels)
    """
    Read the processing queue JSON and estimate the total WAV
    output size.

    IMPORTANT:

    The 'done' property is intentionally ignored.

    This means a project remains part of the total after it has
    been processed, which is useful for a GUI showing:

        "How much storage will this entire queue require?"

    The following JSON properties are used:

        project_path
        export_stems
        export_main
        include_return_and_main_effects

    These are ignored:

        collect_all_and_save
        normalize
        done

    Returns a dictionary suitable for feeding directly into a GUI.

    Example:

        result = estimate_queue("processing_queue.json")

        result["total_wavs"]
        result["total_bytes"]
        result["total_size"]
        result["projects"]
    """

    json_path = Path(json_path)

    if not json_path.exists():

        raise FileNotFoundError(
            f"Queue JSON does not exist: {json_path}"
        )

    # --------------------------------------------------------
    # Read JSON
    # --------------------------------------------------------

    with json_path.open(
        "r",
        encoding="utf-8",
    ) as file:

        queue = json.load(file)

    if not isinstance(queue, list):

        raise ValueError(
            "The processing queue JSON must contain a list."
        )

    # --------------------------------------------------------
    # Totals
    # --------------------------------------------------------

    total_wavs = 0
    total_bytes = 0

    projects = []
    errors = []

    # --------------------------------------------------------
    # Process every queue entry
    # --------------------------------------------------------

    for index, item in enumerate(queue):

        if not isinstance(item, dict):

            errors.append({
                "index": index,
                "error": "Queue entry is not an object.",
            })

            continue

        # ----------------------------------------------------
        # Required project path
        # ----------------------------------------------------

        project_path = item.get("project_path")

        if not project_path:

            errors.append({
                "index": index,
                "error": "Missing project_path.",
            })

            continue

        # ----------------------------------------------------
        # Export settings
        # ----------------------------------------------------

        export_stems = bool(
            item.get(
                "export_stems",
                False,
            )
        )

        export_main = bool(
            item.get(
                "export_main",
                False,
            )
        )

        include_return_and_main_effects = bool(
            item.get(
                "include_return_and_main_effects",
                False,
            )
        )

        # ----------------------------------------------------
        # Estimate project
        # ----------------------------------------------------

        try:

            result = estimate_project(
                project_path=project_path,
                export_stems=export_stems,
                export_main=export_main,
                include_return_and_main_effects=(
                    include_return_and_main_effects
                ),
                sample_rate=sample_rate,
                bit_depth=bit_depth,
                channels=channels,
            )

            # Preserve the queue's done state in the result,
            # even though it does NOT affect the calculation.
            result["done"] = bool(
                item.get("done", False)
            )

            # Preserve the queue index so the GUI can associate
            # the result with the original entry if necessary.
            result["queue_index"] = index

            projects.append(result)

            total_wavs += result["wav_count"]
            total_bytes += result["total_bytes"]

        except Exception as error:

            errors.append({
                "index": index,
                "project_path": str(project_path),
                "error": str(error),
            })

    # --------------------------------------------------------
    # Return aggregate result
    # --------------------------------------------------------

    return {
        "total_wavs": total_wavs,

        "total_bytes": total_bytes,

        "total_size": format_bytes(
            total_bytes
        ),

        "project_count": len(projects),

        "queue_entry_count": len(queue),

        "error_count": len(errors),

        "projects": projects,

        "errors": errors,

        "settings": {
            "sample_rate": sample_rate,
            "bit_depth": bit_depth,
            "channels": channels,
        },
    }


# ============================================================
# OPTIONAL CONSOLE DISPLAY
# ============================================================

def print_queue_estimate(result):
    """
    Print a human-readable summary.

    This is optional. The main purpose of the module is to return
    the result dictionary to another program.
    """

    print()
    print("=" * 78)
    print("ABLETON EXPORT SIZE ESTIMATE")
    print("=" * 78)

    settings = result["settings"]

    print(
        f"Format: "
        f"{settings['bit_depth']}-bit WAV / "
        f"{settings['sample_rate']:,} Hz / "
        f"{settings['channels']} channel(s)"
    )

    print()

    print(
        f"{'PROJECT':<45}"
        f"{'WAVs':>8}"
        f"{'SIZE':>15}"
    )

    print("-" * 78)

    for project in result["projects"]:

        name = Path(
            project["project_path"]
        ).stem

        if len(name) > 43:

            name = (
                name[:40]
                + "..."
            )

        print(
            f"{name:<45}"
            f"{project['wav_count']:>8}"
            f"{project['total_size']:>15}"
        )

    print("-" * 78)

    print(
        f"{'TOTAL':<45}"
        f"{result['total_wavs']:>8}"
        f"{result['total_size']:>15}"
    )

    print()

    print(
        f"Projects: {result['project_count']}"
    )

    if result["error_count"]:

        print(
            f"Errors:   {result['error_count']}"
        )

        print()

        for error in result["errors"]:

            print(
                f"ERROR: "
                f"{error.get('project_path', '')}"
            )

            print(
                f"       {error['error']}"
            )

    print()


# ============================================================
# COMMAND-LINE USAGE
# ============================================================

if __name__ == "__main__":

   


    result = estimate_queue(
        TASK_FILE,
        sample_rate=44100,
        bit_depth=32,
        channels=2,
    )

    print_queue_estimate(result)