import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from account_kit import boards, catalog as catalog_mod, jobs
from account_kit.boards import Bullet, Job

CATALOG, _ = catalog_mod.load()
TODAY = date(2026, 10, 27)


class PostingSignals(unittest.TestCase):
    def test_job_signals_and_stale_req(self):
        job = Job(job_id="1", title="Senior Software Engineer", first_published="2026-01-05",
                  url="https://jobs.example/1")
        bullets = [Bullet("1", "responsibilities",
                          "You will own technical debt across our platform and keep the lights on")]
        signals = jobs.signals_from_postings(CATALOG, [job], bullets, today=TODAY)
        codes = {s["code"] for s in signals}
        self.assertIn("S2", codes)
        self.assertIn("T_STALE_REQ", codes)  # open since Jan, past the 180-day threshold
        stale = next(s for s in signals if s["code"] == "T_STALE_REQ")
        self.assertIn("295 days", stale["claim"])

    def test_fresh_req_is_not_stale(self):
        job = Job(job_id="2", title="Platform Engineer", first_published="2026-09-15")
        signals = jobs.signals_from_postings(CATALOG, [job], [], today=TODAY)
        self.assertEqual([s["code"] for s in signals], ["S12"])

    def test_non_engineering_postings_ignored(self):
        job = Job(job_id="3", title="Account Executive", first_published="2025-01-01",
                  url="https://jobs.example/3")
        self.assertEqual(jobs.signals_from_postings(CATALOG, [job], [], today=TODAY), [])

    def test_multiple_patterns_dedupe_by_meaning(self):
        job = Job(job_id="4", title="Software Engineer",
                  first_published=TODAY.isoformat(), url="https://jobs.example/4")
        bullets = [Bullet("4", "responsibilities",
                          "Lead our migration off the mainframe and modernize the Java 8 services")]
        codes = [s["code"] for s in jobs.signals_from_postings(CATALOG, [job], bullets, today=TODAY)]
        self.assertEqual(codes.count("S1"), 1)

    def test_lever_epoch_millis_are_dates_not_years(self):
        self.assertEqual(jobs._published("1760000000000"), "2025-10-09")
        self.assertEqual(jobs._published("2026-10-01"), "2026-10-01")


class FakeFetch:
    def __init__(self, pages):
        self.pages = dict(pages)
        self.calls = []

    def __call__(self, url, method="GET", json_body=None):
        self.calls.append(url)
        for prefix, (status, body) in self.pages.items():
            if url.startswith(prefix):
                return status, body
        return 404, ""


GREENHOUSE_PAYLOAD = '{"meta": {"total": 1}, "jobs": [{"id": 101, "title": "Staff Engineer", ' \
    '"content": "<h3>Responsibilities</h3><ul><li>Own our SOC 2 compliance work and appsec backlog' \
    '</li></ul>", "first_published": "2026-10-01", "absolute_url": "https://jobs.example/101", ' \
    '"location": {"name": "Remote US"}, "departments": [{"name": "Engineering"}]}]}'

ASHBY_PAYLOAD = '{"jobs": [{"id": "a1", "title": "SRE", ' \
    '"descriptionHtml": "<p>Responsibilities</p><p>You will join the on-call rotation and run ' \
    'incident response for PagerDuty escalations</p>", "jobUrl": "https://jobs.example/a1"}]}'

LEVER_PAYLOAD = '[{"id": "l1", "text": "Data Engineer", "createdAt": 1760000000000, ' \
    '"categories": {"department": "Data", "location": "Remote"}, "descriptionPlain": "", ' \
    '"lists": [{"text": "Responsibilities", "content": "<li>Build our ETL data pipeline</li>"}], ' \
    '"hostedUrl": "https://jobs.example/l1"}]'


class Collectors(unittest.TestCase):
    def test_greenhouse(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = boards.collect("greenhouse", "acme", Path(tmp),
                                    _fetch=FakeFetch({"https://boards-api.greenhouse.io": (200, GREENHOUSE_PAYLOAD)}))
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0].title, "Staff Engineer")
        codes = {b.verbatim for b in result.bullets}
        self.assertTrue(any("SOC 2" in c for c in codes))
        signals = jobs.signals_from_postings(CATALOG, result.jobs, result.bullets, today=TODAY)
        self.assertIn("S7", {s["code"] for s in signals})

    def test_ashby(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = boards.collect("ashby", "acme", Path(tmp),
                                    _fetch=FakeFetch({"https://api.ashbyhq.com": (200, ASHBY_PAYLOAD)}))
        self.assertEqual(result.jobs[0].title, "SRE")
        signals = jobs.signals_from_postings(CATALOG, result.jobs, result.bullets, today=TODAY)
        self.assertIn("S3", {s["code"] for s in signals})

    def test_lever_lists_content_is_rendered(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = boards.collect("lever", "acme", Path(tmp),
                                    _fetch=FakeFetch({"https://api.lever.co": (200, LEVER_PAYLOAD)}))
        self.assertEqual(result.jobs[0].title, "Data Engineer")
        self.assertEqual(result.jobs[0].first_published, "1760000000000")
        signals = jobs.signals_from_postings(CATALOG, result.jobs, result.bullets, today=TODAY)
        self.assertIn("S9", {s["code"] for s in signals})
        self.assertIn("T_STALE_REQ", {s["code"] for s in signals})

    def test_404_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(boards.FetchError):
                boards.collect("greenhouse", "nope", Path(tmp), _fetch=FakeFetch({}))

    def test_domain_of(self):
        self.assertEqual(boards.domain_of("https://www.acme.com/careers"), "acme.com")
        self.assertEqual(boards.domain_of("acme.com"), "acme.com")
        self.assertEqual(boards.domain_of(""), "")


class FindBoard(unittest.TestCase):
    def test_prefers_links_on_company_site(self):
        pages = {
            "https://acme.com/careers": (200, '<a href="https://boards.greenhouse.io/acme">Jobs</a>'),
        }
        ats, board, how = boards.find_board("acme.com", "Acme", _fetch=FakeFetch(pages))
        self.assertEqual((ats, board), ("greenhouse", "acme"))
        self.assertIn("linked from", how)

    def test_token_probe_fallback(self):
        pages = {"https://acme.com": (200, "<html></html>"),
                 "https://api.lever.co/v0/postings/acmecorp": (200, "[]")}
        ats, board, how = boards.find_board("acme.com", "Acme Corp", _fetch=FakeFetch(pages))
        self.assertEqual((ats, board, how), ("lever", "acmecorp", "token probe"))

    def test_no_board_found(self):
        pages = {"https://acme.com": (200, "<html></html>")}
        self.assertEqual(boards.find_board("acme.com", "Acme", _fetch=FakeFetch(pages)),
                         ("", "", "no board found"))


class ScanAccount(unittest.TestCase):
    def test_scan_writes_signals_and_survives_failures(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            pages = {"https://boards-api.greenhouse.io/v1/boards/acme/jobs": (200, GREENHOUSE_PAYLOAD),
                     "https://acme.com/careers": (200, '<a href="https://boards.greenhouse.io/acme">')}
            result = jobs.scan_account(CATALOG, {"Id": "a1", "Name": "Acme", "Website": "acme.com"},
                                       raw, _fetch=FakeFetch(pages))
            self.assertEqual(result["ats"], "greenhouse")
            self.assertEqual(result["n_jobs"], 1)
            self.assertIn("S7", {s["code"] for s in result["signals"]})

            broken = jobs.scan_account(CATALOG, {"Id": "a2", "Name": "Broken", "Website": ""},
                                       raw, _fetch=FakeFetch(pages))
            self.assertIn("no website", broken["notes"])


if __name__ == "__main__":
    unittest.main()
