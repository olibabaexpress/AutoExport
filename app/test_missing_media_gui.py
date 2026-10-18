"""
Standalone visual test harness for gui_missingmedia_tools.py.

Run this file from the same directory as:
    gui_missingmedia_tools.py
    gui_sample_index.py
    gui_bulk_fix_media.py
    gui_theme.py

All expensive operations are replaced with dummy implementations.
No real sample index is built, no real project is scanned, and no .als file
is modified. Folder lists are kept in memory only.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext

import gui_missingmedia_tools as missing_media_ui
import gui_sample_index as sample_ui
import gui_bulk_fix_media as bulk_ui
import gui_file_drop

from gui_theme import (
    BG_WINDOW,
    BG_PANEL,
    BG_TREE,
    BG_TOOLBAR,
    BG_SEPARATOR,
    FG_PRIMARY,
    FG_MUTED,
    ACCENT_GREEN,
    ACCENT_BLUE,
    FONT_UI,
    FONT_UI_BOLD,
)


# ---------------------------------------------------------------------------
# In-memory dummy data. These lists never touch your real config/database.
# ---------------------------------------------------------------------------

SAMPLE_DIRS = [
    r"C:\TEST_AUDIO\Samples",
    r"D:\DEMO_LIBRARY\Drums",
]
PROJECT_DIRS = [
    r"C:\TEST_PROJECTS\Track_01",
    r"D:\DEMO_PROJECTS\Track_02",
]

FAKE_PROJECTS = [
    r"C:\TEST_PROJECTS\Track_01\Track_01.als",
    r"D:\DEMO_PROJECTS\Track_02\Track_02.als",
    r"D:\DEMO_PROJECTS\Track_03\Track_03.als",
]

COMMAND_LOG = None


def log_command(message: str) -> None:
    """Write dummy-operation activity to the visible harness console."""
    print(f"[GUI TEST] {message}")
    if COMMAND_LOG is not None:
        COMMAND_LOG.after(0, lambda m=message: _append_log(m))


def _append_log(message: str) -> None:
    if COMMAND_LOG is None or not COMMAND_LOG.winfo_exists():
        return
    COMMAND_LOG.configure(state=tk.NORMAL)
    COMMAND_LOG.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {message}\n")
    COMMAND_LOG.see(tk.END)
    COMMAND_LOG.configure(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Dummy Sample Index operations
# ---------------------------------------------------------------------------

def dummy_load_sample_dirs():
    return list(SAMPLE_DIRS)


def dummy_save_sample_dirs(directories):
    SAMPLE_DIRS[:] = list(directories)
    log_command(f"Sample directory list updated in memory ({len(SAMPLE_DIRS)} entries)")


def dummy_load_sample_index():
    # Returning None makes the existing UI display "Index: not built yet".
    # It avoids reading the real sample index.
    return None


def dummy_build_or_update_index(progress_callback=None, pause_event=None):
    """Pretend to build an index and emit progress callbacks."""
    log_command("DUMMY: Build / Update Index requested")
    stages = [
        ("discovering", 1, 5),
        ("discovering", 3, 5),
        ("discovering", 5, 5),
        ("validating", 2, 8),
        ("validating", 8, 8),
        ("indexing", 10, 42),
        ("indexing", 24, 42),
        ("indexing", 42, 42),
    ]

    for stage, current, total in stages:
        # Respect the GUI's pause button just as the real worker should.
        if pause_event is not None:
            pause_event.wait()
        if progress_callback:
            progress_callback(stage, current, total)
        time.sleep(0.12)

    log_command("DUMMY: Index build finished (42 pretend files)")
    return {}, 42


# ---------------------------------------------------------------------------
# Dummy Missing Media operations
# ---------------------------------------------------------------------------

def dummy_load_project_dirs():
    return list(PROJECT_DIRS)


def dummy_save_project_dirs(directories):
    PROJECT_DIRS[:] = list(directories)
    log_command(f"Project directory list updated in memory ({len(PROJECT_DIRS)} entries)")


def dummy_find_all_projects():
    log_command("DUMMY: Project discovery requested")
    return list(FAKE_PROJECTS)


def dummy_resolve_missing_media_bulk(
    projects,
    database,
    progress_callback=None,
    dry_run=True,
):
    mode = "PREVIEW ONLY" if dry_run else "SCAN & FIX"
    log_command(f"DUMMY: {mode} requested for {len(projects)} pretend projects")

    if progress_callback:
        progress_callback("Checking project 1 of 3...")
        time.sleep(0.18)
        progress_callback("Checking project 2 of 3...")
        time.sleep(0.18)
        progress_callback("Checking project 3 of 3...")
        time.sleep(0.18)

    # Fake results only. No project file is opened or changed.
    return {
        "projects_scanned": len(projects),
        "missing_found": 7,
        "resolved": 5 if not dry_run else 0,
        "unresolved": 2,
        "dry_run": dry_run,
    }


def dummy_format_bulk_report(results, dry_run=False):
    mode = "PREVIEW ONLY — NO FILES MODIFIED" if dry_run else "DUMMY SCAN & FIX — NO FILES MODIFIED"
    resolved = results["resolved"]
    return (
        "MISSING MEDIA TEST REPORT\n"
        "========================\n\n"
        f"Mode: {mode}\n"
        f"Projects scanned: {results['projects_scanned']}\n"
        f"Missing references found: {results['missing_found']}\n"
        f"Resolved: {resolved}\n"
        f"Still missing: {results['unresolved']}\n\n"
        "This is simulated test data.\n"
        "No Ableton projects or sample files were accessed or modified.\n"
    )


# ---------------------------------------------------------------------------
# Safe substitutes for application-level methods and optional drag/drop.
# ---------------------------------------------------------------------------

class DummyGUI:
    def __init__(self, root):
        self.root = root
        self._tool_windows = {}
        self._missing_media_tabs = {}
        self._missing_media_notebook = None

    def _get_existing_tool_window(self, key):
        window = self._tool_windows.get(key)
        if window is not None:
            try:
                if window.winfo_exists():
                    return window
            except tk.TclError:
                pass
        return None

    def _register_tool_window(self, key, window):
        self._tool_windows[key] = window

    def _center_and_show_toplevel(self, window):
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.lift()

    def suspend_size_estimates_for_heavy_task(self):
        log_command("Stub: size estimates suspended")

    def resume_size_estimates_after_heavy_task(self):
        log_command("Stub: size estimates resumed")


def build_console(root):
    global COMMAND_LOG

    console = tk.Toplevel(root)
    console.title("GUI Test Harness — Dummy Command Log")
    console.configure(bg=BG_WINDOW)
    console.geometry("560x260+40+80")

    tk.Label(
        console,
        text="DUMMY COMMAND LOG",
        bg=BG_WINDOW,
        fg=ACCENT_GREEN,
        font=FONT_UI_BOLD,
        anchor="w",
    ).pack(fill=tk.X, padx=10, pady=(10, 4))

    COMMAND_LOG = scrolledtext.ScrolledText(
        console,
        bg=BG_TREE,
        fg=FG_PRIMARY,
        insertbackground=FG_PRIMARY,
        selectbackground=BG_SEPARATOR,
        relief=tk.FLAT,
        borderwidth=0,
        font=("Consolas", 9),
        state=tk.DISABLED,
        wrap=tk.WORD,
    )
    COMMAND_LOG.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    _append_log("Harness started. All operations are simulated.")
    _append_log("Open either tab and click its normal controls to test the UI.")
    return console


def install_dummy_operations():
    """Replace module-level references used by the two GUI builders."""
    # Sample Index module: no real directories or index are read/written.
    sample_ui.load_sample_dirs = dummy_load_sample_dirs
    sample_ui.save_sample_dirs = dummy_save_sample_dirs
    sample_ui.load_sample_index = dummy_load_sample_index
    sample_ui.build_or_update_index = dummy_build_or_update_index

    # Bulk Fix module: no real projects, database or resolver are used.
    bulk_ui.load_project_dirs = dummy_load_project_dirs
    bulk_ui.save_project_dirs = dummy_save_project_dirs
    bulk_ui.find_all_projects = dummy_find_all_projects
    bulk_ui.load_sample_index = dummy_load_sample_index
    bulk_ui.resolve_missing_media_bulk = dummy_resolve_missing_media_bulk
    bulk_ui.format_bulk_report = dummy_format_bulk_report

    # Disable optional OS drag-and-drop integration in this test harness.
    # The Add Folder dialogs and in-memory list controls still work.
    sample_ui.enable_folder_drop_target = lambda *args, **kwargs: None
    bulk_ui.enable_folder_drop_target = lambda *args, **kwargs: None


def main():
    install_dummy_operations()

    root = tk.Tk()
    root.withdraw()
    root.configure(bg=BG_WINDOW)

    build_console(root)

    gui = DummyGUI(root)

    # Use the production window and tab layout, with the operations above
    # replaced by safe dummy functions.
    missing_media_ui.open_missing_media_window(
        gui,
        selected_tab="sample_index",
    )

    log_command("Missing Media window opened.")
    log_command("Switch tabs and click Build, Preview, or Scan & Fix to test.")

    root.mainloop()


if __name__ == "__main__":
    main()
