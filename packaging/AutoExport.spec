import os
import shutil
import sys
from pathlib import Path
from importlib import metadata

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).parent
tesseract = Path(os.environ["AUTOEXPORT_TESSERACT_DIR"])
if not (tesseract / "tesseract.exe").is_file() or not (tesseract / "tessdata" / "eng.traineddata").is_file():
    raise RuntimeError("A complete Windows Tesseract installation with English OCR data is required.")

datas = []
datas.append((str(root / "packaging" / "autoexport.ico"), "assets"))
binaries = []
hiddenimports = []
for package in ("CTkMenuBar", "customtkinter", "tkinterdnd2", "live", "abletoolz"):
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

for file in (root / "vendor" / "AbletonOSC").rglob("*"):
    if file.is_file() and not any(part in {".git", "__pycache__", "tests", "logs"} for part in file.parts):
        if file.suffix != ".pyc":
            relative = file.parent.relative_to(root / "vendor")
            datas.append((str(file), str(Path("vendor") / relative)))

datas.append((str(tesseract / "tesseract.exe"), r"vendor\Tesseract-OCR"))
for file in tesseract.glob("*.dll"):
    # Keep OCR's DLL search local to tesseract.exe rather than Python's DLLs.
    datas.append((str(file), r"vendor\Tesseract-OCR"))
for name in ("eng.traineddata", "osd.traineddata"):
    file = tesseract / "tessdata" / name
    if file.is_file():
        datas.append((str(file), r"vendor\Tesseract-OCR\tessdata"))
if (tesseract / "doc").is_dir():
    datas.append((str(tesseract / "doc"), r"vendor\Tesseract-OCR\doc"))
else:
    raise RuntimeError("Tesseract doc directory is required to include its license notices.")

notices = root / "build" / "notices"
notices.mkdir(parents=True, exist_ok=True)
inventory = []
for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
    name = distribution.metadata["Name"]
    inventory.append(f"{name}=={distribution.version}")
    for file in distribution.files or []:
        if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts):
            source = Path(distribution.locate_file(file))
            if source.is_file():
                destination = notices / name / Path(*file.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
(notices / "PACKAGES.txt").write_text("\n".join(inventory), encoding="utf-8")
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if not python_license.is_file():
    raise RuntimeError("Python LICENSE.txt is missing.")
shutil.copy2(python_license, notices / "PYTHON-LICENSE.txt")
datas.append((str(notices), "third-party-notices"))

a = Analysis(
    [str(root / "app" / "frozen_launcher.py"), str(root / "app" / "frozen_worker.py")],
    pathex=[str(root / "app")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["nltk", "pytest", "IPython", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
runtime_scripts = a.scripts[:-2]
gui = EXE(
    pyz, runtime_scripts + [a.scripts[-2]], [], exclude_binaries=True,
    name="AutoExport", console=False, debug=False, icon=str(root / "packaging" / "autoexport.ico"),
)
worker = EXE(
    pyz, runtime_scripts + [a.scripts[-1]], [], exclude_binaries=True,
    name="AutoExportWorker", console=True, debug=False, icon=str(root / "packaging" / "autoexport.ico"),
)
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False, name="AutoExport")
