"""
Ableton project-folder structure checks.

A "recursive project" is a Live project folder (one containing an
"Ableton Project Info" folder) that sits somewhere inside another project
folder. Live warns about this on Collect All and Save ("...is itself located
below another Project. Recursive Projects may lead to unexpected results or
even data loss. Please consider moving your Project to another location.")
and the modal stalls the automation, so these are caught before export and
fixed by moving the inner project folder out, next to the outermost project
that contains it.
"""

import os
import shutil

PROJECT_INFO_DIR = "Ableton Project Info"


def is_project_folder(path):
    return os.path.isdir(os.path.join(path, PROJECT_INFO_DIR))


def project_folder_of(als_path):
    """The Live project folder a set belongs to: the nearest folder at or
    above the .als that contains 'Ableton Project Info' (None if there is none)."""
    folder = os.path.dirname(os.path.abspath(als_path))
    while True:
        if is_project_folder(folder):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def find_recursive_nesting(als_path):
    """Return (inner_project, outermost_enclosing_project) if the set's project
    folder lies inside another project folder, else None."""
    inner = project_folder_of(als_path)
    if inner is None:
        return None
    outermost = None
    folder = os.path.dirname(inner)
    while True:
        if is_project_folder(folder):
            outermost = folder
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    return (inner, outermost) if outermost else None


def _unique_destination(dest_dir, name):
    candidate = os.path.join(dest_dir, name)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{name} ({n})")
        n += 1
    return candidate


def plan_recursive_fix(als_path):
    """Describe how to un-nest the project, or None if it isn't recursive.

    The inner project folder moves to the folder that holds the outermost
    enclosing project, so it ends up as that project's sibling. A numbered
    suffix is added if a folder with the same name is already there.
    """
    nesting = find_recursive_nesting(als_path)
    if nesting is None:
        return None
    inner, outer = nesting
    new_inner = _unique_destination(os.path.dirname(outer), os.path.basename(inner))
    als_abs = os.path.abspath(als_path)
    return {
        "als_path": als_abs,
        "inner": inner,
        "outer": outer,
        "new_inner": new_inner,
        "new_als_path": remap_path(als_abs, inner, new_inner),
    }


def remap_path(path, old_root, new_root):
    """Rewrite `path` if it is `old_root` or inside it; otherwise return it unchanged."""
    norm_path = os.path.normcase(os.path.abspath(path))
    norm_root = os.path.normcase(os.path.abspath(old_root))
    if norm_path == norm_root:
        return new_root
    if norm_path.startswith(norm_root + os.sep):
        return os.path.join(new_root, os.path.abspath(path)[len(norm_root) + 1:])
    return path


def apply_recursive_fix(plan):
    """Move the inner project folder per `plan`. Returns the new .als path.

    The destination is on the same drive as the source, so this is a rename
    rather than a copy. Raises OSError if the move fails (e.g. a file is
    locked because the set is open in Live), leaving everything in place.
    """
    if not os.path.isdir(plan["inner"]):
        raise FileNotFoundError(f"Project folder no longer exists: {plan['inner']}")
    if os.path.exists(plan["new_inner"]):
        raise FileExistsError(f"Destination already exists: {plan['new_inner']}")
    shutil.move(plan["inner"], plan["new_inner"])
    return plan["new_als_path"]
