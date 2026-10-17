"""Filesystem drag-and-drop helpers shared by the GUI panels and tool windows."""

import os
import tkinter as tk
from urllib.parse import unquote, urlparse


from gui_keybindings import bind_directory_listbox_hotkeys  # noqa: F401  (re-export)


def parse_dropped_paths(tk_widget, data):
    """Parse tkinterdnd2 event data into normalized filesystem paths."""
    paths = []
    for item in tk_widget.tk.splitlist(data):
        path = item.strip('{}"')
        if path.startswith("file://"):
            uri = urlparse(path)
            path = unquote(uri.path or "")
            if os.name == "nt" and path.startswith("/") and len(path) > 3 and path[2] == ":":
                path = path.lstrip("/")
        path = os.path.normpath(path)
        if path:
            paths.append(path)
    return paths


def enable_folder_drop_target(widget, on_folder_dropped):
    """Register a drop target that forwards dropped folders to a callback."""
    try:
        from tkinterdnd2 import DND_FILES
    except ImportError:
        return False

    def on_drop(event):
        for path in parse_dropped_paths(widget, event.data):
            if os.path.isdir(path):
                on_folder_dropped(path)
            elif os.path.isfile(path):
                on_folder_dropped(os.path.dirname(path))

    widget.drop_target_register(DND_FILES)
    widget.dnd_bind("<<Drop>>", on_drop)
    return True
