"""UI for configuring and building the local Ableton sample index."""

import logging
import os
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk
import customtkinter as ctk
from PIL import Image

from gui_file_drop import (
    bind_directory_listbox_hotkeys,
    enable_folder_drop_target,
)
from gui_theme import (
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    ADD_FOLDER,
    BG_INPUT,
    BG_PANEL,
    BG_SELECTION,
    BG_TREE,
    BG_WINDOW,
    BTN_COLOR_CLEAR,
    BTN_COLOR_GO,
    BTN_COLOR_MAIN,
    BTN_COLOR_PAUSE,
    FG_PRIMARY,
    FG_SECONDARY,
    FG_SELECTED,
    FONT_UI,
    FONT_HEADER,
    FONT_UI_BOLD,
    create_dark_scrollbar,
    bind_directory_context_menu,
    make_icon_button,
    make_outline_button,
)
from create_db_patch import IndexCancelled
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


    def load_index_coverage():
        try:
            database = load_sample_index()
            if database is None:
                indexed = None
                status_text = (
                    "Index: could not be read (see GUI log)"
                    if DB_PATH.exists()
                    else "Index not built yet"
                )
            else:
                updated_at = datetime.fromtimestamp(
                    DB_PATH.stat().st_mtime
                ).strftime("%d %B %Y, %I:%M%p").lstrip("0")
                date_part, time_part = updated_at.rsplit(", ", 1)
                time_part = time_part.lstrip("0").lower()
                updated_at = f"{date_part}, {time_part}"

                status_text = (
                    f"Index has {len(database)} entries, last updated {updated_at}"
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
            if not listbox.winfo_exists():
                return
            index_coverage["directories"] = indexed
            status_var.set(status_text)
            refresh_listbox()



        parent.after(0, update_coverage)
    # index_coverage = {"directories": set()}
    # Status label for displaying the current state of the sample index.
    status_var = tk.StringVar(value="Index: checking...")
    tk.Label(
        parent,
        textvariable=status_var,
        bg=BG_WINDOW,
        fg=FG_PRIMARY,
        font=FONT_UI_BOLD,
        anchor="w",
        padx=4,
        pady=10,
    ).pack(fill=tk.X, padx=6, pady=4,)



    footer = tk.Frame(parent, bg=BG_WINDOW)
    footer.pack(side=tk.BOTTOM, fill=tk.X)

    folders_frame = tk.Frame(
        parent,
        bg=BG_WINDOW,
        bd=1,
        relief=tk.GROOVE,
        padx=5,
        pady=5,
    )
    folders_frame.pack(
        fill=tk.BOTH,
        expand=True,
        padx=5,
        pady=(2, 5),
    )
    directories_header = tk.Frame(folders_frame, bg=BG_WINDOW)
    directories_header.pack(fill=tk.X, padx=2, pady=(0, 5))

    tk.Label(
        directories_header,
        text="Directories to Scan & Index",
        bg=BG_WINDOW,
        fg=FG_SECONDARY,
        font=FONT_HEADER,
        anchor="w",
        padx=4,
        pady=4,
    ).pack(side=tk.LEFT)

    directory_actions = tk.Frame(directories_header, bg=BG_WINDOW)
    directory_actions.pack(side=tk.LEFT, padx=(6, 0))

    list_frame = tk.Frame(folders_frame, bg=BG_WINDOW)
    list_frame.pack(fill=tk.BOTH, expand=True)
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
                label = f"○  {directory}"
                color = ACCENT_RED
            elif (
                indexed_directories is not None
                and canonical_path(directory) not in indexed_directories
            ):
                label = f"●  {directory}  · not indexed"
                color = ACCENT_ORANGE
            else:
                label = f"●  {directory}"
                color = ACCENT_GREEN
            listbox.insert(tk.END, label)
            listbox.itemconfig(index, foreground=color)

    refresh_listbox()



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

    add_folder_button = make_icon_button(
        directory_actions, "add-folder", add_directory, size=20
    )
    add_folder_button.pack(side=tk.LEFT)

    bind_directory_listbox_hotkeys(listbox, remove_selected)
    bind_directory_context_menu(
        gui.root, listbox, remove_selected, add_directory
    )

    button_row = tk.Frame(footer, bg=BG_WINDOW)
    button_row.pack(fill=tk.X, padx=5, pady=(0, 10))




    # Configure the progress bar
    progress_style = ttk.Style(gui.root)
    progress_style.configure(
        "MissingMedia.Horizontal.TProgressbar",
        background=ACCENT_CYAN,
        troughcolor=BG_WINDOW,
        bordercolor=BG_WINDOW,
        lightcolor=ACCENT_CYAN,
        darkcolor=ACCENT_CYAN,
    )
    progress_style.configure(
        "Inactive.Horizontal.TProgressbar",
        background=BG_WINDOW,
        troughcolor=BG_WINDOW,
        bordercolor=BG_WINDOW,
        lightcolor=BG_WINDOW,
        darkcolor=BG_WINDOW,
    )
    # Single horizontal row: progress display on the left, buttons on the right.
    control_row = tk.Frame(footer, bg=BG_WINDOW)
    control_row.pack(fill=tk.X, padx=5, pady=(3, 5))

    progress_var = tk.StringVar(value="")
    # Fixed-size slot so showing/hiding the icons never shifts the layout.
    icon_slot = tk.Frame(control_row, bg=BG_WINDOW, width=64, height=30)
    icon_slot.pack(side=tk.RIGHT, padx=(0, 4))
    icon_slot.pack_propagate(False)

    progress_frame = tk.Frame(control_row, bg=BG_WINDOW)
    progress_frame.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(0, 12),
    )

    # Progress text
    tk.Label(
        progress_frame,
        textvariable=progress_var,
        bg=BG_WINDOW,
        fg=ACCENT_GREEN,
        font=FONT_UI,
        anchor="w",
        wraplength=650,
        justify="left",
    ).pack(fill=tk.X, padx=5, pady=(2, 5))

    # progress bar
    progress_bar = ttk.Progressbar(
        progress_frame,
        mode="determinate",
        maximum=1,
        value=0,
        style="Inactive.Horizontal.TProgressbar",
        # variable=progress_var,
    )
    progress_bar.pack(fill=tk.X, padx=5, pady=(0, 2))
    progress_bar.pack_propagate(False)


    # Buttons
    button_frame = tk.Frame(button_row, bg=BG_WINDOW)
    button_frame.pack(side=tk.RIGHT, anchor="e")

    build_button = make_outline_button(
        button_frame,
        "Build / Update Index",
        None,
        BTN_COLOR_GO,
        font=FONT_UI_BOLD,
        padx=10,
        pady=4,
        width=18,
        height=1,
    )
    build_button.pack(side=tk.LEFT, padx=(0, 5), pady=(4, 4))

    pause_button = make_icon_button(icon_slot, "pause", lambda: None, size=22)
    cancel_button = make_icon_button(icon_slot, "cancel", lambda: None, size=22)

    def show_index_controls(visible):
        if visible:
            pause_button.pack(side=tk.LEFT, padx=(0, 2))
            cancel_button.pack(side=tk.LEFT)
        else:
            pause_button.pack_forget()
            cancel_button.pack_forget()



    index_control = {
        "running": False,
        "resume_event": threading.Event(),
        "cancel_event": threading.Event(),
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
        now = time.monotonic()
        last_update = getattr(on_progress, "_last_gui_update", 0)
        if now - last_update >= 0.05 or current >= total:
            on_progress._last_gui_update = now
            parent.after(0, update_gui)

    def release_size_priority():
        if index_control["estimates_suspended"]:
            gui.resume_size_estimates_after_heavy_task()
            index_control["estimates_suspended"] = False

    def run_build():
        index_control["running"] = True
        index_control["resume_event"].set()
        index_control["cancel_event"].clear()
        build_button.config(state=tk.DISABLED, text="Scanning...")
        show_index_controls(True)
        progress_var.set("Starting scan...")
        progress_bar.config(style="MissingMedia.Horizontal.TProgressbar")
        def worker():
            try:
                index_control["estimates_suspended"] = True
                gui.suspend_size_estimates_for_heavy_task()
                _, count = build_or_update_index(
                    progress_callback=on_progress,
                    pause_event=index_control["resume_event"],
                    cancel_event=index_control["cancel_event"],
                )

                def on_done():
                    index_control["running"] = False
                    index_control["resume_event"].set()
                    show_index_controls(False)
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
                    show_index_controls(False)
                    release_size_priority()


                parent.after(0, on_done)
            except IndexCancelled:
                def on_cancelled():
                    index_control["running"] = False
                    index_control["resume_event"].set()
                    index_control["cancel_event"].clear()
                    progress_bar.config(value=0)
                    progress_var.set(
                        "Cancelled. The last saved index was kept."
                    )
                    build_button.config(
                        state=tk.NORMAL,
                        text="Build / Update Index",
                    )
                    show_index_controls(False)
                    release_size_priority()

                parent.after(0, on_cancelled)
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
                    show_index_controls(False)
                    release_size_priority()

                parent.after(0, on_error)

        threading.Thread(target=worker, daemon=True).start()

    def pause_or_continue():
        if not index_control["running"]:
            return
        resume_event = index_control["resume_event"]
        if resume_event.is_set():
            resume_event.clear()
            progress_var.set(
                "Pausing at the next directory or file boundary... "
                "click pause again to continue."
            )
        else:
            resume_event.set()
            progress_var.set("Resumed...")

    def cancel_build():
        if not index_control["running"]:
            return
        index_control["cancel_event"].set()
        index_control["resume_event"].set()
        show_index_controls(False)
        progress_var.set("Cancelling; the last saved index will be kept...")

    build_button.config(command=run_build)
    pause_button.bind("<Button-1>", lambda _e: pause_or_continue())
    cancel_button.bind("<Button-1>", lambda _e: cancel_build())

    threading.Thread(
    target=load_index_coverage,
    name="sample-index-coverage",
    daemon=True,
    ).start()
