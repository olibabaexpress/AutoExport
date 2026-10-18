"""Audio render settings controls for the launcher GUI."""

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import audio_settings as audio_settings
from gui_theme import (
    BG_BUTTON, BG_INPUT, BG_SELECTION, BG_SEPARATOR, BG_WINDOW,
    FG_PRIMARY, FG_SELECTED, FONT_HEADER, FONT_UI,
)

AUDIO_SETTING_TOOLTIPS = {
    "sample_rate": "Sample rate",
    "bit_depth": "Bit depth",
    "file_type": "File format",
    "dither": "Dither",
}


class AudioSettingsPanelMixin:
    def _build_audio_settings_contents(self, parent):
        """Four dropdowns mirroring Ableton's Export dialog audio options.

        The Dither dropdown is disabled whenever Bit Depth is 32, matching
        Ableton greying it out there (no dither is applied at 32-bit).
        """
        current = audio_settings.load()
        current_values = {
            "sample_rate": current.sample_rate,
            "bit_depth": current.bit_depth,
            "file_type": current.file_type,
            "dither": current.dither,
        }
        last_saved_values = dict(current_values)

        labels = {
            "sample_rate": "Sample Rate",
            "bit_depth": "Bit Depth",
            "file_type": "File Type",
            "dither": "Dither Options",
        }

        frame = tk.LabelFrame(
            parent,
            text="Audio Settings",
            font=FONT_HEADER,
            bg=BG_WINDOW,
            fg=FG_PRIMARY,
            bd=1,
            relief=tk.GROOVE,
            highlightthickness=0,
                                                        # Border thickness            # Border style     # Highlight border thickness  # Border colour when unfocused     # Border colour when focused
        )
        frame.pack(fill=tk.X, padx=5, pady=(0, 4))
        settings_grid = tk.Frame(frame, bg=BG_WINDOW)
        settings_grid.pack(fill=tk.X, padx=3, pady=2)
        for column in range(len(audio_settings.DROPDOWN_KEYS)):
            settings_grid.grid_columnconfigure(
                column, weight=1, uniform="audio_settings"
            )

        dropdown_vars = {}
        dropdown_widgets = {}
        dropdown_font = ctk.CTkFont(family="Segoe UI", size=13)
        active_dropdown = {"window": None, "option_menu": None}

        def close_dropdown():
            popup = active_dropdown["window"]
            if popup is None:
                return
            configure_id = active_dropdown.pop("configure_id", None)
            click_id = active_dropdown.pop("click_id", None)
            if configure_id is not None:
                self.root.unbind("<Configure>", configure_id)
            if click_id is not None:
                self.root.unbind("<ButtonPress-1>", click_id)
            active_dropdown["window"] = None
            active_dropdown["option_menu"] = None
            if popup.winfo_exists():
                popup.destroy()

        def is_child_of(widget, ancestor):
            while widget is not None:
                if widget is ancestor:
                    return True
                widget = getattr(widget, "master", None)
            return False

        def show_audio_dropdown(option_menu, options, x, y):
            if active_dropdown["window"] is not None:
                if active_dropdown["option_menu"] is option_menu:
                    close_dropdown()
                    return
                close_dropdown()

            popup = tk.Toplevel(self.root)
            popup.withdraw()
            popup.overrideredirect(True)
            popup.configure(bg=BG_SEPARATOR)
            popup_frame = tk.Frame(popup, bg=BG_INPUT)
            popup_frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

            menu_buttons = []

            def select_option(value):
                close_dropdown()
                option_menu._dropdown_callback(value)

            for value in options:
                menu_button = tk.Button(
                    popup_frame,
                    text=value,
                    command=lambda selected=value: select_option(selected),
                    bg=BG_INPUT,
                    fg=FG_PRIMARY,
                    activebackground=BG_SELECTION,
                    activeforeground=FG_SELECTED,
                    font=option_menu._dropdown_menu.cget("font"),
                    anchor="w",
                    bd=0,
                    highlightthickness=0,
                    padx=8,
                    pady=5,
                    relief=tk.FLAT,
                )
                menu_button.pack(fill=tk.X)
                menu_button.bind(
                    "<Enter>",
                    lambda _event, button=menu_button: button.configure(
                        bg=BG_SELECTION, fg=FG_SELECTED
                    ),
                )
                menu_button.bind(
                    "<Leave>",
                    lambda _event, button=menu_button: button.configure(
                        bg=BG_INPUT, fg=FG_PRIMARY
                    ),
                )
                menu_buttons.append(menu_button)

            popup.update_idletasks()
            width = option_menu.winfo_width()
            height = sum(button.winfo_reqheight() for button in menu_buttons) + 2

            def position_popup(_event=None):
                if popup.winfo_exists() and option_menu.winfo_exists():
                    popup.geometry(
                        f"{width}x{height}+{option_menu.winfo_rootx()}+"
                        f"{option_menu.winfo_rooty() + option_menu.winfo_height()}"
                    )

            def close_when_clicked_outside(event):
                if (
                    not is_child_of(event.widget, popup)
                    and not is_child_of(event.widget, option_menu)
                ):
                    close_dropdown()

            def close_when_pointer_leaves(_event=None):
                def close_if_outside():
                    if not popup.winfo_exists():
                        return
                    pointer = self.root.winfo_pointerxy()
                    current = self.root.winfo_containing(*pointer)
                    if (
                        not is_child_of(current, popup)
                        and not is_child_of(current, option_menu)
                    ):
                        close_dropdown()

                self.root.after_idle(close_if_outside)

            def close_on_focus_loss(_event):
                popup.after_idle(
                    lambda: close_dropdown()
                    if popup.winfo_exists()
                    and not is_child_of(self.root.focus_get(), popup)
                    else None
                )

            active_dropdown["window"] = popup
            active_dropdown["option_menu"] = option_menu
            active_dropdown["configure_id"] = self.root.bind(
                "<Configure>", position_popup, add="+"
            )
            active_dropdown["click_id"] = self.root.bind(
                "<ButtonPress-1>", close_when_clicked_outside, add="+"
            )
            popup.geometry(f"{width}x{height}+{int(x)}+{int(y)}")
            popup.transient(self.root)
            popup.bind("<Escape>", lambda _event: close_dropdown())
            popup.bind("<FocusOut>", close_on_focus_loss, add="+")
            popup.bind("<Leave>", close_when_pointer_leaves, add="+")
            option_menu.bind("<Leave>", close_when_pointer_leaves, add="+")
            popup.deiconify()
            popup.lift()
            popup.focus_force()

        def update_estimates_for_selection(_value=None):
            selected = audio_settings.AudioSettings(
                **{key: dropdown_vars[key].get()
                   for key in audio_settings.DROPDOWN_KEYS}
            )
            selected_values = {
                key: getattr(selected, key)
                for key in audio_settings.DROPDOWN_KEYS
            }
            if not audio_settings.save(selected_values):
                messagebox.showerror(
                    "Could Not Save Audio Settings",
                    "The audio settings could not be saved. The previous "
                    "settings have been restored.",
                    parent=self.root,
                )
                for key, value in last_saved_values.items():
                    dropdown_vars[key].set(value)
                return

            last_saved_values.update(selected_values)
            self.size_render_settings = selected.render_settings()
            self.refresh_size_estimates()

        def _on_bit_depth_change(*_args):
            # Grey out Dither exactly like Ableton does at 32-bit.
            disabled = str(dropdown_vars["bit_depth"].get()) == "32"
            dropdown_widgets["dither"].configure(
                state="disabled" if disabled else "normal"
            )

        for index, key in enumerate(audio_settings.DROPDOWN_KEYS):
            cell = tk.Frame(settings_grid, bg=BG_WINDOW)
            cell.grid(
                row=index // 4, column=index % 4, sticky="ew",
                padx=3, pady=2,
            )
            tk.Label(
                cell, text=labels[key], bg=BG_WINDOW, fg=FG_PRIMARY,
                font=FONT_UI, anchor="w",
            ).pack(fill=tk.X, pady=(0, 1))
            var = tk.StringVar(value=current_values[key])
            dropdown_vars[key] = var
            options = audio_settings.OPTIONS[key]
            widget = ctk.CTkOptionMenu(
                cell,
                variable=var,
                values=options,
                command=update_estimates_for_selection,
                width=120,
                height=28,
                fg_color=BG_BUTTON,
                button_color=BG_INPUT,
                button_hover_color=BG_SELECTION,
                text_color=FG_PRIMARY,
                dropdown_fg_color=BG_INPUT,
                dropdown_hover_color=BG_SELECTION,
                dropdown_text_color=FG_PRIMARY,
                font=dropdown_font,
                dropdown_font=dropdown_font,
                corner_radius=4,
                dynamic_resizing=False,
                anchor="w",
            )
            self._style_option_menu_selection(widget)
            widget.pack(fill=tk.X)
            dropdown_widgets[key] = widget
            # self.bind_status_tooltip(
            #     cell, AUDIO_SETTING_TOOLTIPS.get(key, f"Set {labels[key]}.")
            # )
            popup = widget._dropdown_menu
            popup.open = (
                lambda x, y, option_menu=widget, values=options:
                show_audio_dropdown(option_menu, values, x, y)
            )

            if key == "bit_depth":
                var.trace_add("write", _on_bit_depth_change)

        # Apply the initial disabled state for the loaded bit depth.
        _on_bit_depth_change()
