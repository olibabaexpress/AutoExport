from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import export_automation as export_automation


class ExportResponsivenessTest(unittest.TestCase):
    def test_brief_hang_does_not_kill_ableton(self):
        monitor = export_automation._ExportResponsivenessMonitor()
        with patch.object(export_automation.ableton_state, "main_window",
                          return_value=SimpleNamespace(hung=True)), \
             patch.object(export_automation.time, "monotonic", side_effect=[0, 5.9]), \
             patch.object(export_automation.ableton_state, "kill_ableton") as kill:
            self.assertTrue(monitor.check())
            self.assertTrue(monitor.check())
        kill.assert_not_called()

    def test_sustained_hang_kills_ableton_and_aborts_export(self):
        monitor = export_automation._ExportResponsivenessMonitor()
        with patch.object(export_automation.ableton_state, "main_window",
                          return_value=SimpleNamespace(hung=True)), \
             patch.object(export_automation.time, "monotonic", side_effect=[0, 6]), \
             patch.object(export_automation.ableton_state, "kill_ableton",
                          return_value=True) as kill:
            self.assertTrue(monitor.check())
            self.assertFalse(monitor.check())
        kill.assert_called_once_with()

    def test_responsiveness_recovery_restarts_timeout(self):
        monitor = export_automation._ExportResponsivenessMonitor()
        with patch.object(
            export_automation.ableton_state,
            "main_window",
            side_effect=[
                SimpleNamespace(hung=True),
                SimpleNamespace(hung=False),
                SimpleNamespace(hung=True),
                SimpleNamespace(hung=True),
            ],
        ), patch.object(
            export_automation.time, "monotonic", side_effect=[0, 1, 2, 8]
        ), patch.object(
            export_automation.ableton_state, "kill_ableton", return_value=True
        ) as kill:
            self.assertTrue(monitor.check())
            self.assertTrue(monitor.check())
            self.assertTrue(monitor.check())
            self.assertFalse(monitor.check())
        kill.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
