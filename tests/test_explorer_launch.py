import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import explorer_launch


class ExplorerLaunchTest(unittest.TestCase):
    def test_paths_from_argv_skips_flags_missing_and_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            result = explorer_launch.paths_from_argv(
                ["--configure", folder, folder, os.path.join(folder, "nope")]
            )
        self.assertEqual(result, [os.path.normpath(folder)])

    def test_concurrent_launches_merge_into_one_leader(self):
        with tempfile.TemporaryDirectory() as root:
            a = os.path.join(root, "a")
            b = os.path.join(root, "b")
            os.mkdir(a)
            os.mkdir(b)
            with patch.object(explorer_launch, "SPOOL_FILE", os.path.join(root, "s.txt")), \
                 patch.object(explorer_launch, "LOCK_FILE", os.path.join(root, "l.lock")), \
                 patch.object(explorer_launch, "INSTANCE_FILE", os.path.join(root, "instance.json")), \
                 patch.object(explorer_launch, "GATHER_SECONDS", 0):
                # A second process arrived first and holds the lock.
                Path(explorer_launch.LOCK_FILE).touch()
                self.assertIsNone(explorer_launch.gather_launch_paths([b]))
                os.remove(explorer_launch.LOCK_FILE)
                result = explorer_launch.gather_launch_paths([a])
        self.assertEqual(result, [os.path.normpath(b), os.path.normpath(a)])


if __name__ == "__main__":
    unittest.main()
