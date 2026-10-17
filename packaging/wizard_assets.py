"""Generate Inno's dark wizard artwork from the supplied application icon."""

from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "wizard"
OUTPUT.mkdir(parents=True, exist_ok=True)
with Image.open(ROOT / "packaging" / "autoexport.ico") as source:
    icon = source.convert("RGBA")
for name, size, icon_size in [
    ("large.bmp", (164, 314), 128),
    ("small.bmp", (55, 55), 48),
]:
    canvas = Image.new("RGB", size, "#1e1e1e")
    resized = icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    canvas.paste(resized, ((size[0] - icon_size) // 2, (size[1] - icon_size) // 2), resized)
    canvas.save(OUTPUT / name)
