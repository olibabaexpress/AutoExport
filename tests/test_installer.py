import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import ableton_build as ableton_build
import app_config as app_config
import installer_setup as installer_setup
import osc_installer as osc_installer
import runtime_launch as runtime_launch
import system_deps as system_deps


class RuntimeLaunchTest(unittest.TestCase):
    def test_worker_console_is_available_in_every_mode(self):
        """The worker console is the export's progress display and cancel key.

        Ctrl+C there interrupts only the worker; the GUI window stays open.
        """
        for frozen in (True, False):
            with self.subTest(frozen=frozen), patch.object(sys, "frozen", frozen, create=True):
                self.assertEqual(runtime_launch.export_creationflags(), subprocess.CREATE_NEW_CONSOLE)

    def test_spec_gives_the_worker_a_console_and_the_gui_none(self):
        spec = Path(__file__).resolve().parents[1] / "packaging" / "AutoExport.spec"
        text = spec.read_text(encoding="utf-8")
        self.assertRegex(text, r'name="AutoExportWorker",\s*console=True')
        self.assertRegex(text, r'name="AutoExport",\s*console=False')

    def test_source_worker_uses_console_python_and_script(self):
        with patch.object(sys, "frozen", False, create=True), \
             patch.object(sys, "executable", r"C:\Python\pythonw.exe"), \
             patch.object(Path, "is_file", return_value=True):
            command = runtime_launch.export_command(r"D:\Tasks\tasks.json")
        self.assertEqual(command[0], r"C:\Python\python.exe")
        self.assertEqual(Path(command[1]).name, "orchestration.py")
        self.assertEqual(command[2], r"D:\Tasks\tasks.json")

    def test_frozen_worker_is_a_separate_executable(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", r"C:\AutoExport\AutoExport.exe"), \
             patch.object(Path, "is_file", return_value=True):
            self.assertEqual(runtime_launch.export_command("tasks.json"), [
                r"C:\AutoExport\AutoExportWorker.exe", "export", "tasks.json",
            ])

    def test_missing_frozen_worker_is_an_explicit_error(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(Path, "is_file", return_value=False):
            with self.assertRaisesRegex(FileNotFoundError, "Reinstall"):
                runtime_launch.export_command("tasks.json")

    def test_frozen_paths_keep_resources_separate_from_writable_state(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            bundle = folder / "install" / "_internal"
            state = folder / "state"
            code = (
                "import sys; sys.frozen = True; sys.executable = sys.argv[1]; "
                "sys._MEIPASS = sys.argv[2]; import paths; "
                "assert paths.ROOT_DIR == sys.argv[3]; "
                "assert paths.VENDOR_DIR == paths.os.path.join(sys.argv[2], 'vendor')"
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root / "app")
            environment["AUTOEXPORT_HOME"] = str(state)
            result = subprocess.run(
                [sys.executable, "-c", code, str(bundle.parent / "AutoExport.exe"),
                 str(bundle), str(state)],
                env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((state / "data").is_dir())
            self.assertFalse((bundle / "data").exists())


class InstallerChoicesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = self.root / "User Library"
        self.project = self.root / "Projects"
        self.export = self.root / "Exports"
        self.library.mkdir()
        self.project.mkdir()
        self.info = {"exe": "Live.exe", "is_beta": True}
        self.enterContext(patch.object(ableton_build, "validate_exe", return_value=(self.info, None)))
        self.enterContext(patch.object(system_deps, "configure_tesseract", return_value="tesseract.exe"))
        self.install = self.enterContext(patch.object(osc_installer, "install", return_value=(True, "ok")))
        self.enterContext(patch.object(
            app_config, "get",
            side_effect=lambda key: {"release_exe": "release.exe"} if key == "ableton" else [],
        ))
        self.update = self.enterContext(patch.object(app_config, "update", return_value=True))

    def test_save_installs_osc_and_preserves_other_build(self):
        installer_setup.apply_choices("Live.exe", str(self.library), str(self.export), str(self.project))
        self.install.assert_called_once_with(str(self.library))
        saved = self.update.call_args.args[0]
        self.assertEqual(saved["ableton"]["build"], "beta")
        self.assertEqual(saved["ableton"]["release_exe"], "release.exe")
        self.assertEqual(saved["user_library_path"], str(self.library))
        self.assertEqual(saved["project_directories"], [str(self.project)])
        self.assertTrue(saved["installer_configured"])
        self.assertTrue(self.export.is_dir())

    def test_missing_library_does_not_install_or_save(self):
        with self.assertRaisesRegex(ValueError, "User Library"):
            installer_setup.apply_choices("Live.exe", "missing", str(self.export), str(self.project))
        self.install.assert_not_called()
        self.update.assert_not_called()

    def test_reconfiguration_preserves_additional_valid_project_roots(self):
        other = self.root / "More Projects"
        other.mkdir()
        with patch.object(app_config, "get", side_effect=lambda key: (
            {} if key == "ableton" else [str(self.project), str(other), "missing"]
        )):
            installer_setup.apply_choices("Live.exe", str(self.library), str(self.export), str(self.project))
        self.assertEqual(
            self.update.call_args.args[0]["project_directories"], [str(self.project), str(other)]
        )
    def test_osc_failure_is_not_configuration_success(self):
        self.install.return_value = (False, "permission denied")
        with self.assertRaisesRegex(OSError, "permission denied"):
            installer_setup.apply_choices("Live.exe", str(self.library), str(self.export), str(self.project))
        self.update.assert_not_called()

    def test_config_save_failure_is_explicit(self):
        self.update.return_value = False
        with self.assertRaisesRegex(OSError, "Could not save configuration"):
            installer_setup.apply_choices("Live.exe", str(self.library), str(self.export), str(self.project))

    def test_saved_library_is_used_when_preferences_are_unavailable(self):
        with patch.object(ableton_build, "ABLETON_APPDATA", self.root), \
             patch.object(ableton_build, "load_selected_build", return_value="release"), \
             patch.object(app_config, "get", return_value=str(self.library)):
            self.assertEqual(ableton_build.user_library_path(), str(self.library))


if __name__ == "__main__":
    unittest.main()
