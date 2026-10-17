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
    return target
