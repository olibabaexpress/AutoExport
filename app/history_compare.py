"""Compare the 03:58 VS Code history snapshot against the current file.

Answers: did the user's logger sweep contain edits beyond log levels that
would be lost if we kept only the current file?
"""

import re
from pathlib import Path

HISTORY = Path.home() / "AppData/Roaming/Code/User/History/-38e4755c"
CURRENT = Path("f:/CODES/AUTOEXPORT/app/popup_automation.py")


def load(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def normalise(text):
    """Collapse log-level differences so only real code differences show."""
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        stripped = re.sub(r"logger\.(debug|info|warning|error|exception)",
                          "logger.LEVEL", stripped)
        out.append(stripped)
    return out


def substantive(line):
    """Drop punctuation/boilerplate fragments that inflate line diffs."""
    if re.fullmatch(r"[\)\(\]\[,;:.]*", line):
        return False
    if line in {"logger.LEVEL(", "return False", "return True", "else:",
                "try:", "except Exception as e:", "finally:", "pass",
                "time.sleep(0.5)", "time.sleep(0.2)"}:
        return False
    return True


def main():
    snapshot = HISTORY / "bnVO.py"
    old = load(snapshot)
    cur = load(CURRENT)

    report = []
    report.append(
        f"03:58 snapshot: debug={old.count('logger.debug(')} "
        f"info={old.count('logger.info(')} size={len(old)}"
    )
    report.append(
        f"current file  : debug={cur.count('logger.debug(')} "
        f"info={cur.count('logger.info(')} size={len(cur)}"
    )
    report.append(
        f"snapshot has fix: {'_confirm_save_as_overwrite' in old}"
    )
    report.append(f"current has fix: {'_confirm_save_as_overwrite' in cur}")
    report.append("")

    old_lines = normalise(old)
    cur_lines = normalise(cur)
    only_old = [l for l in old_lines if old_lines.count(l) > cur_lines.count(l)]
    only_cur = [l for l in cur_lines if cur_lines.count(l) > old_lines.count(l)]
    only_old = [l for l in only_old if substantive(l)]
    only_cur = [l for l in only_cur if substantive(l)]

    report.append(f"--- substantive lines ONLY in 03:58 snapshot ({len(only_old)}) ---")
    for line in only_old:
        report.append("  OLD| " + line)

    report.append("")
    report.append(f"--- substantive lines ONLY in current ({len(only_cur)}) ---")
    for line in only_cur:
        report.append("  CUR| " + line)

    Path("f:/CODES/AUTOEXPORT/build/history_compare.txt").write_text(
        "\n".join(report), encoding="utf-8"
    )
    print(f"wrote {len(report)} lines")


if __name__ == "__main__":
    main()
