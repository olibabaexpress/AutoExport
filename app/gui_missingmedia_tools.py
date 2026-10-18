"""Window orchestration for the Sample Index and Missing Media tools."""

import tkinter as tk

from gui_theme import (
    BG_PANEL,
    BG_SELECTED,
    BG_TREE_HEADER,
    BG_WINDOW,
    FG_PRIMARY,
    FG_SELECTED,
    FONT_UI_BOLD,
)
from gui_bulk_fix_media import (
    build_bulk_fix_contents,
    start_selected_media_scan as _start_selected_media_scan,
)
from gui_sample_index import build_sample_index_contents


def start_selected_media_scan(gui, project_paths, mode):
    """Run a media fix from the Project Browser selection."""
    _start_selected_media_scan(gui, project_paths, mode)


class _FixedTabView:
    def __init__(self, parent):
        self.header = tk.Frame(parent, bg=BG_WINDOW)
        self.header.pack(fill=tk.X)
        self.content = tk.Frame(parent, bg=BG_WINDOW)
        self.content.pack(fill=tk.BOTH, expand=True)
        self.frames = []
        self.buttons = {}
        self.frame_buttons = {}
        self.selected = None

    def add(self, key, label):
        column = len(self.frames)
        self.header.grid_columnconfigure(
            column, weight=1, uniform="missing-media-tabs"
        )
        button = tk.Button(
            self.header,
            text=label,
            command=lambda: self.select(self.frames[column]),
            bg=BG_PANEL,
            fg=FG_PRIMARY,
            activebackground=BG_TREE_HEADER,
            activeforeground=FG_SELECTED,
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=6,
            font=FONT_UI_BOLD,
            cursor="arrow",
        )
        button.grid(row=0, column=column, sticky="ew", padx=(0, 2))
        frame = tk.Frame(self.content, bg=BG_WINDOW)
        self.frames.append(frame)
        self.buttons[key] = button
        self.frame_buttons[frame] = button
        return frame

    def select(self, frame):
        if frame not in self.frames:
            return
        if self.selected is not None:
            self.selected.pack_forget()
        self.selected = frame
        frame.pack(fill=tk.BOTH, expand=True)
        for button in self.buttons.values():
            button.configure(bg=BG_PANEL, fg=FG_PRIMARY)
        self.frame_buttons[frame].configure(bg=BG_SELECTED, fg=FG_SELECTED)


def open_missing_media_window(
    gui, selected_tab="sample_index", selected_projects=None
):
    """Show the Sample Index and Bulk Fix tools in one tabbed window."""
    existing = gui._get_existing_tool_window("missing_media")
    if existing:
        if selected_projects:
            set_projects = getattr(
                gui, "_set_bulk_fix_selected_projects", None
            )
            if set_projects is not None:
                set_projects(selected_projects)
        tab = getattr(gui, "_missing_media_tabs", {}).get(selected_tab)
        if tab is not None:
            gui._missing_media_notebook.select(tab)
        return

    win = tk.Toplevel(gui.root)
    win.withdraw()
    win.title("Missing Media")
    win.configure(bg=BG_WINDOW)
    win.geometry("620x640")
    win.minsize(620, 640)
    win.resizable(False, False)

    content = tk.Frame(win, bg=BG_WINDOW)
    content.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    tabs = _FixedTabView(content)
    sample_tab = tabs.add("sample_index", "Sample Index")
    bulk_tab = tabs.add("bulk_fix", "Bulk Fix Missing Media")
    gui._missing_media_notebook = tabs
    gui._missing_media_tabs = {
        "sample_index": sample_tab,
        "bulk_fix": bulk_tab,
    }
    if selected_projects:
        gui._bulk_fix_selected_projects = tuple(selected_projects)
    build_sample_index_contents(gui, sample_tab)
    build_bulk_fix_contents(gui, bulk_tab)
    tabs.select(gui._missing_media_tabs.get(selected_tab, sample_tab))

    gui._register_tool_window("missing_media", win)
    gui._center_and_show_toplevel(win)
