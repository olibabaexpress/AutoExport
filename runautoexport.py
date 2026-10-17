"""
Start the AutoExport GUI with the project's virtual environment.

Uses .venv (created by setup.ps1) when present, otherwise the Python that
is running this file. pythonw.exe is preferred so no console window opens.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "app" / "GUI.py"


def _python():
    candidates = [
        ROOT / ".venv" / "Scripts" / "pythonw.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
        Path(sys.executable).with_name("pythonw.exe"),
        Path(sys.executable),
    ]
    for exe in candidates:
        if exe.is_file():
            return str(exe)
    return sys.executable


if __name__ == "__main__":
    subprocess.Popen([_python(), str(TARGET)], cwd=str(TARGET.parent), env=os.environ.copy())
