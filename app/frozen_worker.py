"""Background entry point for exports and offline bundle diagnostics."""

import argparse
import json
import multiprocessing
import os
import sys



def self_test(output=None):
    import concurrent.futures
    import tkinter
    from pathlib import Path
    from PIL import Image, ImageDraw, ImageFont
    from tkinterdnd2 import TkinterDnD
    import pytesseract
    import GUI
    import system_deps
    from paths import DATA_DIR, VENDOR_DIR
    root = TkinterDnD.Tk()
    root.withdraw()
    root.update()
    root.destroy()
    if not system_deps.configure_tesseract():
        raise RuntimeError("Tesseract OCR missing from bundle")
    image = Image.new("RGB", (600, 100), "white")
    font = ImageFont.truetype("arial.ttf", 42)
    ImageDraw.Draw(image).text((10, 10), "AutoExport 123", font=font, fill="black")
    text = pytesseract.image_to_string(image, config="--psm 7").strip()
    if "123" not in text or "AutoExport" not in text:
        raise RuntimeError(f"Bundled OCR smoke test failed: {text!r}")
    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:
        if pool.submit(pow, 2, 3).result(timeout=30) != 8:
            raise RuntimeError("Multiprocessing worker failed")
    # Tk drag-and-drop uses STA COM; import the console automation only after closing Tk.
    import orchestration
    result = {"frozen": bool(getattr(sys, "frozen", False)), "ocr": text,
              "tk": tkinter.TkVersion, "data_dir": DATA_DIR,
              "osc_bundled": (Path(VENDOR_DIR) / "AbletonOSC" / "__init__.py").is_file(),
              "multiprocessing": True, "gui_imported": bool(GUI),
              "export_imported": bool(orchestration)}

    # Validate that the database path is writable (catch permission issues early)
    from create_db_patch import DEFAULT_DB_PATH
    try:
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        test_file = DEFAULT_DB_PATH.parent / ".write_test"
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        result["db_writable"] = True
    except OSError as e:
        result["db_writable"] = False
        result["db_write_error"] = str(e)
        # logger.error(f"Database path not writable: {e}")
        # Don't raise - just report the issue

    if not result["osc_bundled"]:
        raise RuntimeError("AbletonOSC missing from bundle")
    if output:
        Path(output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0


class _TeeStream:
    """Console stream that mirrors every write into the worker log file.

    The compiled worker now has a real console (progress display + Ctrl+C
    cancel target), but Worker_*.log must keep capturing stdout/stderr so
    failures remain diagnosable after the console window is gone. Writes to a
    closed console are swallowed - the log copy is the important one then.
    """

    def __init__(self, console, log_stream):
        self._console = console
        self._log = log_stream

    def write(self, data):
        for stream in (self._console, self._log):
            try:
                stream.write(data)
            except (OSError, ValueError):
                pass
        try:
            self._console.flush()
        except (OSError, ValueError):
            pass
        return len(data)

    def flush(self):
        for stream in (self._console, self._log):
            try:
                stream.flush()
            except (OSError, ValueError):
                pass

    def reconfigure(self, **kwargs):
        reconfigure = getattr(self._console, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(**kwargs)

    def __getattr__(self, name):
        # fileno/isatty/encoding/... belong to the console stream.
        return getattr(self._console, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "self-test", "check"))
    parser.add_argument("--output", help="self-test result file")
    args, remaining = parser.parse_known_args()
    if args.mode == "self-test":
        if remaining:
            parser.error(f"Unexpected arguments: {remaining}")
        return self_test(args.output)
    if args.mode == "check":
        import system_deps
        return system_deps.main(["--check", *remaining])
    sys.argv = [sys.argv[0], *remaining]
    import orchestration
    try:
        return orchestration.main()
    except KeyboardInterrupt:
        orchestration.logger.error("Export interrupted by user.")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from datetime import datetime
    from pathlib import Path
    import traceback
    from paths import LOG_DIR
    log = Path(LOG_DIR) / f"Worker_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log"
    with log.open("a", encoding="utf-8", buffering=1) as stream:
        if sys.stdout is None:
            # Windowed build: the console is gone, so this log file is the
            # only place output can go.
            sys.stdout = stream
        else:
            # Console build: show progress live AND keep the log copy, so
            # Worker_*.log still explains failures after the window closes.
            sys.stdout = _TeeStream(sys.stdout, stream)
        if sys.stderr is None:
            sys.stderr = stream
        else:
            sys.stderr = _TeeStream(sys.stderr, stream)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        try:
            exit_code = main()
        except Exception:
            traceback.print_exc()
            exit_code = 1
        sys.exit(exit_code)
