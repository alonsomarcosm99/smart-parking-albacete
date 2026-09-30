import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


PROTOTYPE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTOTYPE_ROOT / "dashboard"))

from demo_data import build_demo_snapshot  # noqa: E402


class DemoDataTests(unittest.TestCase):
    def setUp(self):
        self.seed = PROTOTYPE_ROOT / "infra" / "parking_zone_seed.json"
        self.now = datetime(2026, 6, 22, 18, 30, tzinfo=timezone.utc)

    def test_snapshot_contains_all_spots_and_zones(self):
        snapshot = build_demo_snapshot(self.seed, self.now)
        self.assertEqual(40, len(snapshot["spots"]))
        self.assertEqual(4, len(snapshot["zones"]))
        self.assertEqual(48, len(snapshot["history"]["Z1-CAMPUS"]))

    def test_zone_counts_match_spot_count(self):
        snapshot = build_demo_snapshot(self.seed, self.now)
        total = sum(
            zone["free"] + zone["occupied"] + zone["unknown"]
            for zone in snapshot["zones"]
        )
        self.assertEqual(40, total)

    def test_same_minute_is_deterministic(self):
        first = build_demo_snapshot(self.seed, self.now)
        second = build_demo_snapshot(self.seed, self.now)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
