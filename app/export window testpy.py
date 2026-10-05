from export_window_controls import find_export_window
from export_automation import open_export_window
from window_utils import win_activate_ableton


win_activate_ableton()
open_export_window()

if find_export_window():
    print("Export window found")
else:
    print("Export window not found")
