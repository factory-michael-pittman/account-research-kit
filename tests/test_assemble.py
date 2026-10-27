import csv
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import assemble, batches, catalog as catalog_mod, intake, screen

CATALOG, SETTINGS = catalog_mod.load()
TODAY = date(2026, 10, 27)

POOL = [
    {"Id": "a1", "Name": "Acme", "Website": "acme.com", "Type": "Prospect",
     "BillingCountry": "United States", "BillingState": "California",
     "ZI_Employee_Count__c": "500", "ZI_Engineering_Headcount__c": "80",
     "ZI_Primary_Industry__c": "Software", "ZI_Primary_Sub_Industry__c": ""},
    {"Id": "a2", "Name": "East Coast Co", "Website": "east.com", "Type": "Prospect",
     "BillingCountry": "United States", "BillingState": "New York",
     "ZI_Employee_Count__c": "500", "ZI_Engineering_Headcount__c": "80",
     "ZI_Primary_Industry__c": "Software", "ZI_Primary_Sub_Industry__c": ""},
    {"Id": "a3", "Name": "Nowhere Inc", "Website": "nowhere.com", "Type": "Prospect",
     "BillingCountry": "", "BillingState": "",
     "ZI_Employee_Count__c": "500", "ZI_Engineering_Headcount__c": "80",
     "ZI_Primary_Industry__c": "Software", "ZI_Primary_Sub_Industry__c": ""},
]

JOBS = {"a1": {"Id": "a1", "Name": "Acme", "ats": "greenhouse", "board": "acme",
               "n_jobs": 4, "n_eng_jobs": 2, "signals": [
                   {"code": "S2", "claim": "tech debt in posting", "date": "2026-09-01",
                    "url": "https://jobs.example/1", "label": "observed", "source": "job posting"},
                   {"code": "T_STALE_REQ", "claim": "Staff Engineer open since 2026-01-05",
                    "date": "2026-01-05", "url": "https://jobs.example/2",
                    "label": "observed", "source": "job posting"}]}}

RESEARCH = [{"Id": "a1", "Name": "Acme",
             "summary": "Acme sells routing software to freight companies.",
             "partner": "",
             "signals": [
                 {"code": "T_FUNDING", "claim": "$40M Series C on 2026-06-01",
                  "date": "2026-06-01", "url": "https://acme.com/news", "label": "observed",
                  "source": "acme.com", "confirmation": "tokens: Series C, $40M"},
                 {"code": "S23", "claim": "AI-first engineering mentioned in a posting",
                  "date": "2026-09-01", "url": "https://jobs.example/1", "label": "observed",
                  "source": "job posting", "confirmation": "posting text"},
                 {"code": "S1", "claim": "maybe a Java 8 monolith",
                  "date": "", "url": "", "label": "unknown", "source": "inference",
                  "confirmation": "not confirmed at a loaded source"},
                 {"code": "S9", "claim": "migrating the orders database off Oracle",
                  "date": "2026-08-15", "url": "https://acme.com/blog", "label": "docs",
                  "source": "acme.com blog", "confirmation": "quote: orders database"}],
             "why_now": [{"rank": 1, "signal_ids": ["T_FUNDING", "S9"],
                          "reason": "Fresh $40M funding while they migrate the orders database."}],
             "not_found": ["new engineering leader: searched acme.com/news and LinkedIn; none found"],
             "flags": []}]


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        with (self.data / "pool.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(POOL[0].keys()))
            writer.writeheader()
            writer.writerows(POOL)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_flow(self):
        summary = screen.run(SETTINGS, self.data, today=TODAY)
        self.assertEqual(summary["pool"], 3)
        self.assertEqual(summary["passing"], 1)
        self.assertEqual(summary["excluded"], 1)   # east of the territory
        self.assertEqual(summary["unresolved"], 1)  # missing location
        self.assertTrue((self.data / "enrichment" / "INSTRUCTIONS.md").exists())

        (self.data / "jobs").mkdir()
        (self.data / "jobs" / "a1.json").write_text(json.dumps(JOBS["a1"]), encoding="utf-8")

        result = batches.build(SETTINGS, CATALOG, self.data, TODAY)
        self.assertEqual(result["batches"], ["in-00.jsonl"])
        batch = json.loads((self.data / "research" / "in-00.jsonl").read_text().splitlines()[0])
        self.assertEqual(batch["Name"], "Acme")
        self.assertEqual(batch["job_signals_already_found"], ["S2", "T_STALE_REQ"])
        instructions = (self.data / "research" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        self.assertIn("T_FUNDING", instructions)
        self.assertIn("| `S1` |", instructions)
        self.assertNotIn("{{", instructions)  # every placeholder rendered

        (self.data / "research" / "out-00.jsonl").write_text(
            json.dumps(RESEARCH[0]) + "\n", encoding="utf-8")

        result = assemble.run(CATALOG, SETTINGS, self.data, TODAY)
        self.assertEqual(result["accounts"], 1)
        self.assertTrue(result["researched"])

        with (self.data / "prioritized.csv").open(encoding="utf-8") as h:
            rows = list(csv.DictReader(h))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["Name"], "Acme")
        self.assertEqual(row["timing_signals"], "T_FUNDING, T_STALE_REQ")
        # 2 timing x 6.25 + 2 study x 3.3333 = 19.1667, x 1.3 stack (4 distinct) = 24.9
        self.assertEqual(float(row["final_score"]), 24.9)
        self.assertEqual(row["tier"], "Tier 2")
        self.assertEqual(row["precedent"], "A")
        self.assertEqual(row["company_summary"], "Acme sells routing software to freight companies.")
        self.assertIn("Staff Engineer open since", row["signal_1"])  # soonest timing signal first

        evidence = list(csv.DictReader((self.data / "evidence.csv").open(encoding="utf-8")))
        by_code = {e["code"]: e for e in evidence}
        self.assertTrue(by_code["T_FUNDING"]["counted"])
        self.assertEqual(by_code["S23"]["why"], "no: AI mention in a job posting")
        self.assertEqual(by_code["S1"]["why"], "no: unverified")
        self.assertEqual(by_code["S1"]["meaning"], CATALOG.study["S1"].meaning)

        digest = (self.data / "digests" / "acme.md").read_text(encoding="utf-8")
        for section in ("Why now (the reasons to reach out, ranked)",
                        "## What was found", "## What the score is",
                        "Found but not counted", "Searched and not found"):
            self.assertIn(section, digest)
        self.assertIn("Fresh $40M funding", digest)
        self.assertIn("unverified", digest)  # the S1 rejection is explained
        self.assertIn("acme.com/news", digest)  # claims stay checkable

        coverage = (self.data / "coverage.md").read_text(encoding="utf-8")
        self.assertIn("Research coverage", coverage)
        self.assertIn("Acme", coverage)

    def test_score_without_research_uses_job_signals_alone(self):
        screen.run(SETTINGS, self.data, today=TODAY)
        (self.data / "jobs").mkdir()
        (self.data / "jobs" / "a1.json").write_text(json.dumps(JOBS["a1"]), encoding="utf-8")
        result = assemble.run(CATALOG, SETTINGS, self.data, TODAY)
        with (self.data / "prioritized.csv").open(encoding="utf-8") as h:
            rows = list(csv.DictReader(h))
        self.assertEqual(rows[0]["research_status"], "not researched")
        # 1 timing + 1 study = 6.25 + 3.3333 = 9.5833, x 1.1 stack (2 distinct) = 10.5
        self.assertEqual(float(rows[0]["final_score"]), 10.5)
        self.assertIn("Staff Engineer open since", rows[0]["signal_1"])
        digest = (self.data / "digests" / "acme.md").read_text(encoding="utf-8")
        self.assertIn("ranks on job-posting signals alone", digest)

    def test_name_recovery_for_mistyped_research_id(self):
        screen.run(SETTINGS, self.data, today=TODAY)
        research = {**RESEARCH[0], "Id": "typo-id"}
        (self.data / "research").mkdir()
        (self.data / "research" / "out-00.jsonl").write_text(json.dumps(research) + "\n", encoding="utf-8")
        assemble.run(CATALOG, SETTINGS, self.data, TODAY)
        with (self.data / "prioritized.csv").open(encoding="utf-8") as h:
            rows = list(csv.DictReader(h))
        self.assertEqual(rows[0]["research_status"], "researched")
        self.assertEqual(rows[0]["Id"], "a1")  # matched by exact name


class Intake(unittest.TestCase):
    def test_pool_query(self):
        query = intake.pool_query(["Jason Yu", "O'Brien"], [None, "MM Prospect Pool"])
        self.assertIn("Owner.Name IN ('Jason Yu', 'O\\'Brien')", query)
        self.assertIn("(Ownership__c = null OR Ownership__c = 'MM Prospect Pool')", query)
        self.assertIn("FROM Account", query)

    def test_pool_query_requires_owners(self):
        with self.assertRaises(ValueError):
            intake.pool_query([], [])

    def test_normalize_csv_synthesizes_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "pool.csv"
            source.write_text("Name,Website\nAcme,acme.com\nBeta,beta.com\n", encoding="utf-8")
            out = Path(tmp) / "out.csv"
            missing = intake.normalize_csv(source, out)
            self.assertIn("ZI_Engineering_Headcount__c", missing)
            with out.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([r["Id"] for r in rows], ["csv-0001", "csv-0002"])


if __name__ == "__main__":
    unittest.main()
