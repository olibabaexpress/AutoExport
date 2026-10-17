from pylive_commands import get_tempo
import live
set = live.Set(scan=False)
TICKS_PER_BEAT = 960
tempo = get_tempo(set)
print(f"Current tempo: {tempo} {type(tempo)}")

def render_duration_seconds(render_range, tempo):
    """Approximate audio length in seconds of a (start_tick, end_tick) range.

    Used only to size the post-render file-wait timeout, so a missing or
    unknown tempo just yields None and the caller falls back to the default.
    """
    if not render_range or not tempo:
        return None
    seconds = ((render_range[1] - render_range[0]) / TICKS_PER_BEAT) * (60.0 / float(tempo))
    return seconds if seconds > 0 else None

render_range = (60, 1)
seconds = ((render_range[1] - render_range[0]) / TICKS_PER_BEAT) * (60.0 / float(tempo))
print(seconds)
#print(render_duration_seconds(render_range, tempo))