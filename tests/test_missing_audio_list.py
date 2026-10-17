import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from project_media_status import (
    format_project_missing_lines,
    save_project_media_status,
)


class MissingAudioListTest(unittest.TestCase):
    def test_list_written_next_to_status_with_paths(self):
        results = {
            r"C:\P\a.als": {
                "success": True,
                "missing_after": 1,
                "still_missing_names": ["kick.wav"],
                "missing_sample_details": [
                    {"name": "kick.wav", "stored_path": r"D:\old\kick.wav",
                     "reason": "not_in_index"}
                ],
            },
            r"C:\P\b.als": {"success": True, "missing_after": 0},
        }
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "project_media_status.json"
            save_project_media_status(results, output_path=status)
            text = (Path(tmp) / "missing_audio_list.txt").read_text("utf-8")
        self.assertIn("Referenced In:\n            a", text)
        self.assertIn("kick.wav", text)
        self.assertIn(r"D:\old\kick.wav", text)
        self.assertNotIn("b.als", text)

    def test_names_without_details_still_listed(self):
        lines = format_project_missing_lines(
            {"unresolved_sample_names": ["snare.wav"]}
        )
        self.assertIn("snare.wav", lines[0])


if __name__ == "__main__":
    unittest.main()
