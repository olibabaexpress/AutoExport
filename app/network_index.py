"""
Sample indexes from other computers on the local network.

A second machine running AutoExport keeps its own sample index in its data
folder. This module finds that file over the network (or takes one the user
browses to), stores a copy under data/ named after the computer, and exposes
the copy's samples as reachable UNC paths so the missing-media resolver can
copy files across the network into a project.

Remote drive letters are reached through administrative shares first
(\\\\TOWA\\G$) and then plain shares named after the drive (\\\\TOWA\\G).
"""

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path

import app_config as app_config
from paths import data_file

logger = logging.getLogger("AutoExport")

CONFIG_KEY = "network_indexes"
DEVICES_KEY = "network_devices"
INDEX_NAME = "abletoolz_sample_db.json"
SMB_PORT = 445
CONNECT_TIMEOUT = 1.5
_INSTALLED_DATA = ("AppData", "Local", "AutoExport", "data")

_drive_roots = {}
last_discovery_error = {"message": ""}


def local_computer_name():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def _safe_name(device):
    return re.sub(r"[^A-Za-z0-9_-]", "_", device)


def index_file_for(device):
    """Local copy of a device's index: data/abletoolz_sample_db_<DEVICE>.json."""
    return Path(data_file(f"abletoolz_sample_db_{_safe_name(device)}.json"))


def imported_indexes():
    """Return {device: {db, source, directories, imported_at}} from the config.

    Entries whose copied index file is gone are ignored, so deleting the file
    in data/ really disconnects that device.
    """
    stored = app_config.get(CONFIG_KEY)
    if not isinstance(stored, dict):
        return {}
    return {
        device: entry for device, entry in stored.items()
        if index_file_for(device).exists()
    }


def _save_entry(device, entry):
    indexes = dict(app_config.get(CONFIG_KEY) or {})
    indexes[device] = entry
    app_config.set(CONFIG_KEY, indexes)
    remember_device(device)


def remembered_devices():
    """Computer names seen before, even if their copied index was deleted."""
    stored = app_config.get(DEVICES_KEY)
    names = [n for n in stored if isinstance(n, str)] if isinstance(stored, list) else []
    for device in (app_config.get(CONFIG_KEY) or {}):
        if device not in names:
            names.append(device)
    return names


def remember_device(device):
    names = remembered_devices()
    if device not in names:
        app_config.set(DEVICES_KEY, names + [device])


def mapped_network_servers():
    """Computers behind mapped network drives (`net use`), excluding this one."""
    servers = []
    try:
        completed = subprocess.run(
            ["net", "use"], capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for match in re.finditer(r"\\\\([^\\\s]+)\\", completed.stdout):
            if match.group(1) not in servers:
                servers.append(match.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    own = local_computer_name().casefold()
    return [s for s in servers if s.casefold() != own]


def is_reachable(device, timeout=CONNECT_TIMEOUT):
    """Quick SMB port probe, so a powered-off computer fails fast."""
    try:
        with socket.create_connection((device, SMB_PORT), timeout=timeout):
            return True
    except OSError:
        return False


def discover_devices(timeout=6):
    """Names of computers visible through `net view`, excluding this one."""
    devices = []
    try:
        completed = subprocess.run(
            ["net", "view"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in completed.stdout.splitlines():
            match = re.match(r"\\\\(\S+)", line.strip())
            if match:
                devices.append(match.group(1))
        last_discovery_error["message"] = ""
    except subprocess.TimeoutExpired:
        last_discovery_error["message"] = (
            f"Network discovery timed out after {timeout}s - no computer found"
        )
        logger.info("Network discovery unavailable: net view timed out")
    except (OSError, subprocess.SubprocessError) as error:
        last_discovery_error["message"] = f"Network discovery unavailable: {error}"
        logger.info("Network discovery unavailable: %s", error)
    own = local_computer_name().casefold()
    return [name for name in devices if name.casefold() != own]


def known_devices():
    """Devices worth probing: remembered and mapped ones; `net view` last."""
    ordered = remembered_devices()
    for name in mapped_network_servers():
        if name.casefold() not in {n.casefold() for n in ordered}:
            ordered.append(name)
    if any(is_reachable(name) for name in ordered):
        return ordered
    for name in discover_devices():
        if name.casefold() not in {n.casefold() for n in ordered}:
            ordered.append(name)
    return ordered


def _drive_root(device, drive):
    """First reachable share root for a remote drive letter, or None."""
    key = (device.casefold(), drive.upper())
    if key in _drive_roots:
        return _drive_roots[key]
    root = None
    for share in (f"{drive.upper()}$", drive.upper()):
        candidate = f"\\\\{device}\\{share}\\"
        try:
            if os.path.isdir(candidate):
                root = candidate
                break
        except OSError:
            continue
    _drive_roots[key] = root
    return root


def to_unc(device, remote_path):
    """Translate X:\\folder\\file on `device` to a reachable UNC path, or None."""
    match = re.match(r"^([A-Za-z]):[\\/]*(.*)$", str(remote_path))
    if not match:
        return None
    root = _drive_root(device, match.group(1))
    if root is None:
        return None
    return root + match.group(2).replace("/", "\\")


def find_remote_index(device):
    """Locate AutoExport's index on `device` through its administrative share."""
    root = _drive_root(device, "C")
    if root is None:
        return None
    users_dir = root + "Users"
    try:
        users = [entry.path for entry in os.scandir(users_dir)]
    except OSError:
        return None
    newest = None
    for user in users:
        candidate = Path(user, *_INSTALLED_DATA, INDEX_NAME)
        try:
            modified = candidate.stat().st_mtime
        except OSError:
            continue
        if newest is None or modified > newest[0]:
            newest = (modified, candidate)
    return newest[1] if newest else None


def _directories_for(source, database):
    """The remote machine's configured sample folders, else folders from its index."""
    try:
        with open(Path(source).parent / "config.json", encoding="utf-8") as handle:
            configured = json.load(handle).get("sample_directories")
        if isinstance(configured, list) and configured:
            return [str(path) for path in configured]
    except (OSError, ValueError, AttributeError):
        pass
    roots = []
    for sample_path in database:
        parts = [part for part in re.split(r"[\\/]+", str(sample_path)) if part]
        if not parts:
            continue
        root = "\\".join(parts[:2]) if len(parts) > 2 else parts[0] + "\\"
        if root not in roots:
            roots.append(root)
        if len(roots) >= 50:
            break
    return roots


def device_from_path(path):
    """Computer name for a \\\\SERVER\\share\\... path, else None."""
    match = re.match(r"^[\\/]{2}([^\\/]+)[\\/]", str(path))
    return match.group(1) if match else None


def import_index(source, device):
    """Copy `source` into data/ as this device's index and register it."""
    import create_db_patch as create_db_patch

    source = Path(source)
    destination = index_file_for(device)
    temporary = destination.with_suffix(".json.tmp")
    shutil.copyfile(source, temporary)
    try:
        database = create_db_patch.load_db(temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, destination)
    entry = {
        "db": destination.name,
        "source": str(source),
        "directories": _directories_for(source, database),
        "count": len(database),
        "imported_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_entry(device, entry)
    logger.info(
        "Imported sample index from %s (%d entries) -> %s",
        device, len(database), destination,
    )
    return entry


def auto_connect():
    """
    Find and import a networked sample index.

    Returns {"state": ..., "device": ..., "message": ...} where state is
    "imported", "needs_browse" (a computer answers but no index was found),
    or "unavailable".
    """
    reachable_without_index = []
    last_discovery_error["message"] = ""
    for device in known_devices():
        if not is_reachable(device):
            continue
        source = find_remote_index(device)
        if source is None and index_file_for(device).exists():
            remembered = Path((imported_indexes().get(device) or {}).get("source", ""))
            if remembered.name and _safe_exists(remembered):
                source = remembered
            else:
                return {
                    "state": "imported",
                    "device": device,
                    "message": f"Networked Index for {device} loaded",
                }
        if source is None:
            reachable_without_index.append(device)
            continue
        try:
            local = index_file_for(device)
            current = imported_indexes().get(device)
            if not (
                current and local.exists()
                and local.stat().st_mtime >= source.stat().st_mtime
            ):
                import_index(source, device)
        except Exception as error:
            logger.warning("Could not import index from %s: %s", device, error)
            reachable_without_index.append(device)
            continue
        return {
            "state": "imported",
            "device": device,
            "message": f"Networked Index Found on {device} and imported automatically",
        }
    if reachable_without_index:
        return {
            "state": "needs_browse",
            "device": reachable_without_index[0],
            "message": "",
        }
    return {
        "state": "unavailable",
        "device": None,
        "message": last_discovery_error["message"]
        or "No networked computer found - click the network icon to browse for an index",
    }


def _safe_exists(path):
    try:
        return path.exists()
    except OSError:
        return False


def mapped_drive_server(path):
    """Computer name behind a mapped network drive letter, else None."""
    import ctypes

    drive = os.path.splitdrive(str(path))[0]
    if len(drive) != 2:
        return None
    buffer = ctypes.create_unicode_buffer(512)
    size = ctypes.c_ulong(512)
    try:
        status = ctypes.windll.mpr.WNetGetConnectionW(drive, buffer, ctypes.byref(size))
    except (AttributeError, OSError):
        return None
    return device_from_path(buffer.value) if status == 0 else None


def active_devices():
    """Imported devices that currently answer on the network."""
    return [device for device in imported_indexes() if is_reachable(device)]


def device_directories(device):
    entry = imported_indexes().get(device) or {}
    directories = entry.get("directories")
    return directories if isinstance(directories, list) else []


def load_network_db():
    """
    Merge every reachable device's index into {unc_path: info}.

    Only files whose drive is reachable through a share are included, so every
    key can be opened directly. Returns {} when no device is reachable.
    """
    import create_db_patch as create_db_patch

    merged = {}
    for device in active_devices():
        local = index_file_for(device)
        if not local.exists():
            continue
        try:
            database = create_db_patch.load_db(local)
        except Exception as error:
            logger.warning("Could not read network index for %s: %s", device, error)
            continue
        started = time.monotonic()
        for remote_path, info in database.items():
            unc = to_unc(device, remote_path)
            if unc is not None:
                merged[unc] = info
        logger.info(
            "Network index %s: %d reachable of %d entries (%.1fs)",
            device, sum(1 for key in merged if key.startswith(f"\\\\{device}\\")),
            len(database), time.monotonic() - started,
        )
    return merged
