"""Shared AutoExport palette and window branding."""

import os
import sys
import ctypes
import tkinter as tk
from pathlib import Path

BG_WINDOW = "#1e1e1e"
BG_PANEL = "#3D3D3D"
BG_INPUT = "#131313"

BG_BUTTON = "#141414"


BG_SELECTION = "#CECCCC"
BG_SELECTED = BG_SELECTION
BG_SEPARATOR = "#676767"
BG_TOOLBAR = "#171717"

FG_PRIMARY = "#e0e0e0"
FG_SECONDARY = "#cfcfcf"
FG_SELECTED = "#0e0e0e"
FG_SELECTION = FG_SELECTED
FG_SELECTED_DISABLED = "#3a3a3a"
FG_CHECKMARK = "#ffffff"
FG_MUTED = "#898989"
FG_ACTIVE = "#cdf8ff"


ACCENT_BLUE = "#8BCAFA"
ACCENT_ORANGE = "#f0d7b5"
ACCENT_GREEN = "#b6fdb6"
ACCENT_RED = "#e06060"
ACCENT_CYAN = "#aeffff"
ACCENT_NEUTRAL = "#9d9d9d"
ADD_ALL_TO_QUEUE = "#d4d4d4"

# ---------- Tree Row Styling ---------
BG_TREE        = "#131313" 
BG_TREE_HEADER = "#242424"
BG_ROW_HEADER  = "#242424"   # parent/header row background
BG_SEARCH_MATCH = "#3d004e"
FG_ROW_HEADER  = FG_PRIMARY  # header row text colour
FG_DISABLED    = "#656565"   # disabled row text colour
FG_DISABLED_BUTTON = "#555555"
BG_DISABLED = "#c3c3c3"
FG_TREE_ARROW = "#999999"
FG_TREE_ARROW_ACTIVE = "#cccccc"
FG_TREE_ARROW_PRESSED = "#dddddd"
QUEUE_TASK_MAIN = "#a8c3fb"
QUEUE_TASK_STEMS = "#fcf0ba"
QUEUE_TASK_COLLECT = "#bbffbb"
QUEUE_TASK_DONE = "#A2A2A2"

ACCENT_GREEN_HOVER = "#7acf7a"
  # treeview background
# ---------- Type ----------
FONT_UI      = ("Segoe UI", 9)
FONT_UI_SM   = ("Segoe UI", 8)
FONT_UI_BOLD = ("Segoe UI", 9, "bold")
FONT_HEADER  = ("Segoe UI", 9, "bold")
FONT_TITLE   = ("Segoe UI", 12, "bold")
FONT_TREE    = ("Segoe UI", 9)
FONT_TREE_MONO = ("Segoe UI Mono", 9)
FONT_TREE_HEADER = ("Segoe UI", 10, "bold")
FONT_TREE_BOLD = FONT_TREE_HEADER

BTN_COLOR_OFF = FG_SECONDARY
BTN_COLOR_MAIN = ACCENT_BLUE
BTN_COLOR_STEMS = ACCENT_ORANGE
BTN_COLOR_GO = ACCENT_GREEN
BTN_COLOR_CLEAR = ACCENT_RED
BTN_COLOR_NEUTRAL = ACCENT_NEUTRAL
BTN_COLOR_UTILITY = ACCENT_CYAN

SCROLLBAR_TRACK = "#0E0E0E"
SCROLLBAR_THUMB = "#727272"
SCROLLBAR_THUMB_HOVER = "#B4B4B4"
CTK_APPEARANCE_MODE = "Dark"
CTK_COLOR_THEME = "green"

PROJECT_TREE_COLUMNS = ("#0", "media status", "export", "collect", "returnfx", "normalize", "wetdry")
PROJECT_TREE_COLUMN_WIDTHS = {
    "#0": 240,
    "media status": 54,
    "export": 52,
    "collect": 52,
    "returnfx": 52,
    "normalize": 52,
    "wetdry": 58,
}
QUEUE_TREE_COLUMNS = ("task", "settings", "size", "done")
QUEUE_TREE_COLUMN_WIDTHS = {
    "#0": 230,
    "task": 154,
    "settings": 154,
    "size": 68,
    "done": 34,
}


def _darken(hex_color, factor=0.65):
    """Return a darker version of a '#rrggbb' color string."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"


def _lighten(hex_color, amount=0.18):
    """Return a subtly brighter version of a '#rrggbb' color string."""
    channels = [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]
    return "#" + "".join(
        f"{round(channel + (255 - channel) * amount):02x}"
        for channel in channels
    )


def _add_button_hover_style(button):
    """Apply the shared colored-outline button hover treatment."""
    def on_enter(_event):
        if str(button.cget("state")) == "disabled":
            return
        color = button._outline_base_color
        hover_color = _lighten(color)
        button.configure(fg=hover_color, bg=_darken(color, 0.12))
        if isinstance(button.master, _OutlineButton):
            tk.Frame.configure(button.master, bg=hover_color)

    def on_leave(_event):
        color = button._outline_base_color
        button.configure(fg=color)
        button.configure(bg=BG_BUTTON)
        if isinstance(button.master, _OutlineButton):
            tk.Frame.configure(button.master, bg=color)

    button.bind("<Enter>", on_enter)
    button.bind("<Leave>", on_leave)


class _OutlineButton(tk.Frame):
    """Packable colored border with a classic Tk button inside."""

    def __init__(self, parent, border_color, **button_options):
        super().__init__(parent, bg=border_color, bd=0, highlightthickness=0)
        self._button = tk.Button(self, **button_options)
        self._button.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    def config(self, cnf=None, **kwargs):
        border_color = kwargs.pop("highlightbackground", None)
        kwargs.pop("highlightcolor", None)
        kwargs.pop("highlightthickness", None)
        if border_color is not None:
            tk.Frame.configure(self, bg=border_color)
        return self._button.configure(cnf, **kwargs)

    configure = config

    def cget(self, key):
        if key == "highlightbackground":
            return tk.Frame.cget(self, "bg")
        return self._button.cget(key)

    def __getitem__(self, key):
        return self.cget(key)

    def __setitem__(self, key, value):
        self.config(**{key: value})

    def invoke(self):
        return self._button.invoke()


def make_outline_button(
    parent, text, command, color, font=FONT_UI, padx=6, pady=3,
    state=tk.NORMAL, width=None, **extra
):
    """Create a classic Tk button with a persistent semantic-color outline."""
    kwargs = {}
    if width is not None:
        kwargs["width"] = width
    button = _OutlineButton(
        parent,
        color,
        text=text,
        command=command,
        bg=BG_BUTTON,
        fg=color,
        activebackground=_darken(color, 0.25),
        activeforeground=color,
        highlightbackground=color,
        highlightcolor=color,
        highlightthickness=0,
        bd=0,
        relief=tk.FLAT,
        font=font,
        padx=padx,
        pady=pady,
        state=state,
        **kwargs,
        **extra
    )
    button._button._outline_base_color = color
    _add_button_hover_style(button._button)
    return button



def enable_dark_title_bar(window):
    if sys.platform != "win32":
        return

    attribute = 20
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
        )
        if result != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 19, ctypes.byref(value), ctypes.sizeof(value)
            )
    except (AttributeError, OSError):
        pass


def set_window_icon(window):
    if getattr(sys, "frozen", False):
        icon = Path(sys._MEIPASS) / "assets" / "autoexport.ico"
    else:
        icon = Path(__file__).resolve().parents[1] / "packaging" / "autoexport.ico"
    if os.name == "nt":
        window.iconbitmap(str(icon))


def style_setup(window):
    from tkinter import ttk
    window.configure(background=BG_WINDOW)
    style = ttk.Style(window)
    style.theme_use("clam")
    style.configure(".", background=BG_WINDOW, foreground=FG_PRIMARY, font=("Segoe UI", 9))
    style.configure("TFrame", background=BG_WINDOW)
    style.configure("TLabel", background=BG_WINDOW, foreground=FG_PRIMARY)
    style.configure("Title.TLabel", foreground=ACCENT_GREEN)
    style.configure(
        "TEntry", fieldbackground=BG_INPUT, foreground=FG_PRIMARY,
        insertcolor=FG_PRIMARY, bordercolor=BG_SEPARATOR,
    )
    style.configure(
        "TButton", background=BG_PANEL, foreground=FG_PRIMARY,
        bordercolor=BG_SEPARATOR, padding=(10, 6),
    )
    style.map("TButton", background=[("active", BG_SELECTION)], foreground=[("disabled", FG_MUTED)])
    style.configure("Save.TButton", background=ACCENT_GREEN, foreground=BG_INPUT)
    style.map("Save.TButton", background=[("active", ACCENT_GREEN_HOVER)])
    set_window_icon(window)
    window.after(100, lambda target=window: enable_dark_title_bar(target))


def create_dark_scrollbar(parent, **kwargs):
    """Create a CustomTkinter scrollbar using the shared dark palette."""
    import customtkinter as ctk

    kwargs.setdefault("fg_color", SCROLLBAR_TRACK)
    kwargs.setdefault("button_color", SCROLLBAR_THUMB)
    kwargs.setdefault("button_hover_color", SCROLLBAR_THUMB_HOVER)
    return ctk.CTkScrollbar(parent, **kwargs)
