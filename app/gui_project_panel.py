"""Project Browser panel construction and behavior."""

import json
import logging
import os
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from paths import data_file

from gui_keybindings import PROJECT_HOTKEYS, hotkey_texts
from gui_theme import (
    ACCENT_CYAN,
    ACCENT_NEUTRAL,
    BG_BUTTON,
    BG_PANEL,
    BG_INPUT,
    BG_ROW_HEADER,
    BG_SEARCH_MATCH,
    BG_SELECTED,
    BG_SELECTION,
    BG_SEPARATOR,
    BG_TREE,
    BG_TREE_HEADER,
    BG_WINDOW,
    FG_DISABLED,
    FG_DISABLED_BUTTON,
    FG_MUTED,
    FG_PRIMARY,
    FG_ROW_HEADER,
    FG_SECONDARY,
    FG_SELECTED,
    FG_SELECTED_DISABLED,
    FG_TREE_ARROW,
    FG_TREE_ARROW_ACTIVE,
    FG_TREE_ARROW_PRESSED,
    FONT_HEADER,
    FONT_TREE,
    FONT_TREE_BOLD,
    FONT_TREE_HEADER,
    FONT_UI,
    FONT_UI_BOLD,
    PROJECT_TREE_COLUMNS,
    PROJECT_TREE_COLUMN_WIDTHS,
    BG_DISABLED,
    create_dark_scrollbar,
)

logger = logging.getLogger("AutoExport")

PROJECT_MEDIA_STATUS_LABELS = {
    "Missing",
    "Missing Media",
    "Linked",
    "Portable",
}
PROJECT_MEDIA_STATUS_EXPLANATIONS = {
    "Missing": (
        "The saved scan found samples that are unresolved or missing."
    ),
    "Missing Media": (
        "The saved scan found samples that are unresolved or missing."
    ),
    "Linked": (
        "The saved scan found sample references outside the project, "
        "so the project may depend on external files."
    ),
    "Portable": (
        "The saved scan found no external or unresolved sample references."
    ),
    "—": "No usable media status is available in the saved scan.",
}
def _project_tree_column_width(column, _font=None):
    """Use the configured width; let the project-name column take remaining space."""
    return PROJECT_TREE_COLUMN_WIDTHS[column]


def _created_timestamp(metadata):
    """Return a file creation timestamp where the platform exposes one."""
    value = getattr(metadata, "st_birthtime", None)
    return value if value is not None else getattr(metadata, "st_ctime")


class ProjectFinder:
    """Find Ableton projects in a directory tree."""

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
        """Recursively find .als files and organize them by containing folder."""
        projects_by_folder = {}

        if ProjectFinder.is_backup_path(root_path):
            return projects_by_folder

        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [
                directory for directory in dirnames
                if directory.casefold() not in ProjectFinder.SKIP_DIR_NAMES
            ]
            als_files = [
                filename for filename in filenames
                if filename.endswith(".als") and not filename.startswith("._")
            ]
            if als_files:
                projects_by_folder[dirpath] = {
                    "path": dirpath,
                    "files": [os.path.join(dirpath, filename) for filename in als_files],
                }

        return projects_by_folder


class ProjectPanelMixin:
    """Project Browser widget construction and interaction handlers."""

    def create_left_panel(self):
        # ===== LEFT SIDE: Tree container + scrollbar =====
        self.tree_frame = tk.Frame(
            self.content_frame,
            bg=BG_PANEL,
            highlightthickness=2,
            highlightbackground=BG_INPUT,
            highlightcolor=ACCENT_CYAN,
            padx=10, pady=10
            
        )
        self.tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.tree_frame.grid_rowconfigure(0, weight=0)
        self.tree_frame.grid_rowconfigure(1, weight=1)
        self.tree_frame.grid_columnconfigure(0, weight=1)

        browser_heading = tk.Frame(self.tree_frame, bg=BG_PANEL)
        browser_heading.grid(
            row=0, column=0, columnspan=2, 
            sticky="ew", padx=(0, 5), pady=(0, 4)
        )
        # Columns: title | search | sort
        browser_heading.grid_columnconfigure(1, weight=1)

        tk.Label(
            browser_heading,
            text="Project Browser",
            bg=BG_PANEL,
            fg=ACCENT_NEUTRAL,
            font=FONT_HEADER,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(5, 12))


        #Search Container
        search_container = tk.Frame(browser_heading, bg=BG_PANEL)
        search_container.grid(row=0, column=1, sticky="ew", padx=(0, 5), pady=(0, 4))
        search_container.grid_columnconfigure(0, weight=1)

        # Search icon / label
        tk.Label(
            search_container,
            text="⌕",
            bg=BG_PANEL,
            fg=FG_SECONDARY,
            font=ctk.CTkFont(family="Segoe UI", size=20),
        ).grid(row=0, column=0, sticky="e", padx=(0, 0), pady=(0, 10))

        #Search Entry
        self.project_search_entry = ctk.CTkEntry(
            search_container,
            placeholder_text="Search...",
            width=160,
            height=28,
            fg_color=BG_TREE,
            border_color=BG_SEPARATOR,
            text_color=FG_PRIMARY,
            placeholder_text_color=FG_MUTED,
            
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.project_search_entry._entry.configure(selectbackground=BG_SEARCH_MATCH,selectforeground=FG_PRIMARY,)
        self.project_search_entry.grid(
            row=0, column=1, sticky="ew", padx=(0, 8)
        )
        self.project_search_entry.bind(
            "<KeyRelease>", self._schedule_project_search_update, add="+"
        )
        self.bind_status_tooltip(
            self.project_search_entry,
            "Filter project / file names",
        )


        # Sort controls with bordered container
        sort_container = tk.Frame(browser_heading, bg=BG_PANEL, highlightthickness=0, highlightbackground=BG_SEPARATOR)
        sort_container.grid(row=0, column=2, columnspan=2, sticky="e", padx=(0, 0))

        tk.Label(
            sort_container,
            text="Sort By  ",
            bg=BG_PANEL,
            fg=FG_SECONDARY,
            font=FONT_UI_BOLD,
        ).grid(row=0, column=0, sticky="e", padx=(0, 0))
        self.sort_menu = ctk.CTkOptionMenu(
            sort_container,
            values=[
                "Alphabetical", "Last Modified", "Date Created",
                "Size - Smallest to Largest", "Size - Largest to Smallest",
            ],
            command=self.set_project_sort,
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
        self._style_option_menu_selection(self.sort_menu)
        # self.bind_status_tooltip(
        #     self.sort_menu, "Change how projects and their folders are sorted."
        # )
        self.sort_menu.set(self.sort_by)
        self.sort_menu.grid(row=0, column=1, sticky="e", padx=(0, 5))

        style = ttk.Style()
        style.theme_use('clam')
        scrollbar = create_dark_scrollbar(
            self.tree_frame, orientation="vertical", width=18
        )
        scrollbar.grid(row=1, column=1, sticky="ns")
        
        # Treeview with scrollbar
        self.tree = ttk.Treeview(
            self.tree_frame,
            yscrollcommand=scrollbar.set,
            height=20,
            style="Dark.Treeview",
            columns=tuple(
                column for column in PROJECT_TREE_COLUMNS if column != "#0"
            ),
            show="tree",
        )
        self.tree.column(
            "#0",
            width=PROJECT_TREE_COLUMN_WIDTHS["#0"],
            anchor="w",
            stretch=True,
        )
        self.tree.column(
            "media status",
            width=_project_tree_column_width("media status", None),
            anchor="w",
            stretch=False,
        )
  
        self.tree.column(
            "export",
            width=_project_tree_column_width("export", None),
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "collect",
            width=_project_tree_column_width("collect", None),
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "returnfx",
            width=_project_tree_column_width("returnfx", None),
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "normalize",
            width=_project_tree_column_width("normalize", None),
            anchor="center",
            stretch=False,
        )
        self.tree.column(
            "wetdry",
            width=_project_tree_column_width("wetdry", None),
            anchor="center",
            stretch=False,
        )
        
        
        scrollbar.configure(command=self.tree.yview)
        self.tree.grid(row=1, column=0, sticky="nsew")
        
        # Configure treeview style
        style.configure(
            "Dark.Treeview",
            background=BG_TREE,
            foreground=FG_PRIMARY,
            fieldbackground=BG_TREE,
            font=FONT_TREE,
            bordercolor=BG_TREE,
            lightcolor=BG_TREE,
            darkcolor=BG_TREE,
            borderwidth=0,
            relief=tk.FLAT,
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=BG_TREE_HEADER,
            foreground=FG_PRIMARY,
            font=FONT_TREE_HEADER,
            bordercolor=BG_TREE_HEADER,
            lightcolor=BG_TREE_HEADER,
            darkcolor=BG_TREE_HEADER,
            borderwidth=0,
            relief=tk.FLAT,
        )
        style.map("Dark.Treeview.Heading", background=[("active", BG_TREE_HEADER)])  # Keep same color on hover
        # Selection is painted entirely with row tags. The browser maintains
        # its own multi-selection, so native ttk selection would override the
        # selected-enabled/selected-disabled tag backgrounds.
        
        # Smaller font for queue tree
        style.configure(
            "Queue.Treeview",
            background=BG_TREE,
            foreground=FG_PRIMARY,
            fieldbackground=BG_TREE,
            font=FONT_TREE,
            bordercolor=BG_TREE,
            lightcolor=BG_TREE,
            darkcolor=BG_TREE,
            borderwidth=0,
            relief=tk.FLAT,
        )
        style.configure(
            "Queue.Treeview.Heading",
            background=BG_TREE_HEADER,
            foreground=FG_PRIMARY,
            font=FONT_TREE_HEADER,
            bordercolor=BG_TREE_HEADER,
            lightcolor=BG_TREE_HEADER,
            darkcolor=BG_TREE_HEADER,
            borderwidth=0,
            relief=tk.FLAT,
        )
        style.map("Queue.Treeview.Heading", background=[("active", BG_TREE_HEADER)])  # Match left panel heading style
        style.map(
            "Queue.Treeview",
            foreground=[("selected", FG_SELECTED), ("disabled", FG_DISABLED)],
            background=[("selected", BG_SELECTED), ("disabled", BG_DISABLED)],
        )
        
        # Configure arrow/expand indicator color
        style.configure("Dark.Treeview", arrowcolor=FG_TREE_ARROW)
        style.map(
            "Dark.Treeview",
            arrowcolor=[
                ("active", FG_TREE_ARROW_ACTIVE),
                ("pressed", FG_TREE_ARROW_PRESSED),
            ],
        )
        
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Button-3>", self.on_project_context_menu)
        self.tree.bind("<Motion>", self._on_project_tree_hover, add="+")
        self.tree.bind("<Leave>", self._clear_status_tooltip, add="+")
        # self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<FocusIn>", lambda _event: self._activate_tree("projects"), add="+")
        self.tree.bind("<Tab>", self._switch_active_tree, add="+")
        self.project_search_entry.bind(
            "<FocusIn>", lambda _event: self._activate_tree("projects"), add="+"
        )
        self.project_search_entry.bind("<Down>", self._focus_first_project_item, add="+")
        self.project_search_entry.bind("<Tab>", self._switch_active_tree, add="+")


        
        # Bind mousewheel events for faster scrolling
        self.tree.bind("<MouseWheel>", self._on_mousewheel)
        self.tree.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        self.tree.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down


    def _focus_first_project_item(self, _event=None):
        self.tree.focus_set()
        self._activate_tree("projects")
        folders = [
            folder for folder in self.tree.get_children("")
            if folder in self.folder_items
        ]
        items = folders or self._visible_file_items()
        if items:
            self._set_project_selection(items[0])
        return "break"


    def toggle_all_project_folders(self):
        folders = self.tree.get_children("")
        if not folders:
            return "break"
        expand = not all(self.tree.item(folder, "open") for folder in folders)
        for folder in folders:
            self.tree.item(folder, open=expand)
        return "break"


    def browse_folders(self):
        """Open folder browser"""
        folder = filedialog.askdirectory(title="Select folder containing Ableton projects")
        if folder:
            self.add_path(folder)


    def _schedule_project_search_update(self, *_args):
        if self._project_search_after_id is not None:
            self.root.after_cancel(self._project_search_after_id)
        self._project_search_after_id = self.root.after(
            180, self._apply_project_search
        )


    def _apply_project_search(self):
        self._project_search_after_id = None
        self.project_search_query = self.project_search_entry.get().strip()
        self.update_tree(persist=False)

    def select_missing_media(self):
        """Select visible project rows reported as missing by the latest scan."""
        self._load_project_media_status()
        visible_files = set(self._visible_file_items())
        self.multi_selection = {
            self.path_to_tree_item[path]
            for path in self.path_to_tree_item
            if self.path_to_tree_item[path] in visible_files
            and self._project_media_status_label(path) == "Missing"
        }
        self.last_selected_item = next(iter(self.multi_selection), None)
        self.project_selection_anchor = self.last_selected_item
        self.refresh_multi_selection_visuals()
        return "break"

    def disable_missing_media(self):
        """Disable all loaded projects reported as missing by the latest scan."""
        self._load_project_media_status()
        paths = [
            path for path in self.selected_projects
            if self._project_media_status_label(path) == "Missing"
        ]
        for path in paths:
            self.selected_projects[path]["enabled"] = False
        self._refresh_project_rows(paths)
        return "break"

    def on_project_context_menu(self, event):
        item = self.tree.identify("item", event.x, event.y)
        project_path = self.tree_item_to_path.get(item)
        if (
            not item
            or project_path is None
            or self.tree.identify_column(event.x) != "#1"
        ):
            return "break"

        if item not in self.multi_selection:
            self._set_project_selection(item)
        visible_items = self._visible_file_items()
        selected_items = self.multi_selection
        project_paths = tuple(
            self.tree_item_to_path[selected]
            for selected in visible_items
            if selected in self.tree_item_to_path and selected in selected_items
        )
        if not project_paths:
            return "break"

        count = len(project_paths)
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
            label=f"Scan & Relink ({count} Items)",
            command=lambda paths=project_paths:
                self.start_selected_media_scan(paths, "relink"),
        )
        menu.add_command(
            label=f"Scan & Collect ({count} Items)",
            command=lambda paths=project_paths:
                self.start_selected_media_scan(paths, "collect"),
        )

        def show_menu_tooltip(event):
            index = menu.index(f"@{event.y}")
            if index is None:
                return
            label = menu.entrycget(index, "label")
            mode = "relink" if "Relink" in label else "collect"
            action = (
                "Updates references to indexed audio without copying files."
                if mode == "relink"
                else "Copies matched audio into each project and relinks it."
            )
            self.show_status_tooltip(f"{label}: {action}")

        menu.bind("<Motion>", show_menu_tooltip, add="+")
        menu.bind("<Leave>", self._clear_status_tooltip, add="+")
        menu.tk_popup(event.x_root, event.y_root)
        return "break"

    def _on_project_tree_hover(self, event):
        item = self.tree.identify("item", event.x, event.y)
        if item not in self.tree_item_to_path:
            self._clear_status_tooltip()
            return

        column = self.tree.identify_column(event.x)
        if column == "#1":
            status = self._project_media_status_label(
                self.tree_item_to_path[item]
            )
            self.show_status_tooltip(
                f"Media status: {status}. Right-click to scan selected "
                "projects with Relink or Collect."
            )
            return

        column_help = {
            "#0": ("Enable/Disable Project", "toggle_enabled_selected"),
            "#2": ("Export Mode: Main or Stems (All Individual Tracks)", "toggle_export_state_selected"),
            "#3": ("Collect All and Save", "toggle_collect_selected"),
            "#4": ("Include Return and Main Effects", "toggle_return_fx_selected"),
            "#5": ("Normalize", "toggle_normalize_selected"),
            "#6": ("Wet + Dry renders two tracks: one wet and one dry, sorted into folders in Stems mode. Dry stems are given a (dry) suffix.", "toggle_wet_dry_selected"),
        }
        help_entry = column_help.get(column)
        if help_entry is None:
            self._clear_status_tooltip()
            return

        label, method = help_entry
        shortcuts = hotkey_texts(PROJECT_HOTKEYS, method)
        shortcut_text = f" ({'/'.join(shortcuts)})" if shortcuts else ""
        self.show_status_tooltip(f"{label}{shortcut_text}")

    def enable_only_smart_select(self):
        """Enable only Smart Select picks."""
        smart_paths = self._smart_select_paths()
        for path, config in self.selected_projects.items():
            config["enabled"] = path in smart_paths
        self._refresh_project_rows(tuple(self.selected_projects))
        return "break"


    def _project_row_display(self, file_path):
        cfg = self.selected_projects.get(
            file_path, self._default_project_config(file_path)
        )
        file_name = os.path.basename(file_path)
        if file_name.endswith(".als"):
            file_name = file_name[:-4]

        export_state = cfg.get("export_state", "stems")
        export_text = (
            "Main" if export_state == "main"
            else "—" if export_state == "off"
            else "Stems"
        )
        values = (
            self._project_media_status_label(file_path),
            export_text,
            "Collect" if cfg["collect_all_and_save"] else "—",
            "RtnFX" if cfg.get(
                "include_return_and_main_effects", True
            ) else "—",
            "Nrmlz" if cfg.get("normalize", False) else "—",
            "WetDry" if cfg.get("wet_dry", False) else "—",
        )
        enabled = cfg.get("enabled", True)
        tags = ["enabled" if enabled else "disabled"]
        query = self.project_search_query.casefold()
        if query and query in file_name.casefold():
            tags.append("search_match")
        return file_name, values, tuple(tags)


    @staticmethod
    def _project_media_status_key(project_path):
        return os.path.normcase(
            os.path.abspath(os.path.normpath(os.fspath(project_path)))
        )


    def _load_project_media_status(self):
        """Load saved media scan entries keyed by normalized project path."""
        status_path = data_file("project_media_status.json")
        try:
            with open(status_path, encoding="utf-8") as status_file:
                snapshot = json.load(status_file)
        except (OSError, json.JSONDecodeError) as error:
            logger.debug(
                "Could not load project media status from %s: %s",
                status_path,
                error,
            )
            self._project_media_status_by_path = {}
            return self._project_media_status_by_path

        entries = snapshot.get("projects", ()) if isinstance(snapshot, dict) else ()
        if not isinstance(entries, (list, tuple)):
            entries = ()
        self._project_media_status_by_path = {
            self._project_media_status_key(entry["path"]): entry
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), (str, os.PathLike))
        }
        return self._project_media_status_by_path


    def refresh_project_media_status(self) -> None:
        """Reread project_media_status.json and update open project rows."""
        self._load_project_media_status()
        self._refresh_project_rows(tuple(getattr(self, "path_to_tree_item", {})))


    def _project_media_status_entry(self, project_path):
        entries = getattr(self, "_project_media_status_by_path", {})
        return entries.get(self._project_media_status_key(project_path))


    def _project_media_status_label(self, project_path):
        entry = self._project_media_status_entry(project_path)
        if entry is None:
            return "—"

        saved_status = entry.get("media_status")
        if saved_status == "Missing Media":
            return "Missing"
        if (
            isinstance(saved_status, str)
            and saved_status in PROJECT_MEDIA_STATUS_LABELS
        ):
            return saved_status

        categories = entry.get("categories", ())
        if not isinstance(categories, (list, tuple, set)):
            categories = ()
        if "unresolved_missing_media" in categories:
            return "Missing"
        if "non_portable" in categories:
            return "Linked"
        if "fully_portable" in categories:
            return "Portable"
        return "—"


    @staticmethod
    def _format_saved_sample_names(names):
        if not isinstance(names, (list, tuple, set)):
            return []
        return [str(name) for name in names if name]


    def _show_project_media_status(self, project_path):
        entry = self._load_project_media_status().get(
            self._project_media_status_key(project_path)
        )
        status = self._project_media_status_label(project_path)
        if entry is None:
            details = (
                "No saved project media status entry was found for this "
                "project."
            )
            external_names = []
            unresolved_names = []
            missing_details = None
        else:
            details = PROJECT_MEDIA_STATUS_EXPLANATIONS[status]
            external_names = self._format_saved_sample_names(
                entry.get("external_sample_names", ())
            )
            unresolved_names = self._format_saved_sample_names(
                entry.get("unresolved_sample_names", ())
            )
            missing_details = entry.get("missing_sample_details")

        def format_name_list(names):
            return "\n".join(f"  • {name}" for name in names) or "  None recorded"

        message = (
            f"Status: {status}\n{details}\n\n"
            f"Known external samples ({len(external_names)}):\n"
            f"{format_name_list(external_names)}\n\n"
            f"Unresolved / missing samples ({len(unresolved_names)}):\n"
            f"{format_name_list(unresolved_names)}"
        )
        if missing_details:
            message += (
                "\n\nMissing sample details:\n"
                + json.dumps(missing_details, indent=2, ensure_ascii=False)
            )

        messagebox.showinfo(
            "Project Media Status",
            message,
            parent=self.root,
        )


    def _refresh_project_rows(self, project_paths):
        """Update changed project rows without rebuilding the entire tree."""
        folders_to_refresh = set()
        for path in project_paths:
            item = self.path_to_tree_item.get(path)
            if item is None or not self.tree.exists(item):
                continue
            text, values, tags = self._project_row_display(path)
            if item in self.multi_selection:
                tags = (
                    "multi"
                    if self.selected_projects.get(
                        path, self._default_project_config(path)
                    ).get("enabled", True)
                    else "multi_disabled",
                ) + tags
            current = self.tree.item(item)
            if (
                current.get("text") != text
                or tuple(current.get("values", ())) != values
                or tuple(current.get("tags", ())) != tags
            ):
                self.tree.item(item, text=text, values=values, tags=tags)
            folders_to_refresh.add(self.tree.parent(item))

        for folder_id in folders_to_refresh:
            folder_path = self.folder_items.get(folder_id)
            project_info = self.projects_by_folder.get(folder_path, {})
            files = project_info.get("files", ())
            enabled_count = sum(
                self.selected_projects.get(
                    path, self._default_project_config(path)
                ).get("enabled", True)
                for path in files
            )
            folder_name = os.path.basename(folder_path or "")
            if folder_name.endswith(" Project"):
                folder_name = folder_name[:-8]
            folder_tags = (
                "folder_enabled" if enabled_count else "folder_disabled",
            )
            if folder_id in self.multi_selection:
                folder_tags = (
                    "multi" if enabled_count else "multi_disabled",
                ) + folder_tags
            self.tree.item(
                folder_id,
                text=f"📁 {folder_name} ({len(files)})",
                tags=folder_tags,
            )
        self._save_project_browser()


    def update_tree(self, persist=True):
        """Update the project tree view."""
        self._load_project_media_status()
        selected_paths = [
            self.tree_item_to_path[item]
            for item in self.multi_selection
            if item in self.tree_item_to_path
        ]
        selected_folder_paths = {
            self.folder_items[item]
            for item in self.multi_selection
            if item in self.folder_items
        }
        anchor_path = None
        if self.last_selected_item in self.tree_item_to_path:
            anchor_path = self.tree_item_to_path[self.last_selected_item]
        anchor_folder_path = self.folder_items.get(self.last_selected_item)
        selection_anchor_path = None
        if self.project_selection_anchor in self.tree_item_to_path:
            selection_anchor_path = self.tree_item_to_path[
                self.project_selection_anchor
            ]
        selection_anchor_folder_path = self.folder_items.get(
            self.project_selection_anchor
        )

        old_files = {
            path: item for item, path in self.tree_item_to_path.items()
        }
        old_folders = {
            path: item
            for item, path in self.folder_items.items()
            if self.tree.exists(item)
        }
        new_files = {}
        new_folders = {}
        new_file_order = []
        self.multi_selection = set()
        self.last_selected_item = None
        self.project_selection_anchor = None
        # # self.update_change_all_label()

        folder_items = list(self.projects_by_folder.items())
        folder_items.sort(key=lambda item: self._project_folder_sort_key(item))

        visible_folder_index = 0
        for folder_path, project_info in folder_items:
            folder_name = os.path.basename(folder_path)
            if folder_name.endswith(" Project"):
                folder_name = folder_name[:-8]
            folder_files = project_info["files"]
            query = self.project_search_query.casefold()
            matching_files = [
                path for path in folder_files
                if not query or query in os.path.basename(path).casefold()
            ]
            folder_matches = bool(
                query and query in os.path.basename(folder_path).casefold()
            )
            folder_visible = not query or folder_matches or bool(matching_files)
            folder_id = old_folders.get(folder_path)
            if folder_id is None or not self.tree.exists(folder_id):
                folder_id = self.tree.insert(
                    "", "end", text="", open=True
                )
            if not folder_visible:
                if folder_id not in self._tree_detached_items:
                    self.tree.detach(folder_id)
                    self._tree_detached_items.add(folder_id)
                new_folders[folder_id] = folder_path
                for file_path in folder_files:
                    iid = old_files.get(file_path)
                    if iid is None or not self.tree.exists(iid):
                        row_text, row_values, row_tags = (
                            self._project_row_display(file_path)
                        )
                        iid = self.tree.insert(
                            folder_id, "end", text=row_text,
                            values=row_values, tags=row_tags,
                        )
                    elif self.tree.parent(iid) != folder_id:
                        self.tree.move(iid, folder_id, "end")
                        self._tree_detached_items.discard(iid)
                    new_files[iid] = file_path
                if folder_path in selected_folder_paths:
                    self.multi_selection.add(folder_id)
                if folder_path == anchor_folder_path:
                    self.last_selected_item = folder_id
                if folder_path == selection_anchor_folder_path:
                    self.project_selection_anchor = folder_id
                continue

            folder_index = visible_folder_index
            enabled_count = sum(
                self.selected_projects.get(
                    path, self._default_project_config(path)
                ).get("enabled", True)
                for path in folder_files
            )
            folder_text = f"📁 {folder_name} ({len(folder_files)})"
            folder_tags = (
                "folder_enabled" if enabled_count else "folder_disabled",
            )
            if folder_matches:
                folder_tags += ("search_match",)
            if folder_id in self._tree_detached_items:
                self.tree.reattach(folder_id, "", folder_index)
                self._tree_detached_items.discard(folder_id)
                self.tree.item(
                    folder_id, text=folder_text, tags=folder_tags
                )
            else:
                if (
                    self.tree.item(folder_id, "text") != folder_text
                    or self.tree.item(folder_id, "tags") != folder_tags
                ):
                    self.tree.item(
                        folder_id, text=folder_text, tags=folder_tags
                    )
                if self.tree.index(folder_id) != folder_index:
                    self.tree.move(folder_id, "", folder_index)
            new_folders[folder_id] = folder_path
            if folder_path in selected_folder_paths:
                self.multi_selection.add(folder_id)
            if folder_path == anchor_folder_path:
                self.last_selected_item = folder_id
            if folder_path == selection_anchor_folder_path:
                self.project_selection_anchor = folder_id
            visible_folder_index += 1

            files_sorted = sorted(folder_files, key=self._project_file_sort_key)
            visible_paths = set(
                files_sorted if folder_matches else matching_files
            )

            visible_file_index = 0
            for file_path in files_sorted:
                is_visible = file_path in visible_paths
                display_index = visible_file_index if is_visible else "end"
                row_text, row_values, row_tags = self._project_row_display(
                    file_path
                )
                iid = old_files.get(file_path)
                if iid is None or not self.tree.exists(iid):
                    iid = self.tree.insert(
                        folder_id, display_index, text=row_text,
                        values=row_values, tags=row_tags,
                    )
                else:
                    current = self.tree.item(iid)
                    if (
                        current.get("text") != row_text
                        or tuple(current.get("values", ())) != row_values
                        or tuple(current.get("tags", ())) != row_tags
                    ):
                        self.tree.item(
                            iid, text=row_text, values=row_values, tags=row_tags
                        )
                    if self.tree.parent(iid) != folder_id:
                        self.tree.move(iid, folder_id, display_index)
                        self._tree_detached_items.discard(iid)
                    elif iid in self._tree_detached_items:
                        self.tree.reattach(iid, folder_id, display_index)
                        self._tree_detached_items.discard(iid)
                    elif (
                        is_visible
                        and self.tree.index(iid) != display_index
                    ):
                        self.tree.move(iid, folder_id, display_index)
                new_files[iid] = file_path
                if is_visible:
                    new_file_order.append(iid)
                    visible_file_index += 1
                elif iid not in self._tree_detached_items:
                    self.tree.detach(iid)
                    self._tree_detached_items.add(iid)
                if file_path in selected_paths:
                    self.multi_selection.add(iid)
                if file_path == anchor_path:
                    self.last_selected_item = iid
                if file_path == selection_anchor_path:
                    self.project_selection_anchor = iid

            folder_tags = (
                "folder_enabled" if enabled_count else "folder_disabled",
            )
            if folder_matches:
                folder_tags += ("search_match",)
            self.tree.item(folder_id, tags=folder_tags)

        retained_files = set(new_files)
        for item in old_files.values():
            if item not in retained_files and self.tree.exists(item):
                self.tree.delete(item)
        retained_folders = set(new_folders)
        for item in old_folders.values():
            if item not in retained_folders and self.tree.exists(item):
                self.tree.delete(item)
        self.tree_item_to_path = new_files
        self.path_to_tree_item = {
            path: item for item, path in new_files.items()
        }
        self.folder_items = new_folders
        self._project_file_order = new_file_order
        self._tree_detached_items.intersection_update(
            retained_files | retained_folders
        )

        self._configure_selection_tags()
        self.tree.tag_configure("enabled", foreground=FG_PRIMARY)
        self.tree.tag_configure("disabled", foreground=FG_DISABLED_BUTTON)
        self.tree.tag_configure("folder_enabled", foreground=FG_ROW_HEADER,
                                 background=BG_ROW_HEADER, font=FONT_TREE_BOLD)
        self.tree.tag_configure("folder_disabled", foreground=FG_DISABLED_BUTTON,
                               background=BG_ROW_HEADER, font=FONT_TREE_BOLD)
        self.tree.tag_configure(
            "search_match", background=BG_SEARCH_MATCH, foreground=FG_PRIMARY
        )

        if not visible_folder_index:
            placeholder_text = (
                "  No matching projects"
                if self.project_search_query else "  Drop folders here"
            )
            placeholders = [
                item for item in self.tree.get_children("")
                if "placeholder" in self.tree.item(item, "tags")
            ]
            if not placeholders:
                self.tree.insert(
                    "", "end", text=placeholder_text,
                    tags=("placeholder",),
                )
            else:
                self.tree.item(placeholders[0], text=placeholder_text)
            self.tree.tag_configure(
                "placeholder",
                foreground=FG_MUTED,
                font=("segoe ui", 10),
            )
        else:
            for item in self.tree.get_children(""):
                if "placeholder" in self.tree.item(item, "tags"):
                    self.tree.delete(item)

        self._visual_project_selection = set()
        self.refresh_multi_selection_visuals()
        if persist:
            self._save_project_browser()


    @staticmethod
    def _project_folder_total_size(folder_path):
        total_size = 0
        def log_walk_error(error):
            logger.debug(
                "Could not scan project folder while sorting: %s",
                error.filename,
                exc_info=(type(error), error, error.__traceback__),
            )

        for current_root, _directories, filenames in os.walk(
            folder_path, onerror=log_walk_error
        ):
            for filename in filenames:
                file_path = os.path.join(current_root, filename)
                try:
                    total_size += os.path.getsize(file_path)
                except OSError:
                    logger.debug(
                        "Could not read project file size while sorting: %s",
                        file_path,
                        exc_info=True,
                    )
        return total_size


    def _project_folder_sort_value(self, folder_path, project_info):
        if self.sort_by == "Alphabetical":
            return os.path.basename(folder_path).casefold()
        if self.sort_by == "Last Modified":
            return self._folder_sort_value(folder_path, "modified", "mtime")
        if self.sort_by == "Date Created":
            return self._folder_sort_value(folder_path, "created", "ctime")
        if self.sort_by.startswith("Size"):
            return self._folder_sort_value(folder_path, "size")
        return os.path.basename(folder_path).casefold()


    def _project_folder_sort_key(self, folder_item):
        folder_path, project_info = folder_item
        value = self._project_folder_sort_value(folder_path, project_info)
        name = os.path.basename(folder_path).casefold()
        if self.sort_by == "Alphabetical":
            return (0, name)
        return (*self._ordered_sort_value(value, self._project_sort_descending()), name)


    def _project_file_sort_key(self, file_path):
        name = os.path.basename(file_path).casefold()
        if self.sort_by == "Alphabetical":
            return (0, name)
        try:
            metadata = os.stat(file_path)
        except OSError:
            logger.debug("Could not read project file sort metadata: %s", file_path)
            return (1, 0, name)

        if self.sort_by == "Last Modified":
            value = metadata.st_mtime
        elif self.sort_by == "Date Created":
            value = getattr(metadata, "st_birthtime", None)
            if value is None:
                value = metadata.st_ctime
        else:
            value = metadata.st_size
        return (*self._ordered_sort_value(
            value, self._project_sort_descending()
        ), name)


    def _project_sort_descending(self):
        return self.sort_by in (
            "Last Modified",
            "Date Created",
            "Size - Largest to Smallest",
            "Size",
        )


    @staticmethod
    def _ordered_sort_value(value, descending):
        if value is None or value == float("-inf"):
            return (1, 0)
        return (0, -value if descending else value)


    def _folder_sort_value(self, folder_path, key, fallback_stat=None):
        record = getattr(self, "folder_sort_cache", {}).get(folder_path)
        if record is None:
            if hasattr(self, "folder_sort_executor"):
                self._request_folder_sort_metadata((folder_path,))
                return None
            if fallback_stat is None:
                return self._project_folder_total_size(folder_path)
            try:
                metadata = os.stat(folder_path)
                if fallback_stat == "mtime":
                    return metadata.st_mtime
                return self._created_timestamp(metadata)
            except OSError:
                return None
        if time.monotonic() - record["checked"] > 60:
            self._request_folder_sort_metadata((folder_path,))
        return record.get(key)


    @staticmethod
    def _measure_folder_sort_metadata(folder_path):
        total_size = 0

        def log_walk_error(error):
            logger.debug(
                "Could not scan project folder for sort metadata: %s",
                error.filename,
                exc_info=(type(error), error, error.__traceback__),
            )

        for current_root, _directories, filenames in os.walk(
            folder_path, onerror=log_walk_error
        ):
            for filename in filenames:
                path = os.path.join(current_root, filename)
                try:
                    total_size += os.path.getsize(path)
                except OSError:
                    logger.debug(
                        "Could not read project file size for sorting: %s",
                        path,
                        exc_info=True,
                    )
        folder_metadata = os.stat(folder_path)
        return {
            "size": total_size,
            "modified": folder_metadata.st_mtime,
            "created": _created_timestamp(folder_metadata),
            "checked": time.monotonic(),
        }


    def _request_folder_sort_metadata(self, folder_paths):
        for folder_path in folder_paths:
            if (
                folder_path in self.folder_sort_pending
                or (
                    folder_path in self.folder_sort_cache
                    and time.monotonic()
                    - self.folder_sort_cache[folder_path]["checked"] <= 60
                )
            ):
                continue
            self.folder_sort_pending.add(folder_path)
            future = self.folder_sort_executor.submit(
                self._measure_folder_sort_metadata, folder_path
            )
            future.add_done_callback(
                lambda completed, path=folder_path: self.folder_sort_results.put(
                    (
                        path,
                        self._future_result(
                            completed,
                            "project-folder sort metadata",
                            return_error=True,
                        ),
                    )
                )
            )


    def _schedule_folder_sort_refresh(self):
        if self._folder_sort_refresh_after_id is not None:
            self.root.after_cancel(self._folder_sort_refresh_after_id)
        self._folder_sort_refresh_after_id = self.root.after(
            250, self._refresh_folder_sort_order
        )


    def _refresh_folder_sort_order(self):
        self._folder_sort_refresh_after_id = None
        if not self._closing and self.sort_by != "Alphabetical":
            self.update_tree(persist=False)


    def set_project_sort(self, sort_by):
        if sort_by == "Size":
            sort_by = "Size - Largest to Smallest"
        if sort_by not in (
            "Alphabetical", "Last Modified", "Date Created",
            "Size - Smallest to Largest", "Size - Largest to Smallest",
        ):
            logger.warning("Ignoring - project sort mode %r", sort_by)
            return
        self.sort_by = sort_by
        self.update_tree()


    def on_tree_click(self, event):
        """Handle tree row selection and inline setting toggles."""
        self._activate_tree("projects")
        self.tree.focus_set()
        item = self.tree.identify("item", event.x, event.y)
        col = self.tree.identify_column(event.x)
        if not item:
            return

        if item not in self.tree_item_to_path and item not in self.folder_items:
            if not self.tree.parent(item):
                element = self.tree.identify("element", event.x, event.y)
                if element.endswith("indicator"):
                    return
            return "break"

        if item in self.folder_items:
            element = self.tree.identify("element", event.x, event.y)
            if element.endswith("indicator"):
                return

        if self._handle_row_selection_click(item, event):
            return "break"

        if col == "#0":
            self.multi_selection = {item}
            self.last_selected_item = item
            self.project_selection_anchor = item
            self.refresh_multi_selection_visuals()
            # # self.update_change_all_label()
            return "break"

        if item not in self.multi_selection:
            self.multi_selection = {item}
            self.last_selected_item = item
            self.project_selection_anchor = item

        self.refresh_multi_selection_visuals()
        self._apply_column_action_to_selection(col)
        return "break"


    def _toggle_project_item_enabled(self, item):
        if item in self.folder_items:
            paths = self.projects_by_folder.get(
                self.folder_items[item], {}
            ).get("files", ())
        else:
            path = self.tree_item_to_path.get(item)
            paths = (path,) if path is not None else ()
        if not paths:
            return
        enabled = not all(
            self.selected_projects.get(
                path, self._default_project_config(path)
            ).get("enabled", True)
            for path in paths
        )
        for path in paths:
            config = self.selected_projects.setdefault(
                path, self._default_project_config(path)
            )
            config["enabled"] = enabled
        self._refresh_project_rows(paths)


    def on_tree_double_click(self, event):
        item = self.tree.identify("item", event.x, event.y)
        if not item:
            return
        if (
            item in self.tree_item_to_path
            and self.tree.identify_column(event.x) == "#1"
        ):
            project_path = self.tree_item_to_path[item]
            if self._project_media_status_label(project_path) == "Missing":
                self._show_project_media_status(project_path)
            return "break"
        if (
            not self.tree.parent(item)
            and self.tree.identify("element", event.x, event.y).endswith(
                "indicator"
            )
        ):
            return
        self._toggle_project_item_enabled(item)
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
        paths = []
        for item in self.multi_selection:
            folder_path = self.folder_items.get(item)
            if folder_path is not None:
                paths.extend(
                    self.projects_by_folder.get(folder_path, {}).get("files", ())
                )
            else:
                path = self.tree_item_to_path.get(item)
                if path is not None:
                    paths.append(path)
        return list(dict.fromkeys(paths))


    def _add_project_file_to_browser(self, project_path, task=None):
        project_path = os.path.normpath(project_path)
        if (
            not os.path.isfile(project_path)
            or not project_path.casefold().endswith(".als")
            or ProjectFinder.is_backup_path(project_path)
        ):
            return False
        if project_path not in self.selected_projects:
            config = self._default_project_config(project_path)
            if task is not None:
                config.update({
                    "enabled": bool(
                        task.get("export_stems") or task.get("export_main")
                    ),
                    "collect_all_and_save": bool(
                        task.get("collect_all_and_save", False)
                    ),
                    "export_state": (
                        "stems" if task.get("export_stems")
                        else "main" if task.get("export_main")
                        else "off"
                    ),
                    "include_return_and_main_effects": bool(
                        task.get("include_return_and_main_effects", True)
                    ),
                    "normalize": bool(task.get("normalize", False)),
                    "wet_dry": bool(task.get("wet_dry", False)),
                })
            self.selected_projects[project_path] = config
        folder = os.path.dirname(project_path)
        info = self.projects_by_folder.setdefault(
            folder, {"path": folder, "files": []}
        )
        if project_path not in info["files"]:
            info["files"].append(project_path)
        return True


    def _handle_row_selection_click(self, item, event):
        if event.state & 0x1:
            self._select_range_to_item(item)
            return True

        if event.state & 0x4:
            self.project_selection_anchor = item
            self.last_selected_item = item
            if item in self.multi_selection:
                self.multi_selection.remove(item)
                if not self.multi_selection:
                    self.last_selected_item = None
            else:
                self.multi_selection.add(item)
            self.refresh_multi_selection_visuals()
            # # self.update_change_all_label()
            return True

        return False


    def _select_range_to_item(self, item):
        items = self._project_siblings(item)
        if item not in items:
            return
        if self.project_selection_anchor not in items:
            self.project_selection_anchor = (
                self.last_selected_item
                if self.last_selected_item in items
                else item
            )
        start_idx = items.index(self.project_selection_anchor)
        end_idx = items.index(item)
        lo = min(start_idx, end_idx)
        hi = max(start_idx, end_idx)
        self.multi_selection = set(items[lo:hi + 1])
        self.last_selected_item = item
        self.refresh_multi_selection_visuals()


    def _apply_column_action_to_selection(self, col):
        targets = self._target_projects()
        if not targets:
            return

        if col == "#2":  # Export
            selected_paths = self._selected_project_paths()
            states = [self.selected_projects[path].get("export_state", "stems")
                      for path in selected_paths if path in self.selected_projects]
            current = (
                states[0]
                if states and all(state == states[0] for state in states)
                else self.selected_projects.get(
                    self.tree_item_to_path.get(self.last_selected_item), {}
                ).get("export_state", "stems")
            )
            new_state = self._next_export_state(current)
            for cfg in targets:
                cfg["export_state"] = new_state
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

        self._after_bulk_change()


    def _visible_project_items(self):
        """Return attached folder and file rows in tree display order."""
        folder_items = getattr(self, "folder_items", None)
        if folder_items is None:
            return self._visible_file_items()
        items = []
        for folder in self.tree.get_children(""):
            if folder not in folder_items:
                continue
            items.append(folder)
            items.extend(
                child for child in self.tree.get_children(folder)
                if child in getattr(self, "tree_item_to_path", {})
            )
        return items


    def _project_siblings(self, item):
        folder_items = getattr(self, "folder_items", None)
        if folder_items is None:
            return self._visible_file_items()
        if item in folder_items:
            return [
                folder for folder in self.tree.get_children("")
                if folder in folder_items
            ]
        if item in self.tree_item_to_path:
            return self._visible_file_items()
        return []


    def select_all_rows(self, event=None):
        items = self._visible_project_items()
        self.multi_selection = set(items)
        self.last_selected_item = items[0] if items else None
        self.project_selection_anchor = self.last_selected_item
        self.refresh_multi_selection_visuals()
       # # self.update_change_all_label()
        return "break"


    def invert_selection(self, event=None):
        visible_items = set(self._visible_project_items())
        self.multi_selection = visible_items - self.multi_selection
        self.last_selected_item = next(iter(self.multi_selection), None)
        self.project_selection_anchor = self.last_selected_item
        self.refresh_multi_selection_visuals()
       # # self.update_change_all_label()
        return "break"


    def clear_multi_selection(self, event=None):
        self.multi_selection.clear()
        self.last_selected_item = None
        self.project_selection_anchor = None
        self.refresh_multi_selection_visuals()
        # self.update_change_all_label()
        return "break"


    def extend_selection(self, direction):
        items = (
            self._project_siblings(self.last_selected_item)
            if self.last_selected_item is not None
            else [
                folder for folder in self.tree.get_children("")
                if folder in self.folder_items
            ]
        )
        if not items:
            return "break"

        current = self.last_selected_item
        if current not in items:
            self._set_project_selection(items[0])
            return "break"
        if self.project_selection_anchor not in items:
            self.project_selection_anchor = current
        try:
            idx = items.index(current)
        except ValueError:
            idx = items.index(self.project_selection_anchor)
        new_idx = max(0, min(len(items) - 1, idx + direction))
        self.last_selected_item = items[new_idx]
        anchor_idx = items.index(self.project_selection_anchor)
        self.multi_selection = set(
            items[min(anchor_idx, new_idx):max(anchor_idx, new_idx) + 1]
        )

        self.refresh_multi_selection_visuals()
        # self.update_change_all_label()
        return "break"


    def navigate_project_selection(self, direction):
        """Move across folders or across all projects at the active level."""
        current = self.last_selected_item
        items = self._project_siblings(current) if current else [
            folder for folder in self.tree.get_children("")
            if folder in self.folder_items
        ]
        if not items:
            return "break"
        if current not in items:
            next_item = items[0]
        else:
            index = items.index(current)
            next_item = items[max(0, min(len(items) - 1, index + direction))]
        self._set_project_selection(next_item)
        return "break"


    def navigate_project_level(self, direction):
        """Enter a folder's files with Right or return to its folder with Left."""
        current = self.last_selected_item
        if current is None or current not in self._visible_project_items():
            folders = [
                folder for folder in self.tree.get_children("")
                if folder in self.folder_items
            ]
            if folders:
                self._set_project_selection(folders[0])
            return "break"

        if direction > 0 and current in self.folder_items:
            if not self.tree.item(current, "open"):
                self.tree.item(current, open=True)
            children = [
                child for child in self.tree.get_children(current)
                if child in self.tree_item_to_path
            ]
            if children:
                self._set_project_selection(children[0])
        elif direction < 0 and current in self.tree_item_to_path:
            parent = self.tree.parent(current)
            if parent in self.folder_items:
                self._set_project_selection(parent)
        return "break"


    def _set_project_selection(self, item):
        if item in self.tree_item_to_path:
            parent = self.tree.parent(item)
            if parent in self.folder_items and not self.tree.item(parent, "open"):
                self.tree.item(parent, open=True)
        self.project_selection_anchor = item
        self.last_selected_item = item
        self.multi_selection = {item}
        self.refresh_multi_selection_visuals()
        self.tree.focus(item)
        self.tree.see(item)


    def refresh_multi_selection_visuals(self):
        """Apply 'multi' tag to selected rows, remove it from others."""
        visible_items = self._visible_project_items()
        visible_set = set(visible_items)
        selected = self.multi_selection & visible_set
        previous_selection = getattr(
            self, "_visual_project_selection", set()
        )
        changed = (previous_selection ^ selected) | selected
        exists = getattr(self.tree, "exists", lambda _item: True)
        for iid in changed:
            if not exists(iid):
                continue
            tags = [
                t for t in self.tree.item(iid, "tags")
                if t not in ("multi", "multi_disabled")
            ]
            if iid in selected:
                # Treeview gives earlier tags higher priority. Put the
                # selection tag before the enabled/disabled base tag so its
                # foreground and background remain visible.
                tags.insert(
                    0,
                    "multi_disabled"
                    if (
                        "disabled" in self.tree.item(iid, "tags")
                        or "folder_disabled" in self.tree.item(iid, "tags")
                    )
                    else "multi"
                )
            self.tree.item(iid, tags=tags)
        self._configure_selection_tags()
        current_native_selection = tuple(
            getattr(self.tree, "selection", lambda: ())()
        )
        if current_native_selection:
            self.tree.selection_remove(*current_native_selection)
        self.multi_selection = selected
        self._visual_project_selection = set(selected)


    def _configure_selection_tags(self):
        # Tk gives the first-configured tag priority, so these must be
        # configured before the base row tags.
        self.tree.tag_configure(
            "multi", background=BG_SELECTION, foreground=FG_SELECTED
        )
        self.tree.tag_configure(
            "multi_disabled",
            background=ACCENT_NEUTRAL,
            foreground=FG_SELECTED_DISABLED,
        )

    def _activate_tree(self, active_tree):
        """Make the focused tree the sole owner of selection and hotkeys."""
        changed = active_tree != self._active_tree
        self._active_tree = active_tree
        if changed and active_tree == "projects":
            self.queue_tree.selection_remove(self.queue_tree.selection())
            self.queue_selection_anchor = None
        elif changed:
            self.multi_selection.clear()
            self.last_selected_item = None
            self.project_selection_anchor = None
            self.refresh_multi_selection_visuals()
        self.tree_frame.configure(
            highlightbackground=ACCENT_NEUTRAL
            if active_tree == "projects" else BG_WINDOW
        )
        self.queue_panel.configure(
            highlightbackground=ACCENT_NEUTRAL
            if active_tree == "queue" else BG_WINDOW
        )


    def _target_projects(self):
        """Return the cfg dicts of multi-selected rows.

        If nothing is selected, returns an empty list — the panel buttons
        silently do nothing so they can't accidentally affect every row.
        """
        if not self.multi_selection:
            return []
        paths = self._selected_project_paths()
        return [self.selected_projects[p] for p in paths if p in self.selected_projects]


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
