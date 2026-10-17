import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import ableton_build
import app_config
import osc_installer
import project_and_file_naming as naming
import system_deps


class ProjectNamingTest(unittest.TestCase):
    def test_stopword_vocabulary_matches_previous_corpus(self):
        self.assertEqual(len(naming.ENGLISH_STOPWORDS), 198)
        digest = hashlib.sha256(
            "\n".join(sorted(naming.ENGLISH_STOPWORDS)).encode()
        ).hexdigest()
        self.assertEqual(
            digest, "47608d511aa4fec95139d41e487109ab4a260313745d397210dbf966b1d3c225"
        )

    def test_naming_preserves_existing_tokenization(self):
        cases = [
            ("The Sound of a Dream 3.1.als", "sound dream 3.1.als", "SoundDream3.1"),
            ("YOU'RE in the MIX", "mix", "Mix"),
            ("The, And. dance", "the, and. dance", "TheAnd."),
            ("", "", ""),
            ("THE AND OF", "", ""),
            ("  Bright\tLights  ", "bright lights", "BrightLights"),
        ]
        for text, filtered, shortened in cases:
            with self.subTest(text=text):
                self.assertEqual(naming.remove_stopwords(text), filtered)
                self.assertEqual(naming.shorten_project_name(text), shortened)


class SetupConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / "config.json"
        self.old = {
            "export_path": str(self.root / "old export"),
            "project_directories": [str(self.root / "missing project")],
            "sample_directories": ["preserved samples"],
            "naming": {"main_mix_include_bpm": True},
        }
        self.config.write_text(json.dumps(self.old), encoding="utf-8")
        self.enterContext(patch.object(app_config, "CONFIG_FILE", str(self.config)))
        self.enterContext(patch.object(ableton_build, "ensure_detected", return_value={}))

    def test_explicit_paths_replace_old_machine_paths(self):
        project = self.root / "new project"
        project.mkdir()
        export = self.root / "new export"
        with redirect_stdout(io.StringIO()):
            code = system_deps.main([
                "--export-path", str(export), "--project-dir", str(project),
                "--project-dir", str(project), "--overwrite",
            ])
        self.assertEqual(code, 0)
        self.assertTrue(export.is_dir())
        saved = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(saved["export_path"], str(export))
        self.assertEqual(saved["project_directories"], [str(project)])
        self.assertEqual(saved["sample_directories"], self.old["sample_directories"])
        self.assertEqual(saved["naming"], self.old["naming"])

    def test_invalid_project_fails_without_saving_or_creating_export(self):
        export = self.root / "new export"
        error = io.StringIO()
        with redirect_stderr(error):
            code = system_deps.main([
                "--export-path", str(export), "--project-dir", str(self.root / "missing"),
                "--overwrite",
            ])
        self.assertEqual(code, 1)
        self.assertIn("Project folder does not exist", error.getvalue())
        self.assertFalse(export.exists())
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), self.old)

    def test_invalid_ableton_choice_fails(self):
        with patch.object(ableton_build, "resolve_choice", return_value=(None, "not Live")), \
             redirect_stderr(io.StringIO()) as error:
            code = system_deps.main(["--ableton-exe", "invalid.exe"])
        self.assertEqual(code, 1)
        self.assertIn("not Live", error.getvalue())

    def test_configuration_write_failure_is_not_success(self):
        with patch.object(app_config, "update", return_value=False), \
             redirect_stderr(io.StringIO()) as error:
            code = system_deps.main([
                "--export-path", str(self.root / "new export"), "--overwrite",
            ])
        self.assertEqual(code, 1)
        self.assertIn("Could not save setup choices", error.getvalue())

    def test_missing_tesseract_fails_setup(self):
        with patch.object(system_deps, "configure_tesseract", return_value=None), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(system_deps.main(["--setup"]), 1)
        self.assertIn("NOT FOUND", output.getvalue())

    def test_dependency_cli_prints_details_and_returns_status(self):
        for ok in (True, False):
            with self.subTest(ok=ok), \
                 patch.object(system_deps, "check", return_value=[("Dependency", ok, "details")]), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(system_deps.main(["--check"]), 0 if ok else 1)
                self.assertIn(f"[{'OK' if ok else '!!'}] Dependency: details", output.getvalue())

    def test_rerun_without_new_choices_preserves_configuration(self):
        self.assertEqual(system_deps.configure(), [])
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), self.old)

    def test_dependency_check_accepts_selected_beta_and_reports_stale_paths(self):
        with patch.object(system_deps, "configure_tesseract", return_value="tesseract.exe"), \
             patch.object(ableton_build, "get_build", return_value={"exe": "Live Beta.exe"}), \
             patch.object(ableton_build, "user_library_path", return_value="library") as library, \
             patch.object(osc_installer, "is_up_to_date", return_value=True):
            results = {name: (ok, detail) for name, ok, detail in system_deps.check()}
        library.assert_called_once_with()
        self.assertTrue(results["Ableton Live 12"][0])
        self.assertFalse(results["Export folder"][0])
        self.assertFalse(results["Project folders"][0])
        self.assertIn("missing project", results["Project folders"][1])
        self.assertNotIn("NLTK data", results)

    def test_skip_osc_is_honored_by_final_check(self):
        with patch.object(system_deps, "configure_tesseract", return_value="tesseract.exe"), \
             patch.object(ableton_build, "get_build", return_value={"exe": "Live.exe"}), \
             patch.object(ableton_build, "user_library_path") as library:
            results = system_deps.check(skip_osc=True)
        library.assert_not_called()
        self.assertNotIn("AbletonOSC", [name for name, _, _ in results])

    def test_tesseract_path_write_failure_is_explicit(self):
        with patch.object(system_deps, "_tesseract_cmd", None), \
             patch.object(system_deps, "find_tesseract", return_value="tesseract.exe"), \
             patch.object(app_config, "set", return_value=False):
            with self.assertRaisesRegex(OSError, "Could not save Tesseract"):
                system_deps.configure_tesseract()


class UserLibraryDetectionTest(unittest.TestCase):
    def test_preference_paths_decode_xml_entities_and_preserve_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = root / "Live 12.4.6" / "Preferences" / "Library.cfg"
            cfg.parent.mkdir(parents=True)
            cfg.write_text(
                '<Ableton><UserLibrary><LibraryProject Id="1">'
                '<ProjectPath Value="D:\\Music &amp; Audio"/>'
                '<ProjectName Value="B&#233;ats"/></LibraryProject></UserLibrary></Ableton>',
                encoding="utf-8",
            )
            with patch.object(ableton_build, "ABLETON_APPDATA", root), \
                 patch.object(ableton_build, "load_selected_build", return_value="release"), \
                 patch.object(ableton_build.os.path, "isdir", return_value=True):
                self.assertEqual(
                    ableton_build.user_library_path(),
                    os.path.normpath("D:\\Music & Audio\\B\u00e9ats"),
                )

    def test_corrupt_newest_preferences_are_logged_and_older_copy_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version, content in [
                ("12.4.6", "<invalid"),
                ("12.3", '<Ableton><UserLibrary><LibraryProject Id="1">'
                 '<ProjectPath Value="D:\\Music"/>'
                 '<ProjectName Value="User Library"/></LibraryProject></UserLibrary></Ableton>'),
            ]:
                cfg = root / f"Live {version}" / "Preferences" / "Library.cfg"
                cfg.parent.mkdir(parents=True)
                cfg.write_text(content, encoding="utf-8")
            with patch.object(ableton_build, "ABLETON_APPDATA", root), \
                 patch.object(ableton_build, "load_selected_build", return_value="release"), \
                 patch.object(ableton_build.os.path, "isdir", return_value=True), \
                 self.assertLogs("Orchestration", level="WARNING") as logs:
                self.assertEqual(
                    ableton_build.user_library_path(), os.path.normpath("D:\\Music\\User Library")
                )
            self.assertIn("Could not read Ableton User Library preferences", logs.output[0])


if __name__ == "__main__":
    unittest.main()
