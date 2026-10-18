"""Offline first-run configuration for the installed app."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import ableton_build as ableton_build
import app_config as app_config
import osc_installer as osc_installer
import system_deps as system_deps
from gui_theme import style_setup


def apply_choices(live_exe, user_library, export_folder, project_folder):
    """Validate choices, install OSC and save machine-local configuration."""
    info, error = ableton_build.validate_exe(live_exe)
    if not info:
        raise ValueError(error)
    if not os.path.isdir(user_library):
        raise ValueError("Select an existing Ableton User Library folder.")
    if not export_folder:
        raise ValueError("Choose an export folder.")
    if not os.path.isdir(project_folder):
        raise ValueError("Select an existing folder containing your Ableton projects.")
    if not system_deps.configure_tesseract():
        raise RuntimeError("Bundled Tesseract OCR is missing. Reinstall AutoExport.")
    os.makedirs(export_folder, exist_ok=True)
    ok, message = osc_installer.install(user_library)
    if not ok:
        raise OSError(message)
    build = ableton_build.BETA if info["is_beta"] else ableton_build.RELEASE
    config = app_config.get(ableton_build.CONFIG_KEY) or {}
    config.update({"build": build, f"{build}_exe": info["exe"]})
    projects = [os.path.abspath(project_folder)]
    for existing in app_config.get("project_directories") or []:
        if os.path.isdir(existing) and os.path.normcase(os.path.abspath(existing)) not in {
            os.path.normcase(path) for path in projects
        }:
            projects.append(os.path.abspath(existing))
    if not app_config.update({
        ableton_build.CONFIG_KEY: config,
        "user_library_path": os.path.abspath(user_library),
        "export_path": os.path.abspath(export_folder),
        "project_directories": projects,
        "installer_configured": True,
    }):
        raise OSError(f"Could not save configuration to {app_config.CONFIG_FILE}")


def main():
    ableton_build.ensure_detected()
    root = tk.Tk()
    style_setup(root)
    root.title("AutoExport - First-run Setup")
    root.resizable(False, False)
    panel = ttk.Frame(root, padding=20)
    panel.grid()
    ttk.Label(panel, text="Configure AutoExport", font=("Segoe UI", 14, "bold"), style="Title.TLabel").grid(
        row=0, column=0, columnspan=3, sticky="w"
    )
    ttk.Label(
        panel,
        text="Requires English Ableton Live 12. Close Live before installing AbletonOSC.\n"
        "Detected paths are filled in below. Browse to any path that is missing or incorrect.\n"
        "These choices are saved on this machine; no downloads or Python installation are needed.",
    ).grid(row=1, column=0, columnspan=3, pady=(8, 16), sticky="w")
    projects = app_config.get("project_directories") or []
    initial = [
        ("Ableton Live 12 executable", ableton_build.get_build()["exe"] or ""),
        ("Ableton User Library", ableton_build.user_library_path() or ""),
        ("Export folder", app_config.get("export_path") or ""),
        ("Project folder", projects[0] if projects else ""),
    ]
    fields = []

    def browse(index):
        if index == 0:
            path = filedialog.askopenfilename(
                parent=root, title="Choose Ableton Live 12",
                filetypes=[("Ableton Live", "Ableton Live*.exe")],
            )
        else:
            path = filedialog.askdirectory(parent=root, title=initial[index][0])
        if path:
            fields[index].set(os.path.normpath(path))

    for index, (label, value) in enumerate(initial):
        variable = tk.StringVar(root, value=value)
        fields.append(variable)
        ttk.Label(panel, text=label).grid(row=index + 2, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Entry(panel, textvariable=variable, width=60).grid(row=index + 2, column=1, pady=5)
        ttk.Button(panel, text="Browse...", command=lambda i=index: browse(i)).grid(
            row=index + 2, column=2, padx=(10, 0), pady=5
        )
    completed = False

    def save():
        nonlocal completed
        try:
            apply_choices(*(field.get().strip().strip('"') for field in fields))
        except (OSError, ValueError, RuntimeError) as error:
            messagebox.showerror("Setup Incomplete", str(error), parent=root)
            return
        messagebox.showinfo(
            "AutoExport Configured",
            "AbletonOSC is installed.\n\n"
            "In Live: Settings > Link, Tempo & MIDI > Control Surface > AbletonOSC.\n"
            "Restart Live to load it. Add more project folders from AutoExport's File menu.",
            parent=root,
        )
        completed = True
        root.destroy()

    ttk.Button(panel, text="Save and Install AbletonOSC", command=save, style="Save.TButton").grid(
        row=6, column=1, sticky="e", pady=(16, 0)
    )
    ttk.Button(panel, text="Cancel", command=root.destroy).grid(
        row=6, column=2, sticky="e", pady=(16, 0)
    )
    root.mainloop()
    return 0 if completed else 2
