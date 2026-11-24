"""导入校验测试：非法资料必须被明确拒绝。"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bicycle_rack_visibility_scope.loader import (
    MaterialError,
    load_bundle,
    load_racks,
    load_observations,
)
from tools.generate_data import generate

COLUMNS_RACK = ["installation_id", "vehicle_id", "rack_model", "mount_position",
                "load_state", "valid_from", "valid_to", "review_state"]
COLUMNS_OBS = ["observation_id", "trip_id", "target_class", "camera",
               "review_state", "detected", "review_batch"]


class LoaderValidationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        generate(self.tmp / "fixture")
        self.data_dir = self.tmp / "fixture"

    def tearDown(self):
        self._tmp.cleanup()

    def test_bundle_loads_with_referential_integrity(self):
        bundle = load_bundle(self.data_dir)
        self.assertEqual(len(bundle["vehicles"]), 8)
        self.assertEqual(len(bundle["racks"]), 8)
        self.assertEqual(len(bundle["events"]), 11)
        self.assertEqual(len(bundle["observations"]), 9)
        self.assertEqual(len(bundle["scopes"]), 3)

    def test_bad_enum_rejected(self):
        bad = self.tmp / "bad_racks.csv"
        with bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS_RACK)
            w.writerow(["RI-X", "V-101", "RACK-X", "trunk", "empty",
                        "2026-10-01", "2026-12-31", "confirmed"])
        with self.assertRaises(MaterialError) as ctx:
            load_racks(bad)
        self.assertIn("mount_position", str(ctx.exception))

    def test_bad_date_order_rejected(self):
        bad = self.tmp / "bad_order.csv"
        with bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS_RACK)
            w.writerow(["RI-X", "V-101", "RACK-X", "hitch", "empty",
                        "2026-12-31", "2026-10-01", "confirmed"])
        with self.assertRaises(MaterialError):
            load_racks(bad)

    def test_duplicate_id_rejected(self):
        bad = self.tmp / "dup.csv"
        with bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS_RACK)
            for _ in range(2):
                w.writerow(["RI-X", "V-101", "RACK-X", "hitch", "empty",
                            "2026-10-01", "2026-12-31", "confirmed"])
        with self.assertRaises(MaterialError):
            load_racks(bad)

    def test_dangling_reference_rejected(self):
        bad = self.tmp / "dangling.csv"
        with bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS_OBS)
            w.writerow(["O-X", "T-999", "vehicle", "rear",
                        "confirmed", "yes", "RB-1"])
        with self.assertRaises(MaterialError):
            load_observations(bad, known_trip_ids={"T-101"})

    def test_bad_detected_value_rejected(self):
        bad = self.tmp / "bad_detected.csv"
        with bad.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS_OBS)
            w.writerow(["O-X", "T-101", "vehicle", "rear",
                        "confirmed", "maybe", "RB-1"])
        with self.assertRaises(MaterialError):
            load_observations(bad)


if __name__ == "__main__":
    unittest.main()
