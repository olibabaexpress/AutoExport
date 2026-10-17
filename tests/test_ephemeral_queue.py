import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from task_manager import TaskManager


class EphemeralQueueTest(unittest.TestCase):
    def make(self, root, ephemeral):
        return TaskManager(
            task_file=os.path.join(root, "tasks.json"),
            cache_file=os.path.join(root, "cache.json"),
            flag_file=os.path.join(root, "flag"),
            ephemeral=ephemeral,
        )

    def add(self, manager, name):
        manager.add_task(name, False, True, False)
        manager.flush_pending_save()

    def test_right_click_queue_leaves_saved_queue_restorable(self):
        with tempfile.TemporaryDirectory() as root:
            normal = self.make(root, False)
            self.add(normal, "saved.als")
            normal.close()

            temp = self.make(root, True)
            self.assertEqual(temp.get_tasks(), [])
            self.add(temp, "temp.als")
            self.assertTrue(temp.detached)
            self.assertFalse(
                any(t["project_path"] == "temp.als"
                    for t in self.make(root, False).get_tasks())
            )
            temp.commit_for_export()
            self.assertFalse(temp.detached)
            temp.close()

            restored = self.make(root, False)
            self.assertEqual([t["project_path"] for t in restored.get_tasks()], ["saved.als"])
            self.assertFalse(os.path.exists(os.path.join(root, "flag")))
            restored.close()


if __name__ == "__main__":
    unittest.main()
