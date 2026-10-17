"""File, Edit, and Tools menu construction for the launcher GUI."""

import os
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox

from CTkMenuBar import CTkMenuBar, CustomDropdownMenu
import ableton_build

from export_path_config import get_export_path, set_export_path
from gui_keybindings import menu_label 
from gui_theme import (
    ACCENT_ORANGE, BG_INPUT, BG_SEPARATOR, BG_TOOLBAR, FG_PRIMARY, FG_SELECTED,
)


class FileMenuMixin:
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
            set_export_path(selected_folder)

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
    def create_menu(self):
        """Create the themed File, Edit, and Tools menus."""
        self.menu_bar = CTkMenuBar(
            self.menu_container,
            bg_color=BG_TOOLBAR,
            height=30,
            width=50,
            padx=2,
            pady=2,
        )
        self.menu_bar.pack(fill=tk.X, expand=False)
        menus = []

        def add_menu(label, entries):
            button = self.menu_bar.add_cascade(
                label,
                fg_color="transparent",
                hover_color=ACCENT_ORANGE,
                text_color=FG_PRIMARY,
                font=("Segoe UI", 12, "bold"),
                corner_radius=0,
                cursor="arrow",
            )
            self.bind_status_tooltip(button, f"Open the {label} menu.")
            menu = CustomDropdownMenu(
                widget=button,
                master=self.root,
                bg_color=BG_INPUT,
                fg_color=BG_INPUT,
                border_width=1,
                border_color=BG_SEPARATOR,
                corner_radius=0,
                width=126,
                height=26,
                separator_color=BG_SEPARATOR,
                text_color=ACCENT_ORANGE,
                hover_color=ACCENT_ORANGE,
                font=("Segoe UI", 12, "bold"),
                padx=2,
                pady=1,
                cursor="arrow",
            )

            def show_menu_over_window(menu=menu, cascade=button):
                scale = menu._get_widget_scaling()
                x = (
                    cascade.winfo_rootx() - self.root.winfo_rootx()
                ) / scale
                y = (
                    self.menu_container.winfo_rooty()
                    + self.menu_container.winfo_height()
                    - self.root.winfo_rooty()
                ) / scale
                menu.place(x=x + menu.padx, y=y)
                menu.lift()
                menu.focus()

            menu._show = show_menu_over_window

            def close_menu_when_pointer_leaves(_event):
                def close_if_outside():
                    pointer = self.root.winfo_pointerxy()
                    current = self.root.winfo_containing(*pointer)
                    while current is not None:
                        if current in (menu, button):
                            return
                        current = getattr(current, "master", None)
                    if menu.winfo_viewable():
                        menu._hide()
                    self._restore_menu_cascade(button, menu)

                self.root.after_idle(close_if_outside)

            menu.bind("<Leave>", close_menu_when_pointer_leaves, add="+")

            def switch_menu_on_hover(_event):
                if not any(other.winfo_viewable() for other in menus if other is not menu):
                    return
                for other in menus:
                    if other is not menu:
                        other._hide()
                menu._show()

            def lift_open_menu():
                if menu.winfo_viewable():
                    menu.lift()

            for target in (button, button._canvas, button._text_label):
                target.configure(cursor="arrow")
                target.bind("<Enter>", switch_menu_on_hover, add="+")
                target.bind(
                    "<Button-1>",
                    lambda _event: self.root.after_idle(lift_open_menu),
                    add="+",
                )
            menus.append(menu)

            for target in (button, button._canvas, button._text_label):
                target.bind(
                    "<Enter>",
                    lambda _event, cascade=button: cascade.configure(
                        fg_color=ACCENT_ORANGE, text_color=FG_SELECTED
                    ),
                    add="+",
                )
                target.bind(
                    "<Leave>",
                    lambda _event, cascade=button, current_menu=menu:
                    self.root.after_idle(
                        lambda: self._restore_menu_cascade(
                            cascade, current_menu
                        )
                    ),
                    add="+",
                )

            def bind_selected_text(option):
                def on_enter(_event):
                    option.configure(
                        fg_color=ACCENT_ORANGE, text_color=FG_SELECTED
                    )

                def on_leave(_event):
                    def restore_if_outside():
                        pointer = self.root.winfo_pointerxy()
                        current = self.root.winfo_containing(*pointer)
                        while current is not None:
                            if current in (option, option._canvas, option._text_label):
                                return
                            current = getattr(current, "master", None)
                        option.configure(
                            fg_color="transparent", text_color=ACCENT_ORANGE
                        )

                    self.root.after_idle(restore_if_outside)

                for target in (option, option._canvas, option._text_label):
                    target.bind("<Enter>", on_enter, add="+")
                    target.bind("<Leave>", on_leave, add="+")

            for entry in entries:
                if entry is None:
                    menu.add_separator()
                    
                else:
                    text, command, tip = entry
                    option = menu.add_option(
                        text, command=command, corner_radius=0
                    )
                    self.bind_status_tooltip(option, tip)
                    bind_selected_text(option)
                    for target in (option, option._canvas, option._text_label):
                        target.configure(cursor="arrow")

        add_menu("File", [
            ("Add Folder...", self.browse_folders,
             "Choose a folder to scan for Ableton projects and add them to the Project Browser."),
            None,
            ("Set Ableton Live Exe...",
             lambda: self._prompt_for_ableton_exe(ableton_build.RELEASE),
             "Point AutoExport at the Ableton Live executable."),
            None,
            (menu_label("Set Export Path...", "set_export_path"), self.set_export_path,
             "Choose the folder exported audio is written to."),
            ("Clear All", self.clear_all,
             "Remove every project from the Project Browser."),
            None,
            ("Exit", self.root.quit, "Close AutoExport."),
        ])
        add_menu("Edit", [
            (menu_label("Select All", "select_all_rows"), self.select_all_rows,
             "Select every visible project."),
            (menu_label("Deselect All", "clear_multi_selection"), self.clear_multi_selection,
             "Clear the current project selection."),
            (menu_label("Invert Selection", "invert_selection"), self.invert_selection,
             "Select the projects that are not selected, and deselect the rest."),
            None,
            ("Smart Select", self.smart_select,
             "Select the best pick (Smart Select) in each song/stage group of versions."),
            (menu_label("Enable Only Smart Select", "enable_only_smart_select"),
             self.enable_only_smart_select,
             "Enable only the Smart Select picks and disable every other project."),
            None,
            (menu_label("Expand/Collapse Project Folders", "toggle_all_project_folders"),
             self.toggle_all_project_folders,
             "Expand every project folder, or collapse them all if already expanded."),
            (menu_label("Select Latest Modified", "select_latest_modified"),
             self.select_latest_modified,
             "Select the most recently modified project in each folder."),
            ("Enable Only Latest Modified", self.enable_only_latest_modified,
             "Enable the most recently modified project in each folder and disable the rest."),
            None,
            ("Select Missing", self.select_missing_media,
             "Select every project whose last media scan found missing media."),
            (menu_label("Disable Missing", "disable_missing_media"), self.disable_missing_media,
             "Disable every project whose last media scan found missing media."),
            None,
            (menu_label("Remove Selected", "on_project_delete_key"), self.clear_selected,
             "Remove the selected projects from the Project Browser."),
            None,
            ("Enable Selected", self.enable_selected,
             "Enable the selected projects for export."),
            (menu_label("Disable Selected", "disable_selected"), self.disable_selected,
             "Disable the selected projects so they are skipped."),
            (menu_label("Toggle Enabled for Selection", "toggle_enabled_selected"),
             self.toggle_enabled_selected,
             "Enable the selection, or disable it if every selected project is already enabled."),
        ])
        add_menu("Tools", [
            ("Missing Media", self.open_sample_index_window,
             "Index your samples and bulk fix missing media in Ableton projects."),
            ("Naming Settings", self.open_naming_settings_window,
             "Configure how exported files and folders are named."),
        ])
    def _restore_menu_cascade(self, cascade, menu):
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        left = cascade.winfo_rootx()
        top = cascade.winfo_rooty()
        if (
            left <= pointer_x < left + cascade.winfo_width()
            and top <= pointer_y < top + cascade.winfo_height()
        ):
            return
        cascade.configure(fg_color="transparent", text_color=FG_PRIMARY)


