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


EXPECTED = {'arrival_requests.csv': (['request_id', 'driver_key', 'region', 'arrived_at', 'vehicle_type', 'display_set_id', 'model_version'], 45), 'displayed_options.csv': (['display_set_id', 'parking_id', 'rank', 'walk_minutes', 'price_band', 'available_at_display', 'listing_version'], 135), 'parking_inventory.csv': (['parking_id', 'observed_at', 'capacity', 'occupied', 'closure_state', 'source'], 71), 'parking_outcomes.csv': (['request_id', 'parked_at', 'feedback', 'feedback_state', 'source_version'], 45)}


if __name__ == "__main__":
    unittest.main()
