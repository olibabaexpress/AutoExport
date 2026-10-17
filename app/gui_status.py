"""Status bar: hover tooltips, plus live output and a progress bar for jobs."""

import tkinter as tk

import customtkinter as ctk

from gui_theme import ACCENT_ORANGE, BG_INPUT, BG_TOOLBAR, FG_PRIMARY


class StatusMixin:
    _status_message = ""
    _status_tooltip_visible = False
    _status_job_active = False
    status_label = None
    status_progress = None

    def _create_status_bar(self):
        self.status_bar = tk.Frame(self.root, bg=BG_TOOLBAR, height=20)
        self.status_bar.pack(side="bottom", fill="x")
        self._status_message = "Ready"
        self._status_tooltip_visible = False
        self._status_job_active = False

        self.status_progress = ctk.CTkProgressBar(
            self.status_bar,
            width=160,
            height=8,
            corner_radius=2,
            fg_color=BG_INPUT,
            progress_color=ACCENT_ORANGE,
        )
        self.status_label = tk.Label(
            self.status_bar,
            text=self._status_message,
            bg=BG_TOOLBAR,
            fg=FG_PRIMARY,
        )
        self.status_label.pack(side="left", padx=5)

    def set_status(self, message):
        self._status_message = str(message)
        if self.status_label and (
            self._status_job_active or not self._status_tooltip_visible
        ):
            self.status_label.config(text=self._status_message)

    def begin_status_job(self, message, indeterminate=False):
        """Pin the status bar to job output and show the progress bar."""
        self._status_job_active = True
        self._status_tooltip_visible = False
        self.set_status(message)
        if self.status_progress is None:
            return
        self.status_progress.pack(
            side="left", padx=(8, 0), before=self.status_label
        )
        if indeterminate:
            self.status_progress.configure(mode="indeterminate")
            self.status_progress.start()
        else:
            self.status_progress.stop()
            self.status_progress.configure(mode="determinate")
            self.status_progress.set(0)

    def update_status_job(self, message, fraction=None):
        """Show the latest job output; fraction (0-1) moves a determinate bar."""
        self.set_status(message)
        if (
            fraction is not None
            and self.status_progress is not None
            and self.status_progress.cget("mode") == "determinate"
        ):
            self.status_progress.set(max(0.0, min(1.0, fraction)))

    def end_status_job(self, message):
        self._status_job_active = False
        if self.status_progress is not None:
            self.status_progress.stop()
            self.status_progress.pack_forget()
        self.set_status(message)

    def show_status_tooltip(self, message):
        if not getattr(self, "status_label", None) or self._status_job_active:
            return
        self._status_tooltip_visible = True
        self.status_label.config(text=str(message))

    def _clear_status_tooltip(self, _event=None):
        if not getattr(self, "_status_tooltip_visible", False):
            return
        self._status_tooltip_visible = False
        if getattr(self, "status_label", None):
            self.status_label.config(text=self._status_message)

    def bind_status_tooltip(self, widget, message):
        """Show help text in the status bar while the widget (or a child) is hovered."""
        def bind_tree(target):
            target.bind(
                "<Enter>",
                lambda _event: self.show_status_tooltip(message),
                add="+",
            )
            target.bind("<Leave>", self._clear_status_tooltip, add="+")
            for child in target.winfo_children():
                bind_tree(child)

        bind_tree(widget)

               
