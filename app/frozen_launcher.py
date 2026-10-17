"""Windowed entry point for the self-contained application."""

import multiprocessing
import os
import sys
import traceback


def _redirect_null_streams():
    """Give the windowed (noconsole) build real stdout/stderr streams.

    PyInstaller sets sys.stdout/sys.stderr to None when console=False, so any
    print()/library console write raises AttributeError: 'NoneType' object has
    no attribute 'write' (seen with tqdm during sample indexing). Mirror the
    output into LOGS/exe_console_<ts>.log instead: stray console writes become
    harmless and end up somewhere debuggable.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from datetime import datetime
        from paths import LOG_DIR
        path = os.path.join(
            LOG_DIR, f"exe_console_{datetime.now():%Y%m%d_%H%M%S}.log"
        )
        stream = open(path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    except Exception:
        # Never let diagnostics setup take the app down.
        pass


def main():
    _redirect_null_streams()
    if "--self-test" in sys.argv[1:]:
        import argparse
        from frozen_worker import self_test
        parser = argparse.ArgumentParser()
        parser.add_argument("--self-test", action="store_true")
        parser.add_argument("--output", required=True)
        args = parser.parse_args()
        return self_test(args.output)
    import app_config
    import installer_setup
    if "--configure" in sys.argv[1:]:
        return installer_setup.main()
    if not app_config.get("installer_configured") and installer_setup.main() != 0:
        return 2
    from explorer_launch import gather_launch_paths
    launch_paths = gather_launch_paths()
    if launch_paths is None:
        return 0
    import GUI
    GUI.main(launch_paths)
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        sys.exit(main())
    except Exception:
        from pathlib import Path
        from tkinter import Tk, messagebox
        from paths import LOG_DIR
        error = traceback.format_exc()
        log = Path(LOG_DIR) / "startup-error.log"
        log.write_text(error, encoding="utf-8")
        root = Tk()
        root.withdraw()
        messagebox.showerror("AutoExport Could Not Start", f"{error}\nDetails saved to {log}", parent=root)
        root.destroy()
        sys.exit(1)
