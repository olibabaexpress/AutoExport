# Self-contained Windows installer

Run `.\build-installer.ps1` from the project root on Windows x64.
Build requirements are Python 3.11-3.13 x64, Inno Setup 6, and
Tesseract OCR with English language data. These are needed only on the build
machine. Dependencies are installed into an isolated `.build-venv`.

Optional arguments: `-Python`, `-TesseractDir`, `-InnoCompiler`, `-Version`.
The installer is generated under `dist\installer`. Give the installer EXE to
the other machine; do not transfer virtual environments or machine settings.

## Installation

The Inno wizard installs per-user, without administrator permissions, under
`%LOCALAPPDATA%\AutoExport` by default. Choose another writable folder if desired.
It bundles Python, Python packages, Tesseract and its English OCR data, and the
patched AbletonOSC. End-user installation works offline.

After copying files, a configuration window detects Live and its User Library
from Live's preferences. Every path has a Browse button, including the User
Library when automatic detection fails. Close Live before installing OSC.
Select an export folder and an existing project folder. More project roots can
be added later from the app. A manually selected User Library is saved as a
fallback; valid Live preferences remain the primary source.

Enable **AbletonOSC** in Live's **Settings > Link, Tempo & MIDI > Control
Surface**, then restart Live. Live 12 with the **English UI** and the project's
plugins/sample dependencies must already be installed separately.

Use **Configure AutoExport** in the Start menu to rerun configuration. Cancelling
leaves the application installed but unconfigured; first launch asks again.
Silent installation copies files only; first interactive launch configures it.

## Layout and updates

`AutoExport.exe` is the windowed launcher; `AutoExportWorker.exe` is the background
export worker. The worker opens its own console when an export starts: it shows
live progress (queue, abletoolz missing-sample pass, automation steps) and Ctrl+C
there cancels the export without closing the GUI window. Console output is mirrored
into `LOGS\Worker_*.log`, and the full DEBUG-level trace stays in `LOGS`. `_internal`
contains Python, libraries, bundled vendor resources
and third-party notices. `data`, `LOGS` and `debug` are created alongside the EXEs.
Upgrades preserve them. Uninstall deletes AutoExport's `data`, `LOGS` and `debug`
directories under its installation, then removes the empty installation folder.
It does not delete exported audio, unrelated files placed in the install folder,
external state redirected with `AUTOEXPORT_HOME`, or AbletonOSC from Live.
`AUTOEXPORT_HOME` can override writable state, but never relocates
bundled resources. Keep the entire installed directory together.

Build output includes dependency license files, a package-version inventory,
Python's license, Tesseract's documentation and AbletonOSC's license. Review all
redistribution requirements (including Tesseract's native DLL dependencies)
before public distribution. The installer is unsigned; Windows may display a
SmartScreen warning until a release is signed.

## Verification

The build runs an offline frozen-runtime test before compiling Inno: imports the
GUI, initializes Tk drag-and-drop, exercises bundled Tesseract OCR, starts a
multiprocessing child, and verifies the OSC resources. It uses temporary machine
state and does not drive or modify Ableton.

Final release acceptance still needs a clean Windows machine with no Python:
install, configure, enable OSC, then export one small real project. Also test
different display scaling settings. GUI path entry via simulated keystrokes is
not yet guaranteed to support non-ASCII project/export paths.

Render completion waits for stable, unlocked WAV output before post-processing.
Renames use same-volume atomic file operations with bounded retries for Windows
sharing violations, avoiding copy-then-delete duplicates while Live or an
indexer still holds a render open.

The app and first-run configuration share the dark charcoal/green GUI palette
and the supplied icon. Inno's pages and artwork use the same palette; standard
Windows wizard buttons, title bars and system dialogs retain native styling.
