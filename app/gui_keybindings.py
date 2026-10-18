"""Keyboard shortcuts: one table per panel plus the dispatch machinery.

Edit the PROJECT_HOTKEYS / QUEUE_HOTKEYS tables to change an assignment; every
row reads ``"chord": hotkey(handler_method, "description", *args)``.  Chords are
lowercase and ordered ``ctrl+shift+key`` (for example ``"ctrl+s"``).  Handler
methods are looked up by name on the GUI, so this module needs no GUI imports.
"""

import tkinter as tk

EVENT = object()  # handler argument placeholder: pass the key event itself
NAVIGATION_KEYS = frozenset({"up", "down", "left", "right"})
CONTROL_MASK = 0x4
SHIFT_MASK = 0x1


class Hotkey:
    """A handler method name, its arguments, and a human description."""

    def __init__(self, method, description, *args):
        self.method = method
        self.description = description
        self.args = args


def hotkey(method, description, *args):
    return Hotkey(method, description, *args)


# --------------------------------------------------------------------------
# Hotkey assignments
# --------------------------------------------------------------------------
GLOBAL_HOTKEYS = {
    "tab": "Switch between the Project Browser and the Queue",
    "ctrl+f": "Focus the project search box",
    "down (in search)": "Jump to the first project",
}

PROJECT_HOTKEYS = {
    # Queue
    "return": hotkey("add_all_enabled_to_queue", "Add all enabled to queue"),
    "kp_enter": hotkey("add_all_enabled_to_queue", "Add all enabled to queue"),
    "q": hotkey("enqueue_selected_projects", "Add selected to queue"),
    # Selection
    "ctrl+a": hotkey("select_all_rows", "Select all", EVENT),
    "shift+a": hotkey("select_all_rows", "Select all", EVENT),
    "i": hotkey("invert_selection", "Invert selection"),
    "t": hotkey("select_latest_modified", "Select latest modified"),
    "ctrl+t": hotkey("enable_only_latest_modified", "Enable only latest modified"),
    "l": hotkey("select_only_linked", "Select only Linked"),
    "p": hotkey("select_only_portable", "Select only Portable"),
    "m": hotkey("select_only_missing", "Select only Missing"),
    "escape": hotkey("clear_multi_selection", "Deselect all"),
    "up": hotkey("navigate_project_selection", "Move selection up", -1),
    "down": hotkey("navigate_project_selection", "Move selection down", 1),
    "shift+up": hotkey("extend_selection", "Extend selection up", -1),
    "shift+down": hotkey("extend_selection", "Extend selection down", 1),
    "left": hotkey("navigate_project_level", "Go up a hierarchy level", -1),
    "right": hotkey("navigate_project_level", "Go into children", 1),
    "ctrl+c": hotkey("toggle_all_project_folders", "Expand/collapse folders"),
    "delete": hotkey("on_project_delete_key", "Remove selected", EVENT),
    "ctrl+shift+delete": hotkey("remove_unselected", "Remove all unselected", EVENT),
    "ctrl+delete": hotkey("remove_disabled", "Remove all disabled", EVENT),
    # Enabled state
    "ctrl+l": hotkey("enable_only_linked", "Enable only Linked"),
    "ctrl+p": hotkey("enable_only_portable", "Enable only Portable"),
    "ctrl+m": hotkey("enable_only_missing", "Enable only Missing"),
    "e": hotkey("toggle_enabled_selected", "Toggle enabled for selection"),
    "ctrl+e": hotkey("toggle_enabled_selected", "Toggle enabled for selection"),
    "ctrl+d": hotkey("disable_selected", "Disable selected"),
    "ctrl+s": hotkey("enable_only_smart_select", "Enable only Smart Select"),
    "0": hotkey("toggle_enabled_selected", "Toggle enabled for selection"),
    # Export options
    "s": hotkey("toggle_export_state_selected", "Cycle main/stems/skip"),
    "c": hotkey("toggle_collect_selected", "Toggle collect all and save"),
    "r": hotkey("toggle_return_fx_selected", "Toggle return FX"),
    "x": hotkey("toggle_return_fx_selected", "Toggle return FX"),
    "ctrl+r": hotkey("toggle_return_fx_selected", "Toggle return FX"),
    "ctrl+x": hotkey("toggle_return_fx_selected", "Toggle return FX"),
    "n": hotkey("toggle_normalize_selected", "Toggle normalize"),
    "z": hotkey("toggle_normalize_selected", "Toggle normalize"),
    "ctrl+n": hotkey("toggle_normalize_selected", "Toggle normalize"),
    "ctrl+z": hotkey("toggle_normalize_selected", "Toggle normalize"),
    "w": hotkey("toggle_wet_dry_selected", "Toggle wet/dry"),
    # Misc
    "ctrl+o": hotkey("set_export_path", "Set export path"),
}

QUEUE_HOTKEYS = {
    "ctrl+a": hotkey("select_all_queue_rows", "Select all", EVENT),
    "shift+a": hotkey("select_all_queue_rows", "Select all", EVENT),
    "up": hotkey("navigate_queue_selection", "Move selection up", -1),
    "down": hotkey("navigate_queue_selection", "Move selection down", 1),
    "shift+up": hotkey("extend_queue_selection", "Extend selection up", -1),
    "shift+down": hotkey("extend_queue_selection", "Extend selection down", 1),
    "ctrl+up": hotkey("_move_queue_item_hotkey", "Move task up", -1),
    "ctrl+down": hotkey("_move_queue_item_hotkey", "Move task down", 1),
    "left": hotkey("_queue_horizontal_hotkey", "Start selecting"),
    "right": hotkey("_queue_horizontal_hotkey", "Start selecting"),
    "delete": hotkey("on_queue_delete_key", "Remove selected", EVENT),
    "0": hotkey("remove_missing_queue_tasks", "Remove tasks with missing media"),
    "s": hotkey("_toggle_queue_export_state", "Cycle main/stems/skip"),
    "c": hotkey(
        "_toggle_queue_option_hotkey", "Toggle collect", "collect_all_and_save", False
    ),
    "r": hotkey(
        "_toggle_queue_option_hotkey", "Toggle return FX",
        "include_return_and_main_effects", True,
    ),
    "x": hotkey(
        "_toggle_queue_option_hotkey", "Toggle return FX",
        "include_return_and_main_effects", True,
    ),
    "n": hotkey("_toggle_queue_option_hotkey", "Toggle normalize", "normalize", False),
    "z": hotkey("_toggle_queue_option_hotkey", "Toggle normalize", "normalize", False),
    "w": hotkey("_toggle_queue_option_hotkey", "Toggle wet/dry", "wet_dry", False),
    "ctrl+o": hotkey("set_export_path", "Set export path"),
}


# --------------------------------------------------------------------------
# Lookup helpers
# --------------------------------------------------------------------------
def event_chord(event):
    """Return (chord, bare_key) for a Tk key event, e.g. ('ctrl+s', 's')."""
    key = event.keysym.casefold()
    parts = []
    if event.state & CONTROL_MASK:
        parts.append("ctrl")
    if event.state & SHIFT_MASK:
        parts.append("shift")
    parts.append(key)
    return "+".join(parts), key


def resolve_hotkey(table, event):
    """Find the table entry for an event; arrows fall back to their bare key."""
    chord, key = event_chord(event)
    entry = table.get(chord)
    if entry is None and key in NAVIGATION_KEYS:
        entry = table.get(key)
    return entry


def chord_text(chord):
    return "+".join(part.capitalize() if len(part) > 1 else part.upper()
                    for part in chord.split("+"))


def hotkey_text(table, method):
    """Preferred shortcut text for a handler, e.g. 'Ctrl+S' ('' if unbound).

    Single-key chords win over modifier chords, then table order decides.
    """
    chords = [
        chord for chord, entry in table.items()
        if isinstance(entry, Hotkey) and entry.method == method
    ]
    if not chords:
        return ""
    chords.sort(key=lambda chord: chord.count("+"))
    return chord_text(chords[0])


def hotkey_texts(table, method):
    """Return every assigned shortcut for a handler in table order."""
    return [
        chord_text(chord)
        for chord, entry in table.items()
        if isinstance(entry, Hotkey) and entry.method == method
    ]


def menu_label(text, method, table=None):
    """Menu entry text with its shortcut: 'Select All (Ctrl+A)'."""
    shortcut = hotkey_text(PROJECT_HOTKEYS if table is None else table, method)
    return f"{text} ({shortcut})" if shortcut else text


def describe_hotkeys(table):
    """One 'chord  description' line per binding, for help text."""
    return [
        f"{chord_text(chord):<14}{entry.description}"
        for chord, entry in table.items()
        if isinstance(entry, Hotkey)
    ]


# --------------------------------------------------------------------------
# Mixin: focus-aware dispatch for the project browser and queue panels
# --------------------------------------------------------------------------
class HotkeyMixin:
    def _bind_tree_hotkeys(self):
        """Route project and queue shortcuts through one focus-aware handler."""
        self.root.bind_all("<KeyPress>", self._dispatch_tree_hotkey, add="+")
        for tree in (self.tree, self.queue_tree):
            tags = list(tree.bindtags())
            tags.remove("all")
            tree.bindtags((tags[0], "all", *tags[1:]))
        self._activate_tree("projects")

    def _run_hotkey(self, entry, event):
        args = tuple(event if arg is EVENT else arg for arg in entry.args)
        getattr(self, entry.method)(*args)
        return "break"

    def _dispatch_tree_hotkey(self, event):
        key = event.keysym.casefold()
        if key == "tab":
            if (
                self._widget_is_within(event.widget, self.tree_frame)
                or self._widget_is_within(event.widget, self.queue_panel)
            ):
                return self._switch_active_tree(event)
            return
        if key == "f" and event.state & CONTROL_MASK:
            self.project_search_entry.focus_set()
            return "break"
        search_entry = getattr(self, "project_search_entry", None)
        if (
            search_entry is not None
            and key == "down"
            and self._widget_is_within(event.widget, search_entry)
        ):
            return self._focus_first_project_item(event)
        if event.widget is self.tree:
            self._activate_tree("projects")
            return self._dispatch_project_hotkey(event)
        if event.widget is self.queue_tree:
            self._activate_tree("queue")
            return self._dispatch_queue_hotkey(event)

    def _dispatch_project_hotkey(self, event):
        entry = resolve_hotkey(PROJECT_HOTKEYS, event)
        if entry is not None:
            return self._run_hotkey(entry, event)

    def _dispatch_queue_hotkey(self, event):
        entry = resolve_hotkey(QUEUE_HOTKEYS, event)
        if entry is not None:
            return self._run_hotkey(entry, event)

    def _switch_active_tree(self, _event=None):
        next_tree = (
            self.queue_tree if self._active_tree == "projects" else self.tree
        )
        next_tree.focus_set()
        self._activate_tree(
            "queue" if next_tree is self.queue_tree else "projects"
        )
        return "break"


# --------------------------------------------------------------------------
# Directory-list helper used by the Sample Index and Bulk Fix windows
# --------------------------------------------------------------------------
def bind_directory_listbox_hotkeys(listbox, remove_selected):
    """Bind select-all, range selection, and deletion for directory lists."""
    selection_anchor = {"index": None}

    def select_all(_event=None):
        if listbox.size():
            listbox.selection_set(0, tk.END)
            selection_anchor["index"] = 0
            listbox.activate(0)
        return "break"

    def remember_anchor(event):
        if not (event.state & SHIFT_MASK) and listbox.size():
            selection_anchor["index"] = listbox.nearest(event.y)

    def extend_selection(direction):
        count = listbox.size()
        if not count:
            return "break"
        anchor = selection_anchor["index"]
        if anchor is None:
            selected = listbox.curselection()
            anchor = selected[0] if selected else listbox.index(tk.ACTIVE)
            selection_anchor["index"] = anchor
        current = listbox.index(tk.ACTIVE)
        if current < 0 or current >= count:
            current = anchor
        target = max(0, min(count - 1, current + direction))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(min(anchor, target), max(anchor, target))
        listbox.activate(target)
        listbox.see(target)
        return "break"

    def delete_selected(_event=None):
        remove_selected()
        return "break"

    listbox.bind("<Control-a>", select_all)
    listbox.bind("<Control-A>", select_all)
    listbox.bind("<Shift-a>", select_all)
    listbox.bind("<Shift-A>", select_all)
    listbox.bind("<Shift-Up>", lambda _event: extend_selection(-1))
    listbox.bind("<Shift-Down>", lambda _event: extend_selection(1))
    listbox.bind("<Delete>", delete_selected)
    listbox.bind("<ButtonRelease-1>", remember_anchor, add="+")
