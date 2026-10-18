import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import explorer_launch as explorer_launch


class SingleInstanceTest(unittest.TestCase):
    def run_server(self, root, ephemeral):
        info = os.path.join(root, "instance.json")
        patcher = patch.object(explorer_launch, "INSTANCE_FILE", info)
        patcher.start()
        self.addCleanup(patcher.stop)
        server = explorer_launch.InstanceServer(ephemeral)
        self.assertTrue(server.start())
        self.addCleanup(server.stop)
        return server

    def test_paths_are_forwarded_to_running_instance(self):
        with tempfile.TemporaryDirectory() as root:
            server = self.run_server(root, ephemeral=False)
            self.assertTrue(explorer_launch.forward_to_running([root]))
            self.assertEqual(server.requests.get(timeout=2), [os.path.normpath(root)])

    def test_plain_second_launch_blocked_only_for_right_click_session(self):
        with tempfile.TemporaryDirectory() as root:
            self.run_server(root, ephemeral=False)
            self.assertFalse(explorer_launch.forward_to_running([]))
        with tempfile.TemporaryDirectory() as root:
            self.run_server(root, ephemeral=True)
            self.assertTrue(explorer_launch.forward_to_running([]))

    def test_no_running_instance(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(explorer_launch, "INSTANCE_FILE", os.path.join(root, "x.json")):
                self.assertFalse(explorer_launch.forward_to_running(["a"]))


if __name__ == "__main__":
    unittest.main()
