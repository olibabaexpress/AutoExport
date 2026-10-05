import subprocess
from pathlib import Path

target = Path(__file__).parent / "app" / "GUI.py"

subprocess.Popen(["python", str(target)])