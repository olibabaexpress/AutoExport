import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import size_estimator


class RescaleEstimateTest(unittest.TestCase):
    def setUp(self):
        self.analysis = {
            "bpm": 120,
            "wav_count": 4,
            "render_ranges": [
                {"start": 0, "end": 3840},
                {"start": 7680, "end": 11520},
            ],
            "total_bytes": 0,
            "bytes_per_wav": 0,
            "total_size": "0 B",
        }

    def test_recalculates_size_from_cached_ranges(self):
        result = size_estimator.rescale_estimate(
            self.analysis, sample_rate=44100, bit_depth=16, channels=2
        )

        expected_per_track = sum(
            size_estimator.calculate_track_size(
                (part["end"] - part["start"])
                / size_estimator.TICKS_PER_BEAT * 60 / self.analysis["bpm"],
                44100,
                16,
                2,
            )
            for part in self.analysis["render_ranges"]
        )
        self.assertEqual(result["bytes_per_wav"], expected_per_track)
        self.assertEqual(result["total_bytes"], expected_per_track * 2)
        self.assertEqual(
            result["total_size"], size_estimator.format_bytes(expected_per_track * 2)
        )

    def test_higher_sample_rate_and_bit_depth_need_no_project_parse(self):
        baseline_16 = size_estimator.rescale_estimate(
            self.analysis, sample_rate=44100, bit_depth=16, channels=2
        )
        baseline_32 = size_estimator.rescale_estimate(
            self.analysis, sample_rate=44100, bit_depth=32, channels=2
        )
        higher_rate = size_estimator.rescale_estimate(
            self.analysis, sample_rate=88200, bit_depth=16, channels=2
        )

        header_bytes = self.analysis["wav_count"] * 44
        self.assertEqual(
            baseline_32["total_bytes"] - header_bytes,
            2 * (baseline_16["total_bytes"] - header_bytes),
        )
        self.assertEqual(
            higher_rate["total_bytes"] - header_bytes,
            2 * (baseline_16["total_bytes"] - header_bytes),
        )
        self.assertEqual(self.analysis["total_bytes"], 0)
        self.assertEqual(self.analysis["bytes_per_wav"], 0)

    def test_rejects_inconsistent_wav_count(self):
        self.analysis["wav_count"] = 3
        with self.assertRaises(ValueError):
            size_estimator.rescale_estimate(
                self.analysis, sample_rate=44100, bit_depth=16, channels=2
            )


if __name__ == "__main__":
    unittest.main()
