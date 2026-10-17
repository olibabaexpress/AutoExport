import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from create_db_patch import create_or_update_db


class SampleIndexPauseTests(unittest.TestCase):
    def test_indexing_waits_while_paused_then_finishes_after_continue(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "samples"
            root.mkdir()
            sample = root / "loop.wav"
            sample.write_bytes(b"sample")
            database_path = Path(temporary_directory) / "sample-index.json"
            continue_event = threading.Event()
            discovered = threading.Event()
            errors = []

            def on_progress(stage, _current, _total):
                if stage == "discovering":
                    discovered.set()

            def build_index():
                try:
                    create_or_update_db(
                        [str(root)],
                        db_path=database_path,
                        progress_callback=on_progress,
                        pause_event=continue_event,
                    )
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=build_index)
            worker.start()
            self.assertTrue(discovered.wait(timeout=2))
            worker.join(timeout=0.1)
            self.assertTrue(worker.is_alive(), "indexing should wait while paused")

            continue_event.set()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive(), "indexing did not resume")
            self.assertEqual(errors, [])
            self.assertTrue(database_path.exists())
            self.assertIn(str(sample.resolve()), json.loads(database_path.read_text()))


if __name__ == "__main__":
    unittest.main()
