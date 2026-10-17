"""Bulk and per-selection export-option actions for the project browser."""

import os
from tkinter import messagebox
from gui_theme import BTN_COLOR_GO, BTN_COLOR_NEUTRAL
from smart_select import smart_picks_by_folder

import logging

logger = logging.getLogger("AutoExport")


class BulkActionsMixin:

    def _next_export_state(self, current):
        order = ("stems", "main", "off")
        try:
            return order[(order.index(current) + 1) % len(order)]
        except ValueError:
            return "stems"

    def _visible_file_items(self):
        """Return file-row item IDs in display order, skipping folder rows."""
        if hasattr(self, "_project_file_order"):
            return list(self._project_file_order)
        return [
            child
            for folder_iid in self.tree.get_children("")
            for child in self.tree.get_children(folder_iid)
        ]

    def select_latest_modified(self, event=None):
        latest_paths = set(self._latest_project_paths_by_folder())
        self.multi_selection = {
            iid
            for iid in self._visible_file_items()
            if self.tree_item_to_path.get(iid) in latest_paths
        }
        self.last_selected_item = next(iter(self.multi_selection), None)
        self.project_selection_anchor = self.last_selected_item
        self.refresh_multi_selection_visuals()
        # self.update_change_all_label()
        return "break"

    def _smart_select_paths(self):
        """Return the Smart Select pick paths across all loaded folders."""
        try:
            picks_by_folder = smart_picks_by_folder(self.projects_by_folder)
        except Exception as error:
            logger.exception("Could not calculate Smart Select projects")
            messagebox.showerror(
                "Smart Select",
                f"Could not calculate Smart Select projects:\n\n{error}",
                parent=self.root,
            )
            return set()
        return {p for picks in picks_by_folder.values() for p in picks}

    def smart_select(self, event=None):
        """Select the Smart Select pick in each song/stage cluster."""
        smart_paths = self._smart_select_paths()
        self.multi_selection = {
            iid
            for iid in self._visible_file_items()
            if self.tree_item_to_path.get(iid) in smart_paths
        }
        self.last_selected_item = next(iter(self.multi_selection), None)
        self.project_selection_anchor = self.last_selected_item
        self.refresh_multi_selection_visuals()
        return "break"

    def _after_bulk_change(self):
        """Refresh changed project rows and persist their updated options."""
        self._refresh_project_rows(self._selected_project_paths())

    def toggle_export_state_selected(self):
        targets = self._target_projects()
        if not targets:
            return
        path = self.tree_item_to_path.get(self.last_selected_item)
        current = self.selected_projects.get(path, {}).get(
            "export_state", "stems"
        )
        next_state = self._next_export_state(current)
        for cfg in targets:
            cfg["export_state"] = next_state
            if next_state != "off":
                cfg["enabled"] = True
        self._after_bulk_change()

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
        self.project_selection_anchor = None
        self.update_tree()
        return "break"

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

    def set_all_to_off(self):
        """Set all projects to Off (export_state=off, keep collect_all_and_save unchanged)"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "off"
        self.update_tree()

    def apply_collect_all(self):
        """Toggle Collect between all On and all Off (deprecated - now use set_all_to_collect)"""
        self.set_all_to_collect()

    def apply_export_main_all(self):
        """Set Export=Main for all entries"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "main"
        self.update_tree()

    def apply_export_stems_all(self):
        """Set Export=Stems for all entries"""
        for cfg in self.selected_projects.values():
            cfg["export_state"] = "stems"
        self.update_tree()

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

    def enable_only_smart_select(self):
        """Enable only the Smart Select pick in each song/stage cluster."""
        smart_paths = self._smart_select_paths()
        for project_info in self.projects_by_folder.values():
            for f in project_info.get("files", []):
                cfg = self.selected_projects.get(f) or self._default_project_config(f)
                cfg["enabled"] = f in smart_paths
                self.selected_projects[f] = cfg
        self.update_tree()

    def select_latest_per_folder(self):
        """Backward-compatible alias for the old menu action."""
        return self.enable_only_latest_modified()
