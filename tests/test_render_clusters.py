import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch
from xml.etree import ElementTree as ET

import Size_estimator
import export_automation
import popup_automation
from export_window_controls import ExportWindowState
from find_clusters import render_ranges, render_position
from organize_stems_by_group import StemsOrganizerByGroup


def audio_track(spans):
    root = ET.Element("AudioTrack")
    arranger = ET.SubElement(root, "DeviceChain")
    arranger = ET.SubElement(arranger, "MainSequencer")
    arranger = ET.SubElement(arranger, "Sample")
    events = ET.SubElement(arranger, "ArrangerAutomation")
    events = ET.SubElement(events, "Events")
    for start, end in spans:
        clip = ET.SubElement(events, "AudioClip", Time=str(start))
        ET.SubElement(clip, "CurrentEnd", Value=str(end))
    return SimpleNamespace(type="AudioTrack", track_root=root)


class RenderClustersTest(unittest.TestCase):
    def test_open_export_window_focuses_arrangement_and_reports_success(self):
        with patch.object(export_automation, "win_activate_ableton", return_value=object()), \
             patch.object(export_automation, "get_ableton_window_center", return_value=(400, 300)), \
             patch.object(export_automation, "pyautogui") as ui, \
             patch.object(export_automation, "handle_decoding_popup"), \
             patch.object(export_automation, "win_exists", return_value=True), \
             patch.object(export_automation, "win_activate", return_value=object()) as activate, \
             patch.object(export_automation.time, "sleep"):
            self.assertTrue(export_automation.open_export_window())
            self.assertEqual(ui.method_calls[:2], [
                call.click((400, 300)),
                call.hotkey('ctrl', 'shift', 'r'),
            ])
            activate.assert_called_once_with("Export Audio/Video")

    def test_open_export_window_reports_timeout(self):
        with patch.object(export_automation, "win_activate_ableton", return_value=object()), \
             patch.object(export_automation, "get_ableton_window_center", return_value=(400, 300)), \
             patch.object(export_automation, "pyautogui"), \
             patch.object(export_automation, "handle_decoding_popup"), \
             patch.object(export_automation, "win_exists", return_value=False), \
             patch.object(export_automation.time, "sleep"), \
             patch.object(export_automation.time, "monotonic", side_effect=[0, 31]):
            self.assertFalse(export_automation.open_export_window())

    def test_save_changes_cooldown_is_independent_of_save_as_popup(self):
        with patch.object(popup_automation, "_ocr_cache", {
            "save_popup": (100.0, True),
            "save_changes_to_popup": (0.0, False),
        }), patch.object(popup_automation.time, "time", return_value=100.5), \
             patch.object(popup_automation, "_untitled_live_windows", return_value=[]):
            self.assertFalse(popup_automation.detect_save_changes_to_popup())
            self.assertEqual(popup_automation._ocr_cache["save_popup"], (100.0, True))
            self.assertEqual(
                popup_automation._ocr_cache["save_changes_to_popup"], (100.5, False)
            )

    def test_stems_does_not_prepare_dialog_if_open_fails(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(export_automation.naming_settings, "load_settings",
                          return_value={"stems_folder_include_bpm": False}), \
             patch.object(export_automation, "unmute_all_tracks"), \
             patch.object(export_automation, "disable_external_devices"), \
             patch.object(export_automation, "pyautogui"), \
             patch.object(export_automation.time, "sleep"), \
             patch.object(export_automation, "open_export_window", return_value=False), \
             patch.object(export_automation, "prepare_export_dialog") as prepare:
            live = SimpleNamespace(tempo=120, scanned=True, tracks=[], groups=[])
            self.assertFalse(export_automation._export_stems_range(
                directory, "project.als", "date", "Live", live,
                True, False, (0, 960), None,
            ))
            prepare.assert_not_called()

    def test_main_does_not_prepare_dialog_if_open_fails(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(export_automation, "get_main_mix_export_folder", return_value=directory), \
             patch.object(export_automation, "unsolo_all_tracks"), \
             patch.object(export_automation, "disable_external_devices"), \
             patch.object(export_automation, "pyautogui"), \
             patch.object(export_automation.time, "sleep"), \
             patch.object(export_automation, "open_export_window", return_value=False), \
             patch.object(export_automation, "prepare_export_dialog") as prepare:
            self.assertFalse(export_automation._export_main_range(
                "date", "Live", SimpleNamespace(tempo=120),
                True, False, (0, 960), None, "project.als",
            ))
            prepare.assert_not_called()

    def test_distant_clip_creates_two_ranges_without_silence(self):
        live_set = SimpleNamespace(
            tracks=SimpleNamespace(load=lambda: [audio_track([(0, 624), (1600, 1616)])])
        )
        self.assertEqual(
            [tuple(render_position(t) for t in interval)
             for interval in render_ranges(live_set)],
            [((1, 1, 1), (158, 1, 1)), ((401, 1, 1), (406, 1, 1))],
        )

    def test_fractional_clip_boundaries_cover_audio(self):
        live_set = SimpleNamespace(
            tracks=SimpleNamespace(load=lambda: [audio_track([(0.0009, 4.001)])])
        )
        self.assertEqual(render_ranges(live_set), [(0, 3 * 4 * 960)])

    def test_partial_last_bar_gets_next_bar_plus_one_full_bar(self):
        live_set = SimpleNamespace(
            tracks=SimpleNamespace(load=lambda: [
                audio_track([(0, 623.901), (1600, 1616)])
            ])
        )
        self.assertEqual(
            render_position(render_ranges(live_set)[0][1]),
            (158, 1, 1),
        )

    def test_padding_does_not_extend_into_next_cluster(self):
        live_set = SimpleNamespace(
            tracks=SimpleNamespace(load=lambda: [audio_track([(0, 4), (69, 73)])])
        )
        self.assertEqual(render_ranges(live_set)[0][1], 8 * 960)

    def test_estimate_counts_each_cluster_and_both_export_modes(self):
        ranges = [(0, 624 * 960), (1600 * 960, 1616 * 960)]
        live_set = SimpleNamespace(parse=lambda: None)
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "test.als"
            project.touch()
            with patch.object(Size_estimator, "AbletonSet", return_value=live_set), \
                 patch.object(Size_estimator, "render_ranges", return_value=ranges), \
                 patch.object(Size_estimator, "get_project_duration", return_value=(0, 404, 120)), \
                 patch.object(Size_estimator, "count_stem_tracks", return_value=(2, [])):
                result = Size_estimator.estimate_project(project, True, True, True)
        self.assertEqual(result["wav_count"], 6)
        self.assertEqual(result["duration_seconds"], 320)
        self.assertEqual(result["total_bytes"], 3 * sum(
            Size_estimator.calculate_track_size(seconds) for seconds in (312, 8)
        ))

    def test_dialog_sets_range_after_target_then_resets_focus(self):
        state = SimpleNamespace(
            background_point=lambda: (10, 20),
            render_time_point=lambda label: (10, 20) if label == "Render Start" else (10, 40),
        )
        with patch.object(export_automation, "pyautogui") as ui, \
             patch.object(export_automation.time, "sleep"), \
             patch.object(export_automation, "capture_export_window") as capture:
            self.assertTrue(export_automation.set_render_range(
                state, (800 * 4 * 960, 804 * 4 * 960)
            ))
        capture.assert_not_called()
        typed = [call.args[0] for call in ui.write.call_args_list]
        self.assertEqual(typed, ["801", "1", "1", "4", "0", "0"])
        self.assertEqual(ui.click.call_count, 3)

    def test_dialog_rejects_missing_render_length_control(self):
        state = SimpleNamespace(
            render_time_point=lambda label: (10, 20) if label == "Render Start" else None
        )
        with patch.object(export_automation, "pyautogui"):
            self.assertFalse(export_automation.set_render_range(
                state, (800 * 4 * 960, 804 * 4 * 960)
            ))

    def test_export_button_read_from_separate_crop(self):
        from PIL import Image

        image = Image.new("RGB", (570, 1168))
        state = ExportWindowState((100, 200, *image.size), image, [])
        with patch("export_window_controls.pytesseract.image_to_string",
                   return_value="Export\n") as ocr:
            self.assertEqual(state.export_button_point(), (316, 1304))
            cropped = ocr.call_args.args[0]
            self.assertEqual(cropped.size, (137, 49))
        with patch("export_window_controls.pytesseract.image_to_string",
                   return_value="Export Cancel\n"):
            self.assertIsNone(state.export_button_point())

    def test_export_click_confirms_save_dialog_appears_and_never_tabs(self):
        state = SimpleNamespace(export_button_point=lambda: (200, 300))
        with patch.object(export_automation, "capture_export_window", return_value=state), \
             patch.object(export_automation, "pyautogui") as ui, \
             patch.object(export_automation, "win_activate", return_value=object()), \
             patch.object(export_automation, "win_exists", side_effect=[False, True]) as exists, \
             patch.object(export_automation.time, "sleep"):
            self.assertTrue(export_automation.click_verified_export_button())
            ui.click.assert_called_once_with((200, 300))
            ui.press.assert_not_called()
            self.assertEqual(
                [call.args[0] for call in exists.call_args_list],
                ["Save Audio File As:", "Save Audio File As:"],
            )

    def test_export_click_aborts_when_button_is_not_readable(self):
        state = SimpleNamespace(export_button_point=lambda: None)
        with patch.object(export_automation, "capture_export_window", return_value=state), \
             patch.object(export_automation, "win_activate", return_value=object()), \
             patch.object(export_automation.time, "sleep"), \
             patch.object(export_automation, "pyautogui") as ui:
            self.assertFalse(export_automation.click_verified_export_button())
            ui.click.assert_not_called()

    def test_export_click_reports_missing_save_dialog(self):
        state = SimpleNamespace(export_button_point=lambda: (200, 300))
        with patch.object(export_automation, "capture_export_window", return_value=state), \
             patch.object(export_automation, "win_activate", return_value=object()), \
             patch.object(export_automation, "pyautogui") as ui, \
             patch.object(export_automation, "win_exists", return_value=False), \
             patch.object(export_automation.time, "sleep"), \
             patch.object(export_automation.time, "monotonic", side_effect=[0, 31]):
            self.assertFalse(export_automation.click_verified_export_button())
            ui.click.assert_called_once()

    def test_existing_wav_does_not_count_as_new_render(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory, "project.wav")
            output.write_bytes(b"old")
            snapshot = export_automation.render_file_snapshot(directory, "project", False)
            with patch.object(export_automation, "win_exists", return_value=False), \
                 patch.object(export_automation.time, "sleep"), \
                 patch.object(export_automation.time, "monotonic",
                              side_effect=[0, 31]):
                self.assertFalse(export_automation.wait_for_render(
                    directory, "project", False, snapshot,
                ))

    def test_multiple_exports_wait_and_reuse_renamed_stems_folder(self):
        ranges = [(0, 960), (3840, 4800)]
        with patch.object(export_automation, "_export_stems_range",
                          side_effect=["renamed", "renamed"]) as export:
            self.assertTrue(export_automation.export_stems(
                "original", "set.als", "date", "Live", object(),
                render_ranges=ranges,
            ))
        self.assertEqual(export.call_count, 2)
        self.assertEqual(export.call_args_list[1].args[0], "renamed")
        self.assertEqual(export.call_args_list[1].args[-1], 2)

    def test_multiple_main_exports_use_distinct_cluster_numbers(self):
        ranges = [(0, 960), (3840, 4800)]
        with patch.object(export_automation, "_export_main_range",
                          return_value=True) as export:
            self.assertTrue(export_automation.export_main(
                "date", "Live", object(), project_path="project.als", render_ranges=ranges,
            ))
        self.assertEqual([call.args[-2] for call in export.call_args_list], [1, 2])
        self.assertEqual([call.args[-3] for call in export.call_args_list], ranges)

    def test_export_stops_if_a_cluster_fails(self):
        ranges = [(0, 960), (3840, 4800), (7680, 8640)]
        with patch.object(export_automation, "_export_main_range",
                          side_effect=[True, False]) as export:
            self.assertFalse(export_automation.export_main(
                "date", "Live", object(), project_path="project.als", render_ranges=ranges,
            ))
        self.assertEqual(export.call_count, 2)

    def test_single_cluster_main_has_no_suffix(self):
        with patch.object(export_automation, "_export_main_range", return_value=True) as export:
            self.assertTrue(export_automation.export_main(
                "date", "Live", object(), project_path="project.als",
                render_ranges=[(0, 960)],
            ))
        self.assertIsNone(export.call_args.args[-2])

    def test_main_name_ignores_previous_clusters_save_dialog_name(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            module = export_automation

            def mock(name, **kwargs):
                return stack.enter_context(patch.object(module, name, **kwargs))

            stack.enter_context(patch.object(module.naming_settings, "load_settings",
                                           return_value={
                                               "main_mix_include_bpm": True,
                                               "main_mix_include_date": True,
                                           }))
            stack.enter_context(patch.object(module.time, "sleep"))
            mock("get_main_mix_export_folder", return_value=directory)
            mock("pyautogui")
            mock("unsolo_all_tracks")
            mock("disable_external_devices")
            mock("get_ableton_window_center", return_value=None)
            mock("handle_decoding_popup")
            mock("open_export_window", return_value=True)
            mock("win_exists", return_value=True)
            mock("win_activate")
            mock("wait_for_save_audio_dialog", return_value=True)
            mock("prepare_export_dialog", return_value=True)
            mock("get_save_audio_file_as", return_value=object())
            mock("get_filename_from_dialog", side_effect=[
                "test clusters.wav",
                "test clusters_120bpm_29-09-26 Cluster 1.wav",
            ])
            mock("get_tempo", return_value=120)
            set_name = mock("set_text_on_edit_control", return_value=True)
            mock("set_export_path_in_dialog", return_value=True)
            mock("handle_overwrite_confirmation")
            mock("wait_for_render", return_value=True)

            for index in (1, 2):
                self.assertTrue(module._export_main_range(
                    "29-09-26", "Live", SimpleNamespace(tempo=120),
                    True, False, (0, 960), index, "test clusters.als",
                ))
            self.assertEqual(
                [call.args[1] for call in set_name.call_args_list],
                [
                    "test clusters_120bpm_29-09-26 Cluster 1",
                    "test clusters_120bpm_29-09-26 Cluster 2",
                ],
            )

    def test_stems_master_does_not_replace_standalone_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stems = root / "STEMS"
            main = root / "MAIN"
            stems.mkdir()
            main.mkdir()
            (stems / "working Cluster 1.wav").write_bytes(b"stems master")
            with patch.object(export_automation, "get_main_mix_export_folder",
                              return_value=str(main)), \
                 patch.object(export_automation, "build_main_mix_filename",
                              return_value="project"):
                (main / "project Cluster 1.wav").write_bytes(b"main mix")
                self.assertTrue(export_automation.move_and_rename_main_mix(
                    str(stems), "project", 120, "date",
                    expected_base="working Cluster 1", cluster_index=1,
                ))
            self.assertEqual((main / "project Cluster 1.wav").read_bytes(), b"main mix")
            self.assertEqual((main / "project Cluster 1 Stems.wav").read_bytes(), b"stems master")

    def test_stems_have_separate_cluster_folders_and_normal_stem_names(self):
        with tempfile.TemporaryDirectory() as directory:
            project_folder = Path(directory) / "project"
            project_folder.mkdir()
            self.assertEqual(
                export_automation.stems_cluster_path(str(project_folder), None),
                str(project_folder),
            )
            group_node = SimpleNamespace(name="Drums", is_group=True)
            group_manager = SimpleNamespace(walk=lambda: [(group_node, ["Drums"])])
            with patch("organize_stems_by_group.TrackGroupManager",
                       return_value=group_manager):
                for index in (1, 2):
                    folder = Path(export_automation.stems_cluster_path(
                        str(project_folder), index,
                    ))
                    (folder / f"project Cluster {index} Drums.wav").write_bytes(
                        f"render {index}".encode()
                    )
                    organizer = StemsOrganizerByGroup(
                        str(folder), f"project Cluster {index}", "project.als"
                    )
                    organizer.organize()
            self.assertEqual(
                (project_folder / "Cluster 1" / "Drums" / "Drums (group).wav").read_bytes(),
                b"render 1",
            )
            self.assertEqual(
                (project_folder / "Cluster 2" / "Drums" / "Drums (group).wav").read_bytes(),
                b"render 2",
            )
            self.assertEqual(list(project_folder.glob("*.wav")), [])

    def test_stems_export_saves_and_organizes_in_cluster_folder(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            module = export_automation
            def mock(name, **kwargs):
                return stack.enter_context(patch.object(module, name, **kwargs))

            stack.enter_context(patch.object(module.naming_settings, "load_settings",
                                           return_value={
                                               "stems_folder_include_bpm": False,
                                               "main_mix_include_bpm": True,
                                               "main_mix_include_date": True,
                                               "stem_files_include_bpm": False,
                                               "stem_files_include_project_name": False,
                                           }))
            stack.enter_context(patch.object(module.time, "sleep"))
            mock("unmute_all_tracks")
            mock("disable_external_devices")
            mock("get_ableton_window_center", return_value=None)
            mock("pyautogui")
            mock("handle_decoding_popup")
            mock("open_export_window", return_value=True)
            mock("win_exists", return_value=True)
            mock("win_activate")
            mock("wait_for_save_audio_dialog", return_value=True)
            mock("prepare_export_dialog", return_value=True)
            mock("get_save_audio_file_as", return_value=object())
            mock("get_filename_from_dialog", return_value="project")
            mock("get_tempo", return_value=120)
            mock("process_project_name", return_value="project")
            mock("set_text_on_edit_control", return_value=True)
            set_path = mock("set_export_path_in_dialog", return_value=True)
            mock("handle_overwrite_confirmation")
            mock("move_and_rename_main_mix")
            mock("delete_silent_files_in_folder")
            organizer = mock("StemsOrganizerByGroup")

            def write_rendered_file(folder, name, individual_tracks, previous_files):
                Path(folder, f"{name} Drums.wav").write_bytes(b"audio")
                return True

            mock("wait_for_render", side_effect=write_rendered_file)
            live = SimpleNamespace(tempo=120, scanned=True, tracks=[], groups=[])
            result = module._export_stems_range(
                directory, "project.als", "date", "Live", live,
                True, False, (0, 960), 1,
            )
            self.assertEqual(result, directory)
            cluster_path = str(Path(directory) / "Cluster 1")
            set_path.assert_called_once_with(cluster_path)
            self.assertEqual(organizer.call_args.kwargs["stems_dir"], cluster_path)
            organizer.return_value.organize.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
