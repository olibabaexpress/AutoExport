import threading
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from missing_media_resolver import resolve_missing_media_bulk
from GUI import AutoExportLauncherGUI


class BulkFixPauseTests(unittest.TestCase):
    def test_bulk_fix_pause_suppresses_new_size_estimate_requests(self):
        gui = AutoExportLauncherGUI.__new__(AutoExportLauncherGUI)
        gui._closing = False
        gui.script_running = False
        gui._bulk_fix_priority_active = True
        gui._initial_estimates_started = True
        gui._initial_estimate_paths = set()
        gui.size_pending = set()

        gui.request_size_estimates()
        self.assertEqual(gui._initial_estimate_paths, set())
        self.assertEqual(gui.size_pending, set())

    def test_pause_finishes_current_project_and_resume_uses_remaining_projects(self):
        projects = ["first.als", "second.als", "third.als"]
        pause_requested = threading.Event()
        completed_projects = []
        progress = []

        def resolve_project(
            project_path, _database, dry_run=False, name_index=None, mode="collect",
            network_name_index=None
        ):
            completed_projects.append(project_path)
            return {
                "success": True,
                "missing_before": 0,
                "missing_after": 0,
                "fixed": 0,
                "still_missing_names": [],
            }

        def request_pause(message):
            progress.append(message)
            if message == "  -> no missing samples":
                pause_requested.set()

        with patch(
            "missing_media_resolver.resolve_missing_media",
            side_effect=resolve_project,
        ):
            first_batch = resolve_missing_media_bulk(
                projects,
                {},
                progress_callback=request_pause,
                pause_requested=pause_requested,
                total_count=len(projects),
            )
            self.assertEqual(list(first_batch), ["first.als"])
            self.assertEqual(completed_projects, ["first.als"])
            self.assertTrue(
                any("Paused after 1/3 projects" in item for item in progress)
            )
            self.assertFalse(
                any("Bulk resolve complete" in item for item in progress)
            )

            pause_requested.clear()
            remaining = projects[len(first_batch):]
            resumed_batch = resolve_missing_media_bulk(
                remaining,
                {},
                progress_callback=progress.append,
                pause_requested=pause_requested,
                progress_offset=len(first_batch),
                total_count=len(projects),
            )

        self.assertEqual(list(resumed_batch), ["second.als", "third.als"])
        self.assertEqual(completed_projects, projects)
        self.assertIn("[2/3] Checking second.als...", progress)
        self.assertIn("[3/3] Checking third.als...", progress)
        self.assertTrue(
            any("Bulk resolve complete: 0/0 fixed across 2 project(s)" in item
                for item in progress)
        )

    def test_stop_finishes_current_project_and_returns_partial_results(self):
        projects = ["first.als", "second.als", "third.als"]
        stop_requested = threading.Event()
        completed_projects = []
        progress = []

        def resolve_project(
            project_path, _database, dry_run=False, name_index=None, mode="collect",
            network_name_index=None
        ):
            completed_projects.append(project_path)
            return {
                "success": True,
                "missing_before": 0,
                "missing_after": 0,
                "fixed": 0,
                "still_missing_names": [],
            }

        def request_stop(message):
            progress.append(message)
            if message == "  -> no missing samples":
                stop_requested.set()

        with patch(
            "missing_media_resolver.resolve_missing_media",
            side_effect=resolve_project,
        ):
            results = resolve_missing_media_bulk(
                projects,
                {},
                progress_callback=request_stop,
                stop_requested=stop_requested,
                total_count=len(projects),
            )

        self.assertEqual(list(results), ["first.als"])
        self.assertEqual(completed_projects, ["first.als"])
        self.assertTrue(any("Stopped after 1/3 projects" in item for item in progress))
        self.assertFalse(any("Bulk resolve complete" in item for item in progress))


if __name__ == "__main__":
    unittest.main()
