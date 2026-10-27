import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import catalog as catalog_mod, score as scoring

TODAY = date(2026, 10, 27)


def signal(code, **kw):
    base = {"code": code, "claim": "claim", "date": TODAY.isoformat(), "url": "https://x",
            "label": "observed", "source": "news"}
    base.update(kw)
    return base


class ScoreSignals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, _ = catalog_mod.load()

    def test_equal_buckets_and_stack_multiplier(self):
        signals = ([signal(c, date=TODAY.isoformat()) for c in ("T_FUNDING", "T_MNA", "T_DEADLINE")]
                   + [signal(c) for c in ("S1", "S2", "S3", "S4", "S5")])
        result = scoring.score_signals(self.catalog, signals, today=TODAY)
        self.assertEqual(result.timing_points, 18.75)   # 3 of 8 codes x 6.25
        self.assertEqual(result.study_points, 16.67)   # 5 of 15 codes x 3.3333
        self.assertEqual(result.distinct_signals, 8)
        self.assertEqual(result.multiplier, 1.7)
        self.assertEqual(result.final, 60.2)             # (18.75+16.6667) x 1.7

    def test_duplicate_codes_count_once(self):
        signals = [signal("S1"), signal("S1"), signal("S1", claim="again")]
        result = scoring.score_signals(self.catalog, signals, today=TODAY)
        self.assertEqual(result.study_points, round(50 / 15, 2))

    def test_unknown_label_never_counts(self):
        result = scoring.score_signals(self.catalog, [signal("S1", label="unknown")], today=TODAY)
        self.assertEqual(result.study_points, 0.0)
        self.assertEqual(result.final, 0.0)
        self.assertEqual(
            scoring.why_not_counted(self.catalog, signal("S1", label="unknown"), TODAY),
            "no: unverified")

    def test_posting_level_ai_does_not_count(self):
        s23 = signal("S23", source="job posting")
        result = scoring.score_signals(self.catalog, [s23], today=TODAY)
        self.assertEqual(result.study_points, 0.0)
        self.assertEqual(scoring.why_not_counted(self.catalog, s23, TODAY),
                         "no: AI mention in a job posting")
        # company-level evidence with a different source still counts
        result = scoring.score_signals(self.catalog, [signal("S23", source="newsroom")], today=TODAY)
        self.assertAlmostEqual(result.study_points, 50 / 15, places=2)

    def test_timing_windows(self):
        cases = [
            ("T_FUNDING", TODAY.isoformat(), True),
            ("T_FUNDING", "2025-11-01", True),    # 360 days ago, inside -365
            ("T_FUNDING", "2025-10-01", False),   # 391 days ago, outside
            ("T_FUNDING", "2027-01-01", False),   # future funding does not count
            ("T_NEW_LEADER", TODAY.isoformat(), True),
            ("T_MNA", "2027-06-30", True),        # pending deal, up to +365
            ("T_IPO", "2025-01-01", True),        # inside -730
            ("T_STALE_REQ", "2026-01-01", True),  # 299 days ago, past the 180-day threshold
            ("T_STALE_REQ", "2026-09-01", False), # 56 days ago, not stale
        ]
        for code, when, expected in cases:
            self.assertEqual(scoring.counts(self.catalog, signal(code, date=when), TODAY),
                             expected, f"{code} {when}")
        self.assertEqual(scoring.why_not_counted(
            self.catalog, signal("T_FUNDING", date="2027-01-01"), TODAY), "no: outside window")

    def test_missing_date_on_timing_signal_is_outside(self):
        self.assertFalse(scoring.counts(self.catalog, signal("T_FUNDING", date=""), TODAY))

    def test_buckets_cap(self):
        signals = [signal(c, date="2026-01-05" if c == "T_STALE_REQ" else TODAY.isoformat())
                   for c in self.catalog.timing]
        signals += [signal(c) for c in self.catalog.study]
        result = scoring.score_signals(self.catalog, signals, today=TODAY)
        self.assertEqual(result.timing_points, 50.0)
        self.assertEqual(result.study_points, 50.0)
        self.assertEqual(result.distinct_signals, 23)
        self.assertEqual(result.multiplier, 1 + 0.1 * 22)
        self.assertEqual(result.final, round(100 * 3.2, 1))

    def test_next_and_last_events(self):
        signals = [
            signal("T_DEADLINE", date="2026-12-01", claim="dec deadline"),
            signal("T_DEADLINE", date="2027-03-01", claim="march deadline"),
            signal("T_FUNDING", date="2026-04-01", claim="april round"),
        ]
        result = scoring.score_signals(self.catalog, signals, today=TODAY)
        self.assertEqual(result.next_event["claim"], "dec deadline")
        self.assertEqual(result.next_event["days"], 35)
        self.assertEqual(result.last_event["claim"], "april round")

    def test_month_precision_dates(self):
        self.assertTrue(scoring.counts(self.catalog, signal("T_FUNDING", date="2026-09"), TODAY))
        self.assertTrue(scoring.counts(self.catalog, signal("T_FUNDING", date="2026"), TODAY))


class Helpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, _ = catalog_mod.load()

    def test_precedent_for(self):
        self.assertEqual(scoring.precedent_for(self.catalog, ["S1"]), "A")
        self.assertEqual(scoring.precedent_for(self.catalog, ["S7", "S10"]), "B")
        self.assertEqual(scoring.precedent_for(self.catalog, ["S1", "S7"]), "A")
        self.assertEqual(scoring.precedent_for(self.catalog, ["S_PARTNER"]), "none")
        self.assertEqual(scoring.precedent_for(self.catalog, []), "none")

    def test_suggested_tier(self):
        self.assertEqual(scoring.suggested_tier(self.catalog, 40, 5), "Tier 1")
        self.assertEqual(scoring.suggested_tier(self.catalog, 40, 50), "Tier 1")
        self.assertEqual(scoring.suggested_tier(self.catalog, 40, 51), "Tier 2")
        self.assertEqual(scoring.suggested_tier(self.catalog, 20, 90), "Tier 2")
        self.assertEqual(scoring.suggested_tier(self.catalog, 19.9, 90), "Tier 3")
        self.assertEqual(scoring.suggested_tier(self.catalog, 0, 99), "Tier 3")

    def test_unknown_code_earns_nothing_and_is_flagged(self):
        self.assertFalse(scoring.counts(self.catalog, signal("T_NOT_A_THING"), TODAY))
        self.assertEqual(scoring.why_not_counted(self.catalog, signal("T_NOT_A_THING"), TODAY),
                         "no: code not in the catalog")


if __name__ == "__main__":
    unittest.main()
