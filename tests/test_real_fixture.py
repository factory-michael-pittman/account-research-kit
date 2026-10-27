"""Regression anchors on real research data from the source system.

The signals and expected scores below were produced by the account-value
repo's own pipeline (job scans + batch research, scored as of 2026-10-21) and
verified to reproduce its scored.csv exactly for all 24 accounts. They pin the
kit's arithmetic to the source system; if this test changes, scoring drifted.
"""

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import catalog as catalog_mod, score as scoring

CATALOG, _ = catalog_mod.load()
FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "real-accounts.json")
                     .read_text(encoding="utf-8"))
TODAY = date.fromisoformat(FIXTURE["as_of"])


class RealAccounts(unittest.TestCase):
    def test_reproduces_source_scores(self):
        for account in FIXTURE["accounts"]:
            with self.subTest(account=account["Name"]):
                result = scoring.score_signals(CATALOG, account["signals"], today=TODAY)
                expect = account["expect"]
                self.assertEqual(result.final, expect["final"])
                self.assertEqual(result.timing_points, expect["timing_points"])
                self.assertEqual(result.study_points, expect["study_points"])
                self.assertEqual(result.distinct_signals, expect["stack_count"])
                self.assertEqual(result.timing_codes, expect["timing_codes"])
                self.assertEqual(result.study_codes, expect["study_codes"])
                self.assertEqual(scoring.precedent_for(CATALOG, result.study_codes), expect["precedent"])

    def test_blackline_breakdown_is_arithmetic_not_magic(self):
        # 3 timing codes x 6.25 + 5 study codes x 3.3333 = 35.4167, x 1.7 stack
        blackline = next(a for a in FIXTURE["accounts"] if a["Name"] == "BlackLine")
        result = scoring.score_signals(CATALOG, blackline["signals"], today=TODAY)
        self.assertEqual(result.timing_points, 3 * 6.25)
        self.assertEqual(result.study_points, round(5 * 50 / 15, 2))
        self.assertEqual(result.multiplier, 1.7)
        self.assertEqual(result.final, round((3 * 6.25 + 5 * 50 / 15) * 1.7, 1))


if __name__ == "__main__":
    unittest.main()
