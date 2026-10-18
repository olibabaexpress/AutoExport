"""Shared layout pieces for the Sample Index and Bulk Fix tabs.

Both tabs are built from the same skeleton so borders, spacing and the
footer land in identical places:

    section header   (title + optional icon actions)
    panel            (bordered list / text area, expands)
    ...more header + panel pairs...
    button row       (5 equal grid columns)
    status box       (bordered, two lines tall)
    progress row     (bar + fixed icon slot)

Call build_footer() BEFORE packing any expanding panel. The footer is packed
with side=BOTTOM, and bottom-packed widgets must claim their space first or
the expanding panels will squeeze them out of the window.
"""

import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

from gui_theme import (
    ACCENT_CYAN,
    ACCENT_GREEN,
    BG_SEPARATOR,
    BG_TREE,
    BG_WINDOW,
    FG_PRIMARY,
    FONT_HEADER,
    FONT_UI,
    make_outline_button,
)

PAD_X = 6
SECTION_GAP = 6
HEADER_HEIGHT = 28
BUTTON_COLUMNS = 5
BUTTON_HEIGHT = 32
BUTTON_GAP = 6
ICON_SLOT_WIDTH = 64
ICON_SLOT_HEIGHT = 30

ACTIVE_BAR_STYLE = "MissingMedia.Horizontal.TProgressbar"
IDLE_BAR_STYLE = "Inactive.Horizontal.TProgressbar"


def configure_progress_styles(root):
    style = ttk.Style(root)
    style.configure(
        ACTIVE_BAR_STYLE,
        background=ACCENT_CYAN,
        troughcolor=BG_WINDOW,
        bordercolor=BG_WINDOW,
        lightcolor=ACCENT_CYAN,
        darkcolor=ACCENT_CYAN,
    )
    style.configure(
        IDLE_BAR_STYLE,
        background=BG_WINDOW,
        troughcolor=BG_WINDOW,
        bordercolor=BG_WINDOW,
        lightcolor=BG_WINDOW,
        darkcolor=BG_WINDOW,
    )


def _bordered_frame(parent):
    return tk.Frame(
        parent,
        bg=BG_TREE,
        bd=0,
        highlightthickness=1,
        highlightbackground=BG_SEPARATOR,
        highlightcolor=BG_SEPARATOR,
    )


def build_section_header(parent, title):
    """Pack a fixed-height heading row. Returns a frame for icon buttons."""
    row = tk.Frame(parent, bg=BG_WINDOW, height=HEADER_HEIGHT)
    row.pack(fill=tk.X, padx=PAD_X, pady=(SECTION_GAP, 2))
    row.pack_propagate(False)
    tk.Label(
        row,
        text=title,
        bg=BG_WINDOW,
        fg=FG_PRIMARY,
        font=FONT_HEADER,
        anchor="w",
    ).pack(side=tk.LEFT)
    actions = tk.Frame(row, bg=BG_WINDOW)
    actions.pack(side=tk.LEFT, padx=(8, 0))
    return actions


def make_panel(parent, expand):
    """Pack a bordered panel. Put the list or text widget inside it."""
    panel = _bordered_frame(parent)
    panel.pack(
        fill=tk.BOTH if expand else tk.X,
        expand=expand,
        padx=PAD_X,
        pady=(0, SECTION_GAP),
    )
    return panel


def add_grid_button(
    button_row, column, text, command, color,
    columnspan=1, font=FONT_UI, state=tk.NORMAL,
):
    """Place an outline button in the shared grid.

    The slot requests 1px of width, so column weights (not the label text)
    decide the widths. Every column ends up the same size and the row can
    never be wider than the window.
    """
    slot = tk.Frame(button_row, bg=BG_WINDOW, width=1, height=BUTTON_HEIGHT)
    slot.grid(
        row=0,
        column=column,
        columnspan=columnspan,
        sticky="ew",
        padx=BUTTON_GAP // 2,
    )
    slot.pack_propagate(False)
    button = make_outline_button(
        slot,
        text,
        command,
        color,
        font=font,
        padx=2,
        pady=4,
        state=state,
    )
    button.pack(fill=tk.BOTH, expand=True)
    return button


def build_footer(parent):
    """Pack button row, status box and progress row at the bottom."""
    configure_progress_styles(parent.winfo_toplevel())

    # Packed bottom-up: progress row is lowest, button row is highest.
    progress_row = tk.Frame(parent, bg=BG_WINDOW)
    progress_row.pack(side=tk.BOTTOM, fill=tk.X, padx=PAD_X, pady=(0, 10))
    icon_slot = tk.Frame(
        progress_row,
        bg=BG_WINDOW,
        width=ICON_SLOT_WIDTH,
        height=ICON_SLOT_HEIGHT,
    )
    icon_slot.pack(side=tk.RIGHT, padx=(6, 0))
    icon_slot.pack_propagate(False)
    progress_bar = ttk.Progressbar(
        progress_row,
        mode="determinate",
        maximum=1,
        value=0,
        style=IDLE_BAR_STYLE,
    )
    progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)

    status_var = tk.StringVar(value="Ready")
    status_box = _bordered_frame(parent)
    status_box.pack(
        side=tk.BOTTOM, fill=tk.X, padx=PAD_X, pady=(0, SECTION_GAP)
    )
    status_label = tk.Label(
        status_box,
        textvariable=status_var,
        bg=BG_TREE,
        fg=ACCENT_GREEN,
        font=FONT_UI,
        anchor="w",
        justify="left",
        height=2,
        padx=8,
        pady=4,
    )
    status_label.pack(fill=tk.X)
    status_label.bind(
        "<Configure>",
        lambda event: status_label.configure(
            wraplength=max(100, event.width - 16)
        ),
    )

    # Outer padding is reduced by half a gap because every button carries
    # half a gap on each side, so button edges line up with the panels.
    button_row = tk.Frame(parent, bg=BG_WINDOW)
    button_row.pack(
        side=tk.BOTTOM,
        fill=tk.X,
        padx=PAD_X - BUTTON_GAP // 2,
        pady=(SECTION_GAP, SECTION_GAP),
    )
    for column in range(BUTTON_COLUMNS):
        button_row.grid_columnconfigure(
            column, weight=1, uniform="tool-buttons"
        )

    def set_progress_active(active):
        progress_bar.configure(
            style=ACTIVE_BAR_STYLE if active else IDLE_BAR_STYLE
        )

    def reset_progress():
        progress_bar["value"] = 0
        set_progress_active(False)

    return SimpleNamespace(
        button_row=button_row,
        status_var=status_var,
        progress_bar=progress_bar,
        icon_slot=icon_slot,
        set_progress_active=set_progress_active,
        reset_progress=reset_progress,
    )
