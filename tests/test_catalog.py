import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import catalog as catalog_mod
from account_kit.catalog import ConfigError


class ShippedConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog, cls.settings = catalog_mod.load()

    def test_codes(self):
        self.assertEqual(len(self.catalog.timing), 8)
        self.assertEqual(len(self.catalog.study), 15)
        self.assertIn("T_STALE_REQ", self.catalog.timing)
        self.assertIn("S23", self.catalog.study)
        self.assertIn("S_PARTNER", self.catalog.study)
        self.assertIn("S_CASE", self.catalog.study)

    def test_weights(self):
        self.assertEqual(self.catalog.bucket_max, 50.0)
        self.assertAlmostEqual(self.catalog.timing_points, 6.25)
        self.assertAlmostEqual(self.catalog.study_points, 50 / 15, places=4)

    def test_windows(self):
        self.assertEqual(self.catalog.timing["T_FUNDING"].window, (-365, 0))
        self.assertEqual(self.catalog.timing["T_NEW_LEADER"].window, (-274, 0))
        self.assertEqual(self.catalog.timing["T_MNA"].window, (-365, 365))
        self.assertEqual(self.catalog.timing["T_IPO"].window, (-730, 365))
        self.assertEqual(self.catalog.timing["T_DEADLINE"].window, (-365, 548))
        self.assertEqual(self.catalog.timing["T_STALE_REQ"].window, (None, -180))

    def test_posting_level_ai(self):
        self.assertEqual(self.catalog.ai_code, "S23")
        self.assertEqual(self.catalog.ai_source, "job posting")
        # the stale-req window and the scanner threshold must stay paired
        self.assertEqual(self.catalog.timing["T_STALE_REQ"].window[1], -self.catalog.stale_days)

    def test_precedents(self):
        self.assertEqual(self.catalog.study["S1"].precedent, "A")
        self.assertEqual(self.catalog.study["S7"].precedent, "B")
        self.assertEqual(self.catalog.study["S_PARTNER"].precedent, "")

    def test_scanned_codes(self):
        scanned = {code for code, spec in self.catalog.study.items() if spec.scanned}
        self.assertIn("S1", scanned)
        self.assertIn("S23", scanned)
        self.assertNotIn("S4", scanned)  # research-only codes carry no patterns
        for code, spec in self.catalog.study.items():
            for pattern in spec.patterns:
                import re
                re.compile(pattern)  # every shipped pattern compiles

    def test_settings(self):
        self.assertIn("Jason Yu", self.settings.owners)
        self.assertTrue(self.settings.regions)  # territory allowlist is non-empty
        self.assertIn("CA", self.settings.regions)
        self.assertIn("US", self.settings.countries)
        self.assertEqual(self.settings.min_eng, 50)
        self.assertIn("Custom Software & IT Services", self.settings.it_services_subs)


class BadConfigs(unittest.TestCase):
    def test_bad_window(self):
        data = {"schema_version": 1, "scoring": {"bucket_max": 50, "stack_step": 0.1},
                "tiers": {"tier1": {"max_rank": 50, "min_score": 40},
                          "tier2": {"min_score": 20}},
                "labels": ["observed", "unknown"],
                "posting_level_ai": {"code": "S1", "source": "job posting"},
                "timing": {"T_X": {"meaning": "m", "window": [-365]}},
                "study": {"S1": {"meaning": "m"}},
                "job_scan": {"stale_days": 180, "engineering_title_pattern": "x"},
                "research": {"partner_sis": [], "case_studies": []}}
        with self.assertRaises(ConfigError) as caught:
            catalog_mod._load_catalog(data)
        self.assertIn("window", str(caught.exception))

    def test_bad_pattern(self):
        data = {"schema_version": 1, "scoring": {"bucket_max": 50, "stack_step": 0.1},
                "tiers": {"tier1": {"max_rank": 50, "min_score": 40},
                          "tier2": {"min_score": 20}},
                "labels": ["observed", "unknown"],
                "posting_level_ai": {"code": "S1", "source": "job posting"},
                "timing": {"T_X": {"meaning": "m", "window": [-365, 0]}},
                "study": {"S1": {"meaning": "m", "patterns": ["["]}},
                "job_scan": {"stale_days": 180, "engineering_title_pattern": "x"},
                "research": {"partner_sis": [], "case_studies": []}}
        with self.assertRaises(ConfigError) as caught:
            catalog_mod._load_catalog(data)
        self.assertIn("does not compile", str(caught.exception))

    def test_unknown_ai_code(self):
        data = {"schema_version": 1, "scoring": {"bucket_max": 50, "stack_step": 0.1},
                "tiers": {"tier1": {"max_rank": 50, "min_score": 40},
                          "tier2": {"min_score": 20}},
                "labels": ["observed", "unknown"],
                "posting_level_ai": {"code": "S_NOPE", "source": "job posting"},
                "timing": {"T_X": {"meaning": "m", "window": [-365, 0]}},
                "study": {"S1": {"meaning": "m"}},
                "job_scan": {"stale_days": 180, "engineering_title_pattern": "x"},
                "research": {"partner_sis": [], "case_studies": []}}
        with self.assertRaises(ConfigError) as caught:
            catalog_mod._load_catalog(data)
        self.assertIn("posting_level_ai", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
