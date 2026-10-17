"""Explorer right-click support: collect the folders passed on the command line.

With several folders selected, Explorer starts one process per folder. Each
process appends its folder to a spool file; the first to take the lock file
waits briefly, gathers everyone's folders and opens the GUI. The others exit.
"""

import json
import os
import queue
import socket
import sys
import threading
import time

from paths import data_file

SPOOL_FILE = data_file("EXPLORER_OPEN_SPOOL.txt")
LOCK_FILE = data_file("EXPLORER_OPEN.lock")
INSTANCE_FILE = data_file("RUNNING_INSTANCE.json")
GATHER_SECONDS = 0.8
STALE_LOCK_SECONDS = 15.0
CONNECT_TIMEOUT = 1.5
IGNORED_FLAGS = {"--self-test", "--configure"}


def forward_to_running(paths):
    """Hand paths to an already running AutoExport.

    Returns True when this launch should simply exit: either paths were
    delivered, or the running instance is a right-click session (only one of
    those may exist, so a plain second launch just focuses it).
    """
    try:
        with open(INSTANCE_FILE, "r", encoding="utf-8") as info_file:
            port = int(json.load(info_file)["port"])
        with socket.create_connection(("127.0.0.1", port), CONNECT_TIMEOUT) as conn:
            conn.settimeout(CONNECT_TIMEOUT)
            conn.sendall((json.dumps({"paths": paths}) + "\n").encode("utf-8"))
            reply = json.loads(conn.makefile("r", encoding="utf-8").readline())
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return bool(paths) or bool(reply.get("ephemeral"))


class InstanceServer:
    """Listens on localhost so later launches can pass paths to this window."""

    def __init__(self, ephemeral):
        self.ephemeral = ephemeral
        self.requests = queue.Queue()
        self._socket = None

    def start(self):
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.bind(("127.0.0.1", 0))
            self._socket.listen(8)
            port = self._socket.getsockname()[1]
            with open(INSTANCE_FILE, "w", encoding="utf-8") as info_file:
                json.dump({"port": port, "pid": os.getpid()}, info_file)
        except OSError:
            self._socket = None
            return False
        threading.Thread(target=self._serve, name="instance-server", daemon=True).start()
        return True

    def _serve(self):
        while True:
            try:
                conn, _ = self._socket.accept()
            except OSError:
                return
            try:
                conn.settimeout(CONNECT_TIMEOUT)
                request = json.loads(conn.makefile("r", encoding="utf-8").readline())
                paths = [
                    os.path.normpath(p) for p in request.get("paths", [])
                    if isinstance(p, str) and os.path.exists(p)
                ]
                self.requests.put(paths)
                conn.sendall((json.dumps({"ephemeral": self.ephemeral}) + "\n").encode("utf-8"))
            except (OSError, ValueError, AttributeError):
                pass
            finally:
                conn.close()

    def stop(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        try:
            with open(INSTANCE_FILE, "r", encoding="utf-8") as info_file:
                if json.load(info_file).get("pid") == os.getpid():
                    os.remove(INSTANCE_FILE)
        except (OSError, ValueError):
            pass


def paths_from_argv(argv):
    """Return existing directories/.als files from argv (flags are skipped)."""
    paths = []
    for arg in argv:
        if arg.startswith("--"):
            continue
        arg = arg.strip().strip('"')
        if arg and os.path.exists(arg):
            normalized = os.path.normpath(arg)
            if normalized not in paths:
                paths.append(normalized)
    return paths


def _try_lock():
    try:
        if time.time() - os.path.getmtime(LOCK_FILE) > STALE_LOCK_SECONDS:
            os.remove(LOCK_FILE)
    except OSError:
        pass
    try:
        os.close(os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except OSError:
        return False


def gather_launch_paths(argv=None):
    """Return every folder Explorer sent in this burst, or None when this
    launch should just exit (paths were handed to the running window, a
    right-click session is already open, or another process is collecting)."""
    argv = sys.argv[1:] if argv is None else argv
    own = paths_from_argv(argv)
    if forward_to_running(own):
        return None
    if not own:
        return []
    try:
        with open(SPOOL_FILE, "a", encoding="utf-8") as spool:
            spool.writelines(path + "\n" for path in own)
    except OSError:
        return own
    if not _try_lock():
        return None
    try:
        time.sleep(GATHER_SECONDS)
        with open(SPOOL_FILE, "r", encoding="utf-8") as spool:
            gathered = [line.strip() for line in spool if line.strip()]
        os.remove(SPOOL_FILE)
    except OSError:
        gathered = own
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass
    unique = []
    for path in gathered:
        if path not in unique and os.path.exists(path):
            unique.append(path)
    return unique
