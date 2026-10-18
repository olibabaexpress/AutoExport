import json
import os
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import GUI as GUI
import gui_session as gui_session
from gui_project_panel import _project_tree_column_width
from gui_theme import (
    ACCENT_NEUTRAL,
    BG_SELECTION,
    FG_SELECTED,
    FG_SELECTED_DISABLED,
    PROJECT_TREE_COLUMN_WIDTHS,
)


class TreeStub:
    def __init__(self):
        self.children = {"": ["folder"], "folder": ["a", "b", "c"]}
        self.tags = {item: () for item in ("a", "b", "c")}
        self.selected = ()
        self.tag_options = {}

    def get_children(self, item=""):
        return self.children[item]

    def item(self, item, option=None, **kwargs):
        if "tags" in kwargs:
            self.tags[item] = kwargs["tags"]
        if option == "tags":
            return self.tags[item]
        return {}

    def tag_configure(self, *_args, **_kwargs):
        self.tag_options[_args[0]] = _kwargs

    def selection_set(self, items):
        self.selected = tuple(items)

    def selection(self):
        return self.selected

    def selection_remove(self, *items):
        self.selected = tuple(item for item in self.selected if item not in items)


class MutableTreeStub:
    def __init__(self):
        self.items = {}
        self.children = {"": []}
        self.selected = ()
        self.insert_count = 0
        self.tag_options = {}
        self.widget_focused = False

    def get_children(self, item=""):
        return tuple(self.children.get(item, ()))

    def exists(self, item):
        return item in self.items

    def insert(self, parent, index, iid=None, **kwargs):
        self.insert_count += 1
        iid = iid or f"row-{self.insert_count}"
        self.items[iid] = {
            "parent": parent,
            "text": kwargs.get("text", ""),
            "values": kwargs.get("values", ()),
            "tags": kwargs.get("tags", ()),
            "open": kwargs.get("open", False),
        }
        self.children.setdefault(iid, [])
        self.children.setdefault(parent, [])
        if index == "end":
            self.children[parent].append(iid)
        else:
            self.children[parent].insert(index, iid)
        return iid

    def item(self, item, option=None, **kwargs):
        row = self.items[item]
        row.update(kwargs)
        return row.get(option) if option else dict(row)

    def parent(self, item):
        return self.items[item]["parent"]

    def index(self, item):
        return self.children[self.parent(item)].index(item)

    def move(self, item, parent, index):
        current_parent = self.parent(item)
        if item in self.children.get(current_parent, ()):
            self.children[current_parent].remove(item)
        self.items[item]["parent"] = parent
        self.children.setdefault(parent, [])
        if index == "end":
            self.children[parent].append(item)
        else:
            self.children[parent].insert(index, item)

    def detach(self, item):
        parent = self.parent(item)
        if item in self.children.get(parent, ()):
            self.children[parent].remove(item)

    def reattach(self, item, parent, index):
        self.move(item, parent, index)

    def delete(self, item):
        for child in tuple(self.children.get(item, ())):
            self.delete(child)
        parent = self.parent(item)
        if item in self.children.get(parent, ()):
            self.children[parent].remove(item)
        self.children.pop(item, None)
        self.items.pop(item, None)

    def tag_configure(self, tag, **kwargs):
        self.tag_options[tag] = kwargs

    def selection(self):
        return self.selected

    def selection_set(self, items):
        self.selected = tuple(items)

    def see(self, _item):
        pass

    def focus(self, item=None):
        return item

    def focus_set(self):
        self.widget_focused = True


class OptionMenuStub:
    def __init__(self):
        self._dropdown_menu = DropdownMenuStub()


class DropdownMenuStub:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)


class ProjectBrowserStateTest(unittest.TestCase):
    def test_project_tree_columns_use_configured_widths(self):
        class FontStub:
            @staticmethod
            def measure(_value):
                return 1000

        for column in PROJECT_TREE_COLUMN_WIDTHS:
            with self.subTest(column=column):
                self.assertEqual(
                    _project_tree_column_width(column, FontStub()),
                    PROJECT_TREE_COLUMN_WIDTHS[column],
                )

    def test_project_rows_use_saved_media_status_and_category_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "project_media_status.json"
            paths = {
                label: str(Path(directory) / f"{label}.als")
                for label in ("preferred", "missing", "linked", "portable")
            }
            status_path.write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "path": paths["preferred"],
                                "media_status": "Portable",
                                "categories": ["non_portable"],
                            },
                            {
                                "path": paths["missing"],
                                "categories": [
                                    "non_portable",
                                    "unresolved_missing_media",
                                ],
                            },
                            {
                                "path": paths["linked"],
                                "categories": ["non_portable"],
                            },
                            {
                                "path": paths["portable"],
                                "categories": ["fully_portable"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.selected_projects = {}
            gui.project_search_query = ""

            with patch(
                "gui_project_panel.data_file", return_value=str(status_path)
            ):
                gui._load_project_media_status()

            for label, expected in (
                ("preferred", "Portable"),
                ("missing", "Missing"),
                ("linked", "Linked"),
                ("portable", "Portable"),
            ):
                _text, values, _tags = gui._project_row_display(paths[label])
                self.assertEqual(values[0], expected)

            _text, values, _tags = gui._project_row_display(
                str(Path(directory) / "unrecorded.als")
            )
            self.assertEqual(values[0], "—")

    def test_media_status_popup_lists_saved_sample_names_and_details(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "project_media_status.json"
            project_path = str(Path(directory) / "Linked.als")
            status_path.write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "path": project_path,
                                "media_status": "Linked",
                                "external_sample_names": ["external.wav"],
                                "unresolved_sample_names": ["missing.wav"],
                                "missing_sample_details": [
                                    {"name": "missing.wav",
                                     "stored_path": "Samples/missing.wav"}
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.root = Mock()
            gui._open_copyable_text_window = Mock()

            with patch(
                "gui_project_panel.data_file",
                return_value=str(status_path),
            ):
                gui._show_project_media_status(project_path)

            message = gui._open_copyable_text_window.call_args.args[1]
            self.assertIn("Status: Linked", message)
            self.assertIn("external.wav", message)
            self.assertIn("missing.wav", message)
            self.assertIn("Samples/missing.wav", message)
            gui._open_copyable_text_window.assert_called_once()

    def test_double_clicking_media_status_opens_popup_not_enabled_toggle(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.tree = Mock()
        gui.tree.identify.return_value = "project-row"
        gui.tree.identify_column.return_value = "#1"
        gui.tree.get_children.side_effect = lambda item="": (
            ("folder",) if item == "" else ("project-row",)
        )
        row_tags = {"project-row": ("enabled",)}

        def tree_item(item, option=None, **kwargs):
            row_tags[item] = kwargs.get("tags", row_tags.get(item, ()))
            return row_tags[item] if option == "tags" else {}

        gui.tree.item.side_effect = tree_item
        gui.tree.selection.return_value = ()
        gui.tree_item_to_path = {"project-row": r"C:\Projects\Set.als"}
        gui.folder_items = {"folder": r"C:\Projects"}
        gui._visual_project_selection = set()
        gui._project_media_status_label = Mock(return_value="Missing")
        gui._show_project_media_status = Mock()
        gui._toggle_project_item_enabled = Mock()
        event = type("MouseEvent", (), {"x": 10, "y": 10})()

        result = gui.on_tree_double_click(event)

        self.assertEqual(result, "break")
        gui._show_project_media_status.assert_called_once_with(
            r"C:\Projects\Set.als"
        )
        gui._toggle_project_item_enabled.assert_not_called()

    def test_double_clicking_non_missing_media_status_does_not_open_popup(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.tree = Mock()
        gui.tree.identify.return_value = "project-row"
        gui.tree.identify_column.return_value = "#1"
        gui.tree_item_to_path = {"project-row": r"C:\Projects\Set.als"}
        gui._project_media_status_label = Mock(return_value="Linked")
        gui._show_project_media_status = Mock()
        gui._toggle_project_item_enabled = Mock()
        event = type("MouseEvent", (), {"x": 10, "y": 10})()

        result = gui.on_tree_double_click(event)

        self.assertEqual(result, "break")
        gui._show_project_media_status.assert_not_called()
        gui._toggle_project_item_enabled.assert_not_called()

    def test_setting_column_actions_follow_media_status_column(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        config = {"enabled": True, "collect_all_and_save": True}
        gui._target_projects = Mock(return_value=[config])
        gui._after_bulk_change = Mock()

        gui._apply_column_action_to_selection("#1")
        gui._after_bulk_change.assert_not_called()
        gui._apply_column_action_to_selection("#3")
        self.assertFalse(config["collect_all_and_save"])
        self.assertTrue(config["enabled"])
        gui._after_bulk_change.assert_called_once_with()

    def test_refresh_project_media_status_rereads_saved_status(self):
        with tempfile.TemporaryDirectory() as directory:
            status_path = Path(directory) / "project_media_status.json"
            project_path = str(Path(directory) / "Set.als")
            status_path.write_text(
                json.dumps({"projects": [{
                    "path": project_path, "media_status": "Missing Media",
                }]}),
                encoding="utf-8",
            )
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.path_to_tree_item = {project_path: "row"}
            gui._refresh_project_rows = Mock()

            with patch(
                "gui_project_panel.data_file", return_value=str(status_path)
            ):
                gui.refresh_project_media_status()

            self.assertEqual(
                gui._project_media_status_label(project_path), "Missing"
            )
            gui._refresh_project_rows.assert_called_once_with((project_path,))

    def test_clicking_media_status_selects_project_without_toggling_enabled(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.tree = Mock()
        gui.tree.identify.return_value = "project-row"
        gui.tree.identify_column.return_value = "#1"
        gui.tree.get_children.side_effect = lambda item="": (
            ("folder",) if item == "" else ("project-row",)
        )
        row_tags = {"project-row": ("enabled",)}

        def tree_item(item, option=None, **kwargs):
            row_tags[item] = kwargs.get("tags", row_tags.get(item, ()))
            return row_tags[item] if option == "tags" else {}

        gui.tree.item.side_effect = tree_item
        gui.tree_item_to_path = {"project-row": r"C:\Projects\Set.als"}
        gui.folder_items = {"folder": r"C:\Projects"}
        gui._visual_project_selection = set()
        gui.multi_selection = set()
        gui.tree.selection.return_value = ()
        gui.last_selected_item = None
        gui.project_selection_anchor = None
        gui._activate_tree = Mock()
        gui._handle_row_selection_click = Mock(return_value=False)
        config = {"enabled": True}
        gui._target_projects = Mock(return_value=[config])
        gui._after_bulk_change = Mock()
        event = type("MouseEvent", (), {"x": 10, "y": 10, "state": 0})()

        result = gui.on_tree_click(event)

        self.assertEqual(result, "break")
        self.assertEqual(gui.multi_selection, {"project-row"})
        self.assertTrue(config["enabled"])
        gui._after_bulk_change.assert_not_called()

    def test_selected_disabled_project_uses_neutral_background_and_black_text(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.tree = MutableTreeStub()
        folder = gui.tree.insert("", "end", text="Project")
        project = gui.tree.insert(
            folder, "end", text="Set.als", tags=("disabled",)
        )
        gui.folder_items = {folder: r"C:\Projects"}
        gui.tree_item_to_path = {project: r"C:\Projects\Set.als"}
        gui.multi_selection = {project}
        gui._visual_project_selection = set()

        gui.refresh_multi_selection_visuals()

        self.assertIn("multi_disabled", gui.tree.item(project, "tags"))
        self.assertEqual(
            gui.tree.tag_options["multi_disabled"]["background"],
            ACCENT_NEUTRAL,
        )
        self.assertEqual(
            gui.tree.tag_options["multi_disabled"]["foreground"],
            FG_SELECTED_DISABLED,
        )

    def test_project_cell_tooltip_includes_full_name_and_all_hotkeys(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = Mock()
        gui.tree.identify.return_value = "project-row"
        gui.tree.identify_column.return_value = "#4"
        gui.tree_item_to_path = {"project-row": "Set.als"}
        gui.show_status_tooltip = Mock()

        gui._on_project_tree_hover(Mock(x=10, y=10))

        gui.show_status_tooltip.assert_called_once_with(
            "Include Return and Main Effects (R/X/Ctrl+R/Ctrl+X)"
        )

    def test_project_navigation_switches_between_folders_and_files(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = MutableTreeStub()
        folder = gui.tree.insert("", "end", text="Project", open=True)
        first = gui.tree.insert(folder, "end", text="First.als")
        second = gui.tree.insert(folder, "end", text="Second.als")
        next_folder = gui.tree.insert("", "end", text="Next Project", open=False)
        third = gui.tree.insert(next_folder, "end", text="Third.als")
        gui.folder_items = {
            folder: "Project",
            next_folder: "Next Project",
        }
        gui.tree_item_to_path = {
            first: r"C:\Projects\Project\First.als",
            second: r"C:\Projects\Project\Second.als",
            third: r"C:\Projects\Next Project\Third.als",
        }
        gui.projects_by_folder = {
            "Project": {
                "files": [
                    gui.tree_item_to_path[first],
                    gui.tree_item_to_path[second],
                ],
            },
            "Next Project": {"files": [gui.tree_item_to_path[third]]},
        }
        gui._project_file_order = [first, second, third]
        gui.multi_selection = set()
        gui._visual_project_selection = set()
        gui.last_selected_item = None
        gui.project_selection_anchor = None

        gui.navigate_project_selection(1)
        self.assertEqual(gui.last_selected_item, folder)

        gui.navigate_project_level(1)
        self.assertEqual(gui.last_selected_item, first)
        gui.navigate_project_selection(1)
        self.assertEqual(gui.last_selected_item, second)
        gui.navigate_project_selection(1)
        self.assertEqual(gui.last_selected_item, third)
        self.assertTrue(gui.tree.item(next_folder, "open"))
        gui.navigate_project_level(-1)
        self.assertEqual(gui.last_selected_item, next_folder)
        self.assertEqual(
            set(gui._selected_project_paths()),
            {gui.tree_item_to_path[third]},
        )

    def test_search_down_selects_first_project_row(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = MutableTreeStub()
        folder = gui.tree.insert("", "end", text="Project")
        project = gui.tree.insert(folder, "end", text="First.als")
        gui.folder_items = {folder: "Project"}
        gui.tree_item_to_path = {project: r"C:\Projects\Project\First.als"}
        gui._project_file_order = [project]
        gui.multi_selection = set()
        gui._visual_project_selection = set()
        gui.last_selected_item = None
        gui.project_selection_anchor = None
        gui._activate_tree = Mock()

        result = gui._focus_first_project_item()

        self.assertEqual(result, "break")
        self.assertEqual(gui.last_selected_item, folder)
        self.assertEqual(gui.multi_selection, {folder})
        self.assertTrue(gui.tree.widget_focused)
        gui._activate_tree.assert_called_once_with("projects")

    def test_tab_switches_panels_from_active_panel_state(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = Mock()
        gui.queue_tree = Mock()
        gui._active_tree = "projects"
        gui._activate_tree = Mock()

        self.assertEqual(gui._switch_active_tree(), "break")
        gui.queue_tree.focus_set.assert_called_once_with()
        gui._activate_tree.assert_called_once_with("queue")

        gui._active_tree = "queue"
        self.assertEqual(gui._switch_active_tree(), "break")
        gui.tree.focus_set.assert_called_once_with()
        self.assertEqual(
            [call.args[0] for call in gui._activate_tree.call_args_list],
            ["queue", "projects"],
        )

    def test_project_sort_mode_orders_child_files_in_both_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            small = Path(directory) / "Small.als"
            large = Path(directory) / "Large.als"
            small.write_bytes(b"a")
            large.write_bytes(b"larger")
            gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)

            gui.sort_by = "Size - Smallest to Largest"
            self.assertEqual(
                sorted([large, small], key=lambda path: gui._project_file_sort_key(str(path))),
                [small, large],
            )
            gui.sort_by = "Size - Largest to Smallest"
            self.assertEqual(
                sorted([small, large], key=lambda path: gui._project_file_sort_key(str(path))),
                [large, small],
            )

    def test_project_date_sort_orders_newest_folder_first(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        now = time.monotonic()
        old_folder = (r"C:\Projects\Old", {})
        new_folder = (r"C:\Projects\New", {})
        gui.folder_sort_cache = {
            old_folder[0]: {
                "modified": 100,
                "created": 10,
                "size": 1,
                "checked": now,
            },
            new_folder[0]: {
                "modified": 200,
                "created": 20,
                "size": 2,
                "checked": now,
            },
        }

        for sort_by in ("Last Modified", "Date Created"):
            gui.sort_by = sort_by
            self.assertEqual(
                sorted(
                    [old_folder, new_folder],
                    key=gui._project_folder_sort_key,
                ),
                [new_folder, old_folder],
            )

    def test_queue_hotkeys_distinguish_selected_and_all_enabled(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.add_all_enabled_to_queue = Mock(return_value="break")
        gui.enqueue_selected_projects = Mock(return_value="break")

        enter_result = GUI.AutoExportLauncherGUI._dispatch_project_hotkey(
            gui, type("Event", (), {"keysym": "Return", "state": 0})()
        )
        selected_result = GUI.AutoExportLauncherGUI._dispatch_project_hotkey(
            gui, type("Event", (), {"keysym": "q", "state": 0})()
        )

        self.assertEqual(enter_result, "break")
        gui.add_all_enabled_to_queue.assert_called_once_with()
        self.assertEqual(selected_result, "break")
        gui.enqueue_selected_projects.assert_called_once_with()

    def test_add_all_filters_disabled_but_selected_adds_only_selection(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        enabled_path = r"C:\Projects\Enabled.als"
        disabled_path = r"C:\Projects\Disabled.als"
        gui.selected_projects = {
            enabled_path: {
                **gui._default_project_config(enabled_path),
                "enabled": True,
            },
            disabled_path: {
                **gui._default_project_config(disabled_path),
                "enabled": False,
            },
        }
        gui.task_manager = Mock()
        gui.update_queue_tree = Mock()

        gui.add_all_enabled_to_queue()

        gui.task_manager.add_tasks.assert_called_once_with(
            [gui._queue_entry(enabled_path, gui.selected_projects[enabled_path])]
        )
        gui.task_manager.reset_mock()

        gui.tree = MutableTreeStub()
        selected_item = gui.tree.insert("", "end", text="Disabled.als")
        gui.tree_item_to_path = {selected_item: disabled_path}
        gui.folder_items = {}
        gui.projects_by_folder = {}
        gui.multi_selection = {selected_item}

        gui.add_selected_to_queue()

        gui.task_manager.add_tasks.assert_called_once_with(
            [gui._queue_entry(disabled_path, gui.selected_projects[disabled_path])]
        )

    def test_cancelled_background_future_is_not_logged_as_failure(self):
        future = Future()
        future.cancel()

        with patch.object(GUI.logger, "exception") as log_exception:
            result = GUI.AutoExportLauncherGUI._future_result(
                future, "size estimate worker", return_error=True
            )

        self.assertIsNone(result)
        log_exception.assert_not_called()

    def test_empty_background_result_queues_poll_without_raising(self):
        import queue

        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.meta_results = queue.Queue()
        gui.size_results = queue.Queue()
        gui.folder_sort_results = queue.Queue()
        gui.folder_sort_pending = set()
        gui.size_generation = 0
        gui.size_pending = set()
        gui.size_cache = {}
        gui.task_manager = type("TaskStore", (), {"get_tasks": lambda _self: []})()

        gui._poll_size_estimates_once()

    def test_project_search_detaches_rows_and_reuses_cached_tree_items(self):
        with tempfile.TemporaryDirectory() as directory:
            project_folder = Path(directory) / "Project"
            project_folder.mkdir()
            first = project_folder / "Only Match.als"
            second = project_folder / "Another Song.als"
            first.touch()
            second.touch()

            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.tree = MutableTreeStub()
            gui.tree_item_to_path = {}
            gui.folder_items = {}
            gui._tree_detached_items = set()
            gui._project_file_order = []
            gui.multi_selection = set()
            gui._visual_project_selection = set()
            gui.last_selected_item = None
            gui.project_selection_anchor = None
            gui.projects_by_folder = {
                str(project_folder): {
                    "path": str(project_folder),
                    "files": [str(first), str(second)],
                }
            }
            gui.selected_projects = {
                str(path): gui._default_project_config(str(path))
                for path in (first, second)
            }
            gui.sort_by = "Alphabetical"
            gui.project_search_query = ""
            gui._save_project_browser = Mock()

            gui.update_tree()
            initial_ids = dict(gui.tree_item_to_path)
            inserted_count = gui.tree.insert_count
            folder_id = next(iter(gui.folder_items))
            self.assertEqual(len(gui.tree.get_children(folder_id)), 2)

            gui.multi_selection = {folder_id}
            gui.last_selected_item = folder_id
            gui.project_selection_anchor = folder_id
            gui.project_search_query = "only match"
            gui.update_tree(persist=False)

            self.assertEqual(gui.multi_selection, {folder_id})
            self.assertEqual(gui.last_selected_item, folder_id)
            self.assertEqual(len(gui._visible_file_items()), 1)
            self.assertEqual(
                gui.tree_item_to_path[gui._visible_file_items()[0]],
                str(first),
            )
            self.assertEqual(gui.tree.insert_count, inserted_count)

            gui.project_search_query = ""
            gui.update_tree(persist=False)

            self.assertEqual(gui.tree_item_to_path, initial_ids)
            self.assertEqual(len(gui._visible_file_items()), 2)
            self.assertEqual(gui.tree.insert_count, inserted_count)

    def test_browser_projects_options_and_sort_are_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "Session.als"
            project_path.touch()
            cache_path = Path(directory) / "PROJECT_BROWSER.json"
            state = {
                "files": [str(project_path)],
                "sort_by": "Size",
                "projects": {
                    str(project_path): {
                        "enabled": False,
                        "export_state": "off",
                        "collect_all_and_save": False,
                    }
                },
            }
            cache_path.write_text(json.dumps(state), encoding="utf-8")
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.projects_by_folder = {}
            gui.selected_projects = {}
            gui.sort_by = "Last Modified"

            with patch.object(gui_session, "PROJECT_BROWSER_FILE", str(cache_path)):
                gui._restore_project_browser()

            self.assertEqual(gui.sort_by, "Size - Largest to Smallest")
            self.assertEqual(
                gui.projects_by_folder[str(project_path.parent)]["files"],
                [str(project_path)],
            )
            self.assertFalse(gui.selected_projects[str(project_path)]["enabled"])
            self.assertEqual(
                gui.selected_projects[str(project_path)]["export_state"], "off"
            )

    def test_malformed_project_file_list_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "PROJECT_BROWSER.json"
            cache_path.write_text(
                json.dumps({"files": None, "projects": {}}),
                encoding="utf-8",
            )
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.projects_by_folder = {}
            gui.selected_projects = {}
            gui.sort_by = "Last Modified"

            with patch.object(gui_session, "PROJECT_BROWSER_FILE", str(cache_path)):
                gui._restore_project_browser()

            self.assertEqual(gui.projects_by_folder, {})
            self.assertEqual(gui.selected_projects, {})

    def test_project_browser_cache_retries_transient_replace_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "PROJECT_BROWSER.json"
            original_replace = os.replace
            attempts = []

            def replace_with_transient_denial(source, destination):
                attempts.append((source, destination))
                if len(attempts) == 1:
                    raise PermissionError(5, "Access is denied", destination)
                original_replace(source, destination)

            with patch.object(
                gui_session.os,
                "replace",
                side_effect=replace_with_transient_denial,
            ), patch.object(gui_session.time, "sleep") as sleep:
                gui_session.SessionMixin._write_json_snapshot(
                    str(cache_path), {"files": ["Set.als"]}
                )

            self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")),
                             {"files": ["Set.als"]})
            self.assertEqual(len(attempts), 2)
            sleep.assert_called_once_with(0.05)
            self.assertEqual(list(Path(directory).iterdir()), [cache_path])

    def test_returning_queue_task_restores_its_export_options(self):
        with tempfile.TemporaryDirectory() as directory:
            project_path = Path(directory) / "Session.als"
            project_path.touch()
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.projects_by_folder = {}
            gui.selected_projects = {}

            added = gui._add_project_file_to_browser(
                str(project_path),
                {
                    "export_stems": False,
                    "export_main": True,
                    "collect_all_and_save": False,
                    "include_return_and_main_effects": False,
                    "normalize": False,
                    "wet_dry": True,
                },
            )

            self.assertTrue(added)
            self.assertEqual(
                gui.selected_projects[str(project_path)],
                {
                    "project_path": str(project_path),
                    "enabled": True,
                    "collect_all_and_save": False,
                    "export_state": "main",
                    "include_return_and_main_effects": False,
                    "normalize": False,
                    "wet_dry": True,
                },
            )

    def test_shift_navigation_keeps_fixed_anchor_and_adjusts_range(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.tree = TreeStub()
        gui.tree_item_to_path = {
            "a": "a.als",
            "b": "b.als",
            "c": "c.als",
        }
        gui.multi_selection = {"a"}
        gui.last_selected_item = "a"
        gui.project_selection_anchor = "a"

        gui.extend_selection(1)
        self.assertEqual(gui.multi_selection, {"a", "b"})
        gui.extend_selection(1)
        self.assertEqual(gui.multi_selection, {"a", "b", "c"})
        gui.extend_selection(-1)
        self.assertEqual(gui.multi_selection, {"a", "b"})

    def test_project_folder_size_includes_nested_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project_folder = Path(directory) / "Project"
            nested_folder = project_folder / "Samples"
            nested_folder.mkdir(parents=True)
            (project_folder / "Session.als").write_bytes(b"als")
            (nested_folder / "audio.wav").write_bytes(b"audio-data")

            size = GUI.AutoExportLauncherGUI._project_folder_total_size(
                str(project_folder)
            )

            self.assertEqual(size, len(b"als") + len(b"audio-data"))

    def test_project_sort_value_uses_project_folder_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            older = Path(directory) / "A Project"
            newer = Path(directory) / "Z Project"
            older.mkdir()
            newer.mkdir()
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            gui = GUI.AutoExportLauncherGUI.__new__(
                GUI.AutoExportLauncherGUI
            )
            gui.sort_by = "Last Modified"

            self.assertGreater(
                gui._project_folder_sort_value(str(newer), {}),
                gui._project_folder_sort_value(str(older), {}),
            )

    def test_size_sort_uses_cached_folder_size_without_scanning(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.sort_by = "Size"
        gui.folder_sort_cache = {
            "C:/Projects/Set": {
                "size": 1234,
                "checked": time.monotonic(),
            }
        }

        with patch.object(
            gui, "_project_folder_total_size", side_effect=AssertionError
        ):
            self.assertEqual(
                gui._project_folder_sort_value("C:/Projects/Set", {}),
                1234,
            )

    def test_custom_selection_uses_row_tags_without_native_tree_selection(self):
        gui = GUI.AutoExportLauncherGUI.__new__(
            GUI.AutoExportLauncherGUI
        )
        gui.tree = TreeStub()
        gui.tree.selected = ("b",)
        gui.tree_item_to_path = {
            "a": "a.als",
            "b": "b.als",
            "c": "c.als",
        }
        gui.multi_selection = {"b"}

        gui.refresh_multi_selection_visuals()

        self.assertEqual(gui.tree.selected, ())
        self.assertEqual(
            gui.tree.tag_options["multi"],
            {"background": BG_SELECTION, "foreground": FG_SELECTED},
        )

    def test_selected_folder_swaps_base_tag_for_selected_variant(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = MutableTreeStub()
        folder = gui.tree.insert("", "end", text="Project", tags=("folder_enabled",))
        project = gui.tree.insert(folder, "end", text="Set.als", tags=("enabled",))
        gui.folder_items = {folder: r"C:\Projects"}
        gui.tree_item_to_path = {project: r"C:\Projects\Set.als"}
        gui.multi_selection = {folder}
        gui._visual_project_selection = set()

        gui.refresh_multi_selection_visuals()

        tags = gui.tree.item(folder, "tags")
        self.assertEqual(tags, ("multi_folder",))

        gui.multi_selection = set()
        gui.refresh_multi_selection_visuals()
        self.assertEqual(gui.tree.item(folder, "tags"), ("folder_enabled",))

    def test_option_menu_dropdown_hover_uses_inverse_text_color(self):
        option_menu = OptionMenuStub()

        GUI.AutoExportLauncherGUI._style_option_menu_selection(option_menu)

        self.assertEqual(
            option_menu._dropdown_menu.options["activeforeground"],
            FG_SELECTED,
        )

    def test_queue_task_score_combines_flags_and_return_fx_penalty(self):
        task = {
            "collect_all_and_save": True,
            "export_stems": True,
            "export_main": True,
            "include_return_and_main_effects": False,
        }

        self.assertEqual(
            GUI.AutoExportLauncherGUI._queue_task_score(task),
            14,
        )

    def test_queue_sort_orders_known_sizes_and_leaves_unknown_sizes_last(self):
        tasks = [
            {"project_path": "small.als"},
            {"project_path": "unknown.als"},
            {"project_path": "large.als"},
        ]
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.queue_sort_by = "Size - Largest to Smallest"
        gui.task_manager = type(
            "TaskStore",
            (),
            {
                "get_tasks": lambda _self: tasks,
                "save_tasks": Mock(),
            },
        )()
        gui._task_size_bytes = lambda task: {
            "small.als": 10,
            "unknown.als": None,
            "large.als": 100,
        }[task["project_path"]]

        self.assertTrue(gui._apply_queue_sort())
        self.assertEqual(
            [task["project_path"] for task in tasks],
            ["large.als", "small.als", "unknown.als"],
        )

    def test_queue_remove_batch_saves_once(self):
        manager = GUI.TaskManager(task_file="unused.json")
        manager.tasks = [
            {"project_path": "one.als"},
            {"project_path": "two.als"},
            {"project_path": "three.als"},
        ]
        try:
            with patch.object(manager, "save_tasks") as save_tasks:
                manager.remove_tasks({"one.als", "three.als"})

            self.assertEqual(
                [task["project_path"] for task in manager.get_tasks()],
                ["two.als"],
            )
            save_tasks.assert_called_once_with()
        finally:
            manager.close()

    def test_queue_save_persists_async_snapshot_before_flush_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            task_file = Path(directory) / "EXPORT_TASKS.json"
            manager = GUI.TaskManager(task_file=str(task_file))
            try:
                manager.add_tasks([{
                    "project_path": str(Path(directory) / "Session.als"),
                    "export_stems": True,
                    "export_main": False,
                    "collect_all_and_save": True,
                }])
                manager.flush_pending_save()

                saved = json.loads(task_file.read_text(encoding="utf-8"))
                self.assertEqual(len(saved), 1)
                self.assertTrue(saved[0]["export_stems"])
            finally:
                manager.close()

    def test_folder_toggle_updates_all_project_files(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.folder_items = {"folder-row": "C:/Projects/Set"}
        gui.projects_by_folder = {
            "C:/Projects/Set": {
                "files": ["C:/Projects/Set/A.als", "C:/Projects/Set/B.als"]
            }
        }
        gui.selected_projects = {
            path: {"enabled": True}
            for path in gui.projects_by_folder["C:/Projects/Set"]["files"]
        }
        gui._refresh_project_rows = Mock()

        gui._toggle_project_item_enabled("folder-row")

        self.assertTrue(
            all(not config["enabled"] for config in gui.selected_projects.values())
        )
        gui._refresh_project_rows.assert_called_once_with(
            gui.projects_by_folder["C:/Projects/Set"]["files"]
        )

    def test_tree_hotkey_dispatch_uses_focused_panel(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = object()
        gui.queue_tree = object()
        gui._activate_tree = Mock()
        gui.select_all_rows = Mock(return_value="break")
        event = type(
            "KeyEvent",
            (),
            {"widget": gui.tree, "keysym": "a", "state": 0x4},
        )()

        result = GUI.AutoExportLauncherGUI._dispatch_tree_hotkey(gui, event)

        self.assertEqual(result, "break")
        gui._activate_tree.assert_called_once_with("projects")
        gui.select_all_rows.assert_called_once_with(event)

    def test_queue_display_reserves_task_and_setting_slots_for_skip_rows(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.size_cache = {}
        gui.task_meta = {}
        gui._size_cache_key = Mock(return_value=None)
        skipped = {
            "project_path": "skip.als",
            "collect_all_and_save": False,
            "export_stems": False,
            "export_main": False,
            "normalize": False,
            "include_return_and_main_effects": True,
            "wet_dry": False,
        }
        active = {
            **skipped,
            "project_path": "active.als",
            "collect_all_and_save": True,
            "export_stems": True,
            "export_main": True,
            "normalize": True,
            "wet_dry": True,
        }

        skipped_values, skipped_tag, _error = (
            GUI.AutoExportLauncherGUI._queue_row_display(gui, skipped)
        )
        active_values, _active_tag, _error = (
            GUI.AutoExportLauncherGUI._queue_row_display(gui, active)
        )

        self.assertEqual(skipped_tag, "queue_skip")
        self.assertEqual(len(skipped_values[0]), len(active_values[0]))
        self.assertEqual(len(skipped_values[1]), len(active_values[1]))

    def test_queue_context_remove_can_target_only_clicked_project(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.task_manager = Mock()
        gui.queue_tree = Mock()
        gui.queue_path_to_item = {"first.als": "row-1", "second.als": "row-2"}
        gui.update_queue_tree = Mock()

        GUI.AutoExportLauncherGUI.remove_from_queue(gui, {"second.als"})

        gui.task_manager.remove_tasks.assert_called_once_with({"second.als"})
        gui.update_queue_tree.assert_called_once_with()

    def test_project_hotkey_e_toggles_enabled_for_selection(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.toggle_enabled_selected = Mock()
        event = type("KeyEvent", (), {"keysym": "e", "state": 0})()

        result = GUI.AutoExportLauncherGUI._dispatch_project_hotkey(gui, event)

        self.assertEqual(result, "break")
        gui.toggle_enabled_selected.assert_called_once_with()

    def test_hotkey_tables_resolve_modifiers_and_menu_labels(self):
        import gui_keybindings as keys

        event = lambda keysym, state: type(
            "KeyEvent", (), {"keysym": keysym, "state": state}
        )()
        self.assertEqual(
            keys.resolve_hotkey(keys.PROJECT_HOTKEYS, event("s", 0x4)).method,
            "enable_only_smart_select",
        )
        self.assertEqual(
            keys.resolve_hotkey(keys.PROJECT_HOTKEYS, event("Up", 0x4)).method,
            "navigate_project_selection",
        )
        self.assertIsNone(
            keys.resolve_hotkey(keys.PROJECT_HOTKEYS, event("F5", 0))
        )
        self.assertEqual(
            keys.menu_label("Toggle", "toggle_enabled_selected"), "Toggle (E)"
        )
        self.assertEqual(
            keys.menu_label("Smart", "enable_only_smart_select"),
            "Smart (Ctrl+S)",
        )

    def test_status_job_pins_output_over_hover_tooltips(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.status_label = Mock()
        gui.status_progress = Mock()
        gui.status_progress.cget.return_value = "determinate"

        gui.begin_status_job("Scan started")
        gui.show_status_tooltip("Some hover tip")
        gui.update_status_job("Scan: [1/2] Checking a.als", 0.5)

        self.assertEqual(
            gui.status_label.config.call_args_list[-1].kwargs["text"],
            "Scan: [1/2] Checking a.als",
        )
        self.assertFalse(
            any(
                call.kwargs.get("text") == "Some hover tip"
                for call in gui.status_label.config.call_args_list
            )
        )
        gui.status_progress.set.assert_called_with(0.5)
        gui.end_status_job("Done")
        gui.show_status_tooltip("Tip after job")
        self.assertEqual(
            gui.status_label.config.call_args_list[-1].kwargs["text"],
            "Tip after job",
        )

    def test_queue_zero_hotkey_removes_missing_projects(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui._load_project_media_status = Mock()
        gui.task_manager = Mock()
        gui.task_manager.get_tasks.return_value = [
            {"project_path": "missing.als"},
            {"project_path": "linked.als"},
        ]
        gui._project_media_status_label = Mock(
            side_effect=lambda path: (
                "Missing" if path == "missing.als" else "Linked"
            )
        )
        gui.remove_from_queue = Mock()
        event = type("KeyEvent", (), {"keysym": "0", "state": 0})()

        result = GUI.AutoExportLauncherGUI._dispatch_queue_hotkey(gui, event)

        self.assertEqual(result, "break")
        gui.remove_from_queue.assert_called_once_with({"missing.als"})

    def test_project_media_context_menu_scans_visible_selected_projects(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui.tree = Mock()
        gui.tree.identify.return_value = "row-a"
        gui.tree.identify_column.return_value = "#1"
        gui.tree_item_to_path = {
            "row-a": "a.als",
            "row-b": "b.als",
            "hidden": "hidden.als",
        }
        gui.multi_selection = {"row-a", "row-b", "hidden"}
        gui._visible_file_items = Mock(return_value=["row-a", "row-b"])
        gui._set_project_selection = Mock()
        gui.start_selected_media_scan = Mock()
        gui.open_bulk_fix_window = Mock()
        gui.root = object()
        menu = Mock()
        event = type(
            "MouseEvent", (),
            {"x": 3, "y": 5, "x_root": 30, "y_root": 50},
        )()

        with patch("gui_project_panel.tk.Menu", return_value=menu):
            result = GUI.AutoExportLauncherGUI.on_project_context_menu(
                gui, event
            )

        self.assertEqual(result, "break")
        command_options = [
            call.kwargs for call in menu.add_command.call_args_list
        ]
        self.assertEqual(
            [option["label"] for option in command_options],
            [
                "Scan & Relink (2 Items)",
                "Scan & Collect (2 Items)",
                "Bulk Fix Selected (2 Items)",
            ],
        )
        command_options[0]["command"]()
        gui.start_selected_media_scan.assert_called_once_with(
            ("a.als", "b.als"), "relink"
        )
        command_options[2]["command"]()
        gui.open_bulk_fix_window.assert_called_once_with(("a.als", "b.als"))

    def test_initial_estimates_are_enqueued_in_small_batches(self):
        gui = GUI.AutoExportLauncherGUI.__new__(GUI.AutoExportLauncherGUI)
        gui._closing = False
        gui._initial_estimates_started = False
        gui._initial_estimate_cursor = 0
        gui._initial_estimate_paths = set()
        gui.task_manager = type(
            "TaskStore",
            (),
            {
                "get_tasks": lambda _self: [
                    {"project_path": f"{index}.als"} for index in range(10)
                ]
            },
        )()
        gui.root = type("RootStub", (), {"after": Mock(return_value="after-id")})()
        gui._request_size_estimate = Mock()

        gui._start_initial_size_estimates()

        self.assertTrue(gui._initial_estimates_started)
        self.assertEqual(gui._request_size_estimate.call_count, 8)
        gui.root.after.assert_called_once_with(120, gui._queue_initial_estimate_batch)
        self.assertEqual(gui._initial_estimate_cursor, 8)


if __name__ == "__main__":
    unittest.main()
