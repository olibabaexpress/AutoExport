"""Standalone Naming Settings window."""

import tkinter as tk

import naming_settings as naming_settings
from gui_theme import (
    ACCENT_ORANGE,
    BG_PANEL,
    BG_WINDOW,
    BTN_COLOR_GO,
    BTN_COLOR_OFF,
    FG_PRIMARY,
    FONT_HEADER,
    FONT_UI,
    FONT_UI_BOLD,
    make_outline_button,
)


def open_naming_settings_window(gui):
    """Open the naming options window unless one is already visible."""
    if gui._get_existing_tool_window("naming_settings"):
        return
    win = tk.Toplevel(gui.root)
    win.withdraw()
    win.title("Naming Settings")
    win.configure(bg=BG_WINDOW)
    win.geometry("440x560")
    win.minsize(400, 560)

    content = tk.Frame(win, bg=BG_WINDOW)
    content.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    build_naming_settings_contents(content, win)
    gui._register_tool_window("naming_settings", win)
    gui._center_and_show_toplevel(win)


def build_naming_settings_contents(parent, win):
    """Build the naming setting controls and persist them when saved."""
    settings = naming_settings.load_settings()
    settings_vars = {
        key: tk.BooleanVar(value=value)
        for key, value in settings.items()
    }

    def add_section(title, description, keys_and_labels):
        frame = tk.LabelFrame(
            parent,
            text=title,
            font=FONT_HEADER,
            bg=BG_WINDOW,
            fg=ACCENT_ORANGE,
            highlightthickness=1,
            highlightbackground=BG_PANEL,
        )
        frame.pack(fill=tk.X, padx=5, pady=10)

        if description:
            tk.Label(
                frame,
                text=description,
                bg=BG_WINDOW,
                fg=FG_PRIMARY,
                font=FONT_UI,
                anchor="w",
                justify="left",
                wraplength=460,
            ).pack(fill=tk.X, padx=10, pady=(5, 5))

        for key, label in keys_and_labels:
            tk.Checkbutton(
                frame,
                text=label,
                variable=settings_vars[key],
                font=FONT_UI_BOLD,
                bg=BG_WINDOW,
                fg=FG_PRIMARY,
                activebackground=BG_WINDOW,
                activeforeground=FG_PRIMARY,
                highlightthickness=0,
                selectcolor=BG_WINDOW,
                anchor="w",
            ).pack(fill=tk.X, padx=10, pady=3)

    add_section(
        "Main Mix Export",
        'Filename format: "<name>_<tempo>bpm_<dd-mm-yy>.wav"',
        [
            ("main_mix_include_bpm", "Include BPM"),
            ("main_mix_include_date", "Include date"),
        ],
    )
    add_section(
        "Stems Live-Set Folder",
        'The folder for each exported live set, e.g. "name_140bpm_15-01-24".',
        [
            ("stems_folder_include_bpm", "Include BPM"),
            ("stems_folder_include_date", "Include date"),
        ],
    )
    add_section(
        "Individual Stem Files",
        "Applies to every exported track/group stem file.",
        [
            ("stem_files_include_bpm", 'Include BPM (e.g. "Kick_140bpm.wav")'),
            ("stem_files_include_project_name", "Include project name prefix on every stem"),
        ],
    )
    add_section(
        "Add Ableton Version Suffix",
        'Renames projects with an Ableton version suffix like "(AL12.3)"\n'
        "when opening projects made with older versions of Ableton.",
        [("include_ableton_version", "Include Ableton version suffix")],
    )

    button_frame = tk.Frame(parent, bg=BG_WINDOW)
    button_frame.pack(fill=tk.X, pady=(10, 0))

    def save_and_close():
        naming_settings.save_settings({
            key: var.get() for key, var in settings_vars.items()
        })
        win.destroy()

    make_outline_button(
        button_frame, "Save", save_and_close, BTN_COLOR_GO
    ).pack(side=tk.LEFT, padx=3)
    make_outline_button(
        button_frame, "Cancel", win.destroy, BTN_COLOR_OFF
    ).pack(side=tk.LEFT, padx=3)
