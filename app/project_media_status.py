"""Persist project portability and missing-media status from Bulk Fix scans."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from paths import data_file


def build_project_media_status(results):
    """Build a JSON-serializable snapshot and its three project categories."""
    projects = []
    categories = {
        "non_portable": [],
        "unresolved_missing_media": [],
        "candidate_review": [],
        "fully_portable": [],
    }
    confirmed_missing = {}

    for project_path, result in sorted(
        results.items(), key=lambda item: os.path.normcase(os.fspath(item[0]))
    ):
        path = os.fspath(project_path)
        project = {
            "path": path,
            "name": Path(path).stem,
            "success": bool(result.get("success")),
            "error": result.get("error"),
            "missing_before": int(result.get("missing_before", 0)),
            "missing_after": int(result.get("missing_after", 0)),
            "fixed": int(result.get("fixed", 0)),
            "external_sample_refs": int(result.get("external_sample_refs", 0)),
            "external_sample_names": sorted(
                set(result.get("external_sample_names", ()))
            ),
            "external_sample_details": [
                detail
                for detail in result.get("external_sample_details", ())
                if isinstance(detail, dict)
            ],
            "unresolved_sample_names": sorted(
                set(result.get("still_missing_names", ()))
            ),
            "missing_sample_details": result.get("missing_sample_details", []),
            "categories": [],
            "media_status": None,
        }

        if project["success"]:
            if project["external_sample_refs"]:
                project["categories"].append("non_portable")
                categories["non_portable"].append(path)
            if project["missing_after"] > 0:
                project["categories"].append("unresolved_missing_media")
                categories["unresolved_missing_media"].append(path)
                project["media_status"] = "Missing"
                if any(
                    isinstance(detail, dict)
                    and detail.get("reason") == "ambiguous_candidates"
                    for detail in project["missing_sample_details"]
                ):
                    project["categories"].append("candidate_review")
                    categories["candidate_review"].append(path)
                    project["media_status"] = "Candidates"
            if (
                project["missing_after"] == 0
                and project["external_sample_refs"] == 0
            ):
                project["categories"].append("fully_portable")
                categories["fully_portable"].append(path)
                project["media_status"] = "Portable"
            elif (
                project["missing_after"] == 0
                and project["external_sample_refs"] > 0
            ):
                project["media_status"] = "Linked"

            for detail in project["missing_sample_details"]:
                if detail.get("reason") == "not_in_index":
                    name = detail.get("name")
                    if isinstance(name, str) and name:
                        confirmed_missing.setdefault(name.casefold(), name)

        projects.append(project)

    return {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "projects": projects,
        "categories": categories,
        "confirmed_missing_samples": sorted(
            confirmed_missing.values(), key=str.casefold
        ),
    }


MISSING_AUDIO_LIST_NAME = "missing_audio_list.txt"


def missing_audio_list_path():
    return Path(data_file(MISSING_AUDIO_LIST_NAME))


def _split_unresolved(project):
    """Return (missing_entries, candidate_entries) as unique-friendly tuples.

    missing_entries: (name, stored_path or None)
    candidate_entries: (name, stored_path or None, [candidate paths])
    """
    missing = []
    candidates = []
    seen_names = set()
    for detail in project.get("missing_sample_details", ()):
        if not isinstance(detail, dict):
            continue
        name = str(detail.get("name") or "")
        seen_names.add(name)
        stored = detail.get("stored_path")
        if detail.get("reason") == "ambiguous_candidates":
            candidates.append((name, stored, list(detail.get("candidates", ()))))
        else:
            missing.append((name, stored))
    for name in project.get("unresolved_sample_names", ()):
        if name not in seen_names:
            missing.append((str(name), None))
    return missing, candidates


def format_project_missing_lines(project):
    """Describe one project's missing samples with their stored paths."""
    lines = []
    seen_entries = set()
    for name, stored in _split_unresolved(project)[0]:
        stored = stored or "(not recorded)"
        if (name, stored) in seen_entries:
            continue
        seen_entries.add((name, stored))
        lines.append(f"  {name}\n      Path: {stored}")
    return lines


def format_project_candidate_lines(project):
    """Describe samples with several possible matches needing a human pick."""
    lines = []
    seen_entries = set()
    for name, stored, paths in _split_unresolved(project)[1]:
        stored = stored or "(not recorded)"
        if (name, stored) in seen_entries:
            continue
        seen_entries.add((name, stored))
        body = "\n".join(f"        {p}" for p in paths)
        lines.append(
            f"  {name}\n      Expected: {stored}\n      Candidates:\n{body}"
        )
    return lines


def format_missing_audio_list(projects):
    """Build the plain-text missing audio list for all projects."""
    samples = {}
    for project in projects:
        if project.get("media_status") not in ("Missing", "Candidates"):
            continue
        project_name = Path(str(project.get("path", ""))).stem or str(
            project.get("path", "")
        )
        entries = _split_unresolved(project)[0]
        for name, stored in entries:
            record = samples.setdefault(
                name.lower(), {"name": name, "paths": [], "projects": []}
            )
            if stored and str(stored) not in record["paths"]:
                record["paths"].append(str(stored))
            if project_name not in record["projects"]:
                record["projects"].append(project_name)
    blocks = []
    for key in sorted(samples):
        record = samples[key]
        paths = record["paths"] or ["(not recorded)"]
        blocks.append(
            f"{record['name']}\n    Expected Path(s):\n"
            + "\n".join(f"         {p}" for p in paths)
            + "\n    Referenced In:\n"
            + "\n".join(f"            {p}" for p in record["projects"])
        )
    header = (
        f"Missing audio list - updated "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Unique missing samples: {len(blocks)}\n"
    )
    return header + "\n" + "\n\n".join(blocks) + ("\n" if blocks else "")


def write_missing_audio_list(projects, output_path=None):
    target = Path(output_path or missing_audio_list_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        format_missing_audio_list(projects), encoding="utf-8"
    )
    return target


def save_project_media_status(results, output_path=None, merge=False):
    """Atomically save media status, optionally retaining other projects."""
    target = Path(output_path or data_file("project_media_status.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_project_media_status(results)
    if merge:
        try:
            with target.open(encoding="utf-8") as status_file:
                previous = json.load(status_file)
        except FileNotFoundError:
            previous = {}
        if not isinstance(previous, dict):
            raise ValueError(f"Invalid project media status in {target}")
        previous_projects = previous.get("projects", [])
        if not isinstance(previous_projects, list):
            raise ValueError(f"Invalid project list in {target}")

        projects_by_path = {
            os.path.normcase(os.path.abspath(os.path.normpath(entry["path"]))):
                entry
            for entry in previous_projects
            if isinstance(entry, dict) and isinstance(entry.get("path"), str)
        }
        for project in payload["projects"]:
            key = os.path.normcase(
                os.path.abspath(os.path.normpath(project["path"]))
            )
            projects_by_path[key] = project

        projects = sorted(
            projects_by_path.values(),
            key=lambda project: os.path.normcase(project["path"]),
        )
        categories = {
            "non_portable": [],
            "unresolved_missing_media": [],
            "candidate_review": [],
            "fully_portable": [],
        }
        confirmed_missing = {}
        for project in projects:
            for category in categories:
                if category in project.get("categories", ()):
                    categories[category].append(project["path"])
            for detail in project.get("missing_sample_details", ()):
                if (
                    isinstance(detail, dict)
                    and detail.get("reason") == "not_in_index"
                ):
                    name = detail.get("name")
                    if isinstance(name, str) and name:
                        confirmed_missing.setdefault(name.casefold(), name)

        payload["projects"] = projects
        payload["categories"] = categories
        payload["confirmed_missing_samples"] = sorted(
            confirmed_missing.values(), key=str.casefold
        )

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(payload, temporary_file, indent=2, ensure_ascii=False)
            temporary_file.write("\n")
        os.replace(temporary_path, target)
    except OSError:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    try:
        list_path = (
            target.with_name(MISSING_AUDIO_LIST_NAME)
            if output_path else None
        )
        write_missing_audio_list(payload["projects"], list_path)
    except OSError:
        pass
    return target
