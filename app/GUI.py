"""
AutoExportLauncher GUI - Drag and drop interface for AutoExport
Allows users to configure which operations to run for each project
"""
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from paths import data_file
import subprocess
from datetime import datetime
import queue
import time
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from export_path_config import get_export_path, set_export_path as set_export_path_config


import Size_estimator
from find_clusters import TAIL_BARS

try:
    import customtkinter as ctk
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("green")
    HAS_CUSTOMTKINTER = True
except ImportError:
    HAS_CUSTOMTKINTER = False

import threading
from sample_database import (
    load_sample_dirs,
    save_sample_dirs,
    build_or_update_index,
    get_index_status,
    load_index as load_sample_index,
)
from project_scanner import (
    load_project_dirs,
    save_project_dirs,
    find_all_projects,
)
from missing_media_resolver import resolve_missing_media_bulk, format_bulk_report
import ableton_build
import osc_installer
import naming_settings
import project_structure

# ---------- Palette ----------
BG_WINDOW      = "#1e1e1e"
BG_PANEL       = "#2B2B2B"
BG_INPUT       = "#1a1a1a"
BG_SELECTION   = "#49535B"
BG_SEPARATOR   = "#3e3e3e"

FG_PRIMARY     = "#e0e0e0"
FG_SECONDARY   = "#9d9d9d"
FG_MUTED       = "#555555"

ACCENT_BLUE    = "#6EBAF4"
ACCENT_ORANGE  = "#ffb454"
ACCENT_GREEN   = "#5fbf5f"
ACCENT_RED     = "#e06060"
ACCENT_CYAN    = "#4dc3c3"
ACCENT_NEUTRAL = "#9d9d9d"


# ---------- Tree Row Styling ----------
BG_ROW_EVEN    = "#141414"   # child row stripe A
BG_ROW_ODD     = "#000000"   # child row stripe B
BG_ROW_HEADER  = "#242424"   # parent/header row background
FG_ROW_HEADER  = FG_PRIMARY  # header row text colour

# ---------- Type ----------
FONT_UI      = ("Segoe UI", 9)
FONT_UI_SM   = ("Segoe UI", 8)
FONT_UI_BOLD = ("Segoe UI", 9, "bold")
FONT_HEADER  = ("Segoe UI", 10, "bold")
FONT_TITLE   = ("Segoe UI", 12, "bold")
FONT_MONO    = ("Segoe UI", 9)
FONT_TREE    = ("Segoe UI", 9)
FONT_TREE_HEADER = ("Segoe UI", 12, "bold")
FONT_TREE_BOLD = FONT_TREE_HEADER

# ---------- Spacing ----------
PAD_XS = 3
PAD_SM = 6
PAD_MD = 10
PAD_LG = 15

PROJECT_TREE_COLUMNS = ("enabled", "export", "collect", "returnfx", "normalize", "wetdry")
PROJECT_TREE_COLUMN_WIDTHS = {
    "#0": 200,
    "enabled": 20,
    "export": 82,
    "collect": 82,
    "returnfx": 86,
    "normalize": 88,
    "wetdry": 80,
}
PROJECT_TREE_EDIT_PANEL_WIDTH = sum(PROJECT_TREE_COLUMN_WIDTHS.values())


def _darken(hex_color, factor=0.65):
    """Return a darker version of a '#rrggbb' color string."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"#{int(r*factor):02x}{int(g*factor):02x}{int(b*factor):02x}"


LAUNCHER_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_SCRIPT = os.path.join(LAUNCHER_DIR, "orchestration.py")
TASK_FILE = data_file("EXPORT_TASKS.json")
QUEUE_FILE = data_file("QUEUE_STATE.json")
SIZE_CACHE_FILE = data_file("SIZE_ESTIMATE_CACHE.json")
SCRIPT_STATE_FILE = data_file("SCRIPT_STATE.json")
PAUSE_FLAG_FILE = data_file("PAUSE_FLAG.txt")
# Leave a core free for the GUI (and Ableton, if an export is running).
SIZE_ESTIMATE_WORKERS = max(1, min(3, (os.cpu_count() or 2) - 1))
# How long a queued project's mtime / recursive-nesting check stays fresh.
QUEUE_META_TTL = 30.0

def parse_dropped_paths(tk_widget, data):
    """
    Parse a tkinterdnd2 '<<Drop>>' event's data string into a list of
    normalized filesystem paths. Shared by every drag-and-drop target in this
    GUI (main project tree, sample library folders, bulk-fix project folders).
    """
    raw_items = tk_widget.tk.splitlist(data)  # handles braces and spaces
    paths = []
    for item in raw_items:
        p = item.strip('{}"')
        if p.startswith("file://"):
            from urllib.parse import urlparse, unquote
            uri = urlparse(p)
            p = unquote(uri.path or "")
            # Fix leading slash on Windows: /C:/...
            if os.name == "nt" and p.startswith("/") and len(p) > 3 and p[2] == ":":
                p = p.lstrip("/")
        p = os.path.normpath(p)
        if p:
            paths.append(p)
    return paths


def enable_folder_drop_target(widget, on_folder_dropped):
    """
    Register `widget` as a tkinterdnd2 drop target that only accepts folders,
    calling on_folder_dropped(path) once per valid dropped directory. Silently
    does nothing if tkinterdnd2 isn't installed (drag-and-drop is a nice-to-
    have; the "Add Folder..." browse button always remains available).
    """
    try:
        from tkinterdnd2 import DND_FILES
    except ImportError:
        return False

    def _on_drop(event):
        for path in parse_dropped_paths(widget, event.data):
            if os.path.isdir(path):
                on_folder_dropped(path)
            elif os.path.isfile(path):
                on_folder_dropped(os.path.dirname(path))

    widget.drop_target_register(DND_FILES)
    widget.dnd_bind('<<Drop>>', _on_drop)
    return True


# Colour-coded button convention used throughout the main window:
# white = Off, blue = Main, orange = Stems. Other action buttons use a
# semantically-appropriate colour (green = go/add, red = clear/remove,
# grey = reorder, cyan = utility/tool windows) but always follow the same
# "black background, coloured outline and text" look.
BTN_COLOR_OFF     = FG_SECONDARY
BTN_COLOR_MAIN    = ACCENT_BLUE
BTN_COLOR_STEMS   = ACCENT_ORANGE
BTN_COLOR_GO      = ACCENT_GREEN
BTN_COLOR_CLEAR   = ACCENT_RED
BTN_COLOR_NEUTRAL = ACCENT_NEUTRAL
BTN_COLOR_UTILITY = ACCENT_CYAN


def make_outline_button(parent, text, command, color, font=FONT_UI,
                         padx=6, pady=3, state=tk.NORMAL, width=None, **extra):
    """..."""
    kwargs = {}
    if width is not None:
        kwargs["width"] = width
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=BG_INPUT,
        fg=color,
        activebackground=_darken(color, 0.25),
        activeforeground=color,
        disabledforeground="#555555",
        highlightbackground=_darken(color),
        highlightcolor=_darken(color),
        highlightthickness=2,
        bd=0,
        relief=tk.FLAT,
        font=font,
        padx=padx,
        pady=pady,
        state=state,
        **kwargs,
        **extra
    )




class TaskManager:
    """Manages tasks in EXPORT_TASKS.json - single source of truth"""
    

    
    def __init__(self, task_file=TASK_FILE):
        self.task_file = task_file
        self.tasks = []
        # mtime of the task file as of our last load/save; anything else means
        # another process (the export script) changed it.
        self.known_mtime = None
        self.load_tasks()
        





    
    def load_tasks(self):
        """Load tasks from EXPORT_TASKS.json"""
        if os.path.exists(self.task_file):
            try:
                self.known_mtime = os.path.getmtime(self.task_file)
                with open(self.task_file, 'r', encoding='utf-8') as f:
                    loaded_tasks = json.load(f)
                self.tasks = [
                    task for task in loaded_tasks
                    if not (
                        isinstance(task, dict)
                        and isinstance(task.get("project_path"), str)
                        and ProjectFinder.is_backup_path(task["project_path"])
                    )
                ]
                if len(self.tasks) != len(loaded_tasks):
                    self.save_tasks()
            except Exception as e:
                print(f"Error loading tasks: {e}")
                self.tasks = []
        else:
            self.tasks = []
    
    def save_tasks(self):
        """Save tasks to EXPORT_TASKS.json"""
        try:
            with open(self.task_file, 'w', encoding='utf-8') as f:
                json.dump(self.tasks, f, indent=2)
            self.known_mtime = os.path.getmtime(self.task_file)
            


        except Exception as e:
            print(f"Error saving tasks: {e}")
    
    def add_task(self, project_path, export_stems, export_main, collect_all_and_save,
                 include_return_and_main_effects=True, normalize=False, wet_dry=False):
        """Add or update task in EXPORT_TASKS.json"""
        self.add_tasks([{
            "project_path": project_path,
            "export_stems": export_stems,
            "export_main": export_main,
            "collect_all_and_save": collect_all_and_save,
            "include_return_and_main_effects": include_return_and_main_effects,
            "normalize": normalize,
            "wet_dry": wet_dry,
        }])

    def add_tasks(self, entries):
        """Add or update several tasks (dicts of add_task's arguments) with a
        single save, so adding hundreds of projects doesn't rewrite the file
        hundreds of times."""
        index = {task.get("project_path"): task for task in self.tasks}
        for entry in entries:
            settings = {
                "export_stems": entry["export_stems"],
                "export_main": entry["export_main"],
                "collect_all_and_save": entry["collect_all_and_save"],
                "include_return_and_main_effects": entry.get("include_return_and_main_effects", True),
                "normalize": entry.get("normalize", False),
                "wet_dry": entry.get("wet_dry", False),
            }
            task = index.get(entry["project_path"])
            if task is not None:
                task.update(settings)
                task.setdefault("done", False)
            else:
                task = {"project_path": entry["project_path"], **settings, "done": False}
                self.tasks.append(task)
                index[entry["project_path"]] = task
        if entries:
            self.save_tasks()
    
    def remove_task(self, project_path):
        """Remove task from queue"""
        self.tasks = [t for t in self.tasks if t.get("project_path") != project_path]
        self.save_tasks()
    
    def move_task(self, from_index, to_index):
        """Move task in queue"""
        if 0 <= from_index < len(self.tasks) and 0 <= to_index < len(self.tasks):
            task = self.tasks.pop(from_index)
            self.tasks.insert(to_index, task)
            self.save_tasks()
    
    def clear_queue(self):
        """Clear all tasks from queue completely"""
        self.tasks = []
        self.save_tasks()

    def clear_failure(self, project_path):
        """Drop a task's recorded failure (it stays pending either way)."""
        for task in self.tasks:
            if task.get("project_path") == project_path:
                task.pop("failed", None)
                task.pop("failure", None)
        self.save_tasks()

    def remap_paths(self, old_root, new_root):
        """Point tasks under a moved folder at its new location."""
        for task in self.tasks:
            new_path = project_structure.remap_path(task["project_path"], old_root, new_root)
            if new_path != task["project_path"]:
                task["project_path"] = new_path
                task.pop("failed", None)
                task.pop("failure", None)
        self.save_tasks()
    
    def get_tasks(self):
        """Get all tasks"""
        return self.tasks

class ProjectFinder:
    """Find Ableton projects in directory tree"""

    SKIP_DIR_NAMES = {"backup", "abletoolz_backup"}

    @staticmethod
    def is_backup_path(path):
        """Return whether a path is inside a backup directory."""
        return any(
            part.casefold() in ProjectFinder.SKIP_DIR_NAMES
            for part in Path(path).parts
        )
    
    @staticmethod
    def find_projects(root_path):
        """Recursively find all .als files and organize by folder"""
        projects_by_folder = {}

        if ProjectFinder.is_backup_path(root_path):
            return projects_by_folder

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [
                d for d in dirnames
                if d.casefold() not in ProjectFinder.SKIP_DIR_NAMES
            ]

            als_files = [f for f in filenames if f.endswith('.als') and not f.startswith('._')]
            
            if als_files:
                full_paths = [os.path.join(dirpath, f) for f in als_files]
                projects_by_folder[dirpath] = {
                    "path": dirpath,
                    "files": full_paths
                }
        
        return projects_by_folder

class AutoExportLauncherGUI:
    """GUI for AutoExport launcher"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("AutoExport Launcher")
        self.root.geometry("1200x620")
        self.root.resizable(True, False)  # Prevent window resizing
        self.root.configure(bg=BG_WINDOW)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        self.projects_by_folder = {}
        self.selected_projects = {}
        self.tree_item_to_path = {} 
        
        self.size_cache = {}  
        self._load_size_cache()    # cache_key -> result dict (or {"error": ...})
        self.size_estimate_total = 0.0
        # Size estimates parse whole .als files in pure Python, which holds the
        # GIL and starves Tk if done in threads. They run in a pool of
        # low-priority worker processes instead, fed by a background thread so
        # spawning workers never blocks the UI either.
        self.size_pending = set()          # cache keys queued or running
        self.size_jobs = queue.Queue()     # (key, task) -> submitter thread
        self.size_results = queue.Queue()  # (key, result | None) -> UI thread
        self._size_pool = None
        self._closing = False
        threading.Thread(target=self._size_submitter, name="size-submit", daemon=True).start()

        # Per-project filesystem facts (mtime for the cache key, recursive
        # nesting) are gathered off the UI thread too: on a cold HDD a few
        # stats per queued project add up to seconds.
        self.task_meta = {}                # project_path -> {"mtime", "recursive", "checked"}
        self.meta_pending = set()
        self.meta_results = queue.Queue()
        self.meta_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="queue-meta")
        self.queue_path_to_item = {}
        self.queue_item_errors = {}

        self.multi_selection = set()
        self.last_selected_item = None
        # Queue management
        self.task_manager = TaskManager()
        
        # Toggle state variables
        self.select_all_enabled = tk.BooleanVar(value=True)  # Select All toggle state
        self.collect_all_enabled = tk.BooleanVar(value=True)  # Collect All & Save toggle state
        self.return_fx_all_enabled = tk.BooleanVar(value=True)  # Include Return and Main Effects toggle state
        self.normalize_all_enabled = tk.BooleanVar(value=True)  # Normalize toggle state
        self.wet_dry_all_enabled = tk.BooleanVar(value=False)  # Wet+Dry toggle state
        
        # State tracking for pause/resume
        self.script_running = False  # Is the main script currently running?
        self.script_paused = False   # Is the main script paused?
        self.pause_toggle_button = None  # Reference to pause button in main window
        
        self.size_render_settings = {"sample_rate": 44100, "bit_depth": 32, "channels": 2}
        # Cheap when already configured; only scans the default install
        # folders on first run or if a configured exe has disappeared.
        ableton_build.ensure_detected()
        self.setup_ui()
        self.setup_drag_drop()
        self.start_file_watcher()  # Watch EXPORT_TASKS.json for changes
        self.root.after(400, self._check_ableton_setup)

    def _get_total_size_estimate(self):
        """Sum cached total_bytes for only the tasks currently in the queue."""
        total_bytes = 0
        for task in self.task_manager.get_tasks():
            size = self._task_size_bytes(task)
            if size is not None:
                total_bytes += size
        return total_bytes

    def _task_size_bytes(self, task):
        """Estimated bytes for a task, or None while pending / on error.
        Wet+Dry renders everything twice, so it shows double (an upper bound:
        a dry pass is skipped when there are no effects to switch off)."""
        cached = self.size_cache.get(self._size_cache_key(task))
        if not cached or "error" in cached:
            return None
        return cached["total_bytes"] * (2 if task.get("wet_dry", False) else 1)

    def _size_cache_key(self, task):
        """Cache key for a task's estimate, or None until the background
        inspection has read the project's mtime."""
        path = task["project_path"]
        meta = self.task_meta.get(path)
        if meta is None:
            return None
        s = self.size_render_settings
        return (path, meta["mtime"],
                bool(task.get("export_stems", False)),
                bool(task.get("export_main", False)),
                bool(task.get("include_return_and_main_effects", False)),
                s["sample_rate"], s["bit_depth"], s["channels"])

    def request_size_estimates(self):
        for task in self.task_manager.get_tasks():
            self._request_size_estimate(task)

    def _request_size_estimate(self, task):
        key = self._size_cache_key(task)
        if key is None or key in self.size_cache or key in self.size_pending:
            return
        self.size_pending.add(key)
        self.size_jobs.put((key, dict(task)))

    def _size_submitter(self):
        """Background thread: hands estimate jobs to the process pool."""
        while True:
            key, task = self.size_jobs.get()
            if self._closing:
                return
            try:
                if self._size_pool is None:
                    self._size_pool = ProcessPoolExecutor(
                        max_workers=SIZE_ESTIMATE_WORKERS,
                        initializer=Size_estimator.lower_worker_priority,
                    )
                future = self._size_pool.submit(
                    Size_estimator.estimate_task, task, dict(self.size_render_settings)
                )
            except Exception:
                # The pool broke (a worker died); start a fresh one next time.
                # This job isn't cached, so a later refresh retries it.
                self._size_pool = None
                self.size_results.put((key, None))
                continue
            future.add_done_callback(
                lambda f, key=key: self.size_results.put((key, self._future_result(f)))
            )

    @staticmethod
    def _future_result(future):
        try:
            return future.result()
        except Exception:
            return None  # cancelled or pool broken: not cached, retried later

    @staticmethod
    def _inspect_project(path):
        """Background thread: filesystem facts the queue view needs."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        try:
            recursive = project_structure.plan_recursive_fix(path)
        except OSError:
            recursive = None
        return {"mtime": mtime, "recursive": recursive, "checked": time.monotonic()}

    def _request_inspections(self, tasks):
        """Queue background checks for projects never inspected or not
        inspected for QUEUE_META_TTL seconds."""
        now = time.monotonic()
        for task in tasks:
            path = task["project_path"]
            if path in self.meta_pending:
                continue
            meta = self.task_meta.get(path)
            if meta is not None and now - meta["checked"] < QUEUE_META_TTL:
                continue
            self.meta_pending.add(path)
            future = self.meta_executor.submit(self._inspect_project, path)
            future.add_done_callback(
                lambda f, path=path: self.meta_results.put((path, self._future_result(f)))
            )

    def _invalidate_task_meta(self, paths):
        """Make the next queue refresh re-check these projects (their stale
        data stays on screen until the new check lands)."""
        for path in paths:
            meta = self.task_meta.get(path)
            if meta is not None:
                meta["checked"] = float("-inf")

    def poll_size_estimates(self):
        """UI thread: apply finished background work to just the affected rows."""
        tasks_by_path = None
        changed = False
        while True:
            try:
                path, meta = self.meta_results.get_nowait()
            except queue.Empty:
                break
            self.meta_pending.discard(path)
            if meta is None:
                continue
            self.task_meta[path] = meta
            tasks_by_path = tasks_by_path or {t["project_path"]: t for t in self.task_manager.get_tasks()}
            task = tasks_by_path.get(path)
            if task is not None:
                self._refresh_queue_row(task)
                self._request_size_estimate(task)
                changed = True
        while True:
            try:
                key, result = self.size_results.get_nowait()
            except queue.Empty:
                break
            self.size_pending.discard(key)
            if result is None:
                continue
            self.size_cache[key] = result
            tasks_by_path = tasks_by_path or {t["project_path"]: t for t in self.task_manager.get_tasks()}
            task = tasks_by_path.get(key[0])
            if task is not None:
                self._refresh_queue_row(task)
                changed = True
        if changed:
            self._update_size_total_label()
        self.root.after(200, self.poll_size_estimates)

    def on_close(self):
        self._closing = True
        self._save_size_cache()
        self.meta_executor.shutdown(wait=False, cancel_futures=True)
        pool = self._size_pool
        if pool is not None:
            # Don't wait for in-flight estimates (a big set can take seconds).
            for proc in list((getattr(pool, "_processes", None) or {}).values()):
                try:
                    proc.terminate()
                except Exception:
                    pass
            pool.shutdown(wait=False, cancel_futures=True)
        self.size_jobs.put((None, None))  # wake the submitter so it exits
        self.root.destroy()


    def _load_size_cache(self):
        if not os.path.exists(SIZE_CACHE_FILE):
            return
        try:
            with open(SIZE_CACHE_FILE, "r", encoding="utf-8") as f:
                records = json.load(f)
        except Exception as e:
            print(f"Error loading size cache: {e}")
            return
        for r in records:
            if (not isinstance(r.get("result"), dict)
                    or "render_ranges" not in r["result"]
                    or r["result"].get("tail_padding_bars") != TAIL_BARS):
                continue
            key = (r["path"], r["mtime"], r["export_stems"], r["export_main"],
                   r["include_return_and_main_effects"], r["sample_rate"],
                   r["bit_depth"], r["channels"])
            self.size_cache[key] = r["result"]

    def _prune_size_cache(self):
        """Drop entries for projects that no longer exist or have since changed."""
        stale = []
        for key in self.size_cache:
            path, mtime = key[0], key[1]
            meta = self.task_meta.get(path)
            if meta is not None:
                current = meta["mtime"]
            else:
                try:
                    current = os.path.getmtime(path)
                except OSError:
                    current = None
            if current is None or current != mtime:
                stale.append(key)
        for key in stale:
            del self.size_cache[key]

    def _save_size_cache(self):
        self._prune_size_cache()
        records = []
        for key, result in self.size_cache.items():
            path, mtime, export_stems, export_main, include_rfx, sample_rate, bit_depth, channels = key
            records.append({
                "path": path, "mtime": mtime,
                "export_stems": export_stems, "export_main": export_main,
                "include_return_and_main_effects": include_rfx,
                "sample_rate": sample_rate, "bit_depth": bit_depth, "channels": channels,
                "result": result,
            })
        try:
            with open(SIZE_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
        except Exception as e:
            print(f"Error saving size cache: {e}")


    def create_menu(self):
        menubar = tk.Menu(
            self.root,
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            activebackground=BG_SELECTION,
            activeforeground=FG_PRIMARY,
            bd=0,
            relief=tk.FLAT,
        )

        file_menu = tk.Menu(
            menubar,
            tearoff=0,
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            activebackground=BG_SELECTION,
            activeforeground=FG_PRIMARY,
            bd=0,
            relief=tk.FLAT,
        )


        file_menu.add_command(label="Add Folder…", command=self.browse_folders)
        file_menu.add_separator()
        file_menu.add_command(label="Set Export Path…", command=self.set_export_path)
        file_menu.add_separator()
        file_menu.add_command(
            label="Set Ableton Live Exe…",
            command=lambda: self._prompt_for_ableton_exe(ableton_build.RELEASE),
        )
        file_menu.add_command(
            label="Set Ableton Live Beta Exe…",
            command=lambda: self._prompt_for_ableton_exe(ableton_build.BETA),
        )
        file_menu.add_command(label="Install AbletonOSC to User Library…", command=self.install_ableton_osc)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(
                        menubar,
            tearoff=0,
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            activebackground=BG_SELECTION,
            activeforeground=FG_PRIMARY,
            bd=0,
            relief=tk.FLAT,
        )

        edit_menu.add_command(
            label="Select All",
            accelerator="Ctrl+A",
            command=self.select_all_rows,
        )
        edit_menu.add_command(
            label="Deselect All",
            accelerator="Esc",
            command=self.clear_multi_selection,
        )
        edit_menu.add_command(
            label="Invert Selection",
            command=self.invert_selection,
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Select Latest Modified",
            command=self.select_latest_modified,
        )
        edit_menu.add_command(
            label="Enable Only Latest Modified",
            command=self.enable_only_latest_modified,
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Remove Selected",
            accelerator="Del",
            command=self.clear_selected,
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="Enable Selected",
            command=self.enable_selected,
        )
        edit_menu.add_command(
            label="Disable Selected",
            accelerator="Ctrl+D",
            command=self.disable_selected,
        )
        edit_menu.add_command(
            label="Toggle Enabled for Selection",
            accelerator="Ctrl+E",
            command=self.toggle_enabled_selected,
        )
        edit_menu.add_separator()
        edit_menu.add_command(label="Clear All", command=self.clear_all)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        tools_menu = tk.Menu(
            menubar,
            tearoff=0,
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            activebackground=BG_SELECTION,
            activeforeground=FG_PRIMARY,
            bd=0,
            relief=tk.FLAT,
        )
        tools_menu.add_command(label="Bulk Fix Missing Media", command=self.open_bulk_fix_window)
        tools_menu.add_command(label="Sample Index", command=self.open_sample_index_window)
        tools_menu.add_command(label="Naming Settings", command=self.open_naming_settings_window)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        self.root.config(menu=menubar)
    def create_frame(self):
        # Main content frame with two columns
        self.content_frame = tk.Frame(self.root, bg=BG_WINDOW)
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)  # Tree takes majority
        self.content_frame.grid_columnconfigure(1, weight=0, minsize=400)  # Queue is fixed width

    def create_left_panel(self):
        # ===== LEFT SIDE: Tree container + scrollbar =====
        self.tree_frame = tk.Frame(self.content_frame, bg="#0c0c0c")
        self.tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.tree_frame.grid_rowconfigure(0, weight=1)  # Treeview takes majority of the height
        self.tree_frame.grid_rowconfigure(1, weight=0)  # "Change All" box is fixed height, bottom-anchored
        self.tree_frame.grid_columnconfigure(0, weight=1)
        
        # Configure scrollbar style first
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Dark.Vertical.TScrollbar", background="#2b2b2b", troughcolor="#292929", bordercolor="#3E3E3E", darkcolor="#313131", lightcolor="#535353", width=18)
        style.map("Dark.Vertical.TScrollbar", 
                  background=[("active", "#3e3e3e"), ("pressed", "#acacac")],
                  darkcolor=[("active", "#666666"), ("pressed", "#ababab")],
                  lightcolor=[("active", "#5d5d5d"), ("pressed", "#7e7e7e")])
        
        scrollbar = ttk.Scrollbar(self.tree_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
        scrollbar.grid(row=0, column=1, sticky="ns")
        
        # Treeview with scrollbar
        self.tree = ttk.Treeview(
            self.tree_frame,
            yscrollcommand=scrollbar.set,
            height=20,
            style="Dark.Treeview",
            columns=PROJECT_TREE_COLUMNS,
            show="tree",
        )
        self.tree.column(
            "#0",
            width=PROJECT_TREE_COLUMN_WIDTHS["#0"],
            anchor="w",
            stretch=True,
        )
        self.tree.column(
            "enabled",
            width=PROJECT_TREE_COLUMN_WIDTHS["enabled"],
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "export",
            width=PROJECT_TREE_COLUMN_WIDTHS["export"],
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "collect",
            width=PROJECT_TREE_COLUMN_WIDTHS["collect"],
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "returnfx",
            width=PROJECT_TREE_COLUMN_WIDTHS["returnfx"],
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "normalize",
            width=PROJECT_TREE_COLUMN_WIDTHS["normalize"],
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "wetdry",
            width=PROJECT_TREE_COLUMN_WIDTHS["wetdry"],
            anchor="center",
            stretch=False,
        )
        
        
        scrollbar.config(command=self.tree.yview)
        self.tree.grid(row=0, column=0, sticky="nsew")
        
        # Configure treeview style
        style.configure("Dark.Treeview", background="#000000", foreground="#ffffff", fieldbackground=BG_INPUT, font=FONT_TREE)
        style.configure("Dark.Treeview.Heading", background="#2b2b2b", foreground="#ffffff", font=FONT_TREE_HEADER)
        style.map("Dark.Treeview.Heading", background=[("active", "#2b2b2b")])  # Keep same color on hover
        style.map("Dark.Treeview", foreground=[("disabled", "#888888")], background=[("selected", BG_SELECTION)])  # Lighter selection color
        
        # Smaller font for queue tree
        style.configure("Queue.Treeview", background="#000000", foreground="#ffffff", fieldbackground=BG_INPUT, font=FONT_TREE)
        style.configure("Queue.Treeview.Heading", background="#2b2b2b", foreground="#ffffff", font=FONT_TREE_HEADER)
        style.map("Queue.Treeview.Heading", background=[("active", "#2b2b2b")])  # Match left panel heading style
        style.map("Queue.Treeview", foreground=[("disabled", "#888888")], background=[("selected", BG_SELECTION)])
        
        # Configure arrow/expand indicator color
        style.configure("Dark.Treeview", arrowcolor="#999999")
        style.map("Dark.Treeview", arrowcolor=[("active", "#cccccc"), ("pressed", "#dddddd")])
        
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Control-a>", self.select_all_rows)
        self.tree.bind("<Control-A>", self.select_all_rows)
        self.tree.bind("<Shift-a>", self.select_all_rows)
        self.tree.bind("<Shift-A>", self.select_all_rows)
        self.tree.bind("<Shift-Up>", lambda e: self.extend_selection(-1))
        self.tree.bind("<Shift-Down>", lambda e: self.extend_selection(1))
        self.tree.bind("<Escape>", lambda e: self.clear_multi_selection())
        self.tree.bind("<Delete>", self.on_project_delete_key)
        self.tree.bind("<Control-e>", self.toggle_enabled_selected)
        self.tree.bind("<Control-E>", self.toggle_enabled_selected)
        self.tree.bind("<Control-d>", self.disable_selected)
        self.tree.bind("<Control-D>", self.disable_selected)


        
        # Bind mousewheel events for faster scrolling
        self.tree.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        self.tree.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down
        



    def create_queue_panel(self):
        """Create the right-side export queue panel"""
        
        # ===== RIGHT SIDE: Queue panel =====
        self.queue_panel = tk.LabelFrame(
            self.content_frame,
            text="   Export Queue",
            bg=BG_WINDOW,
            fg=FG_PRIMARY,
            font=FONT_HEADER,
            labelanchor="nw",
            relief=tk.FLAT,
            bd=0,
            
        )
        self.queue_panel.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.queue_panel.grid_rowconfigure(1, weight=1)
        self.queue_panel.grid_columnconfigure(0, weight=1)
        self.queue_panel.pack_propagate(False)
        
        # Queue buttons frame (top)
        queue_btn_frame = tk.Frame(self.queue_panel, bg="#2b2b2b")
        queue_btn_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        queue_btn_frame.grid_columnconfigure(0, weight=1)
        queue_btn_frame.grid_columnconfigure(1, weight=0)
        queue_btn_frame.grid_columnconfigure(2, weight=0)
        
        make_outline_button(
            queue_btn_frame, "Add to Queue", self.add_selected_to_queue, ACCENT_GREEN,
            padx=8, pady=4
        ).grid(row=0, column=0, sticky="ew", padx=2)
        
        make_outline_button(
            queue_btn_frame, "Clear", self.clear_queue, BTN_COLOR_CLEAR,
            font=FONT_UI, padx=4, pady=4
        ).grid(row=0, column=1, sticky="e", padx=2)

        self.size_total_label = tk.Label(
            queue_btn_frame,
            text="Estimated size: —",
            bg="#2b2b2b",
            fg=FG_SECONDARY,
            font=FONT_UI,
            anchor="w",
        )
        self.size_total_label.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        
        # Queue treeview (displays queue items)
        queue_tree_frame = tk.Frame(self.queue_panel, bg="#0c0c0c")
        queue_tree_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        queue_tree_frame.grid_rowconfigure(0, weight=1)
        queue_tree_frame.grid_columnconfigure(0, weight=1)
        
        queue_scrollbar = ttk.Scrollbar(queue_tree_frame, orient="vertical", style="Dark.Vertical.TScrollbar")
        queue_scrollbar.grid(row=0, column=1, sticky="ns")
        
        self.queue_tree = ttk.Treeview(
            queue_tree_frame,
            yscrollcommand=queue_scrollbar.set,
            height=15,
            style="Queue.Treeview",
            columns=("task", "size", "done"),
            show="tree",
        )
        self.queue_tree.column(
            "#0",
            width=230,
            minwidth=150,
            anchor="w",
            stretch=True,
        )

        self.queue_tree.column(
            "task",
            width=105,
            anchor="center",
            stretch=False,
        )

        self.queue_tree.column(
            "size",
            width=105,
            anchor="center",
            stretch=False,
        )

        self.queue_tree.column(
            "done",
            width=45,
            anchor="center",
            stretch=False,
        )



        queue_scrollbar.config(command=self.queue_tree.yview)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        
        
        self.queue_tree.bind("<Button-1>", self.on_queue_tree_click)
        self.queue_tree.bind("<MouseWheel>", self._on_queue_mousewheel)
        self.queue_tree.bind("<Button-4>", self._on_queue_mousewheel)
        self.queue_tree.bind("<Button-5>", self._on_queue_mousewheel)
        self.queue_tree.bind("<Delete>", self.on_queue_delete_key)  # Delete key to remove items
        self.queue_tree.bind("<Control-a>", self.select_all_queue_rows)
        self.queue_tree.bind("<Control-A>", self.select_all_queue_rows)
        self.queue_tree.bind("<Double-1>", self.on_queue_double_click)
        self.queue_item_errors = {}  # tree item id -> ("recursive", plan) | ("failed", task)


        # Drag-drop for queue reordering
        self.queue_tree.bind("<Button-1>", self.on_queue_drag_start, add=True)
        self.queue_tree.bind("<B1-Motion>", self.on_queue_drag_motion)
        self.queue_tree.bind("<ButtonRelease-1>", self.on_queue_drag_release)
        self.dragged_queue_item = None
        self.drag_start_y = None  # Track drag start position
        self.drop_indicator_item = None  # Track insertion point indicator
        
        # Queue action buttons (bottom)
        queue_action_frame = tk.Frame(self.queue_panel, bg="#2b2b2b")
        queue_action_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        queue_action_frame.grid_columnconfigure(0, weight=1)
        queue_action_frame.grid_columnconfigure(1, weight=1)
        queue_action_frame.grid_columnconfigure(2, weight=1)
        
        make_outline_button(
            queue_action_frame, "▲ Up", self.move_queue_item_up, BTN_COLOR_NEUTRAL,
            font=FONT_UI, padx=3, pady=3
        ).grid(row=0, column=0, sticky="ew", padx=2)
        
        make_outline_button(
            queue_action_frame, "Remove", self.remove_from_queue, BTN_COLOR_CLEAR,
            font=FONT_UI, padx=3, pady=3
        ).grid(row=0, column=1, sticky="ew", padx=2)
        
        make_outline_button(
            queue_action_frame, "▼ Down", self.move_queue_item_down, BTN_COLOR_NEUTRAL,
            font=FONT_UI, padx=3, pady=3
        ).grid(row=0, column=2, sticky="ew", padx=2)
    def create_bottom_buttons(self):

        
        # Bottom buttons frame - compact layout
        tk.Frame(self.root, bg=BG_SEPARATOR, height=1).pack(fill=tk.X)
        self.button_frame = tk.Frame(self.root, bg=BG_WINDOW)
        self.button_frame.pack(fill=tk.X, padx=10, pady=10)
        # Middle: optional global controls. Project editing and tool windows live
        # in the top menus to keep this row focused on export runtime controls.
        middle_frame = tk.Frame(self.button_frame, bg="#2b2b2b")
        middle_frame.pack(side=tk.LEFT, fill=tk.X, padx=10, expand=True)
        
        # Which Ableton build the main script drives. Always shown; it only
        # switches to the beta when a Live 12 Beta exe is configured.
        self.ableton_build_btn = make_outline_button(
            middle_frame, "", self.toggle_ableton_build, BTN_COLOR_OFF
        )
        self.ableton_build_btn.pack(side=tk.LEFT, padx=3)
        self.update_ableton_build_button_style()
        
        # Pause/Resume toggle button (single button, changes appearance based on state)
        # pause_frame = tk.Frame(self.button_frame, bg="#2b2b2b")
        # pause_frame.pack(side=tk.RIGHT, padx=5)
        
        #self.pause_toggle_button = tk.Button(
        #    pause_frame,
        #    text="⏸",
        #    command=self.toggle_pause_resume,
        #    bg="#2b2b2b",  # Invisible (matches background) when not running
        #    fg="#2b2b2b",
        #    font=FONT_UI,
        #    padx=6,
        #    pady=3,
        #    relief=tk.FLAT,
        #    bd=0,
        #    highlightthickness=0
        #)
        #self.pause_toggle_button.pack(side=tk.LEFT, padx=2)
        
        # Right side: Start Export button
        make_outline_button(
            self.button_frame, "Start", self.start_export_from_queue, BTN_COLOR_GO,
            font=FONT_TITLE,
            padx=18, pady=6,
        ).pack(side=tk.RIGHT, padx=2)

        # Set initial appearance for the two "Change All" toggle buttons
        self.update_select_all_button_style()
        self.update_collect_all_button_style()
        self.update_return_fx_button_style()
        self.update_normalize_button_style()
        self.update_wet_dry_button_style()
        self.update_queue_tree()
        self.update_tree()

    # def sync_change_selected_width(self, event=None):
    #     """Keep Change Selected controls aligned with the project tree columns."""

    #     tree_width = self.tree.winfo_width()

    #     fixed_width = (
    #         PROJECT_TREE_COLUMN_WIDTHS["enabled"]
    #         + PROJECT_TREE_COLUMN_WIDTHS["export"]
    #         + PROJECT_TREE_COLUMN_WIDTHS["collect"]
    #         + PROJECT_TREE_COLUMN_WIDTHS["returnfx"]
    #         + PROJECT_TREE_COLUMN_WIDTHS["normalize"]
    #     )

    #     # Leave room for the scrollbar and small internal spacing.
    #     project_name_width = max(150, tree_width - fixed_width - 20)

    #     self.tree.column(
    #         "#0",
    #         width=project_name_width,
    #     )

    #     self.change_all_frame.grid_columnconfigure(
    #         0,
    #         minsize=project_name_width,
    #         weight=0,
    #     )

    def setup_ui(self):
        """Setup the user interface"""
        self.create_menu()
        self.create_frame()
        self.create_left_panel()
        self.create_queue_panel()
        self.create_bottom_buttons()


    
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

    def browse_folders(self):
        """Open folder browser"""
        folder = filedialog.askdirectory(title="Select folder containing Ableton projects")
        if folder:
            self.add_path(folder)
    
    def add_path(self, path):
        """Add folder/file to project list"""
        if not os.path.exists(path):
            messagebox.showerror("Error", f"Path does not exist:\n{path}")
            return
        
        projects = {}
        
        if (
            os.path.isfile(path)
            and path.endswith('.als')
            and not ProjectFinder.is_backup_path(path)
        ):
            folder_path = os.path.dirname(path)
            projects[folder_path] = {
                "path": folder_path,
                "files": [path]
            }
        elif os.path.isdir(path):
            projects = ProjectFinder.find_projects(path)
        
        if not projects:
            messagebox.showinfo("No Projects Found", f"No .als files found in:\n{path}")
            return
        
        # Add to our collection (keys are full folder paths, so no collisions)
        self.projects_by_folder.update(projects)

        # Default-config every file that isn't already configured
        for proj in projects.values():
            for file_path in proj["files"]:
                if file_path not in self.selected_projects:
                    self.selected_projects[file_path] = self._default_project_config(
                        file_path
                    )
        
        # Refresh UI
        self.update_tree()

    def set_export_path(self):
        """Allow the user to choose the base export folder."""

        current_path = get_export_path()

        selected_folder = filedialog.askdirectory(
            title="Select Export Folder",
            initialdir=current_path if os.path.isdir(current_path) else None,
            mustexist=True,
        )

        if not selected_folder:
            return False

        try:
            set_export_path_config(selected_folder)

            messagebox.showinfo(
                "Export Folder",
                f"Export folder set to:\n\n{selected_folder}"
            )
            return True

        except Exception as e:
            messagebox.showerror(
                "Export Folder",
                f"Could not save export folder:\n\n{e}"
            )
            return False

    def update_tree(self):
        """Update the project tree view."""
        selected_paths = self._selected_project_paths()
        anchor_path = None
        if self.last_selected_item in self.tree_item_to_path:
            anchor_path = self.tree_item_to_path[self.last_selected_item]

        for item in self.tree.get_children():
            self.tree.delete(item)

        self.tree_item_to_path = {}
        self.multi_selection = set()
        self.last_selected_item = None
        # # self.update_change_all_label()

        for folder_path, project_info in sorted(
            self.projects_by_folder.items(),
            key=lambda kv: os.path.basename(kv[0]).lower(),
        ):
            folder_name = os.path.basename(folder_path)
            if folder_name.endswith(" Project"):
                folder_name = folder_name[:-8]
            folder_id = self.tree.insert(
                "", "end", text=f"📁 {folder_name}", open=True
            )

            has_enabled = False

            try:
                files_sorted = sorted(
                    project_info["files"],
                    key=lambda f: os.path.getmtime(f),
                    reverse=True,
                )
            except OSError:
                files_sorted = sorted(project_info["files"])

            for row_idx, file_path in enumerate(files_sorted):
                cfg = self.selected_projects.get(
                    file_path,
                    self._default_project_config(file_path),
                )

                is_enabled = cfg.get("enabled", True)
                export_state = cfg.get("export_state", "stems")
                if is_enabled:
                    has_enabled = True

                file_name = os.path.basename(file_path)
                if file_name.endswith(".als"):
                    file_name = file_name[:-4]

                # Column values
                enabled_text = "☑" if is_enabled else "☐"
                if export_state == "main":
                    export_text = "Main"
                elif export_state == "off":
                    export_text = "—"
                else:
                    export_text = "Stems"
                collect_text = "Collect" if cfg["collect_all_and_save"] else "—"
                returnfx_text = ("Return FX"
                                if cfg.get("include_return_and_main_effects", True)
                                else "—")
                normalize_text = ("Normalize"
                                if cfg.get("normalize", False)
                                else "—")
                wetdry_text = "Wet+Dry" if cfg.get("wet_dry", False) else "—"

                state = "enabled" if is_enabled else "disabled"
                tag = state

                iid = self.tree.insert(
                    folder_id,
                    "end",
                    text=f"  {file_name}",
                    values=(enabled_text, export_text, collect_text,
                            returnfx_text, normalize_text, wetdry_text),
                    tags=(tag,),
                )
                self.tree_item_to_path[iid] = file_path
                if file_path in selected_paths:
                    self.multi_selection.add(iid)
                if file_path == anchor_path:
                    self.last_selected_item = iid

            if has_enabled:
                self.tree.item(folder_id, tags=("folder_enabled",))
            else:
                self.tree.item(folder_id, tags=("folder_disabled",))

        self.tree.tag_configure("enabled", foreground="#ffffff")
        self.tree.tag_configure("disabled", foreground="#5B5B5B")
        # self.tree.tag_configure("folder_enabled", foreground="#ffffff")
        # self.tree.tag_configure("folder_disabled", foreground="#555555")
        self.tree.tag_configure("folder_enabled", foreground=FG_ROW_HEADER,
                                 background=BG_ROW_HEADER, font=FONT_TREE_BOLD)
        self.tree.tag_configure("folder_disabled", foreground="#555555",
                               background=BG_ROW_HEADER, font=FONT_TREE_BOLD)

        if not self.projects_by_folder:
            self.tree.insert(
                "", "end",
                text="  Drop folders here, or use File > Add Folder…",
                tags=("placeholder",),
            )
            self.tree.tag_configure(
                "placeholder",
                foreground=FG_MUTED,
                font=("Consolas", 8, "italic"),
            )

        self.refresh_multi_selection_visuals()
        # # self.update_change_all_label()
    
    def on_tree_click(self, event):
        """Handle tree row selection and inline setting toggles."""
        item = self.tree.identify("item", event.x, event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return

        # Folder rows: ignore
        if not self.tree.parent(item):
            return

        if item not in self.tree_item_to_path:
            return

        if self._handle_row_selection_click(item, event):
            return "break"

        if col == "#0":
            self.multi_selection = {item}
            self.last_selected_item = item
            self.refresh_multi_selection_visuals()
            # # self.update_change_all_label()
            return "break"

        if item not in self.multi_selection:
            self.multi_selection = {item}
            self.last_selected_item = item

        self._apply_column_action_to_selection(col)
        return "break"

    def _default_project_config(self, project_path):
        return {
            "project_path": project_path,
            "enabled": True,
            "collect_all_and_save": True,
            "export_state": "stems",
            "include_return_and_main_effects": True,
            "normalize": True,
            "wet_dry": False,
        }

    def _selected_project_paths(self):
        return [
            self.tree_item_to_path[iid]
            for iid in self._visible_file_items()
            if iid in self.multi_selection and iid in self.tree_item_to_path
        ]

    def _handle_row_selection_click(self, item, event):
        if event.state & 0x1:
            self._select_range_to_item(item)
            return True

        if event.state & 0x4:
            if item in self.multi_selection:
                self.multi_selection.remove(item)
                if self.last_selected_item == item:
                    self.last_selected_item = next(iter(self.multi_selection), None)
            else:
                self.multi_selection.add(item)
                self.last_selected_item = item
            self.refresh_multi_selection_visuals()
            # # self.update_change_all_label()
            return True

        return False

    def _select_range_to_item(self, item):
        items = self._visible_file_items()
        if item not in items:
            return
        if self.last_selected_item not in items:
            self.last_selected_item = item
            self.multi_selection = {item}
        else:
            start_idx = items.index(self.last_selected_item)
            end_idx = items.index(item)
            lo = min(start_idx, end_idx)
            hi = max(start_idx, end_idx)
            self.multi_selection = set(items[lo:hi + 1])
        self.refresh_multi_selection_visuals()
        # # self.update_change_all_label()

    def _apply_column_action_to_selection(self, col):
        targets = self._target_projects()
        if not targets:
            return

        if col == "#1":  # Enabled
            new_state = not all(c.get("enabled", True) for c in targets)
            for cfg in targets:
                cfg["enabled"] = new_state
        elif col == "#2":  # Export
            for cfg in targets:
                cfg["export_state"] = self._next_export_state(
                    cfg.get("export_state", "stems")
                )
        elif col == "#3":  # Collect All
            new_state = not all(c["collect_all_and_save"] for c in targets)
            for cfg in targets:
                cfg["collect_all_and_save"] = new_state
        elif col == "#4":  # Return FX
            new_state = not all(
                c.get("include_return_and_main_effects", True) for c in targets
            )
            for cfg in targets:
                cfg["include_return_and_main_effects"] = new_state
        elif col == "#5":  # Normalize
            new_state = not all(c.get("normalize", False) for c in targets)
            for cfg in targets:
                cfg["normalize"] = new_state
        elif col == "#6":  # Wet+Dry
            new_state = not all(c.get("wet_dry", False) for c in targets)
            for cfg in targets:
                cfg["wet_dry"] = new_state
        else:
            return

        self.update_tree()

    def _next_export_state(self, current):
        order = ("stems", "main", "off")
        try:
            return order[(order.index(current) + 1) % len(order)]
        except ValueError:
            return "stems"

    def _visible_file_items(self):
        """Return file-row item IDs in display order, skipping folder rows."""
        items = []
        for folder_iid in self.tree.get_children(""):
            for child in self.tree.get_children(folder_iid):
                items.append(child)
        return items

    def select_all_rows(self, event=None):
        self.multi_selection = set(self._visible_file_items())
        self.last_selected_item = None
        self.refresh_multi_selection_visuals()
       # # self.update_change_all_label()
        return "break"

    def invert_selection(self, event=None):
        visible_items = set(self._visible_file_items())
        self.multi_selection = visible_items - self.multi_selection
        self.last_selected_item = next(iter(self.multi_selection), None)
        self.refresh_multi_selection_visuals()
       # # self.update_change_all_label()
        return "break"

    def clear_multi_selection(self, event=None):
        self.multi_selection.clear()
        self.last_selected_item = None
        self.refresh_multi_selection_visuals()
        # self.update_change_all_label()
        return "break"

    def select_latest_modified(self, event=None):
        latest_paths = set(self._latest_project_paths_by_folder())
        self.multi_selection = {
            iid
            for iid in self._visible_file_items()
            if self.tree_item_to_path.get(iid) in latest_paths
        }
        self.last_selected_item = next(iter(self.multi_selection), None)
        self.refresh_multi_selection_visuals()
        # self.update_change_all_label()
        return "break"

    def extend_selection(self, direction):
        items = self._visible_file_items()
        if not items:
            return "break"

        if self.last_selected_item is None or self.last_selected_item not in items:
            self.last_selected_item = items[0]
            self.multi_selection = {items[0]}
        else:
            try:
                idx = items.index(self.last_selected_item)
            except ValueError:
                idx = 0
            new_idx = max(0, min(len(items) - 1, idx + direction))
            self.last_selected_item = items[new_idx]
            self.multi_selection.add(items[new_idx])

        self.refresh_multi_selection_visuals()
        # self.update_change_all_label()
        return "break"

    def refresh_multi_selection_visuals(self):
        """Apply 'multi' tag to selected rows, remove it from others."""
        for iid in self._visible_file_items():
            tags = [t for t in self.tree.item(iid, "tags") if t != "multi"]
            if iid in self.multi_selection:
                tags.append("multi")
            self.tree.item(iid, tags=tags)
        self.tree.tag_configure("multi", background=BG_SELECTION)


    def _target_projects(self):
        """Return the cfg dicts of multi-selected rows.

        If nothing is selected, returns an empty list — the panel buttons
        silently do nothing so they can't accidentally affect every row.
        """
        if not self.multi_selection:
            return []
        paths = self._selected_project_paths()
        return [self.selected_projects[p] for p in paths if p in self.selected_projects]

    def _after_bulk_change(self):
        """Rebuild the tree and refresh the panel label after a bulk change."""
        self.update_tree()

    def apply_export_main_selected(self):
        for cfg in self._target_projects():
            cfg["export_state"] = "main"
            cfg["enabled"] = True
        self._after_bulk_change()

    def apply_export_stems_selected(self):
        for cfg in self._target_projects():
            cfg["export_state"] = "stems"
            cfg["enabled"] = True
        self._after_bulk_change()

    def apply_export_off_selected(self):
        for cfg in self._target_projects():
            cfg["export_state"] = "off"
        self._after_bulk_change()

    def toggle_collect_selected(self):
        targets = self._target_projects()
        if not targets:
            return
        new_state = not all(c["collect_all_and_save"] for c in targets)
        for c in targets:
            c["collect_all_and_save"] = new_state
        self._after_bulk_change()

    def toggle_return_fx_selected(self):
        targets = self._target_projects()
        if not targets:
            return
        new_state = not all(
            c.get("include_return_and_main_effects", True) for c in targets
        )
        for c in targets:
            c["include_return_and_main_effects"] = new_state
        self._after_bulk_change()

    def toggle_normalize_selected(self):
        targets = self._target_projects()
        if not targets:
            return
        new_state = not all(c.get("normalize", False) for c in targets)
        for c in targets:
            c["normalize"] = new_state
        self._after_bulk_change()

    def toggle_wet_dry_selected(self):
        targets = self._target_projects()
        if not targets:
            return
        new_state = not all(c.get("wet_dry", False) for c in targets)
        for c in targets:
            c["wet_dry"] = new_state
        self._after_bulk_change()

    def toggle_enabled_selected(self, event=None):
        targets = self._target_projects()
        if not targets:
            return
        new_state = not all(c.get("enabled", True) for c in targets)
        for cfg in targets:
            cfg["enabled"] = new_state
        self._after_bulk_change()
        return "break"

    def enable_selected(self):
        for cfg in self._target_projects():
            cfg["enabled"] = True
        self._after_bulk_change()

    def disable_selected(self, event=None):
        for cfg in self._target_projects():
            cfg["enabled"] = False
        self._after_bulk_change()
        return "break"

    def clear_selected(self, event=None):
        """Remove the currently selected projects from the list entirely."""
        paths = set(self._selected_project_paths())
        for p in paths:
            self.selected_projects.pop(p, None)
        # Drop removed files from projects_by_folder; clean up empty folders
        for folder in list(self.projects_by_folder.keys()):
            info = self.projects_by_folder[folder]
            info["files"] = [f for f in info["files"] if f not in paths]
            if not info["files"]:
                del self.projects_by_folder[folder]
        self.multi_selection.clear()
        self.last_selected_item = None
        self.update_tree()
        return "break"

    def on_project_delete_key(self, event):
        return self.clear_selected(event)


    def _on_mousewheel(self, event):
        """Handle mousewheel scrolling with faster speed"""
        # Scroll 3 units per wheel tick for faster scrolling
        if event.num == 5 or event.delta < 0:
            self.tree.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            self.tree.yview_scroll(-3, "units")
        return "break"  # Prevent default scrolling

    def _on_queue_mousewheel(self, event):
        """Handle mousewheel scrolling for queue tree"""
        if event.num == 5 or event.delta < 0:
            self.queue_tree.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            self.queue_tree.yview_scroll(-3, "units")
        return "break"

    def _queue_row_display(self, task):
        """(values, tag, error) for a queue row, from in-memory state only."""
        collect = task.get("collect_all_and_save", False)
        export_stems = task.get("export_stems", False)
        export_main = task.get("export_main", False)

        if collect and export_stems:
            task_type = "Collect+Stems"
        elif collect and export_main:
            task_type = "Collect+Main"
        elif collect and not export_stems and not export_main:
            task_type = "Collect"
        elif export_stems:
            task_type = "Stems"
        else:
            task_type = "Main"

        # Color code by task type, fade if done
        if task.get("done", False):
            tag = "queue_done"
        elif task_type == "Collect" or "Collect+" in task_type:
            tag = "queue_collect"
        elif "Stems" in task_type:
            tag = "queue_stems"
        else:
            tag = "queue_main"

        cached = self.size_cache.get(self._size_cache_key(task))
        if cached is None:
            size_display = "…"
        elif "error" in cached:
            size_display = "err"
        elif task.get("wet_dry", False):
            size_display = Size_estimator.format_bytes(self._task_size_bytes(task))
        else:
            size_display = cached["total_size"]

        # Errors take the tick column: a recursive project (from the background
        # filesystem check, so it clears once fixed) or a failure recorded by
        # the export script. Double-click shows details.
        error = None
        if not task.get("done", False):
            meta = self.task_meta.get(task["project_path"])
            if meta is not None and meta["recursive"]:
                error = ("recursive", meta["recursive"])
            elif task.get("failed"):
                error = ("failed", task)
        if error:
            tag = "queue_error"
            done_indicator = "⚠"
        else:
            done_indicator = "✓" if task.get("done", False) else ""

        return (task_type, size_display, done_indicator), tag, error

    def _refresh_queue_row(self, task, item_id=None):
        """Update one queue row in place."""
        item_id = item_id or self.queue_path_to_item.get(task["project_path"])
        if not item_id or not self.queue_tree.exists(item_id):
            return
        values, tag, error = self._queue_row_display(task)
        self.queue_tree.item(item_id, values=values, tags=(tag,))
        if error:
            self.queue_item_errors[item_id] = error
        else:
            self.queue_item_errors.pop(item_id, None)

    def _update_size_total_label(self):
        tasks = self.task_manager.get_tasks()
        computed = sum(1 for t in tasks if self._size_cache_key(t) in self.size_cache)
        self.size_estimate_total = self._get_total_size_estimate()
        self.size_total_label.config(
            text=f"Estimated size: {Size_estimator.format_bytes(self.size_estimate_total)} "
                f"({computed}/{len(tasks)} estimated)"
        )

    def update_queue_tree(self):
        """Rebuild the queue view from EXPORT_TASKS.json.

        Uses in-memory state only, so it stays fast however long the queue is.
        Filesystem checks and size estimates run in the background and update
        their rows as they finish (see poll_size_estimates).
        """
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
        self.queue_item_errors = {}
        self.queue_path_to_item = {}

        tasks = self.task_manager.get_tasks()
        for idx, task in enumerate(tasks):
            project_path = task["project_path"]
            project_name = os.path.basename(project_path)
            if project_name.endswith('.als'):
                project_name = project_name[:-4]

            values, tag, error = self._queue_row_display(task)
            item_id = self.queue_tree.insert(
                "",
                "end",
                text=f"{idx + 1:02d} {project_name}",
                values=values,
                tags=(tag,)
            )
            self.queue_path_to_item[project_path] = item_id
            if error:
                self.queue_item_errors[item_id] = error

        # Configure tags for task types
        self.queue_tree.tag_configure("queue_main", foreground="#6699ff")      # Blue for Main
        self.queue_tree.tag_configure("queue_stems", foreground="#ffdd44")     # Yellow for Stems
        self.queue_tree.tag_configure("queue_collect", foreground="#93fe93")   # Green for Collect
        self.queue_tree.tag_configure("queue_done", foreground="#666666")      # Grey for Done
        self.queue_tree.tag_configure("queue_error", foreground=ACCENT_RED)    # Red for errors

        
        if not tasks:
            self.queue_tree.insert(
            "", "end",
            text="none to show",
            tags=("queue_placeholder",),
        )
        self.queue_tree.tag_configure(
            "queue_placeholder",
            foreground=FG_MUTED,
            font=FONT_UI,
        )

        self._request_inspections(tasks)
        self.request_size_estimates()
        self._update_size_total_label()


    def add_selected_to_queue(self):
        """Add ALL enabled items from tree to EXPORT_TASKS.json queue (not just selected)"""
        entries = []
        for project_path, cfg in self.selected_projects.items():
            if cfg.get("enabled", True):
                entries.append({
                    "project_path": project_path,
                    "export_stems": cfg.get("export_state", "main") == "stems",
                    "export_main": cfg.get("export_state", "main") == "main",
                    "collect_all_and_save": cfg.get("collect_all_and_save", False),
                    "include_return_and_main_effects": cfg.get("include_return_and_main_effects", True),
                    "normalize": cfg.get("normalize", False),
                    "wet_dry": cfg.get("wet_dry", False),
                })
        self.task_manager.add_tasks(entries)
        
        # Update display silently (no popup)
        self.update_queue_tree()

    def remove_from_queue(self):
        """Remove all selected items from queue"""
        selected = self.queue_tree.selection()
        
        if not selected:
            return
        
        # Remove items from highest index to lowest to avoid index shifting issues
        all_items = self.queue_tree.get_children()
        tasks = self.task_manager.get_tasks()
        
        # Get indices and sort in reverse order
        indices_to_remove = []
        for item_id in selected:
            try:
                index = all_items.index(item_id)
                indices_to_remove.append(index)
            except ValueError:
                pass
        
        # Sort in reverse and remove
        for index in sorted(indices_to_remove, reverse=True):
            if 0 <= index < len(tasks):
                removed_item = tasks[index]
                self.task_manager.remove_task(removed_item["project_path"])
        
        self.update_queue_tree()

    def move_queue_item_up(self):
        """Move selected queue item up - keeps item selected for multiple moves"""
        selected = self.queue_tree.selection()
        
        if not selected:
            return
        
        item_id = selected[0]
        all_items = self.queue_tree.get_children()
        
        try:
            index = all_items.index(item_id)
            if index > 0:
                self.task_manager.move_task(index, index - 1)
                self.update_queue_tree()
                # Reselect the item at its new position
                new_items = self.queue_tree.get_children()
                if len(new_items) > index - 1 >= 0:
                    self.queue_tree.selection_set(new_items[index - 1])
        except (ValueError, IndexError):
            pass

    def move_queue_item_down(self):
        """Move selected queue item down - keeps item selected for multiple moves"""
        selected = self.queue_tree.selection()
        
        if not selected:
            return
        
        item_id = selected[0]
        all_items = self.queue_tree.get_children()
        tasks = self.task_manager.get_tasks()
        
        try:
            index = all_items.index(item_id)
            if index < len(tasks) - 1:
                self.task_manager.move_task(index, index + 1)
                self.update_queue_tree()
                # Reselect the item at its new position
                new_items = self.queue_tree.get_children()
                if len(new_items) > index + 1:
                    self.queue_tree.selection_set(new_items[index + 1])
        except (ValueError, IndexError):
            pass

    def clear_queue(self):
        """Clear all items from queue and reset done flags"""
        self.task_manager.clear_queue()
        self.update_queue_tree()

    def on_queue_tree_click(self, event):
        """Handle queue tree click with Ctrl/Shift for multiple selection"""
        item = self.queue_tree.identify("item", event.x, event.y)
        if item:
            # Allow multiple selection with Ctrl and Shift
            if event.state & 0x4:  # Ctrl key
                # Toggle selection
                current = self.queue_tree.selection()
                if item in current:
                    self.queue_tree.selection_remove(item)
                else:
                    self.queue_tree.selection_add(item)
            elif event.state & 0x1:  # Shift key
                # Range selection
                current = self.queue_tree.selection()
                if current:
                    all_items = self.queue_tree.get_children()
                    try:
                        current_idx = all_items.index(current[0])
                        new_idx = all_items.index(item)
                        start_idx = min(current_idx, new_idx)
                        end_idx = max(current_idx, new_idx) + 1
                        self.queue_tree.selection_set(all_items[start_idx:end_idx])
                    except (ValueError, IndexError):
                        self.queue_tree.selection_set(item)
                else:
                    self.queue_tree.selection_set(item)
            else:
                # Single selection (default)
                self.queue_tree.selection_set(item)

    def select_all_queue_rows(self, event=None):
        """Select every task in the queue so Delete/Remove can act on all of them."""
        if not self.task_manager.get_tasks():
            return "break"
        self.queue_tree.selection_set(self.queue_tree.get_children())
        return "break"

    def on_queue_double_click(self, event):
        """Double-click on a row with an error (⚠) shows what's wrong and how to fix it."""
        item = self.queue_tree.identify("item", event.x, event.y)
        error = self.queue_item_errors.get(item)
        if not error:
            return
        kind, data = error
        if kind == "recursive":
            self.prompt_fix_recursive_project(data)
        else:
            self.show_task_failure(data)
        return "break"

    def prompt_fix_recursive_project(self, plan):
        """Explain the nesting and offer to move the inner project out."""
        set_name = os.path.basename(plan["als_path"])
        message = (
            f"'{set_name}' is a recursive project: its project folder\n\n"
            f"    {plan['inner']}\n\n"
            f"is inside another Ableton project:\n\n"
            f"    {plan['outer']}\n\n"
            "Live shows a 'Recursive Projects may lead to unexpected results or "
            "even data loss' warning on Collect All and Save, so the export will "
            "skip this project until it is fixed.\n\n"
            "Fix it by moving the project folder to:\n\n"
            f"    {plan['new_inner']}\n\n"
            "Samples it uses from the outer project keep working (Live also "
            "stores absolute paths) and Collect All and Save will copy them in. "
            "Any set in the outer project that uses files from inside this folder "
            "would need relinking.\n\n"
            "Move the project folder now?"
        )
        if not messagebox.askyesno("Recursive project", message, icon="warning", parent=self.root):
            return
        if self.script_running:
            messagebox.showerror(
                "Recursive project",
                "The export script is running. Wait for it to finish (or stop it) "
                "before moving project folders.",
                parent=self.root,
            )
            return
        try:
            project_structure.apply_recursive_fix(plan)
        except OSError as e:
            messagebox.showerror(
                "Recursive project",
                f"Could not move the project folder:\n\n{e}\n\n"
                "If the set is open in Ableton (or a file in it is open elsewhere), "
                "close it and try again.",
                parent=self.root,
            )
            return
        self._remap_moved_folder(plan["inner"], plan["new_inner"])
        messagebox.showinfo(
            "Recursive project",
            f"Moved to:\n\n{plan['new_inner']}\n\nThe queue entry has been updated.",
            parent=self.root,
        )

    def _remap_moved_folder(self, old_root, new_root):
        """Update the queue and the left-hand project list after a folder move."""
        self.task_manager.remap_paths(old_root, new_root)

        remap = lambda p: project_structure.remap_path(p, old_root, new_root)
        new_folders = {}
        for folder, info in self.projects_by_folder.items():
            new_folder = remap(folder)
            new_folders[new_folder] = {
                **info,
                "path": remap(info.get("path", folder)),
                "files": [remap(f) for f in info.get("files", [])],
            }
        self.projects_by_folder = new_folders
        self.selected_projects = {
            remap(p): {**cfg, "project_path": remap(cfg.get("project_path", p))}
            for p, cfg in self.selected_projects.items()
        }
        self.multi_selection = set()

        self.update_tree()
        self.update_queue_tree()

    def show_task_failure(self, task):
        """Show the failure the export script recorded for this task."""
        failure = task.get("failure") or {}
        project_path = task.get("project_path", "")
        win = tk.Toplevel(self.root)
        win.title("Export failure")
        win.configure(bg="#2b2b2b")
        win.resizable(False, False)

        rows = [
            ("Project", project_path),
            ("Failed step", failure.get("step", "unknown")),
            ("Details", failure.get("detail", "No details were recorded")),
            ("When", failure.get("at", "unknown")),
            ("Screenshot", failure.get("screenshot") or "none"),
            ("Log", failure.get("log") or "none"),
        ]
        body = tk.Frame(win, bg="#2b2b2b")
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(12, 6))
        for r, (label, value) in enumerate(rows):
            tk.Label(
                body, text=label, bg="#2b2b2b", fg=FG_SECONDARY, font=FONT_UI_BOLD, anchor="nw"
            ).grid(row=r, column=0, sticky="nw", padx=(0, 10), pady=2)
            tk.Label(
                body, text=str(value), bg="#2b2b2b",
                fg=ACCENT_RED if label == "Details" else FG_PRIMARY,
                font=FONT_UI, anchor="w", justify="left", wraplength=520,
            ).grid(row=r, column=1, sticky="w", pady=2)
        tk.Label(
            win,
            text="The project stays in the queue and is retried on the next export run.",
            bg="#2b2b2b", fg=FG_SECONDARY, font=FONT_UI_SM,
        ).pack(padx=14, pady=(0, 8), anchor="w")

        buttons = tk.Frame(win, bg="#2b2b2b")
        buttons.pack(pady=(0, 12))

        def open_file(path):
            try:
                os.startfile(path)
            except OSError as e:
                messagebox.showerror("Export failure", f"Could not open:\n{path}\n\n{e}", parent=win)

        def clear_error():
            self.task_manager.clear_failure(project_path)
            self.update_queue_tree()
            win.destroy()

        screenshot = failure.get("screenshot")
        log_path = failure.get("log")
        make_outline_button(
            buttons, "Open screenshot", lambda: open_file(screenshot), BTN_COLOR_UTILITY,
            state=tk.NORMAL if screenshot and os.path.exists(screenshot) else tk.DISABLED,
        ).pack(side=tk.LEFT, padx=4)
        make_outline_button(
            buttons, "Open log", lambda: open_file(log_path), BTN_COLOR_UTILITY,
            state=tk.NORMAL if log_path and os.path.exists(log_path) else tk.DISABLED,
        ).pack(side=tk.LEFT, padx=4)
        make_outline_button(
            buttons, "Clear error", clear_error, BTN_COLOR_CLEAR,
        ).pack(side=tk.LEFT, padx=4)
        make_outline_button(
            buttons, "Close", win.destroy, BTN_COLOR_OFF,
        ).pack(side=tk.LEFT, padx=4)

        self._center_and_show_toplevel(win)

    
    def on_queue_delete_key(self, event):
        """Handle Delete key press on queue tree"""
        self.remove_from_queue()
        return "break"  # Prevent default behavior

    def on_queue_drag_start(self, event):
        """Start dragging a queue item"""
        item = self.queue_tree.identify("item", event.x, event.y)
        if item:
            self.dragged_queue_item = item
            self.drag_start_y = event.y
            self.queue_tree.selection_set(item)
            # No color changes - preserve task-type colors (blue=Main, yellow=Stems, green=Collect)
            # Drop indicator will show where item will be inserted

    def on_queue_drag_motion(self, event):
        """Handle dragging motion - show insertion indicator without changing colors"""
        if not self.dragged_queue_item or self.drag_start_y is None:
            return
        
        # Only start showing indicator after moving at least 5 pixels (prevents accidental drag)
        if abs(event.y - self.drag_start_y) < 5:
            return
        
        # Identify where item would be dropped
        target_item = self.queue_tree.identify("item", event.x, event.y)
        all_items = self.queue_tree.get_children()
        
        try:
            from_index = list(all_items).index(self.dragged_queue_item)
            if target_item and target_item != self.dragged_queue_item:
                to_index = list(all_items).index(target_item)
                # Direction indicator for user feedback
                if to_index > from_index:
                    # Moving down - will insert after target
                    pass
                else:
                    # Moving up - will insert before target
                    pass
        except (ValueError, IndexError):
            pass

    def on_queue_drag_release(self, event):
        """Handle dropping a queue item to reorder"""
        if not self.dragged_queue_item:
            return
        
        target_item = self.queue_tree.identify("item", event.x, event.y)
        if not target_item or target_item == self.dragged_queue_item:
            # No valid drop target
            self.dragged_queue_item = None
            self.drag_start_y = None
            return
        
        # Get indices and perform move
        all_items = self.queue_tree.get_children()
        try:
            from_index = list(all_items).index(self.dragged_queue_item)
            to_index = list(all_items).index(target_item)
            
            # Move item in task manager
            self.task_manager.move_task(from_index, to_index)
            
            # Update display (this preserves color coding)
            self.update_queue_tree()
        except (ValueError, IndexError):
            pass
        finally:
            self.dragged_queue_item = None
            self.drag_start_y = None


    def update_size_readout(self):
        """Update the WAV export size readout."""

        try:
            total_tracks, total_size = Size_estimator.estimate_queue()

            self.total_tracks = total_tracks
            self.total_size = total_size

            # Update GUI labels
            self.track_count_label.config(
                text=f"{total_tracks} WAVs"
            )

            self.size_label.config(
                text=total_size
            )

        except Exception as e:
            ## print(f"Size estimation error: {e}")

            self.track_count_label.config(text="—")
            self.size_label.config(text="Calculating...")



    def start_export_from_queue(self):
        """Start export using items from EXPORT_TASKS.json"""
        tasks = self.task_manager.get_tasks()
        
        if not tasks:
            return
        
        # Only get pending (not done) tasks
        pending_tasks = [t for t in tasks if not t.get("done", False)]
        if not pending_tasks:
            return

        if not ableton_build.is_installed(ableton_build.load_selected_build()):
            if not messagebox.askyesno(
                "Ableton Live Not Set",
                "No Ableton Live 12 executable is configured.\n\nLocate it now?",
                parent=self.root,
            ) or not self._prompt_for_ableton_exe(ableton_build.RELEASE):
                return

        if not get_export_path():
            if not messagebox.askyesno(
                "Export Folder Not Set",
                "No export folder is set.\n\nChoose one now?",
                parent=self.root,
            ) or not self.set_export_path():
                return
        
        summary = "Export Queue (Pending):\n\n"
        for task in pending_tasks:
            project_path = task["project_path"]
            project_name = os.path.basename(project_path)
            
            if project_name.endswith('.als'):
                project_name = project_name[:-4]
            
            task_types = []
            if task.get("export_stems"):
                task_types.append("Stems")
            if task.get("export_main"):
                task_types.append("Main")
            if task.get("collect_all_and_save"):
                task_types.append("Collect")
            
            task_str = " + ".join(task_types) if task_types else "Main"
            summary += f"• {project_name} ({task_str})\n"
        
      ##  if not self.confirm_export_dialog(summary):
      ##      return
        
        try:
            # EXPORT_TASKS.json is already prepared by the GUI
            # Just launch the main script - it will read from EXPORT_TASKS.json
            subprocess.Popen(
                [sys.executable, MAIN_SCRIPT, TASK_FILE],
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        except Exception as e:
            pass

    def toggle_pause_resume(self):
        """Toggle between pause and resume states"""
        if self.script_paused:
            # Currently paused - resume
            self.resume_export()
        else:
            # Currently running - pause
            self.pause_export()

    def pause_export(self):
        """Request pause - creates PAUSE_FLAG.txt file"""
        try:
            pause_flag_path = PAUSE_FLAG_FILE
            # Create the file to signal pause
            with open(pause_flag_path, 'w') as f:
                f.write(f"Paused at {datetime.now().isoformat()}\n")
            self.script_paused = True
            self.update_pause_button_state()
            
        except Exception as e:
            pass

    def resume_export(self):
        """Request resume - deletes PAUSE_FLAG.txt file"""
        try:
            pause_flag_path = PAUSE_FLAG_FILE
            if os.path.exists(pause_flag_path):
                os.remove(pause_flag_path)
            self.script_paused = False
            self.update_pause_button_state()
           
        except Exception as e:
            pass

    def update_pause_button_state(self):
        """Update pause button appearance based on script state"""
        if not self.pause_toggle_button:
            return
        
        if not self.script_running:
            # Script not running - button visible but disabled (shows Pause, grayed out)
            self.pause_toggle_button.config(
                bg="#000000",
                fg="#666666",
                highlightbackground="#666666",
                highlightcolor="#666666",
                highlightthickness=2,
                text="⏸ Pause",
                state=tk.DISABLED
            )
        elif self.script_paused:
            # Script paused - show Resume button (green outline, active)
            self.pause_toggle_button.config(
                bg="#000000",
                fg=BTN_COLOR_GO,
                highlightbackground=BTN_COLOR_GO,
                highlightcolor=BTN_COLOR_GO,
                highlightthickness=2,
                text="▶",
                state=tk.NORMAL
            )
        else:
            # Script running - show Pause button (orange outline, active)
            self.pause_toggle_button.config(
                bg="#000000",
                fg=BTN_COLOR_STEMS,
                highlightbackground=BTN_COLOR_STEMS,
                highlightcolor=BTN_COLOR_STEMS,
                highlightthickness=2,
                text="⏸",
                state=tk.NORMAL
            )

    def update_start_button_style(self):
        """Stub — Start/Pause merge not yet done. Real implementation TBD."""
        pass



    def configure_project(self, project_path):
        """Configure a single project"""
        project_name = os.path.basename(project_path)
        
        # Create configuration window
        config_win = tk.Toplevel(self.root)
        config_win.title(f"Configure: {project_name}")
        config_win.geometry("400x300")
        config_win.configure(bg="#2b2b2b")
        
        tk.Label(
            config_win,
            text=f"Project: {project_name}",
            font=FONT_UI,
            bg="#2b2b2b",
            fg="#ffffff"
        ).pack(pady=10)
        
        # Operation selection
        op_frame = tk.LabelFrame(
            config_win,
            text="Export Operation",
            font=FONT_UI,
            bg="#2b2b2b",
            fg="#ffffff"
        )
        op_frame.pack(fill=tk.X, padx=20, pady=10)
        
        operation = tk.StringVar(value="stems")
        
        tk.Radiobutton(
            op_frame,
            text="Export Stems (includes main mix)",
            variable=operation,
            value="stems",
            bg="#2b2b2b",
            fg="#ffffff",
            selectcolor="#3c3c3c"
        ).pack(anchor=tk.W, padx=10, pady=5)
        
        tk.Radiobutton(
            op_frame,
            text="Export Main Mix only",
            variable=operation,
            value="main",
            bg="#2b2b2b",
            fg="#ffffff",
            selectcolor="#3c3c3c"
        ).pack(anchor=tk.W, padx=10, pady=5)
        
        # Collect all and save
        collect_frame = tk.LabelFrame(
            config_win,
            text="Pre-Export",
            font=FONT_UI,
            bg="#2b2b2b",
            fg="#ffffff"
        )
        collect_frame.pack(fill=tk.X, padx=20, pady=10)
        
        collect_var = tk.BooleanVar(value=True)
        
        tk.Checkbutton(
            collect_frame,
            text="Run Collect All and Save",
            variable=collect_var,
            bg="#2b2b2b",
            fg="#ffffff",
            selectcolor="#3c3c3c"
        ).pack(anchor=tk.W, padx=10, pady=5)
        
        # Buttons
        button_frame = tk.Frame(config_win, bg=BG_WINDOW)
        button_frame.pack(fill=tk.X, padx=20, pady=20)
        
        def save_config():
            config = {
                "project_path": project_path,
                "collect_all_and_save": collect_var.get(),
                "export_stems": operation.get() == "stems",
                "export_main": operation.get() == "main"
            }
            self.selected_projects[project_path] = config
            self.update_tree()
            config_win.destroy()
            messagebox.showinfo("Saved", f"Configuration saved for:\n{project_name}")
        
        tk.Button(
            button_frame,
            text="Save",
            command=save_config,
            bg="#00aa00",
            fg="#ffffff",
            padx=20,
            pady=5
        ).pack(side=tk.RIGHT, padx=5)
        
        tk.Button(
            button_frame,
            text="Cancel",
            command=config_win.destroy,
            bg="#666666",
            fg="#ffffff",
            padx=20,
            pady=5
        ).pack(side=tk.RIGHT, padx=5)
    
    def toggle_select_all(self):
        """(De)Select All toggle - flips between selecting and deselecting every project"""
        new_state = not self.select_all_enabled.get()
        self.select_all_enabled.set(new_state)
        if new_state:
            self.select_all()
        else:
            self.deselect_all()
        self.update_select_all_button_style()

    def update_select_all_button_style(self):
        """Re-style the (De)Select All button to show which action is available next"""
        if not getattr(self, "select_all_toggle_btn", None):
            return
        if self.select_all_enabled.get():
            # Already selected everything - next click deselects
            self.select_all_toggle_btn.config(
                text="Deselect All",
                fg=BTN_COLOR_NEUTRAL,
                highlightbackground=BTN_COLOR_NEUTRAL,
                highlightcolor=BTN_COLOR_NEUTRAL,
            )
        else:
            self.select_all_toggle_btn.config(
                text="Select All",
                fg=BTN_COLOR_GO,
                highlightbackground=BTN_COLOR_GO,
                highlightcolor=BTN_COLOR_GO,
            )
    
    def toggle_ableton_build(self):
        """Switch the main script between the shipping Live build and the beta.

        Doesn't switch unless the target build has an exe configured.
        """
        current = ableton_build.load_selected_build()
        new_build = ableton_build.RELEASE if current == ableton_build.BETA else ableton_build.BETA
        if not ableton_build.is_installed(new_build):
            kind = "Beta" if new_build == ableton_build.BETA else "release"
            if messagebox.askyesno(
                "Build Not Configured",
                f"No Ableton Live 12 {kind} executable is configured, so the build can't be switched.\n\n"
                f"Locate it now?",
            ) and self._prompt_for_ableton_exe(new_build):
                ableton_build.save_selected_build(new_build)
            self.update_ableton_build_button_style()
            return
        ableton_build.save_selected_build(new_build)
        self.update_ableton_build_button_style()

    def update_ableton_build_button_style(self):
        """Show which Ableton build is active, and the version tag it will apply."""
        if not getattr(self, "ableton_build_btn", None):
            return
        build_key = ableton_build.load_selected_build()
        if not ableton_build.is_installed(build_key):
            text, color = "Live: not set", ACCENT_ORANGE
        else:
            is_beta = build_key == ableton_build.BETA
            version = ableton_build.detect_version(build_key)
            text = f"Live: {'BETA' if is_beta else 'Release'}  (AL{version})"
            if not ableton_build.is_installed(ableton_build.BETA):
                color = FG_MUTED  # nothing to switch to
            else:
                color = BTN_COLOR_STEMS if is_beta else BTN_COLOR_OFF
        self.ableton_build_btn.config(
            text=text,
            fg=color,
            highlightbackground=color,
            highlightcolor=color,
        )

    def _prompt_for_ableton_exe(self, build_key):
        """Ask for a Live 12 exe (or a folder to search) for a build slot.

        Returns True if a valid exe was stored.
        """
        beta = build_key == ableton_build.BETA
        kind = "Ableton Live 12 Beta" if beta else "Ableton Live 12"
        current = ableton_build.get_exe(build_key)
        initial = os.path.dirname(current) if current else os.environ.get("PROGRAMDATA", "C:\\")
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"Select the {kind} program (.exe) - Cancel to choose a folder instead",
            initialdir=initial,
            filetypes=[("Ableton Live", "Ableton Live*.exe"), ("Programs", "*.exe")],
        )
        if not path:
            path = filedialog.askdirectory(
                parent=self.root,
                title=f"Select a folder containing {kind} (the newest version found is used)",
                initialdir=initial,
            )
        if not path:
            return False
        path = os.path.normpath(path)

        info, err = ableton_build.resolve_choice(path, beta)
        if not info:
            messagebox.showerror("Ableton Live Not Found", err, parent=self.root)
            return False
        if info["is_beta"] != beta and not messagebox.askyesno(
            "Different Build Type",
            f"{info['window_title']} ({info['version'] or 'unknown version'}) is a "
            f"{'beta' if info['is_beta'] else 'release'} build.\n\n"
            f"Use it as the {'Beta' if beta else 'release'} build anyway?",
            parent=self.root,
        ):
            return False
        info, err = ableton_build.set_exe(build_key, info["exe"])
        if not info:
            messagebox.showerror("Ableton Live", err, parent=self.root)
            return False

        found_beta = None
        if os.path.isdir(path) and not beta and not ableton_build.is_installed(ableton_build.BETA):
            found_beta, _ = ableton_build.resolve_choice(path, beta=True)
            if found_beta:
                ableton_build.set_exe(ableton_build.BETA, found_beta["exe"])

        self.update_ableton_build_button_style()
        msg = f"{'Beta' if beta else 'Release'} build set to:\n{info['exe']}\n\nVersion {info['version'] or 'unknown'}"
        if found_beta:
            msg += f"\n\nAlso found a Beta:\n{found_beta['exe']}"
        messagebox.showinfo("Ableton Live", msg, parent=self.root)
        return True

    def _check_ableton_setup(self):
        """First-run check: make sure some Live 12 exe is configured."""
        if ableton_build.installed_builds():
            return
        if messagebox.askyesno(
            "Ableton Live 12 Not Found",
            "Ableton Live 12 wasn't found in the default install location.\n\n"
            "Locate it now? (You can also do this later from File > Set Ableton Live Exe…)",
            parent=self.root,
        ):
            self._prompt_for_ableton_exe(ableton_build.RELEASE)

    def install_ableton_osc(self):
        """Copy the bundled, patched AbletonOSC into the User Library."""
        user_library = ableton_build.user_library_path()
        if user_library and os.path.isdir(user_library):
            choice = messagebox.askyesnocancel(
                "Install AbletonOSC",
                f"Install AbletonOSC into this User Library?\n\n{user_library}\n\n"
                "Yes = install here, No = choose a different User Library folder.",
                parent=self.root,
            )
            if choice is None:
                return
            if not choice:
                user_library = None
        if not user_library or not os.path.isdir(user_library):
            user_library = filedialog.askdirectory(
                parent=self.root,
                title="Select your Ableton 'User Library' folder",
            )
            if not user_library:
                return
            user_library = os.path.normpath(user_library)
        ok, message = osc_installer.install(user_library)
        if ok:
            messagebox.showinfo(
                "AbletonOSC Installed",
                message + "\n\nIf this is the first install, select AbletonOSC as a Control Surface "
                "in Live (Settings > Link, Tempo & MIDI). Restart Live to load the update.",
                parent=self.root,
            )
        else:
            messagebox.showerror("AbletonOSC", message, parent=self.root)

    def clear_all(self):
        """Clear all projects"""
        self.projects_by_folder.clear()
        self.selected_projects.clear()
        self.update_tree()
     
    
    def deselect_all(self):
        """Disable all projects but keep them in the list"""
        for cfg in self.selected_projects.values():
            cfg["enabled"] = False
        self.update_tree()
    
    def select_all(self):
        """Enable all projects"""
        for cfg in self.selected_projects.values():
            cfg["enabled"] = True
        self.update_tree()
    
    

    def start_export(self):
        """Start the export process - deprecated, use start_export_from_queue instead"""
        messagebox.showinfo("Queue Required", "Please use the queue system:\n\n1. Select projects from the tree\n2. Click 'Add to Queue'\n3. Arrange order if needed\n4. Click 'Start Export from Queue'")
        return

    def _center_and_show_toplevel(self, win):
        """Shared centering helper for the standalone tool windows below."""
        win.transient(self.root)
        win.grab_set()
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww, wh = win.winfo_width(), win.winfo_height()
        x = max(0, (sw - ww) // 2)
        y = max(0, (sh - wh) // 2)
        win.geometry(f"+{x}+{y}")

    def open_bulk_fix_window(self):
        """Standalone window for scanning/fixing missing media across the configured project folders."""
        win = tk.Toplevel(self.root)
        win.title("Bulk Fix Missing Media")
        win.configure(bg="#2b2b2b")
        win.geometry("760x640")
        win.minsize(760, 640)

        content = tk.Frame(win, bg="#2b2b2b")
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._build_bulk_fix_contents(content)

        make_outline_button(
            win, "Close", win.destroy, BTN_COLOR_OFF
        ).pack(pady=(0, 10))

        self._center_and_show_toplevel(win)

    def open_sample_index_window(self):
        """Standalone window for configuring sample directories and building/updating the sample index."""
        win = tk.Toplevel(self.root)
        win.title("Sample Index")
        win.configure(bg="#2b2b2b")
        win.geometry("680x600")
        win.minsize(680, 600)

        content = tk.Frame(win, bg="#2b2b2b")
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._build_sample_index_contents(content)

        make_outline_button(
            win, "Close", win.destroy, BTN_COLOR_OFF
        ).pack(pady=(0, 10))

        self._center_and_show_toplevel(win)

    def _build_sample_index_contents(self, parent):
        """Directory list + build/update index for the abletoolz sample database."""
        tk.Label(
            parent,
            text="Directories to scan for samples (used to auto-relink missing media). "
                 "Drag folders here, or use Add Folder...:",
            bg="#2b2b2b", fg="#ffffff", font=FONT_UI, anchor="w", justify="left"
        ).pack(fill=tk.X, padx=5, pady=(10, 5))

        # Reserve the footer's space at the bottom of the tab FIRST so it is always visible,
        # regardless of the window size - the listbox above simply fills whatever remains.
        footer = tk.Frame(parent, bg="#2b2b2b")
        footer.pack(side=tk.BOTTOM, fill=tk.X)

        list_frame = tk.Frame(parent, bg="#0c0c0c")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            bg="#000000", fg="#ffffff",
            selectbackground="#3a3a3a",
            font=FONT_UI,
            activestyle="none",
        )
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=listbox.yview)

        for d in load_sample_dirs():
            listbox.insert(tk.END, d)

        def refresh_listbox():
            listbox.delete(0, tk.END)
            for d in load_sample_dirs():
                listbox.insert(tk.END, d)

        def add_directory():
            path = filedialog.askdirectory(title="Select folder to add to sample scan list")
            if path:
                dirs = load_sample_dirs()
                if path not in dirs:
                    dirs.append(path)
                    save_sample_dirs(dirs)
                    refresh_listbox()

        def add_dropped_directory(path):
            dirs = load_sample_dirs()
            if path not in dirs:
                dirs.append(path)
                save_sample_dirs(dirs)
                refresh_listbox()

        enable_folder_drop_target(listbox, add_dropped_directory)

        def remove_selected():
            sel = listbox.curselection()
            if not sel:
                return
            path = listbox.get(sel[0])
            dirs = load_sample_dirs()
            if path in dirs:
                dirs.remove(path)
                save_sample_dirs(dirs)
                refresh_listbox()

        btn_row = tk.Frame(footer, bg="#2b2b2b")
        btn_row.pack(fill=tk.X, padx=5, pady=(0, 10))

        make_outline_button(
            btn_row, "Add Folder...", add_directory, BTN_COLOR_MAIN, padx=10, pady=4
        ).pack(side=tk.LEFT, padx=(0, 5))

        make_outline_button(
            btn_row, "Remove Selected", remove_selected, BTN_COLOR_CLEAR, padx=10, pady=4
        ).pack(side=tk.LEFT)

        # Status + progress area
        status = get_index_status()
        status_var = tk.StringVar()
        if status["exists"]:
            status_var.set(f"Index: {status['count']} files, last updated {status['updated_at']}")
        else:
            status_var.set("Index: not built yet")

        tk.Label(
            footer, textvariable=status_var, bg="#2b2b2b", fg="#aaaaaa",
            font=FONT_UI, anchor="w"
        ).pack(fill=tk.X, padx=5)

        progress_var = tk.StringVar(value="")
        tk.Label(
            footer, textvariable=progress_var, bg="#2b2b2b", fg="#93fe93",
            font=FONT_UI, anchor="w", wraplength=650, justify="left"
        ).pack(fill=tk.X, padx=5, pady=(2, 5))

        # Animated progress bar shown while the sample database is being scanned.
        progress_bar = ttk.Progressbar(
            footer,
            mode="determinate",
            length=650,
            maximum=1,
            value=0,
)
        progress_bar.pack(fill=tk.X, padx=5, pady=(0, 10))

        bottom_row = tk.Frame(footer, bg="#2b2b2b")
        bottom_row.pack(fill=tk.X, padx=5, pady=(0, 10))

        build_button = make_outline_button(
            bottom_row, "Build / Update Index", None, BTN_COLOR_GO,
            font=FONT_UI, padx=10, pady=6
        )
        build_button.pack(side=tk.LEFT)
        def on_progress(stage, current, total):
            """Receive sample-index progress from the worker thread."""

            def update_gui():
                if total > 0:
                    percent = (current / total) * 100
                else:
                    percent = 100

                if stage == "discovering":
                    message = (
                        f"Discovering files... "
                        f"{current}/{total} ({percent:.2f}%)"
                    )

                elif stage == "validating":
                    message = (
                        f"Validating index... "
                        f"{current:,}/{total:,} ({percent:.2f}%)"
                    )

                elif stage == "indexing":
                    message = (
                        f"Indexing samples... "
                        f"{current:,}/{total:,} ({percent:.2f}%)"
                    )

                else:
                    message = (
                        f"{current:,}/{total:,} ({percent:.2f}%)"
                    )

                progress_var.set(message)

                progress_bar.config(
                    maximum=max(total, 1),
                    value=current,
                )

            # on_progress() runs in the worker thread.
            # Schedule the actual Tkinter widget update on the GUI thread.
            parent.after(0, update_gui)
        
            

        def run_build():
            build_button.config(state=tk.DISABLED, text="Scanning...")
            progress_var.set("Starting scan...")

            # Start the animated progress bar on the Tk main thread.
            # progress_bar.start(12)

            def worker():
                try:
                    db_path, count = build_or_update_index(
                        progress_callback=on_progress
                    )

                    def on_done():
                        # progress_bar.stop()
                        progress_var.set(
                            f"Finished scanning. {count} files indexed."
                        )
                        status_var.set(
                            f"Index: {count} files, last updated just now"
                        )
                        build_button.config(
                            state=tk.NORMAL,
                            text="Build / Update Index"
                        )

                    parent.after(0, on_done)

                except Exception as e:
                    err = str(e)

                    def on_error():
                        progress_bar.stop()
                        progress_var.set(f"Error: {err}")
                        build_button.config(
                            state=tk.NORMAL,
                            text="Build / Update Index"
                        )

                    parent.after(0, on_error)

            threading.Thread(target=worker, daemon=True).start()

        build_button.config(command=run_build)

    def open_naming_settings_window(self):
        """Standalone window for the export file/folder naming toggles."""
        win = tk.Toplevel(self.root)
        win.title("Naming Settings")
        win.configure(bg="#2b2b2b")
        win.geometry("520x600")
        win.minsize(520, 600)

        content = tk.Frame(win, bg="#2b2b2b")
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        self._build_naming_settings_contents(content, win)

        self._center_and_show_toplevel(win)

    def _build_naming_settings_contents(self, parent, win):
        """Checkboxes for every naming_settings toggle, persisted on Save."""
        settings = naming_settings.load_settings()
        settings_vars = {
            key: tk.BooleanVar(value=value)
            for key, value in settings.items()
        }

        def add_section(title, description, keys_and_labels):
            frame = tk.LabelFrame(
                parent, text=title, font=FONT_UI, bg="#2b2b2b", fg="#ffffff"
            )
            frame.pack(fill=tk.X, padx=5, pady=10)

            if description:
                tk.Label(
                    frame, text=description, bg="#2b2b2b", fg="#aaaaaa",
                    font=FONT_UI, anchor="w", justify="left", wraplength=460
                ).pack(fill=tk.X, padx=10, pady=(5, 5))

            for key, label in keys_and_labels:
                tk.Checkbutton(
                    frame,
                    text=label,
                    variable=settings_vars[key],
                    bg="#2b2b2b",
                    fg="#ffffff",
                    selectcolor="#3c3c3c",
                    anchor="w",
                ).pack(fill=tk.X, padx=10, pady=3)

        add_section(
            "Main Mix Export",
            'Filename format: "<name>_<tempo>bpm_<dd-mm-yy>.wav" (BPM omitted '
            "entirely if it couldn't be queried).",
            [
                ("main_mix_include_bpm", "Include BPM"),
                ("main_mix_include_date", "Include date"),
            ],
        )

        add_section(
            "Stems Live-Set Folder",
            'The folder for each exported live set, e.g. "name_140bpm_15-01-24".',
            [
                ("stems_folder_include_bpm", "Include BPM"),
                ("stems_folder_include_date", "Include date"),
            ],
        )

        add_section(
            "Individual Stem Files",
            "Applies to every exported track/group stem file. Dates are never "
            "included on individual stems.",
            [
                ("stem_files_include_bpm", 'Include BPM (e.g. "Kick_140bpm.wav")'),
                ("stem_files_include_project_name", "Include project name prefix on every stem"),
            ],
        )

        add_section(
            "Add Ableton Version Suffix",
            "Renames projects with Ableton Version Suffix like \"(AL12.3)\" when opening projects made with older versions of Ableton.",
            [
                ("include_ableton_version", "Include Ableton version suffix"),
            ],
        )

        button_frame = tk.Frame(parent, bg="#2b2b2b")
        button_frame.pack(fill=tk.X, pady=(10, 0))

        def save_and_close():
            naming_settings.save_settings({key: var.get() for key, var in settings_vars.items()})
            win.destroy()

        make_outline_button(
            button_frame, "Save", save_and_close, BTN_COLOR_GO
        ).pack(side=tk.LEFT, padx=3)

        make_outline_button(
            button_frame, "Cancel", win.destroy, BTN_COLOR_OFF
        ).pack(side=tk.LEFT, padx=3)

    def _build_bulk_fix_contents(self, parent):
        """Project root directories + bulk abletoolz missing-media fix, with a results report."""
        tk.Label(
            parent,
            text="Project folders to scan for .als files (all missing media in these will be checked/fixed). "
                 "Drag folders here, or use Add Folder...:",
            bg="#2b2b2b", fg="#ffffff", font=FONT_UI, anchor="w", justify="left"
        ).pack(fill=tk.X, padx=5, pady=(10, 5))

        list_frame = tk.Frame(parent, bg="#0c0c0c")
        list_frame.pack(fill=tk.X, padx=5, pady=5)

        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            bg="#000000", fg="#ffffff",
            selectbackground="#3a3a3a",
            font=FONT_UI,
            activestyle="none",
            height=4,
        )
        listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scrollbar.config(command=listbox.yview)

        for d in load_project_dirs():
            listbox.insert(tk.END, d)

        def refresh_listbox():
            listbox.delete(0, tk.END)
            for d in load_project_dirs():
                listbox.insert(tk.END, d)

        def add_directory():
            path = filedialog.askdirectory(title="Select folder containing Ableton projects")
            if path:
                dirs = load_project_dirs()
                if path not in dirs:
                    dirs.append(path)
                    save_project_dirs(dirs)
                    refresh_listbox()

        def add_dropped_directory(path):
            dirs = load_project_dirs()
            if path not in dirs:
                dirs.append(path)
                save_project_dirs(dirs)
                refresh_listbox()

        enable_folder_drop_target(listbox, add_dropped_directory)

        def remove_selected():
            sel = listbox.curselection()
            if not sel:
                return
            path = listbox.get(sel[0])
            dirs = load_project_dirs()
            if path in dirs:
                dirs.remove(path)
                save_project_dirs(dirs)
                refresh_listbox()

        btn_row = tk.Frame(parent, bg="#2b2b2b")
        btn_row.pack(fill=tk.X, padx=5, pady=(0, 10))

        make_outline_button(
            btn_row, "Add Folder...", add_directory, BTN_COLOR_MAIN, padx=10, pady=4
        ).pack(side=tk.LEFT, padx=(0, 5))

        make_outline_button(
            btn_row, "Remove Selected", remove_selected, BTN_COLOR_CLEAR, padx=10, pady=4
        ).pack(side=tk.LEFT)

        progress_var = tk.StringVar(value="")
        tk.Label(
            parent, textvariable=progress_var, bg="#2b2b2b", fg="#93fe93",
            font=FONT_UI, anchor="w", wraplength=650, justify="left"
        ).pack(fill=tk.X, padx=5, pady=(0, 5))

        # Report area (fills remaining space). Packing is deferred until after bottom_row
        # below so the button row's space is reserved first and never gets clipped.
        report_frame = tk.Frame(parent, bg="#0c0c0c")

        report_scroll = tk.Scrollbar(report_frame)
        report_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        report_text = tk.Text(
            report_frame,
            yscrollcommand=report_scroll.set,
            bg="#000000", fg="#dddddd",
            font=("Consolas", 9),
            wrap="none",
            state=tk.DISABLED,
        )
        report_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        report_scroll.config(command=report_text.yview)

        def set_report_text(text):
            report_text.config(state=tk.NORMAL)
            report_text.delete("1.0", tk.END)
            report_text.insert("1.0", text)
            report_text.config(state=tk.DISABLED)

        bottom_row = tk.Frame(parent, bg="#2b2b2b")
        bottom_row.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 10))

        # Now that bottom_row's space is reserved at the bottom, let the report area
        # fill whatever remains above it.
        report_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        fix_button = make_outline_button(
            bottom_row, "Scan & Fix", None, BTN_COLOR_GO,
            font=FONT_UI, padx=10, pady=6
        )
        fix_button.pack(side=tk.LEFT)

        scan_button = make_outline_button(
            bottom_row, "Scan (Preview Only)", None, BTN_COLOR_MAIN,
            font=FONT_UI, padx=10, pady=6
        )
        scan_button.pack(side=tk.LEFT, padx=(5, 0))

        last_report_holder = {"text": ""}

        def save_report():
            if not last_report_holder["text"]:
                messagebox.showinfo("No Report", "Run a scan first.")
                return
            path = filedialog.asksaveasfilename(
                title="Save Missing Media Report",
                defaultextension=".txt",
                initialfile=f"missing_media_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            )
            if path:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(last_report_holder["text"])
                messagebox.showinfo("Saved", f"Report saved to:\n{path}")

        make_outline_button(
            bottom_row, "Save Report...", save_report, BTN_COLOR_NEUTRAL, padx=10, pady=6
        ).pack(side=tk.LEFT, padx=(5, 0))

        def on_progress(message):
            parent.after(0, lambda: progress_var.set(message))

        def run(dry_run):
            if dry_run:
                confirm = messagebox.askyesno(
                    "Scan (Preview Only)",
                    "This will scan every .als file under the configured project folders and check for missing "
                    "media against the current sample index.\n\n"
                    "This is a PREVIEW - no project files will be modified.\n\n"
                    "This can take a while for a large library. Continue?"
                )
            else:
                confirm = messagebox.askyesno(
                    "Scan & Fix",
                    "This will scan every .als file under the configured project folders and SAVE any missing-media "
                    "relinks it can resolve from the current sample index.\n\n"
                    "abletoolz backs up each original automatically under abletoolz_backup/ before saving, and only "
                    "relinks on a strict size-or-date match, so it will not guess.\n\n"
                    "This can take a while for a large library. Continue?"
                )
            if not confirm:
                return

            busy_label = "Scanning..." if dry_run else "Scanning & fixing..."
            active_button = scan_button if dry_run else fix_button
            scan_button.config(state=tk.DISABLED)
            fix_button.config(state=tk.DISABLED)
            active_button.config(text=busy_label)
            progress_var.set("Finding project files...")
            set_report_text("")

            def restore():
                scan_button.config(state=tk.NORMAL, text="Scan (Preview Only)")
                fix_button.config(state=tk.NORMAL, text="Scan & Fix")

            def worker():
                try:
                    projects = find_all_projects()
                    if not projects:
                        parent.after(0, lambda: progress_var.set("No .als files found in the configured project folders."))
                        parent.after(0, restore)
                        return

                    db = load_sample_index()
                    if db is None:
                        parent.after(0, lambda: progress_var.set(
                            f"Found {len(projects)} project(s). No sample index built yet - missing media will "
                            "only be reported, not fixed. Build one in the Sample Index window first."
                        ))
                    else:
                        parent.after(0, lambda: progress_var.set(f"Found {len(projects)} project(s). Checking each against the sample index..."))

                    results = resolve_missing_media_bulk(projects, db, progress_callback=on_progress, dry_run=dry_run)
                    report = format_bulk_report(results, dry_run=dry_run)
                    last_report_holder["text"] = report

                    def on_done():
                        set_report_text(report)
                        restore()
                    parent.after(0, on_done)
                except Exception as e:
                    err = str(e)
                    def on_error():
                        progress_var.set(f"Error: {err}")
                        restore()
                    parent.after(0, on_error)

            threading.Thread(target=worker, daemon=True).start()

        scan_button.config(command=lambda: run(dry_run=True))
        fix_button.config(command=lambda: run(dry_run=False))

    def toggle_collect_all(self):
        """Collect All & Save toggle - flips the bulk collect-and-save flag for every project"""
        new_state = not self.collect_all_enabled.get()
        self.collect_all_enabled.set(new_state)
        for cfg in self.selected_projects.values():
            cfg["collect_all_and_save"] = new_state
        self.update_tree()
        self.update_collect_all_button_style()

    def update_collect_all_button_style(self):
        """Re-style the Collect All & Save button to reflect its current on/off state"""
        if not getattr(self, "collect_all_toggle_btn", None):
            return
        if self.collect_all_enabled.get():
            self.collect_all_toggle_btn.config(
                text="Collect All & Save \u2713",
                fg=BTN_COLOR_GO,
                highlightbackground=BTN_COLOR_GO,
                highlightcolor=BTN_COLOR_GO,
            )
        else:
            self.collect_all_toggle_btn.config(
                text="Collect All & Save",
                fg=BTN_COLOR_NEUTRAL,
                highlightbackground=BTN_COLOR_NEUTRAL,
                highlightcolor=BTN_COLOR_NEUTRAL,
            )
    
    def toggle_return_fx_all(self):
        """Include Return and Main Effects toggle - applies to every project"""
        new_state = not self.return_fx_all_enabled.get()
        self.return_fx_all_enabled.set(new_state)
        for cfg in self.selected_projects.values():
            cfg["include_return_and_main_effects"] = new_state
        self.update_tree()
        self.update_return_fx_button_style()

    def update_return_fx_button_style(self):
        """Re-style the Return FX button to reflect its current on/off state"""
        self._style_state_button(
            getattr(self, "return_fx_toggle_btn", None),
            "Return FX",
            self.return_fx_all_enabled.get(),
        )

    def toggle_normalize_all(self):
        """Normalize toggle - applies to every project"""
        new_state = not self.normalize_all_enabled.get()
        self.normalize_all_enabled.set(new_state)
        for cfg in self.selected_projects.values():
            cfg["normalize"] = new_state
        self.update_tree()
        self.update_normalize_button_style()

    def update_normalize_button_style(self):
        """Re-style the Normalize button to reflect its current on/off state"""
        self._style_state_button(
            getattr(self, "normalize_toggle_btn", None),
            "Normalize",
            self.normalize_all_enabled.get(),
        )

    def toggle_wet_dry_all(self):
        """Wet+Dry toggle - applies to every project"""
        new_state = not self.wet_dry_all_enabled.get()
        self.wet_dry_all_enabled.set(new_state)
        for cfg in self.selected_projects.values():
            cfg["wet_dry"] = new_state
        self.update_tree()
        self.update_wet_dry_button_style()

    def update_wet_dry_button_style(self):
        """Re-style the Wet+Dry button to reflect its current on/off state"""
        self._style_state_button(
            getattr(self, "wet_dry_toggle_btn", None),
            "Wet+Dry",
            self.wet_dry_all_enabled.get(),
        )

    def _style_state_button(self, button, label, is_on):
        """Give a Change All toggle button the shared on/off look"""
        if not button:
            return
        color = BTN_COLOR_GO if is_on else BTN_COLOR_NEUTRAL
        button.config(
            text=f"{label} \u2713" if is_on else label,
            fg=color,
            highlightbackground=color,
            highlightcolor=color,
        )

    def set_all_to_collect(self):
        """Set all projects to Collect Only (export_state=off, collect=True)"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "off"
            cfg["collect_all_and_save"] = True
        self.collect_all_enabled.set(True)
        self.update_collect_all_button_style()
        self.update_tree()
        # # self.update_change_all_label()
    
    def set_all_to_off(self):
        """Set all projects to Off (export_state=off, keep collect_all_and_save unchanged)"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "off"
        self.update_tree()
        # # self.update_change_all_label()

    def apply_collect_all(self):
        """Toggle Collect between all On and all Off (deprecated - now use set_all_to_collect)"""
        self.set_all_to_collect()

    def apply_export_main_all(self):
        """Set Export=Main for all entries"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "main"
        self.update_tree()
        # self.update_change_all_label()

    def apply_export_stems_all(self):
        """Set Export=Stems for all entries"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "stems"
        self.update_tree()
        # # self.update_change_all_label()

    def _latest_project_paths_by_folder(self):
        latest_paths = []
        for project_info in self.projects_by_folder.values():
            files = project_info.get("files", [])
            if not files:
                continue
            try:
                latest = max(files, key=lambda f: os.path.getmtime(f))
            except OSError:
                latest = files[-1]
            latest_paths.append(latest)
        return latest_paths

    def enable_only_latest_modified(self):
        """Enable only the most recently modified .als in each folder."""
        latest_paths = set(self._latest_project_paths_by_folder())
        for project_info in self.projects_by_folder.values():
            for f in project_info.get("files", []):
                cfg = self.selected_projects.get(f) or self._default_project_config(f)
                cfg["enabled"] = f in latest_paths
                self.selected_projects[f] = cfg
        self.update_tree()
        # # self.update_change_all_label()

    def select_latest_per_folder(self):
        """Backward-compatible alias for the old menu action."""
        return self.enable_only_latest_modified()

    def start_file_watcher(self):
        """Start watching EXPORT_TASKS.json for changes from main script"""
        self.check_task_file_changes()
        self.poll_size_estimates()
    
    def check_task_file_changes(self):
        """Periodically check for task updates and script state"""
        try:
            if os.path.exists(TASK_FILE):
                current_mtime = os.path.getmtime(TASK_FILE)
                # Our own saves update known_mtime, so only changes made by
                # another process (the export script) trigger a reload.
                if current_mtime != self.task_manager.known_mtime:
                    before = {t["project_path"]: dict(t) for t in self.task_manager.get_tasks()}
                    self.task_manager.load_tasks()
                    # A task the script just finished or flagged has usually
                    # been re-saved by Collect All and Save: re-check it.
                    self._invalidate_task_meta(
                        t["project_path"] for t in self.task_manager.get_tasks()
                        if before.get(t["project_path"]) != t
                    )
                    self.update_queue_tree()
        except Exception as e:
            pass  # Silently ignore errors in file watcher
        
        # Check if script is running (by looking for SCRIPT_STATE.json)
        try:
            state_file = SCRIPT_STATE_FILE
            script_was_running = self.script_running
            self.script_running = os.path.exists(state_file)
            
            # Check if pause flag exists
            pause_file = PAUSE_FLAG_FILE
            self.script_paused = os.path.exists(pause_file)
            # Refresh the start/pause button to match current state
            self.update_start_button_style()
            
        
            
            # Update button state if script running state changed or pause flag changed
            self.update_start_button_style()
           
            
        except Exception as e:
            pass  # Silently ignore errors
        
        # Schedule next check (every 500ms)
        self.root.after(500, self.check_task_file_changes)

def main():
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = tk.Tk()
    app = AutoExportLauncherGUI(root)
    root.mainloop()

if __name__ == "__main__":
    multiprocessing.freeze_support()  # size-estimate worker processes in a frozen build
    main()