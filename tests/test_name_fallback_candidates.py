import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import missing_media_resolver as resolver
from project_media_status import (
    build_project_media_status,
    format_project_candidate_lines,
)


def make_index(paths):
    return {
        "kick.wav": [(str(p), {"name": "kick.wav", "size": 1}) for p in paths]
    }


def make_ref(expected="C:/Samples/ProjX/kick.wav", size=0):
    return SimpleNamespace(
        name="kick.wav", size=size, absolute=Path(expected)
    )


class NameFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, relative, data=b"a"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_collect_external_copies_into_project(self):
        project = self.root / "Project"
        project.mkdir()
        external = self.write("ext/kick.wav", b"kick")
        calls = []
        ref = SimpleNamespace(
            name="kick.wav", relative_exists=False, relative=None,
            absolute=external, live_pack=None, pack_resolved=False,
            set_relative=lambda value: calls.append(("rel", value)),
            set_relative_type=lambda value: calls.append(("type", value)),
        )
        fake_set = SimpleNamespace(samples=SimpleNamespace(iterate=lambda: [ref]))
        changed = resolver._collect_external_refs(fake_set, project)
        self.assertEqual(changed, 1)
        self.assertTrue((project / "Samples" / "Imported" / "kick.wav").exists())
        self.assertIn(("rel", "Samples/Imported/kick.wav"), calls)

    def test_library_refs_are_not_external_or_collected(self):
        project = self.root / "Project"
        project.mkdir()
        external = self.write("ext/kick.wav", b"kick")
        ref = SimpleNamespace(
            name="kick.wav", relative_exists=False, relative=None,
            absolute=external, live_pack=None, pack_resolved=False,
            get_relative_type=lambda: 2,
            set_relative=lambda value: self.fail("library ref was changed"),
            set_relative_type=lambda value: self.fail("library ref was changed"),
        )
        fake_set = SimpleNamespace(
            samples=SimpleNamespace(iterate=lambda: [ref]),
            find_project_root_folder=lambda: project,
        )
        self.assertEqual(resolver._collect_external_refs(fake_set, project), 0)
        self.assertEqual(
            resolver._collect_external_sample_details(fake_set, project / "a.als"),
            [],
        )

    def test_stale_factory_ref_matches_renamed_library_sample(self):
        project = self.root / "Project"
        project.mkdir()
        old = r"C:\ProgramData\Ableton\Live 10 Suite\Resources\Core Library\Samples\Grand Piano\GrandPiano-C1-ff.aif"
        new = r"C:\ProgramData\Ableton\Live 12 Suite\Resources\Core Library\Samples\Multisamples\Grand Piano\GrandPiano C1 ff.aif"
        ref = SimpleNamespace(
            name="GrandPiano-C1-ff.aif", relative_exists=False, relative=None,
            absolute=Path(old), live_pack=None, pack_resolved=False,
            get_relative_type=lambda: 3,
        )
        fake_set = SimpleNamespace(
            samples=SimpleNamespace(iterate=lambda: [ref]),
            find_project_root_folder=lambda: project,
        )
        db = {new: {"name": "GrandPiano C1 ff.aif", "size": 1, "last_modified": 0}}
        names = resolver._factory_names_for(db)
        self.assertEqual(
            resolver._collect_external_sample_details(
                fake_set, project / "a.als", names
            ),
            [],
        )
        self.assertEqual(
            len(resolver._collect_external_sample_details(fake_set, project / "a.als")),
            1,
        )

    def test_unresolved_refs_are_not_listed_as_external(self):
        project = self.root / "Project"
        project.mkdir()
        ref = SimpleNamespace(
            name="gone.wav", relative_exists=False, absolute_exists=False,
            relative=None, absolute=Path("E:/old/Project/gone.wav"),
            live_pack=None, pack_resolved=False, get_relative_type=lambda: 3,
        )
        fake_set = SimpleNamespace(
            samples=SimpleNamespace(iterate=lambda: [ref]),
            find_project_root_folder=lambda: project,
        )
        self.assertEqual(
            resolver._collect_external_sample_details(fake_set, project / "a.als"),
            [],
        )

    def test_name_with_leading_space_matches_indexed_file(self):
        real = self.write("proj/Consolidate/(Freeze) [2022-12-07 170455].wav", b"x" * 10)
        ref = make_ref(size=10)
        ref.name = " (Freeze) [2022-12-07 170455].wav"
        index = resolver._build_name_index(
            {str(real): {"name": real.name, "size": 10, "last_modified": 0}}
        )
        self.assertEqual(
            [p for p, _ in resolver._get_size_verified_candidates(ref, index)],
            [str(real)],
        )
        strict = resolver._size_verified_database({}, [ref], index)
        self.assertEqual(strict[str(real)]["name"], ref.name)

    def test_single_candidate_is_chosen(self):
        only = self.write("one/kick.wav")
        chosen, _ = resolver._choose_name_candidate(
            make_ref(), make_index([only]), ""
        )
        self.assertEqual(chosen, str(only))

    def test_identical_copies_pick_closest_to_project(self):
        near = self.write("proj/lib/kick.wav")
        far = self.write("elsewhere/deep/kick.wav")
        chosen, _ = resolver._choose_name_candidate(
            make_ref(), make_index([far, near]), str(self.root / "proj")
        )
        self.assertEqual(chosen, str(near))

    def test_different_files_use_expected_folder_similarity(self):
        match = self.write("S/ProjX/kick.wav", b"match")
        other = self.write("S/Unrelated/kick.wav", b"other")
        chosen, _ = resolver._choose_name_candidate(
            make_ref("C:/s/projx/kick.wav"), make_index([other, match]), ""
        )
        self.assertEqual(chosen, str(match))

    def test_unrelated_different_files_need_human_choice(self):
        first = self.write("A/kick.wav", b"one")
        second = self.write("B/kick.wav", b"two")
        chosen, candidates = resolver._choose_name_candidate(
            make_ref("C:/Nope/kick.wav"), make_index([first, second]), ""
        )
        self.assertIsNone(chosen)
        self.assertEqual(len(candidates), 2)

    def test_ambiguous_detail_and_candidates_status(self):
        first = self.write("A/kick.wav", b"one")
        second = self.write("B/kick.wav", b"two")
        details = resolver._unresolved_sample_details(
            [make_ref("C:/Nope/kick.wav")], make_index([first, second])
        )
        self.assertEqual(details[0]["reason"], "ambiguous_candidates")
        result = {
            "success": True,
            "missing_after": 1,
            "missing_sample_details": details,
        }
        snapshot = build_project_media_status({"p.als": result})
        project = snapshot["projects"][0]
        self.assertEqual(project["media_status"], "Candidates")
        self.assertIn("candidate_review", project["categories"])
        self.assertIn(str(first), "\n".join(format_project_candidate_lines(project)))


if __name__ == "__main__":
    unittest.main()
