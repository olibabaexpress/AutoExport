
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

import ableton_build
import osc_installer

from gui_theme import (
    BG_WINDOW,
    BG_BUTTON,
    BG_INPUT,
    BG_SELECTION,
    FG_PRIMARY,
    FG_SELECTED,
    FONT_HEADER,
)


class AbletonVersionMixin:
    """Build and manage the Ableton version selector."""
    def _build_ableton_version_menu(self, parent):
        frame = tk.LabelFrame(
            parent,
            text="Ableton Version",
            font=FONT_HEADER,
            bg=BG_WINDOW,
            fg=FG_PRIMARY,
            bd=1,
            relief=tk.GROOVE,
            highlightthickness=0,
        )
        frame.pack(fill=tk.X, padx=5, pady=(0, 4))
        self.ableton_version_menu = ctk.CTkOptionMenu(
            frame,
            values=["Not configured"],
            command=self.select_ableton_version,
            width=160, #made it smaller
            height=28,
            fg_color=BG_BUTTON,
            button_color=BG_INPUT,
            button_hover_color=BG_SELECTION,
            text_color=FG_PRIMARY,
            dropdown_fg_color=BG_INPUT,
            dropdown_hover_color=BG_SELECTION,
            dropdown_text_color=BG_SELECTION,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            dropdown_font=ctk.CTkFont(family="Segoe UI", size=12),
            corner_radius=4,
            dynamic_resizing=False,
            anchor="w",
        )
        self._style_option_menu_selection(self.ableton_version_menu)
        self.ableton_version_menu.pack(fill=tk.X, padx=3, pady=2)
        self.bind_status_tooltip(
            frame,
            "Choose which installed Ableton Live version is used to export.",
        )

    @staticmethod
    def _style_option_menu_selection(option_menu):
        """Use inverse text color on highlighted items in the dropdown."""
        dropdown_menu = option_menu._dropdown_menu
        dropdown_menu.configure(activeforeground=FG_SELECTED)
        if not hasattr(option_menu, "bind"):
            return

        def arrow_color(color):
            option_menu._canvas.itemconfigure("dropdown_arrow", fill=color)

        def on_enter(event):
            option_menu._on_enter(event)
            arrow_color(FG_SELECTED)

        def close_menu_if_outside(_event=None):
            pointer = option_menu.winfo_pointerxy()
            current = option_menu.winfo_containing(*pointer)
            inside_dropdown = False
            inside_option = False
            while current is not None:
                if current is dropdown_menu:
                    inside_dropdown = True
                    break
                if current is option_menu:
                    inside_option = True
                    break
                current = getattr(current, "master", None)
            if inside_option or inside_dropdown:
                return True
            if dropdown_menu.winfo_viewable():
                dropdown_menu.unpost()
            return False

        pending_close = {"after_id": None}

        def cancel_pending_close(_event=None):
            after_id = pending_close["after_id"]
            if after_id is not None:
                try:
                    option_menu.after_cancel(after_id)
                except tk.TclError:
                    pass
                pending_close["after_id"] = None

        def on_dropdown_enter(_event=None):
                cancel_pending_close()

        def on_leave(_event):
            def close_after_buffer():
                pending_close["after_id"] = None
                if close_menu_if_outside():
                    return
                option_menu._on_leave()
                arrow_color(
                    option_menu._apply_appearance_mode(option_menu._text_color)
                )

            cancel_pending_close()
            pending_close["after_id"] = option_menu.after(
                500, close_after_buffer
            )

        targets = [
            option_menu,
            getattr(option_menu, "_canvas", None),
            getattr(option_menu, "_text_label", None),
        ]
        for target in targets:
            if target is None:
                continue
            target.bind("<Enter>", on_enter, add="+")
            target.bind("<Leave>", on_leave, add="+")
        if hasattr(dropdown_menu, "bind"):
            dropdown_menu.bind("<Enter>", on_dropdown_enter, add="+")
            dropdown_menu.bind(
                "<Leave>",
                on_leave,
                add="+",
            )

    def _ableton_build_labels(self):
        labels = {}
        for key in ableton_build.installed_builds():
            build = ableton_build.get_build(key)
            title = build["window_title"]
            edition = title.replace("Ableton Live 12", "", 1).strip()
            version = build.get("version")
            if not version:
                version = ableton_build.detect_version(key).replace(" BETA", "")
            kind = f" {edition}" if edition else ""
            if build["is_beta"] and "beta" not in edition.casefold():
                kind += " Beta"
            labels[key] = f"Live{kind} {version}".strip()
        return labels

    def update_ableton_version_menu(self):
        """Refresh detected Live choices and mark the active build in its menu."""
        if not getattr(self, "ableton_version_menu", None):
            return
        labels = self._ableton_build_labels()
        selected = ableton_build.load_selected_build()
        self._ableton_version_options = {}
        options = []
        for key, label in labels.items():
            display = label
            options.append(display)
            self._ableton_version_options[display] = key
        if not options:
            options = ["Not configured"]
            self.ableton_version_menu.configure(values=options, state="disabled")
        else:
            self.ableton_version_menu.configure(values=options, state="normal")
        self.ableton_version_menu.set(
            next(
                (value for value, key in self._ableton_version_options.items()
                 if key == selected),
                options[0],
            )
        )

    def select_ableton_version(self, display):
        build_key = getattr(self, "_ableton_version_options", {}).get(display)
        if build_key is None:
            return
        ableton_build.save_selected_build(build_key)
        self.update_ableton_version_menu()

    def _prompt_for_ableton_exe(self, build_key):
        """Ask for a Live 12 exe (or a folder to search) for a build slot.

        Returns True if a valid exe was stored.
        """
        beta = build_key == ableton_build.BETA
        kind = "Ableton Live 12 Beta" if beta else "Ableton Live 12"
        current = ableton_build.get_exe(build_key)
        initial = os.path.dirname(current) if current else os.environ.get("PROGRAMDATA", "C:\\")
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"Select the {kind} program (.exe) - Cancel to choose a folder instead",
            initialdir=initial,
            filetypes=[("Ableton Live", "Ableton Live*.exe"), ("Programs", "*.exe")],
        )
        if not path:
            path = filedialog.askdirectory(
                parent=self.root,
                title=f"Select a folder containing {kind} (the newest version found is used)",
                initialdir=initial,
            )
        if not path:
            return False
        path = os.path.normpath(path)

        info, err = ableton_build.resolve_choice(path, beta)
        if not info:
            messagebox.showerror("Ableton Live Not Found", err, parent=self.root)
            return False
        if info["is_beta"] != beta and not messagebox.askyesno(
            "Different Build Type",
            f"{info['window_title']} ({info['version'] or 'unknown version'}) is a "
            f"{'beta' if info['is_beta'] else 'release'} build.\n\n"
            f"Use it as the {'Beta' if beta else 'release'} build anyway?",
            parent=self.root,
        ):
            return False
        info, err = ableton_build.set_exe(build_key, info["exe"])
        if not info:
            messagebox.showerror("Ableton Live", err, parent=self.root)
            return False

        found_beta = None
        if os.path.isdir(path) and not beta and not ableton_build.is_installed(ableton_build.BETA):
            found_beta, _ = ableton_build.resolve_choice(path, beta=True)
            if found_beta:
                ableton_build.set_exe(ableton_build.BETA, found_beta["exe"])

        self.update_ableton_version_menu()
        msg = f"{'Beta' if beta else 'Release'} build set to:\n{info['exe']}\n\nVersion {info['version'] or 'unknown'}"
        if found_beta:
            msg += f"\n\nAlso found a Beta:\n{found_beta['exe']}"
        messagebox.showinfo("Ableton Live", msg, parent=self.root)
        return True


    def _check_ableton_setup(self):
        """First-run check: make sure some Live 12 exe is configured."""
        if ableton_build.installed_builds():
            return
        if messagebox.askyesno(
            "Ableton Live 12 Not Found",
            "Ableton Live 12 wasn't found in the default install location.\n\n"
            "Locate it now? (You can also do this later from File > Set Ableton Live Exe…)",
            parent=self.root,
        ):
            self._prompt_for_ableton_exe(ableton_build.RELEASE)


    def install_ableton_osc(self):
        """Copy the bundled, patched AbletonOSC into the User Library."""
        user_library = ableton_build.user_library_path()
        if user_library and os.path.isdir(user_library):
            choice = messagebox.askyesnocancel(
                "Install AbletonOSC",
                f"Install AbletonOSC into this User Library?\n\n{user_library}\n\n"
                "Yes = install here, No = choose a different User Library folder.",
                parent=self.root,
            )
            if choice is None:
                return
            if not choice:
                user_library = None
        if not user_library or not os.path.isdir(user_library):
            user_library = filedialog.askdirectory(
                parent=self.root,
                title="Select your Ableton 'User Library' folder",
            )
            if not user_library:
                return
            user_library = os.path.normpath(user_library)
        ok, message = osc_installer.install(user_library)
        if ok:
            import app_config
            if not app_config.set("user_library_path", user_library):
                messagebox.showerror(
                    "Could Not Save User Library",
                    f"AbletonOSC was installed, but its path could not be saved to {app_config.CONFIG_FILE}.",
                    parent=self.root,
                )
                return
            messagebox.showinfo(
                "AbletonOSC Installed",
                message + "\n\nIf this is the first install, select AbletonOSC as a Control Surface "
                "in Live (Settings > Link, Tempo & MIDI). Restart Live to load the update.",
                parent=self.root,
            )
        else:
            messagebox.showerror("AbletonOSC", message, parent=self.root)
