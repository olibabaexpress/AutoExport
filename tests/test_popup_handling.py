"""Regression tests for the slow-disk popup-handling races.

The PC these run against exports to an HDD, so dialogs and prompts that the
laptop raises within 200ms can appear seconds later. Every test drives a
virtual clock, so the "slow machine" cases are deterministic and instant.
"""

import contextlib
import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import popup_automation
import window_utils

SAVE_DLG = "Save Live Set As:"
AUDIO_DLG = "Save Audio File As:"
CONFIRM = "Confirm Save As"


class Clock:
    """Virtual clock so slow-dialog scenarios run instantly."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    @property
    def fake(self):
        return FakeClockModule(self)

    @property
    def elapsed(self):
        return self.now - 1000.0


class FakeClockModule:
    def __init__(self, clock):
        self.time = clock.time
        self.monotonic = clock.monotonic
        self.sleep = clock.sleep


class WindowWorld:
    """Which windows are open, with mutations scheduled against the clock."""

    def __init__(self, clock, initial=()):
        self.clock = clock
        self.open = set(initial)
        self.events = []

    def schedule(self, at, fn):
        # `at` is a delay relative to *now*, not an absolute clock time - the
        # clock starts at 1000.0, so a bare 1.5 would fire on the first poll.
        self.events.append((self.clock.now + at, fn))

    def titles_now(self):
        due = [event for event in self.events if self.clock.now >= event[0]]
        for _, fn in due:
            fn()
        self.events = [e for e in self.events if self.clock.now < e[0]]
        return set(self.open)

    def win_exists(self, title):
        return any(title.lower() in t.lower() for t in self.titles_now())


@contextlib.contextmanager
def live_set_save(world, confirm_side_effect=None):
    """Drive handle_save_live_set_as against a scripted window world."""
    clock = world.clock
    with patch.object(popup_automation, "time", clock.fake), \
         patch.object(popup_automation, "pyautogui", MagicMock()), \
         patch.object(popup_automation, "get_save_live_set_as", return_value=object()), \
         patch.object(popup_automation, "get_filename_from_dialog", return_value="track.als"), \
         patch.object(popup_automation, "set_text_on_edit_control", return_value=True), \
         patch.object(popup_automation.naming_settings, "load_settings",
                      return_value={"include_ableton_version": False}), \
         patch.object(popup_automation, "win_exists", world.win_exists), \
         patch.object(popup_automation, "_confirm_save_as_overwrite",
                      side_effect=confirm_side_effect) as confirm:
        yield confirm


class LateOverwritePromptTest(unittest.TestCase):
    """The PC failure: Alt+S is sent, Confirm Save As arrives ~1.5s later."""

    def test_late_overwrite_prompt_is_confirmed_and_dialog_closes(self):
        clock = Clock()
        world = WindowWorld(clock, initial={SAVE_DLG})
        world.schedule(1.5, lambda: world.open.add(CONFIRM))

        def confirm():
            world.open.discard(CONFIRM)
            world.schedule(0.4, lambda: world.open.discard(SAVE_DLG))

        with live_set_save(world, confirm) as confirm_mock:
            result = popup_automation.handle_save_live_set_as("12.0")

        self.assertTrue(result, "late Confirm Save As left the save dialog blocked")
        confirm_mock.assert_called_once()

    def test_undismissable_prompt_still_times_out(self):
        """A prompt that cannot be confirmed must not be silently reported OK."""
        clock = Clock()
        world = WindowWorld(clock, initial={SAVE_DLG})
        world.schedule(1.5, lambda: world.open.add(CONFIRM))

        with live_set_save(world, confirm_side_effect=lambda: None) as confirm_mock:
            result = popup_automation.handle_save_live_set_as("12.0")

        self.assertFalse(result, "reported success even though the dialog never closed")
        confirm_mock.assert_called_once()

    def test_no_overwrite_prompt_still_succeeds(self):
        clock = Clock()
        world = WindowWorld(clock, initial={SAVE_DLG})
        world.schedule(1.0, lambda: world.open.discard(SAVE_DLG))

        with live_set_save(world) as confirm_mock:
            result = popup_automation.handle_save_live_set_as("12.0")

        self.assertTrue(result)
        confirm_mock.assert_not_called()
class OverwriteConfirmationTest(unittest.TestCase):
    """handle_overwrite_confirmation guards every audio render."""

    def test_returns_as_soon_as_save_dialog_closes_without_prompt(self):
        """The common no-prompt path must not stall for the full timeout."""
        clock = Clock()
        world = WindowWorld(clock, initial={AUDIO_DLG})
        world.schedule(1.0, lambda: world.open.discard(AUDIO_DLG))

        with patch.object(popup_automation, "time", clock.fake), \
             patch.object(popup_automation, "win_exists", world.win_exists):
            result = popup_automation.handle_overwrite_confirmation(timeout=30)

        self.assertTrue(result)
        self.assertLess(clock.elapsed, 5, "waited far longer than the dialog was open")

    def test_late_prompt_is_confirmed(self):
        clock = Clock()
        world = WindowWorld(clock, initial={AUDIO_DLG})
        world.schedule(2.0, lambda: world.open.add(CONFIRM))

        with patch.object(popup_automation, "time", clock.fake), \
             patch.object(popup_automation, "win_exists", world.win_exists), \
             patch.object(popup_automation, "_confirm_save_as_overwrite") as confirm:
            result = popup_automation.handle_overwrite_confirmation(timeout=30)

        self.assertTrue(result)
        confirm.assert_called_once()


class ProgressBarAppearTimeoutTest(unittest.TestCase):
    def test_bar_that_appears_late_is_still_reported(self):
        clock = Clock()
        world = WindowWorld(clock)
        world.schedule(2.0, lambda: world.open.add("progress"))
        world.schedule(4.0, world.open.clear)

        def find_dialog(*_args):
            return (object(), 0.48, (100, 48)) if world.titles_now() else (None, None, None)

        with patch.object(popup_automation, "time", clock.fake), \
             patch.object(popup_automation, "_find_dialog_by_ratio_progress_bar",
                          side_effect=find_dialog):
            result = popup_automation.handle_progress_bar_wait(timeout=30, appear_timeout=5)

        self.assertTrue(result, "appear_timeout ignored - bar reported as never seen")


class ConfirmDialogButtonTest(unittest.TestCase):
    """The real 'Confirm Save As' dialog titles its buttons '&Yes'/'&No'.

    Matching the literal caption 'Yes' never finds them, which silently
    pushed every confirmation onto the Alt+Y fallback.
    """

    def _dialog(self, yes_caption):
        dialog = MagicMock()
        yes = MagicMock()
        yes.window_text.return_value = yes_caption
        yes.class_name.return_value = "Button"
        no = MagicMock()
        no.window_text.return_value = "&No"
        no.class_name.return_value = "Button"
        other = MagicMock()
        other.window_text.return_value = "Something Else"
        other.class_name.return_value = "Button"
        dialog.children.return_value = [other, no, yes]
        return dialog, yes

    def test_ampersand_caption_is_clicked(self):
        dialog, yes = self._dialog("&Yes")
        with patch.object(popup_automation, "_find_all_window_elements",
                          return_value=[dialog]), \
             patch.object(popup_automation, "pyautogui", MagicMock()) as gui:
            popup_automation._confirm_save_as_overwrite()

        yes.click.assert_called_once()
        gui.hotkey.assert_not_called()

    def test_plain_caption_is_clicked(self):
        dialog, yes = self._dialog("Yes")
        with patch.object(popup_automation, "_find_all_window_elements",
                          return_value=[dialog]), \
             patch.object(popup_automation, "pyautogui", MagicMock()) as gui:
            popup_automation._confirm_save_as_overwrite()

        yes.click.assert_called_once()
        gui.hotkey.assert_not_called()

    def test_falls_back_to_alt_y_when_no_button_found(self):
        dialog, yes = self._dialog("&Yes")
        dialog.children.return_value = []
        with patch.object(popup_automation, "_find_all_window_elements",
                          return_value=[dialog]), \
             patch.object(popup_automation, "pyautogui", MagicMock()) as gui:
            popup_automation._confirm_save_as_overwrite()

        gui.hotkey.assert_called_once_with("alt", "y")


class RawTitleLookupTest(unittest.TestCase):
    """Titles must be read with GetWindowTextW, not pywinauto's window_text().

    pywinauto reads titles with SendMessageTimeout(SMTO_ABORTIFHUNG), which
    returns '' while the owning thread is busy - Ableton stops pumping
    messages while writing a large .als to an HDD. That made every
    win_exists() miss a popup that was genuinely on screen.
    """

    def test_matching_uses_raw_api_not_pywinauto(self):
        """Titles come from GetWindowTextW, applied to every real top-level hwnd."""
        seen = []

        def fake_raw(hwnd):
            seen.append(hwnd)
            return "Confirm Save As"

        with patch.object(window_utils, "_raw_window_title", side_effect=fake_raw):
            hwnds = window_utils._raw_matching_hwnds(".*Confirm Save As.*")

        self.assertTrue(hwnds, "no top-level window was matched at all")
        self.assertGreater(len(seen), 1, "only one window was inspected")
        self.assertEqual(set(hwnds), {h for h in seen if h},)
        self.assertNotIn(0, seen, "a null hwnd should never be title-checked")

    def test_hidden_windows_are_skipped_unless_visible_only_false(self):
        with patch.object(window_utils, "_raw_window_title",
                          return_value="Confirm Save As") as raw:
            window_utils._raw_matching_hwnds(".*Confirm Save As.*",
                                             visible_only=False)
        self.assertTrue(raw.called)

    def test_raw_lookup_ignores_empty_titles(self):
        with patch.object(window_utils, "_raw_window_title", return_value=""):
            self.assertEqual(window_utils._raw_matching_hwnds(".*Confirm.*"), [])

    def test_invalid_regex_is_logged_not_raised(self):
        self.assertEqual(window_utils._raw_matching_hwnds("[unclosed"), [])


class FindWindowElementTest(unittest.TestCase):
    def test_hidden_ghost_window_is_not_returned(self):
        """Application.connect() forces visible_only=False and would return this."""
        ghost = MagicMock()
        ghost.is_visible.return_value = False
        ghost.is_enabled.return_value = False
        real = MagicMock()
        real.is_visible.return_value = True
        real.is_enabled.return_value = True

        with patch.object(window_utils, "_find_all_window_elements", return_value=[ghost, real]):
            self.assertIs(window_utils._find_window_element(".*Ableton Live.*"), real)

    def test_returns_none_after_timeout_when_absent(self):
        clock = Clock()
        with patch.object(window_utils, "time", clock.fake), \
             patch.object(window_utils, "_find_all_window_elements", return_value=[]):
            self.assertIsNone(window_utils._find_window_element(".*Nope.*", timeout=1))


def _win_info(hwnd, title, class_name=None, width=400, height=200):
    """Ableton state window entry the way list_ableton_windows builds them."""
    import ableton_state
    return ableton_state.WindowInfo(
        hwnd=hwnd, pid=4321, title=title,
        class_name=class_name or ableton_state.STANDARD_DIALOG_CLASS,
        left=0, top=0, width=width, height=height,
        enabled=True, hung=False,
    )


class PluginMessageboxPopupTest(unittest.TestCase):
    """The Kontakt popup: a named #32770 dialog, matched by handle - no OCR.

    Regression for the project-open stall where detect_save_changes_to_popup
    OCR'd the Kontakt dialog's pixels through the untitled Ableton window
    (0.45 vs 0.80) while the OCR-based Kontakt detector returned a cached
    False without ever looking.
    """

    def setUp(self):
        import ableton_state
        popup_automation._reported_popup_hwnds.clear()
        self.ableton_state = ableton_state

    def _world(self, windows):
        return patch.object(
            self.ableton_state, "list_ableton_windows",
            side_effect=lambda: list(windows),
        )

    def test_kontakt_named_dialog_is_found_by_handle(self):
        kontakt = _win_info(0x1234, "Kontakt 7")
        with self._world([kontakt]):
            popups = popup_automation.find_plugin_messagebox_popups()
            self.assertEqual([w.hwnd for w in popups], [0x1234])
            self.assertTrue(popup_automation.detect_kontakt_popup())

    def test_no_windows_means_no_popup(self):
        with self._world([]):
            self.assertEqual(popup_automation.find_plugin_messagebox_popups(), [])
            self.assertFalse(popup_automation.detect_kontakt_popup())

    def test_ableton_and_native_dialog_windows_are_filtered_out(self):
        """Ableton's own windows must never be treated as plugin popups."""
        ableton_cls = self.ableton_state.ABLETON_WINDOW_CLASS
        windows = [
            _win_info(1, "Project - Ableton Live 12 Suite", class_name=ableton_cls,
                      width=1920, height=1080),          # main window
            _win_info(2, "", class_name=ableton_cls),    # Ableton's themed popup (OCR's job)
            _win_info(3, "Save Live Set As:"),           # native dialog, own handler
            _win_info(4, "Confirm Save As"),             # native dialog, own handler
            _win_info(5, "Open Document:"),              # native dialog, own handler
            _win_info(6, "Kontakt 7", width=50, height=40),  # below MIN_POPUP_SIZE
        ]
        with self._world(windows):
            self.assertEqual(popup_automation.find_plugin_messagebox_popups(), [])

    def test_title_filter_narrows_results(self):
        windows = [
            _win_info(10, "Kontakt 7"),
            _win_info(11, "Qubiq Engine"),
        ]
        with self._world(windows):
            only_kontakt = popup_automation.find_plugin_messagebox_popups("kontakt")
            self.assertEqual([w.hwnd for w in only_kontakt], [10])

    def test_dismiss_presses_enter_once_per_handle_and_reports_it(self):
        clock = Clock()
        windows = [_win_info(0x55, "Kontakt 7")]  # hwnd=85
        kb = MagicMock()
        kb.press.side_effect = lambda key: windows.clear()  # Enter closes it
        with self._world(windows), \
             patch.object(popup_automation, "time", clock.fake), \
             patch.object(popup_automation, "pyautogui", kb), \
             patch.object(popup_automation, "win_activate") as activate, \
             self.assertLogs("AutoExport", level="INFO") as logs:
            result = popup_automation.dismiss_plugin_messagebox_popups()

        self.assertTrue(result, "Enter on a fresh plugin messagebox was not reported")
        kb.press.assert_called_once_with("enter")
        self.assertTrue(activate.called, "popup should be focused before Enter")
        # The handle must be in the log line so unfamiliar popups are
        # identifiable after the fact.
        self.assertTrue(
            any("hwnd=85" in line and "Kontakt 7" in line for line in logs.output),
            f"missing handle report in: {logs.output}",
        )

    def test_surviving_popup_is_warned_with_handle(self):
        clock = Clock()
        windows = [_win_info(99, "Stubborn Plugin")]  # never closes
        kb = MagicMock()
        with self._world(windows), \
             patch.object(popup_automation, "time", clock.fake), \
             patch.object(popup_automation, "pyautogui", kb), \
             patch.object(popup_automation, "win_activate"), \
             self.assertLogs("AutoExport", level="WARNING") as logs:
            popup_automation.dismiss_plugin_messagebox_popups()

        self.assertEqual(kb.press.call_count, 1, "one Enter per handle per call")
        self.assertTrue(
            any("survived Enter" in line and "hwnd=99" in line
                for line in logs.output),
            f"missing survival warning in: {logs.output}",
        )

    def test_no_popups_means_no_keys_pressed(self):
        clock = Clock()
        kb = MagicMock()
        with self._world([]), \
             patch.object(popup_automation, "time", clock.fake), \
             patch.object(popup_automation, "pyautogui", kb):
            self.assertFalse(popup_automation.dismiss_plugin_messagebox_popups())
        kb.press.assert_not_called()


class WindowInfoDescribeTest(unittest.TestCase):
    """State dumps must carry handles/pids so popups can be filtered later."""

    def test_describe_includes_handle_and_pid(self):
        text = _win_info(0x77, "Kontakt 7").describe()
        self.assertIn("hwnd=119", text)
        self.assertIn("pid=4321", text)
        self.assertIn("Kontakt 7", text)
        self.assertIn("class=#32770", text)


if __name__ == "__main__":
    unittest.main()