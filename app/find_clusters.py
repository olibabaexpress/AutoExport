"""
Prints the bar locations of clusters of arrangement audio clips in an Ableton project.
The export pipeline and size estimator share render_ranges() so the gaps
between clusters are neither rendered nor counted in the size estimate.
Built on abletoolz (pip install abletoolz).

Usage:
    python find_clusters.py [project.als] [--gap-bars 16] [--beats-per-bar 4] [--include-midi]
"""
import argparse
import math
import pathlib
import sys

from abletoolz.live_set import sections
from abletoolz.live_set.document import AbletonSet


def pick_file():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Select Ableton project",
        filetypes=[("Ableton Live Set", "*.als"), ("All files", "*.*")],
    )
    root.destroy()
    return path


def val(node, path, default=None):
    n = node.find(path)
    return n.get("Value") if n is not None else default




def arrangement_spans(track, include_midi):
    if track.type == "MidiTrack" and not include_midi:
        return
    ev = sections.arrangement_events(track.track_root, track.type)
    if ev is None:
        return
    for clip in ev.clips():
        if val(clip, "Disabled") == "true":
            continue
        yield sections.event_span(clip)


def cluster(intervals, gap_beats):
    if not intervals:
        return []
    intervals = sorted(intervals)
    clusters, cur, cur_end = [], [intervals[0]], intervals[0][1]
    for iv in intervals[1:]:
        if iv[0] - cur_end > gap_beats:
            clusters.append(cur)
            cur, cur_end = [], iv[1]
        cur.append(iv)
        cur_end = max(cur_end, iv[1])
    clusters.append(cur)
    return clusters


def pos(beats, bpb):
    return f"{int(beats // bpb) + 1}.{int(beats % bpb) + 1}.1"


GAP_BARS = 16
BEATS_PER_BAR = 4
TICKS_PER_BEAT = 960
TAIL_BARS = 1


def span_range(spans):
    start = max(0, min(span[0] for span in spans))
    end = max(span[1] for span in spans)
    first_tick = math.floor(start * TICKS_PER_BEAT + 1e-7)
    last_tick = math.ceil(end * TICKS_PER_BEAT - 1e-7)
    if last_tick <= first_tick:
        raise ValueError(f"Invalid render range: {start} to {end} beats")
    return first_tick, last_tick


def pad_ranges(raw_ranges, beats_per_bar=BEATS_PER_BAR):
    bar_ticks = beats_per_bar * TICKS_PER_BEAT
    ranges = []
    for index, (start, end) in enumerate(raw_ranges):
        # Allow the remainder of the last clip's bar plus a full bar for tails.
        padded_end = (math.ceil(end / bar_ticks) + TAIL_BARS) * bar_ticks
        if index + 1 < len(raw_ranges):
            padded_end = min(padded_end, raw_ranges[index + 1][0])
        ranges.append((start, padded_end))
    return ranges


def render_ranges(live_set, gap_bars=GAP_BARS, beats_per_bar=BEATS_PER_BAR):
    """Return ranges in Ableton ticks, with a bar of tail after each cluster."""
    intervals = [
        span
        for track in live_set.tracks.load()
        for span in arrangement_spans(track, include_midi=False)
        if span[1] > span[0]
    ]
    if not intervals:
        # MIDI-only sets still need their original arrangement-length render.
        length = live_set.transport.length()
        end = float(length.bars) * beats_per_bar
        if end <= 0:
            raise ValueError("No arrangement audio clips or positive arrangement length to export")
        intervals = [(0, end)]

    raw_ranges = [
        span_range(spans)
        for spans in cluster(intervals, gap_bars * beats_per_bar)
    ]
    return pad_ranges(raw_ranges, beats_per_bar)


def render_position(ticks, beats_per_bar=BEATS_PER_BAR):
    beats, tick = divmod(ticks, TICKS_PER_BEAT)
    bars, beat = divmod(beats, beats_per_bar)
    return bars + 1, beat + 1, tick + 1


def render_length(ticks, beats_per_bar=BEATS_PER_BAR):
    beats, tick = divmod(ticks, TICKS_PER_BEAT)
    bars, beat = divmod(beats, beats_per_bar)
    return bars, beat, tick


def project_render_ranges(project_path):
    live_set = AbletonSet(pathlib.Path(project_path))
    live_set.parse()
    return render_ranges(live_set)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", nargs="?")
    ap.add_argument("--gap-bars", type=float, default=GAP_BARS)
    ap.add_argument("--beats-per-bar", type=int, default=BEATS_PER_BAR)
    ap.add_argument("--include-midi", action="store_true")
    args = ap.parse_args()

    path = args.project or pick_file()
    if not path:
        sys.exit("No file selected.")

    als = AbletonSet(pathlib.Path(path))
    als.parse()  # Tracks.load() reads the private root, so parse first
    bpb = args.beats_per_bar

    ivs = [
        iv
        for t in als.tracks.load()
        for iv in arrangement_spans(t, args.include_midi)
    ]
    if not ivs:
        sys.exit("No enabled arrangement audio clips found on tracks routed to the main output.")

    print(f"{len(ivs)} clips, gap threshold {args.gap_bars:g} bars, "
          f"{TAIL_BARS} bar of tail padding\n")
    clusters = cluster(ivs, args.gap_bars * bpb)
    padded = pad_ranges([span_range(c) for c in clusters], bpb)
    for i, ((start, end), c) in enumerate(zip(padded, clusters), 1):
        start_text = ".".join(map(str, render_position(start, bpb)))
        end_text = ".".join(map(str, render_position(end, bpb)))
        print(f"Cluster {i}: render start {start_text}, length "
              f"{'.'.join(map(str, render_length(end - start, bpb)))} "
              f"(to {end_text}, {len(c)} clips)")


if __name__ == "__main__":
    main()