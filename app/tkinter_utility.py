
import win32gui
import win32process
import win32con
import psutil

def query_windows():
    results = []

    def callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return

        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return

        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        try:
            process = psutil.Process(pid)
            process_name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "Unknown"

        # Get outer window dimensions
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top

        # Get client area dimensions (excludes borders/title bar)
        client_left, client_top, client_right, client_bottom = (
            win32gui.GetClientRect(hwnd)
        )
        client_width = client_right - client_left
        client_height = client_bottom - client_top

        results.append({
            "hwnd": hwnd,
            "pid": pid,
            "process": process_name,
            "title": title,
            "x": left,
            "y": top,
            "width": width,
            "height": height,
            "client_width": client_width,
            "client_height": client_height,
        })

    win32gui.EnumWindows(callback, None)
    return results


if __name__ == "__main__":
    windows = query_windows()

    for w in windows:
        print(
            f"PID={w['pid']:<7} "
            f"HWND={w['hwnd']:<10} "
            f"{w['width']}x{w['height']} "
            f"at ({w['x']}, {w['y']}) | "
            f"{w['process']} | {w['title']}"
        )
