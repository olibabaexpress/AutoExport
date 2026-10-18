import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import win32con
import win32file
import rendered_files as rendered_files


def sharing_error():
    error = PermissionError("file in use")
    error.winerror = 32
    return error


class RenderedFilesTest(unittest.TestCase):
    def test_real_windows_lock_never_creates_destination_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "render.wav"
            destination = Path(directory) / "renamed.wav"
            source.write_bytes(b"render data")
            handle = win32file.CreateFile(
                str(source), win32con.GENERIC_READ, win32con.FILE_SHARE_READ,
                None, win32con.OPEN_EXISTING, 0, None,
            )
            try:
                self.assertFalse(rendered_files.file_is_released(source))
                with self.assertRaises(PermissionError):
                    rendered_files.move_rendered_file(source, destination, timeout=0)
                self.assertTrue(source.exists())
                self.assertFalse(destination.exists())
            finally:
                handle.Close()
            self.assertTrue(rendered_files.file_is_released(source))
            rendered_files.move_rendered_file(source, destination)
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"render data")

    def test_transient_sharing_error_is_retried(self):
        with patch.object(rendered_files.os, "replace", side_effect=[sharing_error(), None]) as move, \
             patch.object(rendered_files.time, "sleep") as sleep:
            rendered_files.move_rendered_file("source", "dest")
        self.assertEqual(move.call_count, 2)
        sleep.assert_called_once_with(0.2)

    def test_access_denied_is_not_retried(self):
        error = PermissionError("access denied")
        error.winerror = 5
        with patch.object(rendered_files.os, "replace", side_effect=error) as move:
            with self.assertRaises(PermissionError):
                rendered_files.move_rendered_file("source", "dest")
        move.assert_called_once()

    def test_waits_for_stable_snapshot_and_released_handles(self):
        snapshots = [
            {"render.wav": (1, 10)}, {"render.wav": (2, 20)},
            {"render.wav": (2, 20)}, {"render.wav": (2, 20)},
        ]
        with patch.object(rendered_files.time, "monotonic", side_effect=[0, 0, 0, 0.5, 1, 1.5]), \
             patch.object(rendered_files.time, "sleep"), \
             patch.object(rendered_files, "file_is_released", side_effect=[False, False, True, True]):
            self.assertTrue(rendered_files.wait_for_finished_files(
                lambda: snapshots.pop(0), "folder"
            ))
        self.assertEqual(snapshots, [])

    def test_locked_or_empty_output_times_out(self):
        for snapshot in ({}, {"render.wav": (1, 0)}, {"render.wav": (1, 10)}):
            with self.subTest(snapshot=snapshot), \
                 patch.object(rendered_files, "file_is_released", return_value=False):
                self.assertFalse(rendered_files.wait_for_finished_files(
                    lambda: snapshot, "folder", timeout=0
                ))

    def test_abort_check_stops_waiting(self):
        snapshot = lambda: self.fail("snapshot should not be read after abort")
        self.assertFalse(rendered_files.wait_for_finished_files(
            snapshot, "folder", abort_check=lambda: False
        ))

    def test_same_path_does_not_replace_itself(self):
        with patch.object(rendered_files.os, "replace") as move:
            rendered_files.move_rendered_file("same.wav", os.path.abspath("same.wav"))
        move.assert_not_called()


if __name__ == "__main__":
    unittest.main()
