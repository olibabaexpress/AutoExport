"""
Which Ableton Live executable(s) the pipeline drives.

Two slots are supported: the shipping build ("release") and the public beta
("beta"). Each slot stores the absolute path of a Live 12 executable in
the "ableton" section of data/config.json:

    "ableton": {"build": "release",
                "release_exe": "C:\\ProgramData\\Ableton\\Live 12 Suite\\Program\\Ableton Live 12 Suite.exe",
                "beta_exe": "C:\\ProgramData\\Ableton\\Live 12 Beta\\Program\\Ableton Live 12 Beta.exe"}

Paths are auto-detected from the default install locations the first time
they're needed, and can be set by the user (GUI File menu) by picking either
the exe itself or a folder to search, in which case the newest Live 12 found
is used.

Everything else is derived from the exe, so Suite / Standard / Intro / Lite /
Trial editions all work:
  * window_title - the exe name without extension ("Ableton Live 12 Standard"),
    which is what Live puts after " - " in its main window title.
  * version      - read from the exe's version resource ("12.4.6", "12.4.15b4").

Only Live 12 is supported: Live 11's export dialog and window layout differ.
"""

import glob
import logging
import os
import re
from pathlib import Path
from xml.etree import ElementTree as ET

import app_config

logger = logging.getLogger("Orchestration")

CONFIG_KEY = "ableton"
ABLETON_APPDATA = Path(os.path.expandvars(r"%APPDATA%")) / "Ableton"

RELEASE = "release"
BETA = "beta"
BUILD_KEYS = (RELEASE, BETA)
DEFAULT_BUILD = RELEASE
SUPPORTED_MAJOR = 12

_EXE_GLOB = "Ableton Live 12*.exe"
_EXE_NAME_RE = re.compile(r"^Ableton Live (\d+)\b(.*)$", re.IGNORECASE)
# "12.4.15b4" -> (12, 4, 15, 4); "12.4.6" -> (12, 4, 6, None); "12.3" -> (12, 3, 0, None)
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:b(\d+))?")
_VERSION_DIR_RE = re.compile(r"^Live (\d+\.\d+(?:\.\d+)?(?:b\d+)?)$")


def _default_install_roots():
    roots = [os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "Ableton")]
    for var in ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)"):
        if os.environ.get(var):
            roots.append(os.path.join(os.environ[var], "Ableton"))
    seen, unique = set(), []
    for r in roots:
        if os.path.normcase(r) not in seen:
            seen.add(os.path.normcase(r))
            unique.append(r)
    return unique


# ---------------------------------------------------------------------------
# Inspecting an executable
# ---------------------------------------------------------------------------

def parse_version(text):
    """'12.4.15b4' -> (12, 4, 15, 4); stable builds have None in the beta slot."""
    match = _VERSION_RE.match((text or "").strip())
    if not match:
        return None
    major, minor, patch, beta = match.groups()
    return (int(major), int(minor), int(patch or 0), int(beta) if beta else None)


def _version_sort_key(parsed):
    # A beta of x.y.z sorts below the x.y.z release.
    major, minor, patch, beta = parsed
    return (major, minor, patch, beta if beta is not None else 10**6)


def exe_version_string(exe):
    """ProductVersion from the exe's version resource, e.g. '12.4.15b4'."""
    try:
        import win32api
        lang, codepage = win32api.GetFileVersionInfo(exe, "\\VarFileInfo\\Translation")[0]
        for key in ("ProductVersion", "FileVersion"):
            value = win32api.GetFileVersionInfo(
                exe, "\\StringFileInfo\\%04x%04x\\%s" % (lang, codepage, key)
            )
            if value:
                return str(value).strip()
    except Exception as e:
        logger.debug(f"Could not read version info from {exe}: {e}")
    return None


def inspect_exe(exe):
    """Describe a Live executable, or return None if it isn't one.

    Returns {"exe", "window_title", "version", "parsed", "major", "is_beta"}.
    """
    if not exe or not os.path.isfile(exe) or not exe.lower().endswith(".exe"):
        return None
    stem = Path(exe).stem
    name_match = _EXE_NAME_RE.match(stem)
    if not name_match:
        return None
    version = exe_version_string(exe)
    parsed = parse_version(version)
    major = parsed[0] if parsed else int(name_match.group(1))
    is_beta = (parsed is not None and parsed[3] is not None) or "beta" in stem.lower()
    return {
        "exe": os.path.normpath(exe),
        "window_title": stem,
        "version": version,
        "parsed": parsed,
        "major": major,
        "is_beta": is_beta,
    }


def validate_exe(exe):
    """(info, error_message). info is None when the exe can't be used."""
    info = inspect_exe(exe)
    if info is None:
        return None, f"Not an Ableton Live executable:\n{exe}"
    if info["major"] != SUPPORTED_MAJOR:
        return None, (
            f"{info['window_title']} is Live {info['major']}.\n"
            f"AutoExport only supports Ableton Live {SUPPORTED_MAJOR}."
        )
    return info, None


# ---------------------------------------------------------------------------
# Finding executables
# ---------------------------------------------------------------------------

def find_live_exes(root, max_depth=4):
    """All valid Live 12 executables under root (an install folder or a parent)."""
    found = []
    if not root or not os.path.isdir(root):
        return found
    root = os.path.normpath(root)
    base_depth = root.count(os.sep)
    for dirpath, dirnames, _files in os.walk(root):
        # Skip updater staging copies like ".Live 12 Beta_updated".
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if dirpath.count(os.sep) - base_depth >= max_depth:
            dirnames[:] = []
        for exe in glob.glob(os.path.join(glob.escape(dirpath), _EXE_GLOB)):
            info, _err = validate_exe(exe)
            if info:
                found.append(info)
    return found


def pick_latest(infos, beta):
    """Newest exe of the requested channel, or None."""
    matching = [i for i in infos if i["is_beta"] == beta]
    if not matching:
        return None
    return max(matching, key=lambda i: _version_sort_key(i["parsed"] or (i["major"], 0, 0, None)))


def detect_default_installs():
    """{"release": info|None, "beta": info|None} from the standard install folders."""
    infos = []
    for root in _default_install_roots():
        infos.extend(find_live_exes(root))
    return {RELEASE: pick_latest(infos, beta=False), BETA: pick_latest(infos, beta=True)}


def resolve_choice(path, beta):
    """Turn a user's pick (exe or folder) into (info, error_message).

    An exe is used as-is (after validation). A folder is searched and the
    newest Live 12 of the requested channel is used.
    """
    if not path:
        return None, "Nothing selected."
    if os.path.isfile(path):
        info, err = validate_exe(path)
        if info and info["is_beta"] != beta:
            # Allowed, but worth telling the user.
            logger.info(f"{info['window_title']} assigned to the {'beta' if beta else 'release'} slot")
        return info, err
    if os.path.isdir(path):
        infos = find_live_exes(path, max_depth=6)
        info = pick_latest(infos, beta=beta)
        if info:
            return info, None
        kind = "Beta" if beta else "release"
        return None, f"No Ableton Live {SUPPORTED_MAJOR} {kind} executable found in:\n{path}"
    return None, f"Path not found:\n{path}"


# ---------------------------------------------------------------------------
# Persisted configuration
# ---------------------------------------------------------------------------

def _load_config():
    data = app_config.get(CONFIG_KEY)
    return data if isinstance(data, dict) else {}


def _save_config(data):
    return app_config.set(CONFIG_KEY, data)


def _exe_key(build_key):
    return f"{build_key}_exe"


def ensure_detected():
    """Fill empty / stale exe slots from the default install locations.

    Returns the config dict. Cheap enough to call on every startup; only
    scans disk when a slot is empty or points at a file that no longer exists.
    """
    data = _load_config()
    missing = [k for k in BUILD_KEYS if not (data.get(_exe_key(k)) and os.path.isfile(data[_exe_key(k)]))]
    if missing:
        detected = detect_default_installs()
        changed = False
        for key in missing:
            if detected[key]:
                data[_exe_key(key)] = detected[key]["exe"]
                logger.info(f"Detected Ableton {key} build: {detected[key]['exe']}")
                changed = True
            elif data.get(_exe_key(key)):
                logger.warning(f"Configured Ableton {key} exe no longer exists: {data[_exe_key(key)]}")
        if changed:
            _save_config(data)
    return data


def get_exe(build_key):
    exe = _load_config().get(_exe_key(build_key))
    return exe if exe and os.path.isfile(exe) else None


def set_exe(build_key, exe):
    """Store a validated exe for a slot. Returns (info, error_message)."""
    if build_key not in BUILD_KEYS:
        raise ValueError(f"Unknown Ableton build: {build_key}")
    info, err = validate_exe(exe)
    if not info:
        return None, err
    data = _load_config()
    data[_exe_key(build_key)] = info["exe"]
    _save_config(data)
    return info, None


def clear_exe(build_key):
    data = _load_config()
    data.pop(_exe_key(build_key), None)
    if data.get("build") == build_key:
        data["build"] = DEFAULT_BUILD
    _save_config(data)


def is_installed(build_key):
    """Whether this slot has a usable executable configured."""
    return get_exe(build_key) is not None


def installed_builds():
    return [key for key in BUILD_KEYS if is_installed(key)]


def load_selected_build():
    """The persisted build choice, falling back to whichever slot is usable."""
    stored = _load_config().get("build", DEFAULT_BUILD)
    if stored in BUILD_KEYS and is_installed(stored):
        return stored
    for key in (DEFAULT_BUILD, BETA):
        if is_installed(key):
            if stored != key:
                logger.warning(f"Selected Ableton build '{stored}' is not configured - using '{key}'")
            return key
    return DEFAULT_BUILD


def save_selected_build(build_key):
    if build_key not in BUILD_KEYS:
        raise ValueError(f"Unknown Ableton build: {build_key}")
    data = _load_config()
    data["build"] = build_key
    return _save_config(data)


def get_build(build_key=None):
    """Config dict for a build (the selected one by default).

    Keys: key, label, exe, window_title, is_beta, version. exe is None
    when nothing usable is configured for that slot.
    """
    key = build_key or load_selected_build()
    exe = get_exe(key)
    info = inspect_exe(exe) if exe else None
    if info:
        title = info["window_title"]
        label = title.replace("Ableton ", "", 1)
    else:
        title = f"Ableton Live {SUPPORTED_MAJOR}"
        label = f"Live {SUPPORTED_MAJOR} {'Beta' if key == BETA else ''}".strip()
    return {
        "key": key,
        "label": label,
        "exe": exe,
        # Used as a pywinauto title_re fragment and as a substring for
        # win_exists(" - <title>") project-loaded checks.
        "window_title": title,
        "is_beta": key == BETA,
        "version": info["version"] if info else None,
    }


# ---------------------------------------------------------------------------
# Version string for file naming
# ---------------------------------------------------------------------------

def detect_version(build_key=None):
    """Version string used for file naming, e.g. '12.3' or '12.4 BETA'.

    Read from the configured exe; falls back to the newest matching
    %APPDATA%\\Ableton\\Live x.y folder if the exe has no version resource.
    """
    build = get_build(build_key)
    want_beta = build["is_beta"]
    parsed = parse_version(build["version"]) if build["version"] else None
    if parsed is None:
        parsed = _appdata_version(want_beta)
    if parsed is None:
        return _fallback_version(want_beta)
    version = f"{parsed[0]}.{parsed[1]}"
    return f"{version} BETA" if want_beta else version


def _appdata_version(want_beta):
    try:
        candidates = []
        for item in os.listdir(ABLETON_APPDATA):
            m = _VERSION_DIR_RE.match(item.strip())
            parsed = parse_version(m.group(1)) if m else None
            if parsed and parsed[0] == SUPPORTED_MAJOR and (parsed[3] is not None) == want_beta:
                candidates.append(parsed)
        return max(candidates, key=_version_sort_key) if candidates else None
    except OSError:
        return None


def _fallback_version(want_beta):
    return f"{SUPPORTED_MAJOR} BETA" if want_beta else f"{SUPPORTED_MAJOR}"


def user_library_path(build_key=None):
    """User Library folder from the newest matching Live 12 Library.cfg, or None."""
    want_beta = (build_key or load_selected_build()) == BETA
    try:
        dirs = []
        for item in os.listdir(ABLETON_APPDATA):
            m = _VERSION_DIR_RE.match(item.strip())
            parsed = parse_version(m.group(1)) if m else None
            if parsed and parsed[0] == SUPPORTED_MAJOR:
                cfg = ABLETON_APPDATA / item / "Preferences" / "Library.cfg"
                if cfg.is_file():
                    # Prefer the matching channel, then the newest version.
                    dirs.append(((parsed[3] is not None) == want_beta, _version_sort_key(parsed), cfg))
        for _match, _ver, cfg in sorted(dirs, reverse=True):
            try:
                user_library = ET.parse(cfg).find(".//UserLibrary")
            except (OSError, ET.ParseError) as error:
                logger.warning(f"Could not read Ableton User Library preferences from {cfg}: {error}")
                continue
            if user_library is None:
                continue
            project_path = user_library.find(".//ProjectPath")
            project_name = user_library.find(".//ProjectName")
            path = project_path.get("Value") if project_path is not None else None
            name = project_name.get("Value", "User Library") if project_name is not None else "User Library"
            if path:
                library = os.path.normpath(os.path.join(path, name))
                if os.path.isdir(library):
                    return library
                logger.warning(f"User Library from {cfg} does not exist: {library}")
    except OSError as error:
        logger.warning(f"Could not inspect Ableton preferences in {ABLETON_APPDATA}: {error}")
    saved = app_config.get("user_library_path")
    if saved and os.path.isdir(saved):
        return os.path.normpath(saved)
    default = Path.home() / "Documents" / "Ableton" / "User Library"
    return str(default) if default.is_dir() else None
