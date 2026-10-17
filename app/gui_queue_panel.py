"""Export Queue panel construction and behavior."""

import logging
import os
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk
import project_structure
import size_estimator
from gui_theme import (
    ACCENT_CYAN,
    ACCENT_NEUTRAL,
    ACCENT_RED,
    BG_BUTTON,
    BG_INPUT,
    BG_PANEL,
    BG_SELECTED,
    BG_SEPARATOR,   
    ADD_ALL_TO_QUEUE,
    BG_TREE,
    BG_WINDOW,
    BTN_COLOR_CLEAR,
    BTN_COLOR_GO,
    BTN_COLOR_OFF,
    BTN_COLOR_UTILITY,
    FG_CHECKMARK,
    FG_MUTED,
    FG_PRIMARY,
    FG_SECONDARY,
    FG_SELECTED,
    FONT_HEADER,
    FONT_TREE_MONO,
    FONT_UI,
    FONT_UI_BOLD,
    FONT_UI_SM,
    FONT_TREE_MONO,
    QUEUE_TASK_COLLECT,
    QUEUE_TASK_DONE,
    QUEUE_TASK_MAIN,
    QUEUE_TASK_STEMS,
    QUEUE_TREE_COLUMNS,
    QUEUE_TREE_COLUMN_WIDTHS,
    create_dark_scrollbar,
    make_outline_button,
)

logger = logging.getLogger("AutoExport")


class QueuePanelMixin:
    """Queue widget construction and task interaction handlers."""

    def _toggle_queue_export_state(self):
        paths = self._selected_queue_paths()
        if not paths:
            return "break"
        tasks = [
            task for task in self.task_manager.get_tasks()
            if task.get("project_path") in paths
        ]
        if not tasks:
            return "break"
        first = tasks[0]
        current = (
            "stems" if first.get("export_stems")
            else "main" if first.get("export_main")
            else "off"
        )
        next_state = self._next_export_state(current)
        for task in tasks:
            task["export_stems"] = next_state == "stems"
            task["export_main"] = next_state == "main"
        self.task_manager.save_tasks()
        self.request_size_estimates()
        if self._apply_queue_sort():
            self.update_queue_tree()
        else:
            for task in tasks:
                self._refresh_queue_row(task)
            self._update_size_total_label()
        return "break"

    def create_queue_panel(self):
        """Create the right-side export queue panel"""

        # ===== RIGHT SIDE: Queue =====
        right_panel = tk.Frame(self.content_frame, bg=BG_WINDOW)
        right_panel.grid(
            row=0, column=1, sticky="nsew", padx=(5, 0)
        )
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)
        self.queue_panel = tk.Frame(
            right_panel,
            bg=BG_PANEL,
            highlightthickness=2,
            highlightbackground=BG_PANEL,
            highlightcolor=ACCENT_CYAN,
            padx=10,
            pady=10,
            
        )
        self.queue_panel.grid(row=0, column=0, sticky="nsew")
        self.queue_panel.grid_rowconfigure(1, weight=1)
        self.queue_panel.grid_columnconfigure(0, weight=1)

        heading = tk.Frame(self.queue_panel, bg=BG_PANEL)
        heading.grid(row=0, column=0, sticky="ew", padx=5, pady=(0, 4))
        heading.grid_columnconfigure(1, weight=1)
        tk.Label(
            heading, text="Tasks Queue", bg=BG_PANEL, fg=ACCENT_NEUTRAL,
            font=FONT_HEADER, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        reorder = tk.Frame(heading, bg=BG_PANEL)  # Removed border and relief settings
        reorder.grid(row=0, column=1, sticky="e")
        tk.Label(
            reorder, text="Reorder By  ", bg=BG_PANEL, fg=FG_SECONDARY,
            font=FONT_UI_BOLD,
        ).pack(side=tk.LEFT, padx=(0, 0))
        self.queue_sort_menu = ctk.CTkOptionMenu(
            reorder,
            values=[
                "Manual",
                "Size - Largest to Smallest", "Size - Smallest to Largest",
                "Alphabetical", "Date Modified", "Date Created",
            ],
            command=self.set_queue_sort,
            width=180,
            height=28,
            fg_color=BG_BUTTON,
            button_color=BG_INPUT,
            button_hover_color=BG_SELECTED,
            text_color=FG_PRIMARY,
            dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=BG_SELECTED,
            dropdown_text_color=FG_PRIMARY,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=12),
            corner_radius=4,
            dynamic_resizing=False,
        )
        self._style_option_menu_selection(self.queue_sort_menu)
        self.bind_status_tooltip(
            self.queue_sort_menu, "Change the order of queued export tasks."
        )
        self.queue_sort_menu.set(self.queue_sort_by)
        self.queue_sort_menu.pack(side=tk.LEFT)

        # Queue treeview (displays queue items)
        queue_tree_frame = tk.Frame(self.queue_panel, bg=BG_TREE)
        queue_tree_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=(0, 5))
        queue_tree_frame.grid_rowconfigure(0, weight=1)
        queue_tree_frame.grid_columnconfigure(0, weight=1)
        
        queue_scrollbar = create_dark_scrollbar(
            queue_tree_frame, orientation="vertical", width=18
        )
        queue_scrollbar.grid(row=0, column=1, sticky="ns")
        
        ttk.Style().configure("Queue.Treeview", font=FONT_TREE_MONO)
        self.queue_tree = ttk.Treeview(
            queue_tree_frame,
            yscrollcommand=queue_scrollbar.set,
            height=15,
            style="Queue.Treeview",
            columns=QUEUE_TREE_COLUMNS,
            show="tree",
            
        )
        
        self.queue_tree.column(
            "#0",
            width=QUEUE_TREE_COLUMN_WIDTHS["#0"],
            minwidth=150,
            anchor="w",
            stretch=True,
        )

        self.queue_tree.column(
            "task",
            width=QUEUE_TREE_COLUMN_WIDTHS["task"],
            anchor="center",
            stretch=False,
        )

        self.queue_tree.column(
            "settings",
            width=QUEUE_TREE_COLUMN_WIDTHS["settings"],
            anchor="center",
            stretch=False,
        )

        self.queue_tree.column(
            "size",
            width=QUEUE_TREE_COLUMN_WIDTHS["size"],
            anchor="center",
            stretch=False,
        )

        self.queue_tree.column(
            "done",
            width=QUEUE_TREE_COLUMN_WIDTHS["done"],
            anchor="center",
            stretch=False,
        )



        queue_scrollbar.configure(command=self.queue_tree.yview)
        self.queue_tree.grid(row=0, column=0, sticky="nsew")
        
        
        self.queue_tree.bind("<Button-1>", self.on_queue_tree_click)
        self.queue_tree.bind("<Button-3>", self.on_queue_context_menu)
        self.queue_tree.bind("<Motion>", self._on_queue_tree_hover, add="+")
        self.queue_tree.bind("<Leave>", self._clear_status_tooltip, add="+")
        self.queue_tree.bind("<FocusIn>", lambda _event: self._activate_tree("queue"), add="+")
        self.queue_tree.bind("<Tab>", self._switch_active_tree, add="+")
        self.queue_tree.bind(
            "<<TreeviewSelect>>",
            lambda _event: self.refresh_queue_selection_visuals(),
            add="+",
        )
        self.queue_tree.bind("<MouseWheel>", self._on_queue_mousewheel)
        self.queue_tree.bind("<Button-4>", self._on_queue_mousewheel)
        self.queue_tree.bind("<Button-5>", self._on_queue_mousewheel)
        self.queue_tree.bind("<Double-1>", self.on_queue_double_click)
        self.queue_item_errors = {}  # tree item id -> ("recursive", plan) | ("failed", task)


        # Drag-drop for queue reordering
        self.queue_tree.bind("<B1-Motion>", self.on_queue_drag_motion)
        self.queue_tree.bind("<ButtonRelease-1>", self.on_queue_drag_release)
        self.dragged_queue_item = None
        self.drag_start_y = None  # Track drag start position
        self.drop_indicator_item = None  # Track insertion point indicator
        self._create_queue_action_bar()
        self._bind_tree_hotkeys()


    def _move_queue_item_hotkey(self, direction):
        if self.queue_sort_by != "Manual":
            return
        if direction < 0:
            self.move_queue_item_up()
        else:
            self.move_queue_item_down()


    def _toggle_queue_option_hotkey(self, option, default):
        paths = self._selected_queue_paths()
        if not paths:
            return
        tasks = [
            task for task in self.task_manager.get_tasks()
            if task.get("project_path") in paths
        ]
        value = not all(bool(task.get(option, default)) for task in tasks)
        self._set_queue_option(paths, option, value, default)


    def _queue_horizontal_hotkey(self):
        if not self.queue_tree.selection():
            self.navigate_queue_selection(1)


    def navigate_queue_selection(self, direction):
        items = list(self.queue_tree.get_children(""))
        if not items:
            return "break"
        selected = list(self.queue_tree.selection())
        if not selected or selected[0] not in items:
            next_item = items[0]
        else:
            index = items.index(selected[0])
            next_item = items[max(0, min(len(items) - 1, index + direction))]
        self.queue_tree.selection_set(next_item)
        self.queue_tree.focus(next_item)
        self.queue_tree.see(next_item)
        return "break"


    def _selected_queue_paths(self):
        selected_items = set(self.queue_tree.selection())
        return {
            path for path, item in self.queue_path_to_item.items()
            if item in selected_items
        }

    def remove_missing_queue_tasks(self):
        """Remove every queued project whose latest saved media scan is Missing."""
        self._load_project_media_status()
        missing_paths = {
            task["project_path"]
            for task in self.task_manager.get_tasks()
            if self._project_media_status_label(task["project_path"]) == "Missing"
        }
        self.remove_from_queue(missing_paths)
        return "break"


    def _create_queue_action_bar(self):
        """Place queue actions and the estimate in a single aligned row."""
        actions = tk.Frame(self.queue_panel, bg=BG_PANEL)
        actions.grid(row=2, column=0, sticky="ew", padx=2, pady=(2, 0))
        actions.grid_columnconfigure(2, weight=1)
        self.size_total_label = tk.Label(
            actions,
            text="Estimated size: —",
            bg=BG_INPUT,
            fg=FG_PRIMARY,
            font=FONT_UI_BOLD,
            anchor="w",
            width=34,
            padx=6,
            pady=4,
        )

        add_button = make_outline_button(
            actions,
            "Add All to Queue",
            self.add_all_enabled_to_queue,
            ADD_ALL_TO_QUEUE,
            font=FONT_UI_BOLD,
            padx=6,
            pady=4,
        )
        add_button.grid(row=0, column=0, sticky="ew", padx=2)
        self.bind_status_tooltip(
            add_button,
            "Add every enabled project to the export queue (Enter).",
        )
        clear_button = make_outline_button(
            actions,
            "Clear All",
            self.clear_queue,
            BTN_COLOR_CLEAR,
            font=FONT_UI_BOLD,
            padx=6,
            pady=4,
        )
        clear_button.grid(row=0, column=1, sticky="ew", padx=2)
        self.bind_status_tooltip(
            clear_button, "Remove every task from the export queue."
        )

        self.size_total_label.grid(row=0, column=2, sticky="e", padx=4)
        self.bind_status_tooltip(
            self.size_total_label,
            "Estimated total size of the queued exports at the current audio settings.",
        )
        start_button = make_outline_button(
            actions,
            "Start",
            self.start_export_from_queue,
            BTN_COLOR_GO,
            font=FONT_HEADER,
            padx=10,
            pady=4,
        )
        start_button.grid(row=0, column=3, sticky="e", padx=2)
        self.bind_status_tooltip(
            start_button, "Begin automation procedure, exporting every task in the queue."
        )


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

        task_type = (
            f"{'Collect' if collect else '—':<7} "
            f"{'Stems' if export_stems else '—':<5} "
            f"{'Main' if export_main else '—':<4}"
        )
        settings_display = (
            f"{'Nrmlz' if task.get('normalize', False) else '—':<5} "
            f"{'RtnFX' if task.get('include_return_and_main_effects', True) else '—':<5} "
            f"{'WetDry' if task.get('wet_dry', False) else '—':<6}"
        )

        # Color code by task type, fade if done
        if task.get("done", False):
            tag = "queue_done"
        elif not (collect or export_stems or export_main):
            tag = "queue_skip"
        elif collect and not export_stems and not export_main:
            tag = "queue_collect"
        elif export_stems:
            tag = "queue_stems"
        else:
            tag = "queue_main"

        cached = self.size_cache.get(self._size_cache_key(task))
        if cached is None:
            size_display = "…"
        elif "error" in cached:
            size_display = "err"
        elif task.get("wet_dry", False):
            size_display = size_estimator.format_bytes(self._task_size_bytes(task))
        else:
            size_display = cached["total_size"]
            number_end = next(
                (i for i, char in enumerate(size_display)
                 if not (char.isdigit() or char == ".")),
                len(size_display),
            )
            if size_display[number_end:].strip().upper() in {"KB", "MB"}:
                size_display = (
                    str(int(float(size_display[:number_end])))
                    + size_display[number_end:]
                )

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

        return (task_type, settings_display, size_display, done_indicator), tag, error


    @staticmethod
    def _queue_task_score(task):
        return (
            10 * bool(task.get("export_stems", False))
            + 2 * bool(task.get("export_main", False))
            + 2 * (not bool(task.get("include_return_and_main_effects", True)))
        )


    def _queue_sort_key(self, task):
        name = os.path.basename(task["project_path"]).casefold()
        mode = self.queue_sort_by
        if mode == "Alphabetical":
            return name
        if mode in ("Date Modified", "Date Created"):
            try:
                metadata = os.stat(task["project_path"])
                return (
                    metadata.st_mtime
                    if mode == "Date Modified"
                    else getattr(metadata, "st_ctime")
                )
            except OSError:
                return float("-inf")

        estimated_size = self._task_size_bytes(task)
        if mode.startswith("Task - "):
            score_size = getattr(self, "_queue_sort_fallback_size", 1_000_000)
            value = (
                estimated_size if estimated_size is not None
                else self._queue_task_score(task) * score_size
            )
            return value, name
        if mode == "Size - Largest to Smallest":
            return estimated_size is None, -(estimated_size or 0), name
        return (
            estimated_size is None,
            estimated_size if estimated_size is not None else 0,
            name,
        )


    def _apply_queue_sort(self):
        mode = self.queue_sort_by
        if mode == "Manual":
            return False
        tasks = self.task_manager.get_tasks()
        if mode.startswith("Task - "):
            sizes = sorted(
                value for task in tasks
                if (value := self._task_size_bytes(task)) is not None and value > 0
            )
            self._queue_sort_fallback_size = (
                sizes[len(sizes) // 2] / 10 if sizes else 1_000_000
            )
        reverse = mode in {
            "Date Modified", "Date Created",
        }
        sorted_tasks = sorted(tasks, key=self._queue_sort_key, reverse=reverse)
        if [task["project_path"] for task in tasks] == [
            task["project_path"] for task in sorted_tasks
        ]:
            return False
        tasks[:] = sorted_tasks
        self.task_manager.save_tasks()
        return True


    def set_queue_sort(self, sort_by):
        valid_modes = {
            "Manual",
            #"Task - Light to Heavy",
            #"Task - Heavy to Light",
            "Size - Largest to Smallest",
            "Size - Smallest to Largest",
            "Alphabetical",
            "Date Modified",
            "Date Created",
        }
        if sort_by not in valid_modes:
            logger.warning("Ignoring unknown queue sort mode %r", sort_by)
            return
        self.queue_sort_by = sort_by
        if self._apply_queue_sort():
            self.update_queue_tree()


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
        computed = sum(
            1 for task in tasks
            if (
                (cached := self.size_cache.get(self._size_cache_key(task)))
                is not None
                and "error" not in cached
            )
        )
        self.size_estimate_total = self._get_total_size_estimate()
        self.size_total_label.config(
            text=f"Total Estimated Size: {size_estimator.format_bytes(self.size_estimate_total)} "
                f"({computed}/{len(tasks)})"
        )


    def update_queue_tree(self):
        """Rebuild the queue view from EXPORT_TASKS.json.

        Uses in-memory state only, so it stays fast however long the queue is.
        Filesystem checks and size estimates run in the background and update
        their rows as they finish (see poll_size_estimates).
        """
        self._apply_queue_sort()
        tasks = self.task_manager.get_tasks()
        previous_items = self.queue_path_to_item
        updated_items = {}
        updated_errors = {}
        for idx, task in enumerate(tasks):
            project_path = task["project_path"]
            project_name = os.path.basename(project_path)
            if project_name.endswith('.als'):
                project_name = project_name[:-4]

            values, tag, error = self._queue_row_display(task)
            item_id = previous_items.get(project_path)
            if item_id is None or not self.queue_tree.exists(item_id):
                item_id = self.queue_tree.insert(
                    "", idx, text=f"{idx + 1:02d} {project_name}",
                    values=values, tags=(tag,),
                )
            else:
                self.queue_tree.item(
                    item_id,
                    text=f"{idx + 1:02d} {project_name}",
                    values=values,
                    tags=(tag,),
                )
                self.queue_tree.move(item_id, "", idx)
            updated_items[project_path] = item_id
            if error:
                updated_errors[item_id] = error

        retained_items = set(updated_items.values())
        for item_id in self.queue_tree.get_children():
            if item_id not in retained_items:
                self.queue_tree.delete(item_id)
        self.queue_path_to_item = updated_items
        self.queue_item_errors = updated_errors

        # Configure tags for task types
        self.queue_tree.tag_configure("queue_main", foreground=QUEUE_TASK_MAIN)
        self.queue_tree.tag_configure("queue_stems", foreground=QUEUE_TASK_STEMS)
        self.queue_tree.tag_configure("queue_collect", foreground=QUEUE_TASK_COLLECT)
        self.queue_tree.tag_configure("queue_done", foreground=QUEUE_TASK_DONE)
        self.queue_tree.tag_configure(
            "queue_skip", foreground=FG_MUTED
        )
        self.queue_tree.tag_configure("queue_error", foreground=ACCENT_RED)    # Red for errors
        self.queue_tree.tag_configure(
            "queue_selected", background=BG_SELECTED, foreground=FG_SELECTED
        )

        
        if not tasks:
            self.queue_tree.insert(
            "", "end",
            text="Export Queue",
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
        self.refresh_queue_selection_visuals()


    def refresh_queue_selection_visuals(self):
        """Apply the shared inverse selection colors without losing task tags."""
        if not getattr(self, "queue_tree", None):
            return
        selected = set(self.queue_tree.selection())
        for item in self.queue_tree.get_children():
            tags = [
                tag for tag in self.queue_tree.item(item, "tags")
                if tag != "queue_selected"
            ]
            if item in selected:
                tags.append("queue_selected")
            self.queue_tree.item(item, tags=tags)


    def add_all_enabled_to_queue(self):
        """Add every enabled project to the export queue."""
        entries = [
            self._queue_entry(project_path, cfg)
            for project_path, cfg in self.selected_projects.items()
            if cfg.get("enabled", True)
        ]
        self.task_manager.add_tasks(entries)
        
        # Update display silently (no popup)
        self.update_queue_tree()
        return "break"


    def add_selected_to_queue(self):
        """Add selected projects (or selected project folders) to the queue."""
        return self.enqueue_selected_projects()


    @staticmethod
    def _queue_entry(project_path, cfg):
        return {
            "project_path": project_path,
            "export_stems": cfg.get("export_state", "main") == "stems",
            "export_main": cfg.get("export_state", "main") == "main",
            "collect_all_and_save": cfg.get("collect_all_and_save", False),
            "include_return_and_main_effects": cfg.get(
                "include_return_and_main_effects", True
            ),
            "normalize": cfg.get("normalize", False),
            "wet_dry": cfg.get("wet_dry", False),
        }


    def enqueue_selected_projects(self, _event=None):
        """Add only the currently selected browser projects to the queue."""
        entries = [
            self._queue_entry(path, self.selected_projects[path])
            for path in self._selected_project_paths()
            if path in self.selected_projects
        ]
        if entries:
            self.task_manager.add_tasks(entries)
            self.update_queue_tree()
        return "break"


    def return_queue_selection_to_browser(self, _event=None):
        """Remove selected queue tasks and restore missing projects to the browser."""
        selected_paths = {
            path for path, item_id in self.queue_path_to_item.items()
            if item_id in self.queue_tree.selection()
        }
        tasks_by_path = {
            task["project_path"]: task
            for task in self.task_manager.get_tasks()
        }
        changed = False
        for path in selected_paths:
            changed |= self._add_project_file_to_browser(
                path, tasks_by_path.get(path)
            )
        if changed:
            self.update_tree()
        self.remove_from_queue()
        return "break"


    def remove_from_queue(self, project_paths=None):
        """Remove selected queue items, or the supplied project paths."""
        if project_paths is None:
            selected = self.queue_tree.selection()
            if not selected:
                return
            project_paths = {
                path for path, item_id in self.queue_path_to_item.items()
                if item_id in selected
            }
        if not project_paths:
            return
        self.task_manager.remove_tasks(project_paths)
        self.update_queue_tree()
        self.queue_selection_anchor = None

    def _on_queue_tree_hover(self, event):
        if self.queue_tree.identify_column(event.x) == "#0":
            self.show_status_tooltip(
                "Right-click a queue name to remove that project."
            )
        else:
            self._clear_status_tooltip()


    def _move_selected_queue_item(self, offset):
        selected = self.queue_tree.selection()
        tasks = self.task_manager.get_tasks()
        if not selected or not tasks:
            return "break"

        selected_paths = {
            path for path, item_id in self.queue_path_to_item.items()
            if item_id in selected
        }
        anchor_path = next(
            (
                path for path, item_id in self.queue_path_to_item.items()
                if item_id == self.queue_selection_anchor
            ),
            None,
        )
        focused_path = next(
            (
                path for path, item_id in self.queue_path_to_item.items()
                if item_id == self.queue_tree.focus()
            ),
            None,
        )
        selected_flags = [
            task["project_path"] in selected_paths for task in tasks
        ]
        if offset < 0:
            for index in range(1, len(tasks)):
                if selected_flags[index] and not selected_flags[index - 1]:
                    tasks[index - 1], tasks[index] = tasks[index], tasks[index - 1]
                    selected_flags[index - 1], selected_flags[index] = (
                        selected_flags[index], selected_flags[index - 1]
                    )
        else:
            for index in range(len(tasks) - 2, -1, -1):
                if selected_flags[index] and not selected_flags[index + 1]:
                    tasks[index], tasks[index + 1] = tasks[index + 1], tasks[index]
                    selected_flags[index], selected_flags[index + 1] = (
                        selected_flags[index + 1], selected_flags[index]
                    )

        self.task_manager.save_tasks()
        self.update_queue_tree()
        new_selection = [
            self.queue_path_to_item[task["project_path"]]
            for task in tasks
            if task["project_path"] in selected_paths
            and task["project_path"] in self.queue_path_to_item
        ]
        if new_selection:
            self.queue_tree.selection_set(new_selection)
            self.queue_selection_anchor = self.queue_path_to_item.get(
                anchor_path, new_selection[0]
            )
            self.queue_tree.focus(
                self.queue_path_to_item.get(focused_path, new_selection[0])
            )
        return "break"


    def move_queue_item_up(self, _event=None):
        """Move the selected queue item up and keep it selected."""
        return self._move_selected_queue_item(-1)


    def move_queue_item_down(self, _event=None):
        """Move the selected queue item down and keep it selected."""
        return self._move_selected_queue_item(1)


    def clear_queue(self):
        """Clear all items from queue and reset done flags"""
        self.task_manager.clear_queue()
        self.update_queue_tree()


    def on_queue_tree_click(self, event):
        """Handle queue tree click with Ctrl/Shift for multiple selection"""
        self._activate_tree("queue")
        self.queue_tree.focus_set()
        item = self.queue_tree.identify("item", event.x, event.y)
        if not item:
            return "break"
        all_items = self.queue_tree.get_children()
        if event.state & 0x4:
            if item in self.queue_tree.selection():
                self.queue_tree.selection_remove(item)
            else:
                self.queue_tree.selection_add(item)
            if self.queue_selection_anchor is None:
                self.queue_selection_anchor = item
        elif event.state & 0x1:
            if self.queue_selection_anchor not in all_items:
                self.queue_selection_anchor = item
            start = all_items.index(self.queue_selection_anchor)
            end = all_items.index(item)
            self.queue_tree.selection_set(
                all_items[min(start, end):max(start, end) + 1]
            )
        else:
            self.queue_tree.selection_set(item)
            self.queue_selection_anchor = item
        self.queue_tree.focus(item)
        self.refresh_queue_selection_visuals()
        self.on_queue_drag_start(event)
        return "break"


    def on_queue_context_menu(self, event):
        self._activate_tree("queue")
        self.queue_tree.focus_set()
        item = self.queue_tree.identify("item", event.x, event.y)
        if not item:
            return "break"
        if item not in self.queue_tree.selection():
            self.queue_tree.selection_set(item)
            self.queue_selection_anchor = item
        self.queue_tree.focus(item)
        self.refresh_queue_selection_visuals()

        column = self.queue_tree.identify_column(event.x)
        if column == "#0":
            project_path = next(
                (
                    path for path, item_id in self.queue_path_to_item.items()
                    if item_id == item
                ),
                None,
            )
            if project_path is None:
                return "break"
            menu = tk.Menu(
                self.root,
                tearoff=False,
                bg=BG_INPUT,
                fg=FG_PRIMARY,
                activebackground=BG_SELECTED,
                activeforeground=FG_SELECTED,
                borderwidth=1,
                relief=tk.FLAT,
                font=FONT_UI,
            )
            menu.add_command(
                label="Remove",
                command=lambda path=project_path:
                    self.remove_from_queue({path}),
            )
            menu.bind(
                "<Motion>",
                lambda _event: self.show_status_tooltip(
                    "Remove this project from the export queue."
                ),
                add="+",
            )
            menu.bind("<Leave>", self._clear_status_tooltip, add="+")
            menu.tk_popup(event.x_root, event.y_root)
            return "break"
        if column == "#1":
            options = (
                ("Collect All and Save", "collect_all_and_save", False),
                ("Stems", "export_stems", False),
                ("Main", "export_main", False),
            )
        elif column == "#2":
            options = (
                ("Normalize", "normalize", False),
                ("Return FX", "include_return_and_main_effects", True),
                ("Wet+Dry", "wet_dry", False),
            )
        else:
            return "break"

        selected_paths = {
            path for path, item_id in self.queue_path_to_item.items()
            if item_id in self.queue_tree.selection()
        }
        task_by_path = {
            task["project_path"]: task for task in self.task_manager.get_tasks()
        }
        # Right click menu design
        menu = tk.Menu(
            self.root, 
            tearoff=False, 
            bg=BG_INPUT, 
            fg=FG_PRIMARY,
            activebackground=BG_SELECTED, 
            activeforeground=FG_SELECTED,
            selectcolor=FG_CHECKMARK, 
            borderwidth=1, 
            activeborderwidth=1,
            relief=tk.FLAT, 
            font=FONT_UI,
        )
        for label, key, default in options:
            selected_tasks = [
                task_by_path[path] for path in selected_paths
                if path in task_by_path
            ]
            variable = tk.BooleanVar(
                self.root,
                value=bool(selected_tasks) and all(
                    bool(task.get(key, default)) for task in selected_tasks
                ),
            )

            def apply_option(option_key=key, option_default=default, var=variable):
                self._set_queue_option(
                    selected_paths, option_key, bool(var.get()), option_default
                )
                self.root.after_idle(menu.unpost)

            menu.add_checkbutton(
                label=label,
                variable=variable,
                onvalue=True,
                offvalue=False,
                command=apply_option,
            )
        menu.tk_popup(event.x_root, event.y_root)
        return "break"


    def _set_queue_option(self, project_paths, option, value, default=False):
        changed = False
        for task in self.task_manager.get_tasks():
            if task.get("project_path") in project_paths:
                if bool(task.get(option, default)) != value:
                    task[option] = value
                    changed = True
        if not changed:
            return
        self.task_manager.save_tasks()
        self.request_size_estimates()
        if self._apply_queue_sort():
            self.update_queue_tree()
            return
        for task in self.task_manager.get_tasks():
            if task.get("project_path") in project_paths:
                self._refresh_queue_row(task)
        self._update_size_total_label()


    def extend_queue_selection(self, direction):
        """Extend the queue selection from its click/keyboard anchor."""
        all_items = self.queue_tree.get_children()
        if not all_items:
            return "break"
        anchor = self.queue_selection_anchor
        if anchor not in all_items:
            anchor = self.queue_tree.focus()
        if anchor not in all_items:
            anchor = all_items[0]
        current = self.queue_tree.focus()
        if current not in all_items:
            current = anchor
        next_index = max(
            0, min(len(all_items) - 1, all_items.index(current) + direction)
        )
        next_item = all_items[next_index]
        anchor_index = all_items.index(anchor)
        self.queue_tree.selection_set(
            all_items[min(anchor_index, next_index):max(anchor_index, next_index) + 1]
        )
        self.queue_tree.focus(next_item)
        self.queue_tree.see(next_item)
        self.refresh_queue_selection_visuals()
        return "break"


    def select_all_queue_rows(self, event=None):
        """Select every task in the queue so Delete/Remove can act on all of them."""
        if not self.task_manager.get_tasks():
            return "break"
        items = self.queue_tree.get_children()
        self.queue_tree.selection_set(items)
        self.queue_selection_anchor = items[0] if items else None
        self.refresh_queue_selection_visuals()
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
        win.withdraw()
        win.title("Export failure")
        win.configure(bg=BG_PANEL)
        win.resizable(False, False)

        rows = [
            ("Project", project_path),
            ("Failed step", failure.get("step", "unknown")),
            ("Details", failure.get("detail", "No details were recorded")),
            ("When", failure.get("at", "unknown")),
            ("Screenshot", failure.get("screenshot") or "none"),
            ("Log", failure.get("log") or "none"),
        ]
        body = tk.Frame(win, bg=BG_PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=(12, 6))
        for r, (label, value) in enumerate(rows):
            tk.Label(
                body, text=label, bg=BG_PANEL, fg=FG_SECONDARY, font=FONT_UI_BOLD, anchor="nw"
            ).grid(row=r, column=0, sticky="nw", padx=(0, 10), pady=2)
            tk.Label(
                body, text=str(value), bg=BG_PANEL,
                fg=ACCENT_RED if label == "Details" else FG_PRIMARY,
                font=FONT_UI, anchor="w", justify="left", wraplength=520,
            ).grid(row=r, column=1, sticky="w", pady=2)
        tk.Label(
            win,
            text="The project stays in the queue and is retried on the next export run.",
            bg=BG_PANEL, fg=FG_SECONDARY, font=FONT_UI_SM,
        ).pack(padx=14, pady=(0, 8), anchor="w")

        buttons = tk.Frame(win, bg=BG_PANEL)
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
        if self.queue_sort_by != "Manual":
            self.dragged_queue_item = None
            self.drag_start_y = None
            return
        item = self.queue_tree.identify("item", event.x, event.y)
        if item:
            self.dragged_queue_item = item
            self.drag_start_y = event.y


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
