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
    load_icon,
    make_icon_button,
    make_outline_button,
)
import network_index as network_index
from gui_missingmedia_layout import (
    add_grid_button,
    build_footer,
    build_section_header,
    make_panel,
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
            if not index_control["running"]:
                progress_var.set(status_text)
            refresh_listbox()



        parent.after(0, update_coverage)

        
    # Footer first so the expanding list panel can't push it off-screen.
    footer = build_footer(parent)
    progress_var = footer.status_var
    progress_var.set("Index: checking...")
    progress_bar = footer.progress_bar
    icon_slot = footer.icon_slot

    network_state = {"devices": []}
    directory_actions = build_section_header(
        parent, "Directories to Scan & Index"
    )
    list_frame = make_panel(parent, expand=True)
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
        bd=0,
        highlightthickness=0,
        relief=tk.FLAT,
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
        for device in network_state["devices"]:
            for directory in network_index.device_directories(device):
                listbox.insert(tk.END, f"●  {device}  ·  {directory}")
                listbox.itemconfig(tk.END, foreground=ACCENT_CYAN)

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

    network_button = make_icon_button(
        directory_actions, "network_inactive", lambda: browse_network_index(),
        size=20,
    )
    network_button.pack(side=tk.LEFT, padx=(6, 0))
    network_images = {
        name: load_icon(name, 20)
        for name in ("network_active", "network_inactive")
    }

    def apply_network_state(devices, message=None):  # devices pre-probed off-thread
        if not listbox.winfo_exists():
            return
        network_state["devices"] = devices
        icon = network_images["network_active" if devices else "network_inactive"]
        if icon is not None:
            network_button.config(image=icon)
            network_button.image = icon
        if message and not index_control["running"]:
            progress_var.set(message)
        refresh_listbox()

    def run_in_background(work, finish):
        def runner():
            try:
                outcome = work()
            except Exception as error:
                logger.exception("Network index task failed")
                outcome = error
            try:
                parent.after(0, lambda: finish(outcome))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=runner, daemon=True).start()

    def browse_network_index():
        path = filedialog.askopenfilename(
            title="Select a networked AutoExport sample index (abletoolz_sample_db.json)",
            filetypes=[("Sample index", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        device = (
            network_index.device_from_path(path)
            or network_index.mapped_drive_server(path)
        )
        if not device:
            apply_network_state(
                network_state["devices"],
                f"Could not tell which computer that index belongs to"
                f"\n- browse to it through the network path",
            )
            return
        device = device.strip().upper()

        def finish(outcome):
            if isinstance(outcome, Exception):
                apply_network_state(
                    network_state["devices"],
                    f"Could not import the index from {device}: {outcome}",
                )
                return
            apply_network_state(
                outcome[1], f"Networked Index imported from {device}"
            )

        run_in_background(
            lambda: (network_index.import_index(path, device), network_index.active_devices()),
            finish,
        )

    def finish_auto_connect(outcome):
        if isinstance(outcome, Exception):
            return
        if outcome["state"] == "imported":
            apply_network_state(outcome["active"], outcome["message"])
        else:
            prompt = ""
            if outcome["state"] == "needs_browse":
                prompt = (
                    f"{outcome['device']} is on the network but no sample "
                    "index was found - click the network icon to browse for one"
                )
            apply_network_state(outcome["active"], prompt)

    def auto_connect_work():
        result = network_index.auto_connect()
        result["active"] = network_index.active_devices()
        return result

    run_in_background(auto_connect_work, finish_auto_connect)

    bind_directory_listbox_hotkeys(listbox, remove_selected)
    bind_directory_context_menu(
        gui.root, listbox, remove_selected, add_directory
    )

    build_button = add_grid_button(
        footer.button_row,
        0,
        "Build / Update Index",
        None,
        BTN_COLOR_GO,
        columnspan=2,
        font=FONT_UI_BOLD,
    )

    pause_button = make_icon_button(icon_slot, "pause", lambda: None, size=22)
    cancel_button = make_icon_button(icon_slot, "cancel", lambda: None, size=22)

    def show_index_controls(visible):
        if visible:
            pause_button.pack(side=tk.LEFT, padx=(0, 2))
            cancel_button.pack(side=tk.LEFT, padx=(2, 0))
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
        footer.set_progress_active(True)
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

                    
                    footer.reset_progress()
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
                    footer.reset_progress()
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
                    footer.reset_progress()
                    
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
