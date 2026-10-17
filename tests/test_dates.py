"""Offline tests: every source's date field is fed integer, millisecond, float,
None and garbage values. Nothing here touches the network or saves any file.
Run with:  python -m unittest discover -s tests -v
"""
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import internship_finder  # noqa: E402
from sources import adzuna, company_feeds, internshala, jsearch, remote_boards  # noqa: E402
from sources.common import is_recent, make_job, passes_prefilter  # noqa: E402

NOW = time.time()
OLD = NOW - 90 * 86400
BAD_STRINGS = ["not a date", "31/31/2024", "soon", "   ", "NaN", "9" * 40]
# (value, expected is_recent)
CASES = (
    [(int(NOW), True), (int(NOW * 1000), True), (NOW, True), (NOW * 1000, True),
     (int(OLD), False), (int(OLD * 1000), False), (OLD, False),
     (None, True), (0, True), (float("nan"), True), (float("inf"), True),
     (10**30, True), (-10**30, True), (True, True), ([], True), ({}, True), (object(), True),
     (str(int(NOW)), True), (str(int(OLD * 1000)), False),
     ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(), False),
     ("2 days ago", True), ("Yesterday", True), ("5 months ago", False)]
    + [(s, True) for s in BAD_STRINGS]
)
STIPEND = "Python. ₹20,000 / month"


class IsRecentTests(unittest.TestCase):
    def test_cases(self):
        for value, expected in CASES:
            with self.subTest(value=repr(value)[:50]):
                self.assertIs(is_recent(value), expected)

    def test_never_raises_on_weird_input(self):
        for value in [b"bytes", 1j, range(3), lambda: 1, float("-inf"), "\x00", "T" * 10000]:
            self.assertIn(is_recent(value), (True, False))

    def test_make_job_posted_is_always_a_string(self):
        for value, _ in CASES:
            with self.subTest(value=repr(value)[:50]):
                self.assertIsInstance(make_job("x", "c", "t", "l", "u", "text", posted=value)["posted"], str)
                self.assertIn(passes_prefilter({"title": "Software Intern", "location": "India",
                                                "text": "", "posted": value}), (True, False))


def resp(payload):
    r = mock.Mock(status_code=200, text="x" * 5000)
    r.json.return_value = payload
    return r


class SourceDateTests(unittest.TestCase):
    """Each source gets one listing per case. It must not crash, must drop old
    postings, and must keep recent and unparseable ones."""

    def check(self, run):
        for value, expected in CASES:
            with self.subTest(value=repr(value)[:50]):
                jobs = run(value)
                self.assertEqual(len(jobs), 1 if expected else 0)
                for j in jobs:
                    self.assertIsInstance(j["posted"], str)

    def test_greenhouse(self):
        def run(v):
            data = {"jobs": [{"title": "Software Intern", "location": {"name": "Hyderabad, India"},
                              "content": STIPEND, "first_published": v, "updated_at": v,
                              "absolute_url": "u"}]}
            with mock.patch.object(company_feeds, "_get", return_value=data):
                return company_feeds._greenhouse_jobs({"slug": "s", "name": "N"})
        self.check(run)

    def test_lever(self):
        def run(v):
            data = [{"text": "Software Intern", "categories": {"location": "Hyderabad"},
                     "descriptionPlain": STIPEND, "createdAt": v, "hostedUrl": "u"}]
            with mock.patch.object(company_feeds, "_get", return_value=data):
                return company_feeds._lever_jobs({"slug": "s", "name": "N"})
        self.check(run)

    def test_ashby(self):
        def run(v):
            data = {"jobs": [{"title": "Software Intern", "location": "Hyderabad, India",
                              "descriptionPlain": STIPEND, "publishedAt": v, "jobUrl": "u"}]}
            with mock.patch.object(company_feeds, "_get", return_value=data):
                return company_feeds._ashby_jobs({"slug": "s", "name": "N"})
        self.check(run)

    def test_remotive(self):
        def run(v):
            data = {"jobs": [{"title": "Software Intern", "candidate_required_location": "Worldwide",
                              "description": "Python", "publication_date": v, "company_name": "C",
                              "url": "u"}]}
            with mock.patch.object(remote_boards.requests, "get", return_value=resp(data)):
                return remote_boards._remotive()[0]
        self.check(run)

    def test_remoteok(self):
        def run(v):
            data = [{"legal": "x"}, {"position": "Software Intern", "location": "Worldwide",
                                      "description": "Python", "date": v, "company": "C", "url": "/u"}]
            with mock.patch.object(remote_boards.requests, "get", return_value=resp(data)):
                return remote_boards._remoteok()[0]
        self.check(run)

    def test_himalayas(self):
        def run(v):
            data = {"jobs": [{"title": "Software Intern", "locationRestrictions": [],
                              "description": "Python", "pubDate": v, "companyName": "C",
                              "applicationLink": "u"}]}
            with mock.patch.object(remote_boards.requests, "get", return_value=resp(data)):
                return remote_boards._himalayas()[0]
        self.check(run)

    def test_adzuna(self):
        def run(v):
            data = {"results": [{"title": "Software Intern", "company": {"display_name": "C"},
                                 "location": {"display_name": "Hyderabad"}, "redirect_url": "u",
                                 "description": "Python", "created": v}]}
            env = {"ADZUNA_APP_ID": "i", "ADZUNA_APP_KEY": "k"}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(adzuna, "CITIES", []), \
                    mock.patch.object(adzuna.requests, "get", return_value=resp(data)):
                return adzuna.fetch()[0]
        self.check(run)

    def test_jsearch(self):
        def run(v):
            item = {"job_title": "Software Intern", "employer_name": "C",
                    "job_location": "Hyderabad, India", "job_apply_link": "u",
                    "job_description": "Python", "job_posted_at_datetime_utc": v,
                    "job_posted_at": v}
            usage = {"month": "m", "monthly_count": 0, "daily": {}}
            with mock.patch.dict(os.environ, {"RAPIDAPI_KEY": "k"}), \
                    mock.patch.object(jsearch, "QUERIES", [("q", "in")]), \
                    mock.patch.object(jsearch, "load_usage", return_value=usage), \
                    mock.patch.object(jsearch, "_search", return_value=([item], None)):
                return jsearch.fetch()[0]
        self.check(run)

    def test_internshala_text_dates(self):
        def page(posted):
            return ('<a href="/internship/detail/abc-123">x</a> AI Intern Hyderabad '
                    "₹ 20,000 /month " + posted + ' <a href="/internship/detail/abc-123">y</a>')
        for posted, expected in [("2 days ago", 1), ("Yesterday", 1), ("5 months ago", 0),
                                 ("", 1), ("sometime", 1)]:
            with self.subTest(posted=posted):
                self.assertEqual(len(internshala._parse_listings(page(posted), set())), expected)


class CollectJobsIsolationTests(unittest.TestCase):
    def test_one_crashing_source_does_not_stop_the_others(self):
        good = [{"source": "adzuna", "company": "Acme", "title": "Software Intern",
                 "location": "Hyderabad, India", "url": "https://x/1", "stipend": "s",
                 "posted": "", "text": "Python in India"}]
        usage = {"month": "m", "monthly_count": 0, "daily": {}}
        f = internship_finder
        with mock.patch.object(f.company_feeds, "fetch", return_value=([], {"companies_failed": []})), \
                mock.patch.object(f.jsearch, "fetch", return_value=([], {"errors": []}, usage)), \
                mock.patch.object(f.jsearch, "save_usage") as save_usage, \
                mock.patch.object(f.adzuna, "fetch", return_value=(good, {"errors": []})), \
                mock.patch.object(f.remote_boards, "fetch", side_effect=AttributeError("boom")), \
                mock.patch.object(f.internshala, "fetch", return_value=([], {"pages_ok": 1})), \
                mock.patch("builtins.print") as printed:
            jobs, meta = f.collect_jobs({})
        self.assertEqual([j["url"] for j in jobs], ["https://x/1"])
        save_usage.assert_called_once_with(usage)
        self.assertEqual(meta["remote_boards"], {"errors": ["crashed"]})
        self.assertTrue(any("AttributeError: boom" in str(c) for c in printed.call_args_list))
        _, body = f.compose_email(meta, [], 0)
        self.assertIn("Remote boards: crashed", body)

    def test_every_source_crashing_still_produces_an_email(self):
        f = internship_finder
        names = [(f.company_feeds, "Company career pages"), (f.jsearch, "JSearch"),
                 (f.adzuna, "Adzuna"), (f.remote_boards, "Remote boards"),
                 (f.internshala, "Internshala")]
        with mock.patch.object(f.company_feeds, "fetch", side_effect=RuntimeError("x")), \
                mock.patch.object(f.jsearch, "fetch", side_effect=RuntimeError("x")), \
                mock.patch.object(f.adzuna, "fetch", side_effect=RuntimeError("x")), \
                mock.patch.object(f.remote_boards, "fetch", side_effect=RuntimeError("x")), \
                mock.patch.object(f.internshala, "fetch", side_effect=RuntimeError("x")), \
                mock.patch("builtins.print"):
            jobs, meta = f.collect_jobs({})
        self.assertEqual(jobs, [])
        _, body = f.compose_email(meta, [], 0)
        for _, label in names:
            self.assertIn(f"{label}: crashed", body)


if __name__ == "__main__":
    unittest.main()
