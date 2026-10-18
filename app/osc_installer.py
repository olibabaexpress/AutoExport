"""
Install the patched AbletonOSC remote script into Ableton's User Library.

AutoExport relies on a patched AbletonOSC (master-track device handlers etc.),
vendored at <root>/vendor/AbletonOSC. Live loads remote scripts from
<User Library>/Remote Scripts/<name>, and the User Library location is read
from Live 12's Library.cfg (see ableton_build.user_library_path).

After installing, AbletonOSC still has to be selected once as a Control
Surface in Live (Settings > Link, Tempo & MIDI) and Live restarted.

CLI (used by the installer):
    python osc_installer.py                  install into the detected User Library
    python osc_installer.py --user-library P install into P
    python osc_installer.py --check          exit 0 if up to date, 1 otherwise
"""

import argparse
import filecmp
import os
import shutil
import sys

import ableton_build as ableton_build
from paths import VENDOR_DIR

SCRIPT_NAME = "AbletonOSC"
VENDORED_DIR = os.path.join(VENDOR_DIR, SCRIPT_NAME)
# Left alone in the target: runtime output, not part of the script.
_PRESERVE = {"logs"}
_SKIP = {"__pycache__", ".git", "tests", "logs"}


def target_dir(user_library=None):
    user_library = user_library or ableton_build.user_library_path()
    if not user_library:
        return None
    return os.path.join(user_library, "Remote Scripts", SCRIPT_NAME)


def _source_files():
    for dirpath, dirnames, files in os.walk(VENDORED_DIR):
        dirnames[:] = [d for d in dirnames if d not in _SKIP]
        for name in files:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            yield os.path.relpath(full, VENDORED_DIR)


def outdated_files(user_library=None):
    """Relative paths that are missing or differ in the installed copy.

    Returns None if the vendored copy or the User Library can't be found.
    """
    dest = target_dir(user_library)
    if not dest or not os.path.isdir(VENDORED_DIR):
        return None
    stale = []
    for rel in _source_files():
        installed = os.path.join(dest, rel)
        if not os.path.isfile(installed) or not filecmp.cmp(
            os.path.join(VENDORED_DIR, rel), installed, shallow=False
        ):
            stale.append(rel)
    return stale


def is_up_to_date(user_library=None):
    stale = outdated_files(user_library)
    return stale is not None and not stale


def install(user_library=None):
    """Copy the vendored AbletonOSC into the User Library. Returns (ok, message)."""
    if not os.path.isdir(VENDORED_DIR):
        return False, f"Vendored AbletonOSC not found at:\n{VENDORED_DIR}"
    dest = target_dir(user_library)
    if not dest:
        return False, "Could not find the Ableton User Library. Choose it manually."
    if not os.path.isdir(os.path.dirname(os.path.dirname(dest))):
        return False, f"User Library folder does not exist:\n{os.path.dirname(os.path.dirname(dest))}"
    try:
        if os.path.isdir(dest):
            for name in os.listdir(dest):
                if name in _PRESERVE:
                    continue
                path = os.path.join(dest, name)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
        os.makedirs(dest, exist_ok=True)
        count = 0
        for rel in _source_files():
            out = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(VENDORED_DIR, rel), out)
            count += 1
    except OSError as e:
        return False, f"Could not install AbletonOSC into:\n{dest}\n\n{e}"
    return True, f"Installed AbletonOSC ({count} files) into:\n{dest}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install the patched AbletonOSC remote script")
    parser.add_argument("--user-library", help="User Library folder (default: read from Live 12's Library.cfg)")
    parser.add_argument("--check", action="store_true", help="only report whether the installed copy is current")
    args = parser.parse_args(argv)

    if args.check:
        stale = outdated_files(args.user_library)
        if stale is None:
            print("AbletonOSC: User Library or vendored copy not found")
            return 1
        if stale:
            print(f"AbletonOSC: {len(stale)} file(s) missing or outdated in {target_dir(args.user_library)}")
            return 1
        print(f"AbletonOSC: up to date in {target_dir(args.user_library)}")
        return 0

    ok, message = install(args.user_library)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
