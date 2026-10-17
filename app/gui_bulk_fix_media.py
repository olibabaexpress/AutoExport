"""UI for scanning and repairing missing media in Ableton projects."""

import logging
import os
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox

from gui_file_drop import (
    bind_directory_listbox_hotkeys,
    enable_folder_drop_target,
)
from gui_theme import (
    ACCENT_GREEN,
    ACCENT_ORANGE,
    ACCENT_RED,
    ACCENT_BLUE,

    BG_SELECTION,
    BG_SEPARATOR,
    BG_TREE,
    BG_WINDOW,
    BTN_COLOR_CLEAR,
    BTN_COLOR_GO,
    BTN_COLOR_MAIN,
    BTN_COLOR_NEUTRAL,
    ACCENT_NEUTRAL,
    FG_PRIMARY,
    FG_SELECTED,
    FONT_HEADER,
    FONT_UI,
    BG_PANEL,
    FONT_UI_BOLD,
    create_dark_scrollbar,
    make_outline_button,
)

from missing_media_resolver import format_bulk_report, resolve_missing_media_bulk
from project_scanner import find_all_projects, load_project_dirs, save_project_dirs
from project_media_status import (
    missing_audio_list_path,
    save_project_media_status,
)
from sample_database import load_index as load_sample_index

logger = logging.getLogger("AutoExport")


def start_selected_media_scan(gui, project_paths, mode):
    """Resolve media for browser-selected projects without opening Bulk Fix."""
    if mode not in {"relink", "collect"}:
        raise ValueError(f"Unsupported selected media scan mode: {mode!r}")
    paths = tuple(dict.fromkeys(project_paths))
    if not paths:
        return
    if getattr(gui, "_selected_media_scan_active", False):
        gui.set_status("A selected-project media scan is already running.")
        return
    if getattr(gui, "_bulk_fix_priority_active", False):
        gui.set_status("Another missing-media job is already running.")
        return

    gui._selected_media_scan_active = True
    mode_label = "Scan & Relink" if mode == "relink" else "Scan & Collect"
    gui.begin_status_job(
        f"{mode_label}: preparing {len(paths)} project(s)...",
        indeterminate=len(paths) == 1,
    )
    gui.suspend_size_estimates_for_heavy_task()

    def show_progress(message):
        match = re.match(r"\[(\d+)/(\d+)\]", message)
        fraction = (
            (int(match.group(1)) - 1) / max(1, int(match.group(2)))
            if match else None
        )
        gui.update_status_job(f"{mode_label}: {message.strip()}", fraction)

    def update_progress(message):
        gui.root.after(0, lambda text=message: show_progress(text))

    def finish(results, error=None):
        gui._selected_media_scan_active = False
        gui.resume_size_estimates_after_heavy_task()
        if error is not None:
            gui.end_status_job(f"{mode_label} failed: {error}")
            return
        gui.refresh_project_media_status()
        fixed = sum(result.get("fixed", 0) for result in results.values())
        missing = sum(
            result.get("missing_before", 0) for result in results.values()
        )
        gui.end_status_job(
            f"{mode_label} complete: {fixed}/{missing} references fixed "
            f"across {len(results)} project(s)."
        )

    def worker():
        try:
            database = load_sample_index()
            results = resolve_missing_media_bulk(
                paths,
                database,
                progress_callback=update_progress,
                mode=mode,
            )
            save_project_media_status(results, merge=True)
        except Exception as error:
            logger.exception(
                "Selected-project %s job failed", mode
            )
            gui.root.after(0, lambda caught=error: finish({}, caught))
            return
        gui.root.after(0, lambda completed=results: finish(completed))

    threading.Thread(
        target=worker,
        name="selected-media-scan",
        daemon=True,
    ).start()


def build_bulk_fix_contents(gui, parent):
    """Build project-folder controls and the missing-media scan/report UI."""
    tk.Label(
        parent,
        text=(
            "Project folders"
        ),
        bg=BG_WINDOW,
        fg=FG_PRIMARY,
        font=FONT_HEADER,
        anchor="w",
        justify="left",
    ).pack(fill=tk.X, padx=5, pady=(10, 5))

    list_frame = tk.Frame(
        parent,
        bg=BG_TREE,
        bd=0,
        highlightthickness=1,
        highlightbackground=BG_SEPARATOR,
        highlightcolor=BG_SEPARATOR,
    )
    list_frame.pack(fill=tk.X, padx=5, pady=5)

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
        height=4,
        bd=0,
        highlightthickness=0,
        relief=tk.FLAT,
        borderwidth=0,
        highlightbackground=BG_TREE,
        highlightcolor=BG_TREE,
    )
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, anchor="nw")
    scrollbar.configure(command=listbox.yview)

    def refresh_listbox():
        listbox.delete(0, tk.END)
        for directory in load_project_dirs():
            listbox.insert(tk.END, directory)

    refresh_listbox()

    def add_directory():
        path = filedialog.askdirectory(
            title="Select folder containing Ableton projects"
        )
        if path:
            directories = load_project_dirs()
            if path not in directories:
                directories.append(path)
                save_project_dirs(directories)
                refresh_listbox()

    def add_dropped_directory(path):
        directories = load_project_dirs()
        if path not in directories:
            directories.append(path)
            save_project_dirs(directories)
            refresh_listbox()

    enable_folder_drop_target(listbox, add_dropped_directory)

    def remove_selected():
        selection = listbox.curselection()
        if not selection:
            return
        selected_paths = {listbox.get(index) for index in selection}
        directories = [
            path for path in load_project_dirs() if path not in selected_paths
        ]
        save_project_dirs(directories)
        refresh_listbox()

    bind_directory_listbox_hotkeys(listbox, remove_selected)

    button_row = tk.Frame(parent, bg=BG_WINDOW)
    button_row.pack(fill=tk.X, padx=5, pady=(0, 10))

    top_button_group = tk.Frame(button_row, bg=BG_WINDOW)
    top_button_group.pack(anchor="center")

    TOP_BUTTON_WIDTH = 150
    TOP_BUTTON_HEIGHT = 30
    TOP_BUTTON_GAP = 6

    def make_top_button(text, command, color):
        slot = tk.Frame(
            top_button_group,
            bg=BG_WINDOW,
            width=TOP_BUTTON_WIDTH,
            height=TOP_BUTTON_HEIGHT,
        )
        slot.pack(side=tk.LEFT, padx=(0, TOP_BUTTON_GAP))
        slot.pack_propagate(False)

        button = make_outline_button(
            slot,
            text,
            command,
            color,
            font=FONT_UI,
            padx=4,
            pady=4,
        )
        button.pack(fill=tk.BOTH, expand=True)
        return button
    make_top_button(
    "Add Folder...",
    add_directory,
    BTN_COLOR_MAIN,
    )

    make_top_button(
        "Remove Selected",
        remove_selected,
        BTN_COLOR_CLEAR,
    )
    

    progress_var = tk.StringVar(value="Ready")

    status_frame = tk.Frame(
        parent,
        bg=BG_PANEL,
        highlightthickness=1,
        highlightbackground=BG_SEPARATOR,
        bd=0,
    )
    status_frame.pack(
        fill=tk.X,
        padx=(5, 20),
        pady=(2, 8),
    )


    status_label = tk.Label(
        status_frame,
        textvariable=progress_var,
        bg=BG_TREE,
        fg=ACCENT_GREEN,
        font=FONT_UI,
        anchor="center",
        justify="left",
        wraplength=560,
        padx=8,
        pady=7,
    )
    status_label.pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
    )

    report_frame = tk.Frame(
        parent,
        bg=BG_TREE,
        highlightthickness=0,
        highlightbackground="#343A40",
        highlightcolor=ACCENT_GREEN,
        bd=0,
    )
    report_scroll = create_dark_scrollbar(
        report_frame, orientation="vertical"
    )
    report_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    report_text = tk.Text(
        report_frame,
        yscrollcommand=report_scroll.set,
        bg=BG_TREE,
        fg=FG_PRIMARY,
        font=("Consolas", 9),
        wrap="none",
        state=tk.DISABLED,
    )
    report_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    report_scroll.configure(command=report_text.yview)

    def set_report_text(text):
        report_text.config(state=tk.NORMAL)
        report_text.delete("1.0", tk.END)
        report_text.insert("1.0", text)

        # Configure tags using the existing theme.
        report_text.tag_configure(
            "success",
            foreground=ACCENT_GREEN,
        )
        report_text.tag_configure(
            "warning",
            foreground=ACCENT_ORANGE,
        )
        report_text.tag_configure(
            "error",
            foreground=ACCENT_RED,
        )
        report_text.tag_configure(
            "heading",
            foreground=ACCENT_BLUE,
            font=FONT_UI_BOLD,
        )

        line_count = int(
            report_text.index("end-1c").split(".")[0]
        )

        for line_no in range(1, line_count + 1):
            line_start = f"{line_no}.0"
            line_end = f"{line_no}.end"

            line = report_text.get(
                line_start,
                line_end,
            ).strip().lower()

            if not line:
                continue

            if line.startswith(("==", "---")):
                tag = "heading"

            elif any(
                word in line
                for word in ("error", "failed", "exception")
            ):
                tag = "error"

            elif any(
                word in line
                for word in ("warning", "missing")
            ):
                tag = "warning"

            elif any(
                word in line
                for word in ("fixed", "success", "resolved")
            ):
                tag = "success"

            else:
                continue

            report_text.tag_add(
                tag,
                line_start,
                line_end,
            )

        report_text.config(state=tk.DISABLED)


    bottom_row = tk.Frame(parent, bg=BG_WINDOW)
    bottom_row.pack(
        side=tk.BOTTOM,
        fill=tk.X,
        padx=5,
        pady=(0, 10),
    )
    button_group = tk.Frame(bottom_row, bg=BG_WINDOW)
    button_group.pack(fill=tk.X)
    scan_row = tk.Frame(button_group, bg=BG_WINDOW)
    scan_row.pack(anchor="w")
    control_row = tk.Frame(button_group, bg=BG_WINDOW)
    control_row.pack(fill=tk.X)
    control_left = tk.Frame(control_row, bg=BG_WINDOW)
    control_left.pack(side=tk.LEFT)
    control_right = tk.Frame(control_row, bg=BG_WINDOW)
    control_right.pack(side=tk.RIGHT)

    # Equal-width slots prevent buttons resizing when their text changes.
    BUTTON_WIDTH = 132
    BUTTON_HEIGHT = 32
    BUTTON_GAP = 6

    def make_bottom_button(
        text, command, color, container, state=tk.NORMAL
    ):
        slot = tk.Frame(
            container,
            bg=BG_WINDOW,
            width=BUTTON_WIDTH,
            height=BUTTON_HEIGHT,
        )
        slot.pack(side=tk.LEFT, padx=BUTTON_GAP // 2, pady=2)
        slot.pack_propagate(False)

        button = make_outline_button(
            slot,
            text,
            command,
            color,
            font=FONT_UI,
            padx=4,
            pady=4,
            state=state,
        )
        button.pack(fill=tk.BOTH, expand=True)

        return button

    relink_button = make_bottom_button(
        "Scan & Relink", None, BTN_COLOR_GO, scan_row
    )

    collect_button = make_bottom_button(
        "Scan & Collect", None, BTN_COLOR_GO, scan_row
    )

    scan_button = make_bottom_button(
        "Scan (Preview Only)", None, BTN_COLOR_MAIN, scan_row
    )

    pause_button = make_bottom_button(
        "Pause", None, BTN_COLOR_NEUTRAL, control_left, state=tk.DISABLED
    )

    stop_button = make_bottom_button(
        "Stop", None, BTN_COLOR_CLEAR, control_left, state=tk.DISABLED
    )

    save_report_button = make_bottom_button(
        "Save Report...", None, BTN_COLOR_NEUTRAL, control_right
    )

    open_missing_list_button = make_bottom_button(
        "Open Missing List",
        None,
        BTN_COLOR_NEUTRAL,
        control_right,
    )

    scan_state = {
        "active": False,
        "paused": False,
        "priority_suspended": False,
        "pause_requested": threading.Event(),
        "stop_requested": threading.Event(),
        "projects": None,
        "next_index": 0,
        "results": {},
        "database": None,
        "database_loaded": False,
        "dry_run": False,
        "mode": "collect",
        "live_log_handler": None,
    }
    last_report = {"text": ""}

    def save_report():
        if not last_report["text"]:
            messagebox.showinfo("No Report", "Run a scan first.")
            return

        path = filedialog.asksaveasfilename(
            title="Save Missing Media Report",
            defaultextension=".txt",
            initialfile=(
                f"missing_media_report_"
                f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            ),
            filetypes=[
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if path:
            with open(path, "w", encoding="utf-8") as report_file:
                report_file.write(last_report["text"])
            messagebox.showinfo("Saved", f"Report saved to:\n{path}")

    save_report_button.config(command=save_report)

    def open_missing_list():
        path = missing_audio_list_path()
        if not path.exists():
            try:
                save_project_media_status({}, merge=True)
            except OSError:
                pass
        if not path.exists():
            messagebox.showinfo(
                "No Missing List",
                "No missing audio list exists yet. Run a scan first.",
            )
            return
        try:
            os.startfile(path)
        except OSError as error:
            messagebox.showerror("Open Missing List", str(error))

    open_missing_list_button.config(command=open_missing_list)

    report_frame.pack(
        fill=tk.BOTH,
        expand=True,
        padx=5, pady=(5, 20)
    )


    def append_live_report_line(message):
        report_text.config(state=tk.NORMAL)
        report_text.insert(tk.END, f"{message}\n")
        report_text.see(tk.END)
        report_text.config(state=tk.DISABLED)

    def on_progress(message):
        parent.after(0, lambda: progress_var.set(message))
        parent.after(0, lambda: append_live_report_line(message))

    class BulkFixLogHandler(logging.Handler):
        def emit(self, record):
            if not (
                record.name.startswith("abletoolz")
                or record.name == "missing_media_resolver"
            ):
                return
            message = self.format(record)
            try:
                parent.after(
                    0, lambda line=message: append_live_report_line(line)
                )
            except tk.TclError:
                self.handleError(record)

    def remove_live_log_handler():
        handler = scan_state.get("live_log_handler")
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
            scan_state["live_log_handler"] = None

    def restore_buttons():
        scan_button.config(state=tk.NORMAL, text="Scan (Preview Only)")
        relink_button.config(state=tk.NORMAL, text="Scan & Relink")
        collect_button.config(state=tk.NORMAL, text="Scan & Collect")
        save_report_button.config(state=tk.NORMAL)
        pause_button.config(state=tk.DISABLED, text="Pause")
        stop_button.config(state=tk.DISABLED, text="Stop")

    def release_size_priority():
        if scan_state["priority_suspended"]:
            gui.resume_size_estimates_after_heavy_task()
            scan_state["priority_suspended"] = False

    def finish_batch(batch_results, paused, stopped, status_path, status_error):
        remove_live_log_handler()
        scan_state["results"].update(batch_results)
        scan_state["next_index"] += len(batch_results)
        scan_state["active"] = False
        project_count = len(scan_state["projects"] or ())

        if paused and scan_state["next_index"] < project_count:
            scan_state["paused"] = True
            release_size_priority()
            pause_button.config(state=tk.NORMAL, text="Continue")
            progress_var.set(
                f"Paused after {scan_state['next_index']}/{project_count} "
                "projects. Click Continue to resume."
            )
            return

        if stopped:
            report = format_bulk_report(
                scan_state["results"], dry_run=scan_state["dry_run"]
            )
            last_report["text"] = report
            set_report_text(report)
            completed = scan_state["next_index"]
            scan_state["paused"] = False
            scan_state["pause_requested"].clear()
            scan_state["stop_requested"].clear()
            scan_state["projects"] = None
            scan_state["next_index"] = 0
            scan_state["results"] = {}
            scan_state["database"] = None
            scan_state["database_loaded"] = False
            release_size_priority()
            restore_buttons()
            progress_var.set(
                f"Stopped after {completed}/{project_count} projects. "
                "Completed work is safe; the partial scan was not saved as "
                "the latest project status."
            )
            return

        report = format_bulk_report(
            scan_state["results"], dry_run=scan_state["dry_run"]
        )
        results = scan_state["results"].values()
        missing_before = sum(result["missing_before"] for result in results)
        fixed = sum(result["fixed"] for result in results)
        still_missing = sum(result["missing_after"] for result in results)
        mode_word = (
            "would fix" if scan_state["dry_run"]
            else "relinked" if scan_state["mode"] == "relink"
            else "collected/relinked"
        )
        progress_var.set(
            f"Bulk resolve complete: {fixed}/{missing_before} {mode_word} "
            f"across {len(scan_state['results'])} project(s), "
            f"{still_missing} still missing."
        )
        last_report["text"] = report
        set_report_text(report)
        if status_error is not None:
            progress_var.set(
                f"{progress_var.get()} Project status could not be saved; "
                "see the GUI log."
            )
        elif status_path is not None:
            gui.refresh_project_media_status()
            progress_var.set(
                f"{progress_var.get()} Project status saved to "
                f"{status_path.name}."
            )
        scan_state["paused"] = False
        scan_state["pause_requested"].clear()
        release_size_priority()
        restore_buttons()

    def fail_scan(error):
        remove_live_log_handler()
        message = str(error)
        logger.error("Missing-media scan/fix failed: %s", message)
        scan_state["active"] = False
        scan_state["paused"] = False
        release_size_priority()
        progress_var.set(f"Error: {message} (full traceback in GUI log)")
        restore_buttons()

    def run_batch():
        scan_state["active"] = True
        scan_state["paused"] = False
        pause_button.config(state=tk.NORMAL, text="Pause")
        stop_button.config(state=tk.NORMAL, text="Stop")
        scan_button.config(state=tk.DISABLED)
        relink_button.config(state=tk.DISABLED)
        collect_button.config(state=tk.DISABLED)
        save_report_button.config(state=tk.DISABLED)

        def worker():
            try:
                if not scan_state["priority_suspended"]:
                    scan_state["priority_suspended"] = True
                    gui.suspend_size_estimates_for_heavy_task()

                if scan_state["projects"] is None:
                    scan_state["projects"] = find_all_projects()
                projects = scan_state["projects"]
                if not projects:
                    def on_no_projects():
                        remove_live_log_handler()
                        scan_state["active"] = False
                        progress_var.set(
                            "No .als files found in the configured project folders."
                        )
                        release_size_priority()
                        restore_buttons()

                    parent.after(0, on_no_projects)
                    return

                if not scan_state["database_loaded"]:
                    scan_state["database"] = load_sample_index()
                    scan_state["database_loaded"] = True
                if scan_state["database"] is None:
                    on_progress(
                        f"Found {len(projects)} project(s). No sample index "
                        "built yet - missing media will only be reported, "
                        "not fixed. Build one in the Sample Index window first."
                    )
                else:
                    on_progress(
                        f"Found {len(projects)} project(s). Checking each "
                        "against the sample index..."
                    )

                start_index = scan_state["next_index"]
                remaining = projects[start_index:]
                batch_results = resolve_missing_media_bulk(
                    remaining,
                    scan_state["database"],
                    progress_callback=on_progress,
                    dry_run=scan_state["dry_run"],
                    pause_requested=scan_state["pause_requested"],
                    stop_requested=scan_state["stop_requested"],
                    progress_offset=start_index,
                    total_count=len(projects),
                    mode=scan_state["mode"],
                )
                paused = len(batch_results) < len(remaining)
                stopped = (
                    scan_state["stop_requested"].is_set()
                    and len(batch_results) < len(remaining)
                )
                status_path = None
                status_error = None
                if not paused and not stopped and not scan_state["dry_run"]:
                    complete_results = dict(scan_state["results"])
                    complete_results.update(batch_results)
                    try:
                        status_path = save_project_media_status(complete_results)
                    except OSError as error:
                        logger.exception(
                            "Could not save project media status snapshot"
                        )
                        status_error = str(error)
                parent.after(
                    0,
                    lambda results=batch_results, is_paused=paused,
                    is_stopped=stopped, saved_path=status_path,
                    save_error=status_error:
                    finish_batch(
                        results,
                        is_paused,
                        is_stopped,
                        saved_path,
                        save_error,
                    ),
                )
            except Exception as error:
                logger.exception(
                    "Missing-media scan/fix failed (%s)", str(error)
                )
                parent.after(0, lambda caught=error: fail_scan(caught))

        threading.Thread(target=worker, daemon=True).start()

    def pause_or_continue():
        if scan_state["active"]:
            scan_state["pause_requested"].set()
            pause_button.config(
                state=tk.DISABLED,
                text="Pausing...",
            )
            progress_var.set(
                "Pausing after the current project finishes..."
            )
        elif scan_state["paused"]:
            scan_state["pause_requested"].clear()
            run_batch()

    def stop_scan():
        if scan_state["active"]:
            scan_state["stop_requested"].set()
            pause_button.config(state=tk.DISABLED)
            stop_button.config(state=tk.DISABLED, text="Stopping...")
            progress_var.set(
                "Stopping after the current project finishes safely..."
            )
            return
        if not scan_state["paused"]:
            return

        completed = scan_state["next_index"]
        project_count = len(scan_state["projects"] or ())
        if scan_state["results"]:
            report = format_bulk_report(
                scan_state["results"], dry_run=scan_state["dry_run"]
            )
            last_report["text"] = report
            set_report_text(report)
        scan_state["paused"] = False
        scan_state["pause_requested"].clear()
        scan_state["stop_requested"].clear()
        scan_state["projects"] = None
        scan_state["next_index"] = 0
        scan_state["results"] = {}
        scan_state["database"] = None
        scan_state["database_loaded"] = False
        release_size_priority()
        restore_buttons()
        progress_var.set(
            f"Stopped after {completed}/{project_count} projects. "
            "The partial scan was not saved as the latest project status."
        )

    def run(dry_run, mode="collect"):
        if dry_run:
            confirm = messagebox.askyesno(
                "Scan (Preview Only)",
                "This will scan every .als file under the configured project "
                "folders and check for missing media against the current "
                "sample index.\n\nThis is a PREVIEW - no project files will "
                "be modified.\n\nThis can take a while for a large library. "
                "Continue?",
            )
        else:
            confirm = messagebox.askyesno(
                "Scan & Relink" if mode == "relink" else "Scan & Collect",
                "This will scan every .als file under the configured project "
                "folders and SAVE any missing-media fixes it can resolve "
                "from the current sample index.\n\nabletoolz backs up each "
                "original automatically under abletoolz_backup/ before "
                "saving. A match requires the same filename and exact "
                "OriginalFileSize; modification time alone is not enough. "
                + (
                    "Matched references are updated to the indexed audio "
                    "file's absolute path; no audio files are copied."
                    if mode == "relink"
                    else "Matched external samples are copied into the project "
                    "and references are updated to those collected copies."
                )
                + "\n\nThis "
                "can take a while for a large "
                "library. Continue?",
            )
        if not confirm:
            return

        scan_state.update({
            "active": False,
            "paused": False,
            "priority_suspended": False,
            "projects": None,
            "next_index": 0,
            "results": {},
            "database": None,
            "database_loaded": False,
            "dry_run": dry_run,
            "mode": mode,
        })
        scan_state["pause_requested"].clear()
        scan_state["stop_requested"].clear()
        scan_button.config(text="Scanning..." if dry_run else "Scan (Preview Only)")
        relink_button.config(
            text="Scanning..." if mode == "relink" and not dry_run
            else "Scan & Relink"
        )
        collect_button.config(
            text="Scanning..." if mode == "collect" and not dry_run
            else "Scan & Collect"
        )
        live_handler = BulkFixLogHandler()
        live_handler.setLevel(logging.INFO)
        live_handler.setFormatter(
            logging.Formatter("%(levelname)s - %(name)s - %(message)s")
        )
        scan_state["live_log_handler"] = live_handler
        logging.getLogger().addHandler(live_handler)
        pause_button.config(state=tk.NORMAL, text="Pause")
        stop_button.config(state=tk.NORMAL, text="Stop")
        progress_var.set("Preparing Bulk Fix and finding project files...")
        set_report_text("")
        run_batch()

    scan_button.config(command=lambda: run(dry_run=True, mode="collect"))
    relink_button.config(
        command=lambda: run(dry_run=False, mode="relink")
    )
    collect_button.config(
        command=lambda: run(dry_run=False, mode="collect")
    )
    pause_button.config(command=pause_or_continue)
    stop_button.config(command=stop_scan)
