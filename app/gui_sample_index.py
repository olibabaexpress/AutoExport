"""UI for configuring and building the local Ableton sample index."""

import logging
import os
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

from gui_file_drop import (
    bind_directory_listbox_hotkeys,
    enable_folder_drop_target,
)
from gui_theme import (
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    BG_PANEL,
    BG_SELECTION,
    BG_TREE,
    BG_WINDOW,
    BTN_COLOR_CLEAR,
    BTN_COLOR_GO,
    BTN_COLOR_MAIN,
    FG_PRIMARY,
    FG_SELECTED,
    FONT_UI,
    create_dark_scrollbar,
    make_outline_button,
)
from sample_database import (
    DB_PATH,
    build_or_update_index,
    load_sample_dirs,
    save_sample_dirs,
    load_index as load_sample_index,
)

logger = logging.getLogger("AutoExport")


def build_sample_index_contents(gui, parent):
    """Build the Sample Index tab content and its index-building controls."""
    tk.Label(
        parent,
        text="Directories to scan for samples. ",
        bg=BG_WINDOW,
        fg=FG_PRIMARY,
        font=FONT_UI,
        anchor="w",
        justify="left",
    ).pack(fill=tk.X, padx=5, pady=(10, 5))

    footer = tk.Frame(parent, bg=BG_WINDOW)
    footer.pack(side=tk.BOTTOM, fill=tk.X)

    list_frame = tk.Frame(parent, bg=BG_WINDOW)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    scrollbar = create_dark_scrollbar(list_frame, orientation="vertical")
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    listbox = tk.Listbox(
        list_frame,
        yscrollcommand=scrollbar.set,
        bg=BG_TREE,
        fg=FG_PRIMARY,
        selectbackground=BG_SELECTION,
        selectforeground=FG_SELECTED,
        selectmode=tk.EXTENDED,
        font=FONT_UI,
        activestyle="none",
    )
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.configure(command=listbox.yview)

    index_coverage = {"directories": None}
    displayed_directories = []

    def canonical_path(path):
        return os.path.normcase(os.path.abspath(os.path.normpath(path)))

    def refresh_listbox():
        nonlocal displayed_directories
        listbox.delete(0, tk.END)
        displayed_directories = load_sample_dirs()
        indexed_directories = index_coverage["directories"]
        for index, directory in enumerate(displayed_directories):
            if not os.path.isdir(directory):
                label = f"○ Disconnected  {directory}"
                color = ACCENT_RED
            elif (
                indexed_directories is not None
                and canonical_path(directory) not in indexed_directories
            ):
                label = f"● Connected · not indexed  {directory}"
                color = ACCENT_ORANGE
            else:
                label = f"● Connected  {directory}"
                color = ACCENT_GREEN
            listbox.insert(tk.END, label)
            listbox.itemconfig(index, foreground=color)

    refresh_listbox()

    def load_index_coverage():
        try:
            database = load_sample_index()
            if database is None:
                indexed = None
                status_text = (
                    "Index: could not be read (see GUI log)"
                    if DB_PATH.exists()
                    else "Index: not built yet"
                )
            else:
                updated_at = datetime.fromtimestamp(
                    DB_PATH.stat().st_mtime
                ).isoformat(timespec="seconds")
                status_text = (
                    f"Index: {len(database)} files, last updated {updated_at}"
                )
                configured = {
                    canonical_path(directory)
                    for directory in load_sample_dirs()
                }
                indexed = set()
                for sample_path in database:
                    current = canonical_path(sample_path)
                    while current:
                        if current in configured:
                            indexed.add(current)
                            break
                        parent_path = os.path.dirname(current)
                        if parent_path == current:
                            break
                        current = parent_path
        except Exception:
            logger.exception("Could not check sample-index directory coverage")
            return

        def update_coverage():
            index_coverage["directories"] = indexed
            status_var.set(status_text)
            refresh_listbox()

        parent.after(0, update_coverage)

    def add_directory():
        path = filedialog.askdirectory(
            title="Select folder to add to sample scan list"
        )
        if path:
            directories = load_sample_dirs()
            if path not in directories:
                directories.append(path)
                save_sample_dirs(directories)
                refresh_listbox()

    def add_dropped_directory(path):
        directories = load_sample_dirs()
        if path not in directories:
            directories.append(path)
            save_sample_dirs(directories)
            refresh_listbox()

    enable_folder_drop_target(listbox, add_dropped_directory)

    def remove_selected():
        selection = listbox.curselection()
        if not selection:
            return
        selected_paths = {
            displayed_directories[index]
            for index in selection
            if index < len(displayed_directories)
        }
        directories = [
            path for path in load_sample_dirs() if path not in selected_paths
        ]
        save_sample_dirs(directories)
        refresh_listbox()

    bind_directory_listbox_hotkeys(listbox, remove_selected)

    button_row = tk.Frame(footer, bg=BG_WINDOW)
    button_row.pack(fill=tk.X, padx=5, pady=(0, 10))
    make_outline_button(
        button_row,
        "Add Folder...",
        add_directory,
        BTN_COLOR_MAIN,
        padx=10,
        pady=4,
    ).pack(side=tk.LEFT, padx=(0, 5))
    make_outline_button(
        button_row,
        "Remove Selected",
        remove_selected,
        BTN_COLOR_CLEAR,
        padx=10,
        pady=4,
    ).pack(side=tk.LEFT)

    status_var = tk.StringVar(value="Index: checking...")

    tk.Label(
        footer,
        textvariable=status_var,
        bg=BG_WINDOW,
        fg=ACCENT_GREEN,
        font=FONT_UI,
        anchor="w",
    ).pack(fill=tk.X, padx=5)
    threading.Thread(
        target=load_index_coverage,
        name="sample-index-coverage",
        daemon=True,
    ).start()

    progress_var = tk.StringVar(value="")
    tk.Label(
        footer,
        textvariable=progress_var,
        bg=BG_WINDOW,
        fg=ACCENT_GREEN,
        font=FONT_UI,
        anchor="w",
        wraplength=650,
        justify="left",
    ).pack(fill=tk.X, padx=5, pady=(2, 5))

    progress_style = ttk.Style(gui.root)
    progress_style.configure(
        "MissingMedia.Horizontal.TProgressbar",
        background=ACCENT_CYAN,
        troughcolor=BG_PANEL,
        bordercolor=BG_PANEL,
        lightcolor=ACCENT_CYAN,
        darkcolor=ACCENT_CYAN,
    )
    progress_bar = ttk.Progressbar(
        footer,
        mode="determinate",
        length=650,
        maximum=1,
        value=0,
        style="MissingMedia.Horizontal.TProgressbar",
    )
    progress_bar.pack(fill=tk.X, padx=5, pady=(0, 2))

    bottom_row = tk.Frame(footer, bg=BG_WINDOW)
    bottom_row.pack(fill=tk.X, padx=5, pady=(0, 2))
    build_button = make_outline_button(
        bottom_row,
        "Build / Update Index",
        None,
        BTN_COLOR_GO,
        font=FONT_UI,
        padx=10,
        pady=2,
    )
    build_button.pack(side=tk.LEFT)
    pause_button = make_outline_button(
        bottom_row,
        "Pause",
        None,
        BTN_COLOR_MAIN,
        font=FONT_UI,
        padx=10,
        pady=2,
        state=tk.DISABLED,
    )
    pause_button.pack(side=tk.LEFT, padx=(5, 0))

    index_control = {
        "running": False,
        "resume_event": threading.Event(),
        "estimates_suspended": False,
    }
    index_control["resume_event"].set()

    def on_progress(stage, current, total):
        if stage != getattr(on_progress, "_last_stage", None):
            on_progress._last_stage = stage
            logger.info(
                "[SAMPLE_INDEX] Stage '%s' (%s/%s)", stage, current, total
            )

        def update_gui():
            percent = (current / total) * 100 if total > 0 else 100
            if stage == "discovering":
                message = (
                    f"Discovering files... {current}/{total} ({percent:.2f}%)"
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
                message = f"{current:,}/{total:,} ({percent:.2f}%)"
            progress_var.set(message)
            progress_bar.config(maximum=max(total, 1), value=current)

        parent.after(0, update_gui)

    def release_size_priority():
        if index_control["estimates_suspended"]:
            gui.resume_size_estimates_after_heavy_task()
            index_control["estimates_suspended"] = False

    def run_build():
        index_control["running"] = True
        index_control["resume_event"].set()
        build_button.config(state=tk.DISABLED, text="Scanning...")
        pause_button.config(state=tk.NORMAL, text="Pause")
        progress_var.set("Starting scan...")

        def worker():
            try:
                index_control["estimates_suspended"] = True
                gui.suspend_size_estimates_for_heavy_task()
                _, count = build_or_update_index(
                    progress_callback=on_progress,
                    pause_event=index_control["resume_event"],
                )

                def on_done():
                    index_control["running"] = False
                    index_control["resume_event"].set()
                    progress_var.set(
                        f"Finished scanning. {count} files indexed."
                    )
                    status_var.set(
                        f"Index: {count} files, last updated just now"
                    )
                    build_button.config(
                        state=tk.NORMAL,
                        text="Build / Update Index",
                    )
                    pause_button.config(state=tk.DISABLED, text="Pause")
                    release_size_priority()
                    threading.Thread(
                        target=load_index_coverage,
                        name="sample-index-coverage",
                        daemon=True,
                    ).start()

                parent.after(0, on_done)
            except Exception as error:
                message = str(error)
                logger.exception(
                    "Sample index build/update failed (%s)", message
                )

                def on_error():
                    index_control["running"] = False
                    index_control["resume_event"].set()
                    progress_bar.stop()
                    progress_var.set(
                        f"Error: {message} (full traceback in GUI log)"
                    )
                    build_button.config(
                        state=tk.NORMAL,
                        text="Build / Update Index",
                    )
                    pause_button.config(state=tk.DISABLED, text="Pause")
                    release_size_priority()

                parent.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def pause_or_continue():
        if not index_control["running"]:
            return
        resume_event = index_control["resume_event"]
        if resume_event.is_set():
            resume_event.clear()
            pause_button.config(text="Continue")
            progress_var.set(
                "Pausing at the next directory or file boundary..."
            )
        else:
            resume_event.set()
            pause_button.config(text="Pause")

    build_button.config(command=run_build)
    pause_button.config(command=pause_or_continue)
