"""Launch a visible source worker or a background installed worker."""

import os
import sys
import subprocess
from pathlib import Path


def export_creationflags():
    """Give the worker its own console in every mode.

    The console is the export's progress display and its cancel button: it
    shows live output (queue, abletoolz missing-sample pass, automation steps)
    and Ctrl+C there interrupts only the worker, leaving the GUI window open.
    """
    return subprocess.CREATE_NEW_CONSOLE


def export_command(task_file):
    if getattr(sys, "frozen", False):
        worker = Path(sys.executable).with_name("AutoExportWorker.exe")
        if not worker.is_file():
            raise FileNotFoundError(f"Export worker is missing: {worker}. Reinstall AutoExport.")
        return [str(worker), "export", str(task_file)]
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        console = python.with_name("python.exe")
        if console.is_file():
            python = console
    script = Path(__file__).resolve().with_name("orchestration.py")
    return [os.fspath(python), os.fspath(script), str(task_file)]
