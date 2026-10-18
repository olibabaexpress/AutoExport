"""
AutoExportLauncher GUI - Drag and drop interface for AutoExport
Allows users to configure which operations to run for each project
"""
import os
import sys
import tkinter as tk
from tkinter import messagebox
from gui_file_drop import parse_dropped_paths
from gui_keybindings import HotkeyMixin
from gui_size_estimates import SizeEstimateMixin
from gui_bulk_actions import BulkActionsMixin
from gui_export_runner import ExportRunnerMixin
from gui_session import SessionMixin
from gui_status import StatusMixin
from gui_window_helpers import GuiwindowhelpersMixin
from paths import data_file, LOG_DIR
from datetime import datetime
import queue
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from gui_theme import (
    BG_INPUT, BG_WINDOW, BG_SEPARATOR, FG_PRIMARY,
    PROJECT_TREE_COLUMN_WIDTHS,
    BTN_COLOR_NEUTRAL, CTK_APPEARANCE_MODE, CTK_COLOR_THEME,
    set_window_icon,
    enable_dark_title_bar,
)
import customtkinter as ctk

ctk.set_appearance_mode(CTK_APPEARANCE_MODE)
ctk.set_default_color_theme(CTK_COLOR_THEME)

import threading
import ableton_build
import audio_settings
import gui_missingmedia_tools
from gui_project_panel import ProjectFinder, ProjectPanelMixin
from gui_queue_panel import QueuePanelMixin
from gui_filemenu import FileMenuMixin
from gui_audio_settings import AudioSettingsPanelMixin
import gui_namingsettings
from gui_ableton_version import AbletonVersionMixin

# ---------- GUI PROCESS LOGGING ----------
# The GUI runs in the windowed (noconsole) frozen exe (or pythonw in source
# mode), so nothing a worker thread prints or raises ever reaches a file on
# its own. Without this block, errors like the sample-index "'NoneType'
# object has no attribute 'write'" crash showed only as a one-line label in
# the progress bar with no traceback recorded anywhere.
#
# Handlers go on the ROOT logger because GUI-stage modules (sample_database,
# create_db_patch, project_scanner, ...) all use logging.getLogger(__name__)
# - configuring only "AutoExport" would drop their records. Everything lands
# in LOGS/GUI_<ts>.log.
import logging
import logging.handlers
import sys
if sys.stdout is None or sys.stderr is None:
    # pythonw / windowed build: give print() somewhere harmless to go so a
    # stray console write can't crash a GUI worker thread.
    try:
        _null_stream = open(
            os.path.join(LOG_DIR, f"GUI_console_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
            "a", encoding="utf-8", buffering=1,
        )
        if sys.stdout is None:
            sys.stdout = _null_stream
        if sys.stderr is None:
            sys.stderr = _null_stream
    except OSError:
        pass

if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(errors="backslashreplace")
    except (OSError, ValueError):
        logging.getLogger().warning(
            "Could not configure stderr for Unicode-safe logging",
            exc_info=True,
        )

gui_log_filename = os.path.join(
    LOG_DIR, f"GUI_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.DEBUG)
if not _root_logger.handlers:
    _gui_file_handler = logging.handlers.RotatingFileHandler(
        gui_log_filename,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        errors="backslashreplace",
    )
    _gui_file_handler.setLevel(logging.DEBUG)
    _gui_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _gui_file_handler.setFormatter(_gui_formatter)
    _root_logger.addHandler(_gui_file_handler)
    if sys.stderr is not None:
        _gui_console_handler = logging.StreamHandler(sys.stderr)
        _gui_console_handler.setLevel(logging.INFO)
        _gui_console_handler.setFormatter(_gui_formatter)
        _root_logger.addHandler(_gui_console_handler)

# The GUI's own logger (same name the worker-side modules use, so log lines
# look identical across processes). Propagates to root -> GUI_*.log.
logger = logging.getLogger("AutoExport")
# ----------------------------------------

PROJECT_TREE_EDIT_PANEL_WIDTH = sum(PROJECT_TREE_COLUMN_WIDTHS.values())

from task_manager import TaskManager

class AutoExportLauncherGUI(
    FileMenuMixin, 
    HotkeyMixin,
    StatusMixin, 
    AudioSettingsPanelMixin, 
    ProjectPanelMixin, 
    QueuePanelMixin,
    AbletonVersionMixin,
    GuiwindowhelpersMixin,
    SizeEstimateMixin,
    BulkActionsMixin,
    ExportRunnerMixin,
    SessionMixin,
):
    """GUI for AutoExport launcher"""
    
    def __init__(self, root, initial_paths=None):
        self.root = root
        # Explorer right-click sessions show only the chosen folders and never
        # overwrite the saved project browser list.
        self.initial_paths = list(initial_paths or [])
        self._ephemeral_browser = bool(self.initial_paths)
        self.root.title("AutoExport Launcher")
        set_window_icon(self.root)
        self.root.geometry("1400x800")
        self.root.resizable(True, True) 
        self.root.configure(bg=BG_WINDOW)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, lambda: enable_dark_title_bar(root))
        
        self.projects_by_folder = {}
        self.selected_projects = {}
        self.tree_item_to_path = {} 
        self.path_to_tree_item = {}
        self.sort_by = "Last Modified"
        
        self.size_cache = {}  
        self._load_size_cache()    # cache_key -> result dict (or {"error": ...})
        self.size_estimate_total = 0.0
        # Size estimates parse whole .als files in pure Python, which holds the
        # GIL and starves Tk if done in threads. They run in a pool of
        # low-priority worker processes instead, fed by a background thread so
        # spawning workers never blocks the UI either.
        self.size_pending = set()          # cache keys queued or running
        self.size_generation = 0
        self.size_jobs = queue.Queue()     # (key, task, generation) -> submitter thread
        self.size_results = queue.Queue()  # (key, generation, result | None) -> UI thread
        self._size_pool = None
        self._size_pool_lock = threading.Lock()
        self._bulk_fix_priority_active = False
        self._closing = False
        threading.Thread(target=self._size_submitter, name="size-submit", daemon=True).start()

        # Per-project filesystem facts (mtime for the cache key, recursive
        # nesting) are gathered off the UI thread too: on a cold HDD a few
        # stats per queued project add up to seconds.
        self.task_meta = {}                # project_path -> {"mtime", "recursive", "checked"}
        self.meta_pending = set()
        self.meta_results = queue.Queue()
        self.meta_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="queue-meta")
        self.folder_sort_cache = {}
        self.folder_sort_pending = set()
        self.folder_sort_results = queue.Queue()
        self.folder_sort_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="folder-sort"
        )
        self._folder_sort_refresh_after_id = None
        self.queue_path_to_item = {}
        self.queue_item_errors = {}

        self.multi_selection = set()
        self._visual_project_selection = set()
        self._project_file_order = []
        self._tree_detached_items = set()
        self.last_selected_item = None
        self.project_selection_anchor = None
        self._active_tree = "projects"
        self.queue_selection_anchor = None
        # Queue management
        self.task_manager = TaskManager(ephemeral=self._ephemeral_browser)
        self._cache_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gui-cache-save"
        )
        self.queue_sort_by = "Manual"
        self.project_search_query = ""
        self._project_search_after_id = None
        self._initial_estimates_started = False
        self._initial_estimate_cursor = 0
        self._initial_estimate_after_id = None
        self._initial_estimate_paths = set()
        self.folder_items = {}
        if not self._ephemeral_browser:
            self._restore_project_browser()
        
        # Toggle state variables
        self.select_all_enabled = tk.BooleanVar(value=True)  # Select All toggle state
        self.collect_all_enabled = tk.BooleanVar(value=True)  # Collect All & Save toggle state
        self.return_fx_all_enabled = tk.BooleanVar(value=True)  # Include Return and Main Effects toggle state
        self.normalize_all_enabled = tk.BooleanVar(value=True)  # Normalize toggle state
        self.wet_dry_all_enabled = tk.BooleanVar(value=False)  # Wet+Dry toggle state
        
        # State tracking for pause/resume
        self.script_running = False
        self.script_paused = False
        self.pause_toggle_button = None
        self._script_state_signature = None
        self._legacy_script_state_running = False
        self._script_state_pid = None
        self._last_export_process_scan = 0.0
        self._export_process_scan_result = False
        
        self.size_render_settings = audio_settings.load().render_settings()
        # Cheap when already configured; only scans the default install
        # folders on first run or if a configured exe has disappeared.
        ableton_build.ensure_detected()
        self._request_folder_sort_metadata(self.projects_by_folder)
        self.setup_ui()
        self.setup_drag_drop()
        self.root.after(700, self._start_initial_size_estimates)
        self.start_file_watcher()  # Watch EXPORT_TASKS.json for changes
        self.root.after(400, self._check_ableton_setup)
        self._start_instance_server()
        for initial_path in self.initial_paths:
            self.add_path(initial_path)





    def _create_status_bar(self):
        """Create the status bar at the bottom of the application window."""
        self.status_bar = tk.Frame(self.root, bg=BG_WINDOW)
        self.status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_label = tk.Label(
            self.status_bar,
            text="Ready",
            bg=BG_WINDOW,
            fg=FG_PRIMARY,
            anchor="w",
            padx=10,
        )
        self.status_label.pack(fill=tk.X)



    def create_top_bar(self):
        """Create the shared application menu, Ableton version, and audio settings row."""
        self.top_bar = tk.Frame(self.root, bg=BG_WINDOW)
        self.top_bar.pack(fill=tk.X, padx=10, pady=(6, 0))
        self.top_bar.grid_columnconfigure(0, weight=0)
        self.top_bar.grid_columnconfigure(1, weight=1)
        self.top_bar.grid_columnconfigure(2, weight=0)

        self.menu_container = tk.Frame(
            self.top_bar, 
            bg=BG_INPUT, 
            highlightthickness=1,
            highlightbackground=BG_SEPARATOR,
            highlightcolor=BG_SEPARATOR,
            bd=0,
            width=300, 
            height=36
        )
        self.menu_container.grid(row=0, column=0, sticky="nw")
        self.menu_container.grid_propagate(False)
        self.create_menu()

        version_host = tk.Frame(self.top_bar, bg=BG_WINDOW)
        version_host.grid(row=0, column=1, sticky="e", padx=(10, 0), pady=(24, 0))
        self._build_ableton_version_menu(version_host)

        audio_host = tk.Frame(self.top_bar, bg=BG_WINDOW)
        audio_host.grid(row=0, column=2, sticky="e")
        self._build_audio_settings_contents(audio_host)

    

    def create_frame(self):
        # Main content frame with two columns
        self.content_frame = tk.Frame(self.root, bg=BG_WINDOW)
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)  # Tree takes majority
        self.content_frame.grid_columnconfigure(1, weight=0, minsize=520)


    @staticmethod
    def _widget_is_within(widget, parent):
        while widget is not None:
            if widget is parent:
                return True
            widget = getattr(widget, "master", None)
        return False




    def setup_ui(self):
        """Setup the user interface"""
        self.create_top_bar()
        self._create_status_bar()
        self.create_frame()
        self.create_left_panel()
        self.create_queue_panel()
        self.update_select_all_button_style()
        self.update_collect_all_button_style()
        self.update_return_fx_button_style()
        self.update_normalize_button_style()
        self.update_wet_dry_button_style()
        self.update_ableton_version_menu()
        self.update_queue_tree()
        self.update_tree()


    
    def setup_drag_drop(self):
        """Setup drag and drop functionality"""
        try:
            from tkinterdnd2 import DND_FILES
            # Register tree for drag-drop
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind('<<Drop>>', self.drop_handler)
        except ImportError:
            messagebox.showwarning(
                "Drag & Drop Not Available",
                "tkinterdnd2 not installed.\nUse 'Browse Folders' button instead.\n\nInstall with: pip install tkinterdnd2"
            )
    
    def drop_handler(self, event):
        """Handle dropped files/folders"""
        files = parse_dropped_paths(self.root, event.data)
        for file_path in files:
            self.add_path(file_path)
    
    def open_bulk_fix_window(self, selected_projects=None):
         """Open Missing Media on its Bulk Fix tab."""
         self.open_missing_media_window(
             "bulk_fix", selected_projects=selected_projects
         )

    def open_missing_media_window(
        self, selected_tab="sample_index", selected_projects=None
    ):
         """Show the Sample Index and Bulk Fix tools in one tabbed window."""
         gui_missingmedia_tools.open_missing_media_window(
             self, selected_tab, selected_projects=selected_projects
         )

    def start_selected_media_scan(self, project_paths, mode):
         """Scan and fix selected projects asynchronously from the browser."""
         gui_missingmedia_tools.start_selected_media_scan(
             self, project_paths, mode
         )

    def open_sample_index_window(self):
         """Open Missing Media on its default Sample Index tab."""
         self.open_missing_media_window("sample_index")

    def _build_sample_index_contents(self, parent):
        gui_missingmedia_tools.build_sample_index_contents(self, parent)


    def open_naming_settings_window(self):
        """Open the separate naming-settings tool window."""
        gui_namingsettings.open_naming_settings_window(self)



    def _build_bulk_fix_contents(self, parent):
        gui_missingmedia_tools.build_bulk_fix_contents(self, parent)



def main(initial_paths=None):
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    app = AutoExportLauncherGUI(root, initial_paths=initial_paths)
    root.mainloop()

if __name__ == "__main__":
    multiprocessing.freeze_support()  # size-estimate worker processes in a frozen build
    from explorer_launch import gather_launch_paths
    launch_paths = gather_launch_paths()
    if launch_paths is not None:
        main(launch_paths)