"""
External (non-pip) runtime dependencies and per-machine setup choices.

Tesseract is looked up in this order and the result is cached in
config.json ("tesseract_cmd"):
    1. config.json "tesseract_cmd" (if it still exists)
    2. <root>/vendor/Tesseract-OCR/tesseract.exe
    3. tesseract on PATH
    4. the default install folders (Program Files, LocalAppData)

CLI (used by setup.ps1):
    python system_deps.py --setup   detect + save Tesseract; fail if missing
    python system_deps.py --check   report status; exit code 1 if anything is missing
    python system_deps.py --ableton-exe X --export-path Y --project-dir Z ...
                                    write setup choices into config.json
"""

import argparse
import logging
import os
import shutil
import sys

import app_config
from paths import VENDOR_DIR

logger = logging.getLogger("AutoExport")

TESSERACT_KEY = "tesseract_cmd"
_tesseract_cmd = None


def _tesseract_candidates():
    yield app_config.get(TESSERACT_KEY)
    yield os.path.join(VENDOR_DIR, "Tesseract-OCR", "tesseract.exe")
    yield shutil.which("tesseract")
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            yield os.path.join(base, "Tesseract-OCR", "tesseract.exe")
            yield os.path.join(base, "Programs", "Tesseract-OCR", "tesseract.exe")


def find_tesseract():
    for path in _tesseract_candidates():
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return None


def configure_tesseract():
    """Point pytesseract at tesseract.exe. Returns the path, or None if not found."""
    global _tesseract_cmd
    if _tesseract_cmd:
        return _tesseract_cmd
    path = find_tesseract()
    if not path:
        logger.warning("Tesseract OCR not found - install it or set tesseract_cmd in data/config.json")
        return None
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = path
    if app_config.get(TESSERACT_KEY) != path:
        if not app_config.set(TESSERACT_KEY, path):
            raise OSError(f"Could not save Tesseract path to {app_config.CONFIG_FILE}")
    _tesseract_cmd = path
    return path


def check(skip_osc=False):
    """Return [(name, ok, detail)] for every external dependency."""
    import ableton_build
    import osc_installer
    results = []

    tess = configure_tesseract()
    results.append(("Tesseract OCR", bool(tess), tess or "not found"))

    ableton_build.ensure_detected()
    exe = ableton_build.get_build()["exe"]
    results.append(("Ableton Live 12", bool(exe), exe or "not configured"))

    if not skip_osc:
        lib = ableton_build.user_library_path()
        if lib:
            ok = osc_installer.is_up_to_date(lib)
            results.append(("AbletonOSC", ok, "installed in " + lib if ok else "not installed / outdated in " + lib))
        else:
            results.append(("AbletonOSC", False, "Ableton User Library not found; open Live once"))

    export = app_config.get("export_path") or ""
    results.append(("Export folder", bool(export) and os.path.isdir(export), export or "not set"))
    projects = app_config.get("project_directories") or []
    missing = [path for path in projects if not os.path.isdir(path)]
    results.append(("Project folders", bool(projects) and not missing,
                    "missing: " + ", ".join(missing) if missing else
                    f"{len(projects)} configured" if projects else "not set"))
    return results


def configure(ableton_exe=None, ableton_beta_exe=None, export_path=None, project_dirs=None, overwrite=False):
    """Write setup choices into config.json. Existing values are kept unless overwrite=True."""
    import ableton_build
    messages = []
    ableton = ableton_build.ensure_detected()
    values = {}
    for key, exe in ((ableton_build.RELEASE, ableton_exe), (ableton_build.BETA, ableton_beta_exe)):
        if not exe:
            continue
        info, err = ableton_build.resolve_choice(exe, beta=(key == ableton_build.BETA))
        if info:
            ableton[f"{key}_exe"] = info["exe"]
            values[ableton_build.CONFIG_KEY] = ableton
            messages.append(f"Ableton {key}: {info['exe']}")
        else:
            raise ValueError(f"Ableton {key}: {err}")
    if export_path and (overwrite or not app_config.get("export_path")):
        values["export_path"] = os.path.abspath(export_path)
        messages.append(f"Export folder: {values['export_path']}")
    if project_dirs:
        current = app_config.get("project_directories") or []
        merged = []
        seen = set()
        for directory in (list(project_dirs) if overwrite else current + list(project_dirs)):
            path = os.path.abspath(directory)
            if not os.path.isdir(path):
                raise ValueError(f"Project folder does not exist: {path}")
            key = os.path.normcase(path)
            if key not in seen:
                merged.append(path)
                seen.add(key)
        values["project_directories"] = merged
        messages.append(f"Project folders: {len(merged)}")
    if "export_path" in values:
        os.makedirs(values["export_path"], exist_ok=True)
    if values and not app_config.update(values):
        raise OSError(f"Could not save setup choices to {app_config.CONFIG_FILE}")
    return messages


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--setup", action="store_true", help="detect and save Tesseract; fail if missing")
    parser.add_argument("--check", action="store_true", help="report dependency status")
    parser.add_argument("--skip-osc", action="store_true", help="omit AbletonOSC from the dependency check")
    parser.add_argument("--ableton-exe", help="Live 12 exe, or a folder to search")
    parser.add_argument("--ableton-beta-exe", help="Live 12 Beta exe, or a folder to search")
    parser.add_argument("--export-path", help="default export folder (created if missing)")
    parser.add_argument("--project-dir", action="append", help="project root folder (repeatable)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing config values")
    args = parser.parse_args(argv)

    configuring = any((args.ableton_exe, args.ableton_beta_exe, args.export_path, args.project_dir))
    if configuring:
        try:
            messages = configure(args.ableton_exe, args.ableton_beta_exe, args.export_path,
                                 args.project_dir, args.overwrite)
        except (OSError, ValueError) as error:
            print(f"Configuration failed: {error}", file=sys.stderr)
            return 1
        for line in messages:
            print(line)

    if args.setup:
        tess = configure_tesseract()
        print("Tesseract:", tess or "NOT FOUND")
        if not tess:
            return 1

    if args.check or not (args.setup or configuring):
        all_ok = True
        for name, ok, detail in check(skip_osc=args.skip_osc):
            all_ok &= ok
            print(f"[{'OK' if ok else '!!'}] {name}: {detail}")
        return 0 if all_ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
