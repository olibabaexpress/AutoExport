import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import missing_media_resolver as resolver
import network_index as network_index
import sample_database as sample_database


class FakeRef:
    def __init__(self, name="kick.wav"):
        self.name = name
        self.relative = None
        self.relative_type = None

    def set_relative(self, value):
        self.relative = value

    def set_relative_type(self, value):
        self.relative_type = value


class NetworkIndexTest(unittest.TestCase):
    def test_to_unc_translates_drive_letters(self):
        with mock.patch.object(network_index, "_drive_root", return_value="\\\\TOWA\\G$\\"):
            self.assertEqual(
                network_index.to_unc("TOWA", "G:\\OCTATRACK\\a.wav"),
                "\\\\TOWA\\G$\\OCTATRACK\\a.wav",
            )
        with mock.patch.object(network_index, "_drive_root", return_value=None):
            self.assertIsNone(network_index.to_unc("TOWA", "G:\\a.wav"))
        self.assertIsNone(network_index.to_unc("TOWA", "relative\\a.wav"))

    def test_device_from_path(self):
        self.assertEqual(network_index.device_from_path("//Towa/c/Users/x.json"), "Towa")
        self.assertEqual(network_index.device_from_path("\\\\TOWA\\C$\\x.json"), "TOWA")
        self.assertIsNone(network_index.device_from_path("C:\\x.json"))

    def test_import_index_copies_and_registers(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "remote.json"
            source.write_text(json.dumps({
                "G:\\OCTATRACK\\a.wav": {"name": "a.wav", "size": 5, "last_modified": 1},
            }))
            destination = Path(tmp) / "abletoolz_sample_db_TOWA.json"
            saved = {}
            with mock.patch.object(network_index, "index_file_for", return_value=destination), \
                    mock.patch.object(network_index, "_save_entry",
                                      side_effect=lambda d, e: saved.update({d: e})):
                entry = network_index.import_index(source, "TOWA")
            self.assertTrue(destination.exists())
            self.assertEqual(entry["count"], 1)
            self.assertIn("TOWA", saved)
            self.assertIn("G:", entry["directories"][0])

    def test_automatic_dirs_include_roaming_ableton(self):
        with tempfile.TemporaryDirectory() as tmp:
            roaming = Path(tmp) / "Ableton"
            roaming.mkdir()
            with mock.patch.dict(os.environ, {"APPDATA": tmp}):
                self.assertIn(str(roaming), [str(p) for p in sample_database.automatic_sample_dirs()])
                added = sample_database._with_automatic_dirs(["C:\\other"])
                self.assertEqual(len(added), 2)

    def test_copy_network_sample_goes_into_project_imported_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "net" / "kick.wav"
            source.parent.mkdir()
            source.write_bytes(b"abc")
            project = Path(tmp) / "Project"
            project.mkdir()
            ref = FakeRef()
            resolver._copy_network_sample(ref, str(source), project)
            self.assertEqual((project / "Samples" / "Imported" / "kick.wav").read_bytes(), b"abc")
            self.assertEqual(ref.relative, "Samples/Imported/kick.wav")
            self.assertEqual(ref.relative_type, 3)
    def test_copy_sample_with_asd_copies_analysis_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.wav"
            source.write_bytes(b"x")
            Path(str(source) + ".asd").write_bytes(b"asd")
            target = Path(tmp) / "out" / "a.wav"
            target.parent.mkdir()
            resolver._copy_sample_with_asd(source, target)
            self.assertTrue(Path(str(target) + ".asd").exists())

    def test_find_all_projects_skips_dot_files(self):
        import project_scanner as project_scanner

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "real.als").write_bytes(b"")
            (Path(tmp) / ".blue.als").write_bytes(b"")
            found = project_scanner.find_all_projects([tmp])
            self.assertEqual([Path(p).name for p in found], ["real.als"])

    def test_merge_keeps_status_of_projects_outside_the_run(self):
        import project_media_status as pms

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "status.json"
            ok = {"success": True, "missing_before": 0, "missing_after": 0}
            pms.save_project_media_status({"C:\\a.als": ok}, out, merge=True)
            pms.save_project_media_status({"C:\\b.als": ok}, out, merge=True)
            names = [p["name"] for p in json.loads(out.read_text())["projects"]]
            self.assertEqual(sorted(names), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
