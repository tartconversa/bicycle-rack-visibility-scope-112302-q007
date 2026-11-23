from __future__ import annotations

import csv
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MaterialTests(unittest.TestCase):
    def test_files_have_declared_headers_and_rows(self):
        expected = EXPECTED
        for filename, (columns, minimum_rows) in expected.items():
            with self.subTest(filename=filename):
                with (ROOT / "data" / filename).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    rows = list(reader)
                self.assertEqual(reader.fieldnames, columns)
                self.assertGreaterEqual(len(rows), minimum_rows)
                self.assertTrue(all(None not in row for row in rows))


EXPECTED = {'vehicles.csv': (['vehicle_id', 'model', 'hardware', 'region', 'camera_config'], 35), 'rack_installations.csv': (['installation_id', 'vehicle_id', 'rack_model', 'mount_position', 'load_state', 'valid_from', 'valid_to', 'review_state'], 47), 'drive_events.csv': (['trip_id', 'vehicle_id', 'version', 'started_at', 'rear_visibility', 'upload_state', 'intervention_id'], 60), 'rear_observations.csv': (['observation_id', 'trip_id', 'target_class', 'camera', 'review_state', 'detected', 'review_batch'], 41), 'trial_scopes.csv': (['scope_id', 'model', 'hardware', 'rack_model', 'region', 'status', 'vehicle_count', 'source_version'], 18)}


if __name__ == "__main__":
    unittest.main()
