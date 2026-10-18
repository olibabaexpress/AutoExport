import tempfile
import unittest
from pathlib import Path

from missing_media_resolver import _filter_pack_refs


class FactoryVersionReferenceTests(unittest.TestCase):
    def test_installed_newer_live_factory_path_is_not_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ableton = Path(temporary_directory) / "Ableton"
            old_sample = (
                ableton / "Live 11 Suite" / "Resources" / "Builtin"
                / "Samples" / "Hybrid" / "ImpulseResponses"
                / "Hybrid_Halls_Rich Verb L.wav"
            )
            current_sample = (
                ableton / "Live 12 Suite" / "Resources" / "Builtin"
                / "Samples" / "Hybrid" / "ImpulseResponses"
                / "Hybrid_Halls_Rich Verb L.wav"
            )
            current_sample.parent.mkdir(parents=True)
            current_sample.write_bytes(b"factory sample")

            class Reference:
                absolute = old_sample
                pack_resolved = False
                live_pack = None

                @staticmethod
                def get_relative_type():
                    return 1

            missing, resolved = _filter_pack_refs([Reference()])
            self.assertEqual(missing, [])
            self.assertEqual(len(resolved), 1)

    def test_factory_path_without_an_installed_counterpart_stays_missing(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            old_sample = (
                Path(temporary_directory) / "Ableton" / "Live 11 Suite"
                / "Resources" / "Builtin" / "Samples" / "Hybrid"
                / "ImpulseResponses" / "missing.wav"
            )

            class Reference:
                absolute = old_sample
                pack_resolved = False
                live_pack = None

                @staticmethod
                def get_relative_type():
                    return 1

            missing, resolved = _filter_pack_refs([Reference()])
            self.assertEqual(len(missing), 1)
            self.assertEqual(resolved, [])


if __name__ == "__main__":
    unittest.main()
