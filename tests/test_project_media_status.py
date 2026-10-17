import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from missing_media_resolver import (
    _build_name_index,
    _collect_external_sample_names,
    _size_verified_database,
    _simulate_fix,
    format_bulk_report,
    resolve_missing_media,
)
from project_media_status import build_project_media_status, save_project_media_status


class ProjectMediaStatusTests(unittest.TestCase):
    def test_match_requires_exact_filename_and_original_file_size(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "same.wav"
            source.write_bytes(b"indexed-data")
            root = Path(temporary_directory) / "Project"
            root.mkdir()

            class Reference:
                name = "same.wav"
                size = 20
                last_modified = 123
                absolute = Path("E:/offline/same.wav")
                relative = Path("Samples/same.wav")

                @staticmethod
                def get_relative_type():
                    return 1

                @staticmethod
                def get_relative_value():
                    return "Samples/same.wav"

            class Samples:
                @staticmethod
                def iterate():
                    return iter([Reference()])

            class AbletonSet:
                samples = Samples()

                @staticmethod
                def find_project_root_folder():
                    return root

            db = {
                str(source): {
                    "name": "same.wav",
                    "size": source.stat().st_size,
                    "last_modified": 123,
                }
            }
            name_index = _build_name_index(db)
            strict_db = _size_verified_database(
                db, [Reference()], name_index
            )
            self.assertEqual(strict_db, {})
            collected, linked, missing, missing_refs, _linked_refs = _simulate_fix(
                AbletonSet(), [Reference()], db, name_index
            )

            self.assertEqual(collected, [])
            self.assertEqual(linked, [])
            self.assertEqual(missing, ["same.wav"])
            self.assertEqual(len(missing_refs), 1)

            class SizeMatchReference(Reference):
                size = source.stat().st_size
                last_modified = 0

            collected, linked, missing, _refs, _linked_refs = _simulate_fix(
                AbletonSet(), [SizeMatchReference()], db, name_index
            )
            self.assertEqual(collected, ["same.wav"])
            self.assertEqual(linked, [])
            self.assertEqual(missing, [])
            collected, linked, missing, _refs, linked_refs = _simulate_fix(
                AbletonSet(), [SizeMatchReference()], db, name_index,
                mode="relink",
            )
            self.assertEqual(collected, [])
            self.assertEqual(linked, ["same.wav"])
            self.assertEqual(missing, [])
            self.assertEqual(len(linked_refs), 1)
            strict_db = _size_verified_database(
                db, [SizeMatchReference()], name_index
            )
            self.assertIsNone(strict_db[str(source)]["last_modified"])

    def test_resolution_modes_choose_relink_or_collect_without_ambiguity(self):
        for mode, expected_collect, expected_names in (
            ("relink", False, ([], ["same.wav"])),
            ("collect", True, (["same.wav"], [])),
        ):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "Project"
                root.mkdir()
                source = Path(tmp) / "Library" / "same.wav"
                source.parent.mkdir()
                source.write_bytes(b"matched-audio")
                als_path = root / "Set.als"
                als_path.touch()

                class Reference:
                    name = "same.wav"
                    size = source.stat().st_size
                    absolute = source
                    relative = Path("Samples/Imported/same.wav")
                    relative_exists = False
                    pack_resolved = False
                    live_pack = None

                    @staticmethod
                    def get_relative_type():
                        return 1

                    @staticmethod
                    def get_relative_value():
                        return "Samples/Imported/same.wav"

                state = {"fixed": False, "collect_modes": []}

                class Samples:
                    def __init__(self):
                        self._cache = [Reference()]

                    def check(self):
                        return [] if state["fixed"] else [Reference()]

                    @staticmethod
                    def iterate():
                        return iter([Reference()])

                    @staticmethod
                    def fix(db, collect_and_save):
                        state["collect_modes"].append(collect_and_save)
                        state["fixed"] = True
                        return True

                class FakeAbletonSet:
                    def __init__(self, _path):
                        self.samples = Samples()

                    @staticmethod
                    def parse():
                        return True

                    @staticmethod
                    def find_project_root_folder():
                        return root

                    @staticmethod
                    def save_set():
                        pass

                database = {
                    str(source): {
                        "name": source.name,
                        "size": source.stat().st_size,
                        "last_modified": 123,
                    }
                }
                with patch(
                    "missing_media_resolver.AbletonSet", FakeAbletonSet
                ):
                    result = resolve_missing_media(
                        als_path, database, mode=mode
                    )

                self.assertTrue(result["success"])
                self.assertEqual(result["fixed"], 1)
                self.assertEqual(state["collect_modes"], [expected_collect])
                self.assertEqual(
                    (result["fixed_names"], result["linked_names"]),
                    expected_names,
                )
                if mode == "relink":
                    report = format_bulk_report(
                        {str(als_path): result}, mode=mode
                    )
                    self.assertIn("OK relinked:      same.wav", report)

    def test_external_samples_are_classified_by_resolved_project_location(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "Project"
            (root / "Samples").mkdir(parents=True)
            (root / "Samples" / "inside.wav").touch()
            external = Path(temporary_directory) / "outside.wav"
            external.touch()

            class Reference:
                def __init__(self, name, relative, absolute):
                    self.name = name
                    self.relative = relative
                    self.absolute = absolute
                    self.relative_exists = (root / relative).exists()
                    self.pack_resolved = False
                    self.live_pack = None

                @staticmethod
                def get_relative_type():
                    return 3

            class Samples:
                def iterate(self):
                    return iter(
                        [
                            Reference("inside.wav", Path("Samples/inside.wav"), external),
                            Reference("outside.wav", Path("Samples/missing.wav"), external),
                        ]
                    )

            class AbletonSet:
                samples = Samples()

                @staticmethod
                def find_project_root_folder():
                    return root

            self.assertEqual(
                _collect_external_sample_names(
                    AbletonSet(), root / "Project.als"
                ),
                ["outside.wav"],
            )

    def test_builds_overlapping_categories_and_saves_json(self):
        results = {
            r"C:\Projects\both.als": {
                "success": True,
                "missing_before": 3,
                "missing_after": 1,
                "fixed": 2,
                "external_sample_refs": 1,
                "external_sample_names": ["outside.wav"],
                "still_missing_names": ["lost.wav"],
                "missing_sample_details": [
                    {"name": "lost.wav", "reason": "not_in_index"},
                    {"name": "LOST.WAV", "reason": "not_in_index"},
                    {"name": "unknown.wav", "reason": "size_mismatch"},
                ],
            },
            r"C:\Projects\portable.als": {
                "success": True,
                "missing_before": 0,
                "missing_after": 0,
                "fixed": 0,
            },
            r"C:\Projects\error.als": {
                "success": False,
                "error": "unreadable project",
            },
        }

        snapshot = build_project_media_status(results)
        self.assertEqual(
            snapshot["categories"]["non_portable"],
            [r"C:\Projects\both.als"],
        )
        self.assertEqual(
            snapshot["categories"]["unresolved_missing_media"],
            [r"C:\Projects\both.als"],
        )
        self.assertEqual(
            snapshot["categories"]["fully_portable"],
            [r"C:\Projects\portable.als"],
        )
        both = next(
            project for project in snapshot["projects"]
            if project["path"] == r"C:\Projects\both.als"
        )
        self.assertEqual(both["media_status"], "Missing")
        self.assertEqual(snapshot["confirmed_missing_samples"], ["lost.wav"])

        linked_snapshot = build_project_media_status({
            "linked.als": {
                "success": True,
                "missing_before": 2,
                "missing_after": 0,
                "external_sample_refs": 2,
            }
        })
        self.assertEqual(
            linked_snapshot["projects"][0]["media_status"], "Linked"
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "project_media_status.json"
            self.assertEqual(
                save_project_media_status(results, output_path),
                output_path,
            )
            saved = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 1)
        self.assertEqual(saved["categories"], snapshot["categories"])

    def test_save_can_merge_selected_project_results(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "status.json"
            first = str(Path(temporary_directory) / "first.als")
            second = str(Path(temporary_directory) / "second.als")
            save_project_media_status({
                first: {
                    "success": True,
                    "missing_before": 0,
                    "missing_after": 0,
                    "fixed": 0,
                    "external_sample_refs": 0,
                }
            }, output_path)

            save_project_media_status({
                second: {
                    "success": True,
                    "missing_before": 1,
                    "missing_after": 0,
                    "fixed": 1,
                    "external_sample_refs": 1,
                }
            }, output_path, merge=True)

            snapshot = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {project["path"] for project in snapshot["projects"]},
                {first, second},
            )
            self.assertEqual(snapshot["categories"]["fully_portable"], [first])
            self.assertEqual(snapshot["categories"]["non_portable"], [second])


if __name__ == "__main__":
    unittest.main()
