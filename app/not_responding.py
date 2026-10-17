# ableton_monitor.py
import csv, ctypes, os, sys, threading, time
from ctypes import wintypes
import psutil

# ---- constants ----
INTERVAL_S = 1.0
PROBE_TIMEOUT_MS = 500
STALL_S = 30              # seconds without a meaningful disk write while the export window is open
MIN_WRITE_KB = 64         # write KB per tick that counts as progress
KILL_ON_HUNG = False      # flip to True once it has survived a few real exports
STOP_WHEN_DONE = False
MAX_DURATION_S = 6 * 3600
SUSPEND_AT_S = None       # e.g. 60 to induce a hang for testing
SUSPEND_FOR_S = 30

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsHungAppWindow.argtypes = [wintypes.HWND]
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
SMTO_ABORTIFHUNG = 0x0002


def snapshot_windows():
    """Top-level windows as (hwnd, pid, title, class, visible). Never blocks on the target."""
    out = []
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        t = ctypes.create_unicode_buffer(256)
        c = ctypes.create_unicode_buffer(128)
        user32.GetWindowTextW(hwnd, t, 256)
        user32.GetClassNameW(hwnd, c, 128)
        out.append((hwnd, pid.value, t.value, c.value, bool(user32.IsWindowVisible(hwnd))))
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def probe(hwnd):
    res = ctypes.c_size_t()
    t = time.perf_counter()
    ok = user32.SendMessageTimeoutW(hwnd, 0, 0, 0, SMTO_ABORTIFHUNG, PROBE_TIMEOUT_MS, ctypes.byref(res))
    return bool(ok), round((time.perf_counter() - t) * 1000)


def find_ableton():
    best = None
    for p in psutil.process_iter(["name", "memory_info"]):
        n = (p.info["name"] or "").lower()
        if n.startswith("ableton live") and n.endswith(".exe"):
            if best is None or p.info["memory_info"].rss > best.info["memory_info"].rss:
                best = p
    return best


def tree_counters(proc):
    """Summed cpu and write bytes over Ableton plus all descendants, with per-pid cpu."""
    cpu = wr = 0
    per_pid, names = {}, {}
    try:
        procs = [proc] + proc.children(recursive=True)
    except psutil.Error:
        procs = [proc]
    for p in procs:
        try:
            c = sum(p.cpu_times()[:2])
            w = p.io_counters().write_bytes
            cpu += c
            wr += w
            per_pid[p.pid] = c
            names[p.pid] = p.name()
        except psutil.Error:
            continue
    return cpu, wr, per_pid, names, {p.pid for p in procs}


def kill_tree(proc):
    for c in proc.children(recursive=True):
        try:
            c.kill()
        except psutil.Error:
            pass
    try:
        proc.kill()
    except psutil.Error:
        pass


def main():
    p = find_ableton()
    if p is None:
        sys.exit("Ableton process not found")
    proc = psutil.Process(p.pid)

    outdir = time.strftime("ableton_monitor_%Y%m%d_%H%M%S")
    os.makedirs(outdir)
    tick_f = open(os.path.join(outdir, "ticks.csv"), "w", newline="")
    ev_f = open(os.path.join(outdir, "events.log"), "w")
    tick_w = csv.writer(tick_f)
    tick_w.writerow(["t", "export_open", "verdict", "main_ok", "main_ms", "main_hung_flag",
                     "export_ok", "export_ms", "ghost_count", "ghost_run_s", "n_windows",
                     "cpu_s", "d_cpu", "write_b", "d_write_kb", "since_write_s",
                     "threads", "rss_mb", "n_procs", "busiest", "suspended"])

    lock = threading.Lock()
    t0 = time.time()

    def event(msg):
        line = f"[{time.time() - t0:8.1f}s] {msg}"
        with lock:
            ev_f.write(line + "\n")
            ev_f.flush()
        print(line)

    def marker_thread():
        for line in sys.stdin:
            if line.strip():
                event("MARKER " + line.strip())
    threading.Thread(target=marker_thread, daemon=True).start()

    event(f"watching pid={proc.pid} name={proc.name()} children={len(proc.children(recursive=True))}")
    event("type a label + Enter to drop a marker (e.g. 'export started')")
    event(f"config STALL_S={STALL_S} MIN_WRITE_KB={MIN_WRITE_KB} KILL_ON_HUNG={KILL_ON_HUNG}")

    known = {}
    main_hwnd = None
    suspended = resumed = False
    export_open_prev = False
    export_started_t = last_write_t = None
    prev = None
    ghost_since = None
    verdict_prev = "idle"
    acted = False
    tick_n = 0

    try:
        while time.time() - t0 < MAX_DURATION_S:
            loop_start = now = time.time()
            t = round(now - t0, 1)

            if SUSPEND_AT_S is not None:
                if not suspended and not resumed and t >= SUSPEND_AT_S:
                    proc.suspend()
                    suspended = True
                    event("ACTION suspended Ableton")
                elif suspended and t >= SUSPEND_AT_S + SUSPEND_FOR_S:
                    proc.resume()
                    suspended = False
                    resumed = True
                    event("ACTION resumed Ableton")

            if not proc.is_running():
                event("PROCESS GONE (crash, exit or kill)")
                break

            cpu, wr, per_pid, names, pids = tree_counters(proc)

            wins = snapshot_windows()
            mine = [w for w in wins if w[1] in pids and w[4]]
            ghosts = [w for w in wins if w[3] == "Ghost" and "ableton" in w[2].lower()]

            # window churn logging (informational only)
            cur = {w[0]: (w[2], w[3]) for w in mine}
            for g in ghosts:
                if g[0] not in known:
                    event(f"GHOST present hwnd={g[0]} title={g[2]!r}")
            for h, v in cur.items():
                if h not in known:
                    event(f"window new hwnd={h} class={v[1]!r} title={v[0]!r}")
                elif known[h] != v:
                    event(f"window retitled hwnd={h} title={v[0]!r}")
            for h in known:
                if h not in cur and not any(g[0] == h for g in ghosts):
                    event(f"window gone/hidden hwnd={h} title={known[h][0]!r}")
            known = {**{g[0]: (g[2], g[3]) for g in ghosts}, **cur}

            if ghosts and ghost_since is None:
                ghost_since = now
            if not ghosts:
                ghost_since = None
            ghost_run = round(now - ghost_since, 1) if ghost_since else 0

            if main_hwnd is None or not user32.IsWindow(main_hwnd):
                main_hwnd = next((w[0] for w in mine
                                  if w[3] == "Ableton Live Window Class" and "ableton live" in w[2].lower()), None)
            export_w = next((w for w in mine if w[2].startswith("Export Audio...")), None)
            export_open = export_w is not None

            m_ok = m_ms = m_hung = e_ok = e_ms = ""
            if main_hwnd:
                m_ok, m_ms = probe(main_hwnd)
                m_hung = bool(user32.IsHungAppWindow(main_hwnd))
            if export_w:
                e_ok, e_ms = probe(export_w[0])

            d_cpu = d_wr = 0
            busiest = ""
            if prev:
                d_cpu = cpu - prev[0]
                d_wr = wr - prev[1]
                deltas = {pid: per_pid[pid] - prev[2].get(pid, per_pid[pid]) for pid in per_pid}
                if deltas:
                    top = max(deltas, key=deltas.get)
                    busiest = f"{names.get(top, '?')}:{top}:{round(deltas[top], 2)}"
            prev = (cpu, wr, per_pid)

            # export lifecycle
            if export_open and not export_open_prev:
                export_started_t = last_write_t = now
                acted = False
                event(f"EXPORT WINDOW OPEN title={export_w[2]!r}")
            if not export_open and export_open_prev:
                event(f"EXPORT WINDOW CLOSED after {round(now - export_started_t)}s")
                if STOP_WHEN_DONE:
                    break
            export_open_prev = export_open

            # the verdict: export window open and no meaningful disk write for STALL_S
            since_write = 0
            if not export_open:
                verdict = "idle"
            else:
                if d_wr >= MIN_WRITE_KB * 1024:
                    last_write_t = now
                since_write = round(now - last_write_t, 1)
                verdict = "HUNG" if since_write > STALL_S else "ok"

            if verdict != verdict_prev:
                event(f"VERDICT {verdict_prev} -> {verdict} (since_write={since_write}s ghost_run={ghost_run}s)")
                if verdict == "HUNG":
                    event("WINDOWS AT FLAG TIME: " + " | ".join(f"{w[3]}:{w[2]!r}" for w in mine))
                verdict_prev = verdict

            if verdict == "HUNG" and KILL_ON_HUNG and not acted:
                acted = True
                event("ACTION killing Ableton process tree")
                kill_tree(proc)

            try:
                thr = proc.num_threads()
                rss = round(proc.memory_info().rss / 2**20)
            except psutil.Error:
                thr = rss = ""

            tick_w.writerow([t, export_open, verdict, m_ok, m_ms, m_hung, e_ok, e_ms,
                             len(ghosts), ghost_run, len(mine), round(cpu, 2), round(d_cpu, 2),
                             wr, round(d_wr / 1024), since_write,
                             thr, rss, len(pids), busiest, suspended])
            tick_f.flush()

            tick_n += 1
            if tick_n % 5 == 0:
                print(f"  t={t:7.1f} {verdict:6s} dwrite={d_wr / 1024:8.0f}KB "
                      f"since_write={since_write}s ghost_run={ghost_run}s")

            time.sleep(max(0, INTERVAL_S - (time.time() - loop_start)))
    except KeyboardInterrupt:
        pass
    finally:
        if suspended:
            try:
                proc.resume()
                event("ACTION resumed Ableton (cleanup)")
            except psutil.Error:
                pass
        tick_f.close()
        ev_f.close()
        print(f"saved to {outdir}")


if __name__ == "__main__":
    main()