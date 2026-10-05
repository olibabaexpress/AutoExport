"""Manual test: open a set in Live (AbletonOSC active), then run this.
Every audio effect on regular/group tracks should switch off;
return tracks and the main track should be untouched."""
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))

import live
from pylive_commands import disable_audio_effects, disable_main_effects

logging.basicConfig(level=logging.DEBUG, format="%(message)s")
logging.getLogger("AutoExport").setLevel(logging.DEBUG)

s = live.Set(scan=False)
print("Disabled on tracks:", disable_audio_effects(s))
print("Disabled on main:", disable_main_effects(s))
