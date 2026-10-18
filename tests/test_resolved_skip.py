﻿import json, os, tempfile, time, unittest
from pathlib import Path
from project_media_status import load_resolved_project_paths, build_project_media_status


class ResolvedProjectTests(unittest.TestCase):
    def test_skip_only_unchanged_portable_or_linked(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            paths = {}
            for name in ("a", "b", "c"):
                paths[name] = folder / f"{name}.als"
                paths[name].write_text("x")
            index = folder / "sample-index.json"
            index.write_text("{}")
            old_index_time = time.time() - 100
            os.utime(index, (old_index_time, old_index_time))
            ok = {"success": True, "missing_after": 0}
            results = {
                str(paths["a"]): dict(ok, external_sample_refs=0),
                str(paths["b"]): dict(ok, external_sample_refs=2),
                str(paths["c"]): {"success": True, "missing_after": 1},
            }
            status = folder / "status.json"
            status.write_text(json.dumps(build_project_media_status(results)))
            values = list(map(str, paths.values()))
            found = load_resolved_project_paths(
                values, status_path=status, index_path=index
            )
            self.assertEqual(set(found), {
                os.path.normcase(os.path.abspath(paths["a"])),
            })
            automation_found = load_resolved_project_paths(
                values,
                status_path=status,
                index_path=index,
                include_missing=True,
            )
            self.assertEqual(len(automation_found), 3)
            found = load_resolved_project_paths(
                values, mode="relink", status_path=status, index_path=index
            )
            self.assertEqual(set(found), {
                os.path.normcase(os.path.abspath(paths["a"])),
                os.path.normcase(os.path.abspath(paths["b"])),
            })

            future = time.time() + 100
            os.utime(index, (future, future))
            found = load_resolved_project_paths(
                values, status_path=status, index_path=index
            )
            self.assertEqual(len(found), 1)
            automation_found = load_resolved_project_paths(
                values,
                status_path=status,
                index_path=index,
                include_missing=True,
            )
            self.assertEqual(len(automation_found), 2)

            # A newer index makes a saved Missing result eligible for re-scan.
            results[str(paths["a"])] = {
                "success": True,
                "missing_after": 1,
                "missing_sample_details": [],
            }
            status.write_text(json.dumps(build_project_media_status(results)))
            found = load_resolved_project_paths(
                values, status_path=status, index_path=index
            )
            self.assertNotIn(
                os.path.normcase(os.path.abspath(paths["a"])), found
            )
            future = time.time() + 100
            os.utime(paths["a"], (future, future))
            found = load_resolved_project_paths(
                values, status_path=status, index_path=index
            )
            self.assertNotIn(os.path.normcase(os.path.abspath(paths["a"])), found)


if __name__ == "__main__":
    unittest.main()
