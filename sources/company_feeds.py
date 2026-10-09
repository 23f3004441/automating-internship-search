"""Company career page feeds: Greenhouse, Lever and Ashby public job boards.

Companies are listed in companies.json, each already checked by hand to have
a working feed and genuine India, remote-India or UAE postings. Only postings
whose title looks like an internship are kept; full time roles never reach
scoring.
"""
import json
import os
import time

import requests

from sources.common import is_internship_title, location_tag, make_job, parse_stipend, strip_html

COMPANIES_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "companies.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 20


def load_companies():
    with open(COMPANIES_FILE, encoding="utf-8") as f:
        return json.load(f)


def _get(url):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except (requests.RequestException, ValueError):
        return None


def _greenhouse_jobs(company):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{company['slug']}/jobs?content=true")
    if data is None:
        return None
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = (j.get("location") or {}).get("name", "")
        if not is_internship_title(title):
            continue
        tag = location_tag(loc)
        if not tag:
            continue
        text = strip_html(j.get("content", ""))
        keep, stipend = parse_stipend(text)
        if not keep:
            continue
        out.append(
            make_job(
                "greenhouse", company["name"], title, loc,
                j.get("absolute_url", ""), text, stipend,
            )
        )
    return out


def _lever_jobs(company):
    data = _get(f"https://api.lever.co/v0/postings/{company['slug']}?mode=json")
    if data is None or not isinstance(data, list):
        return None
    out = []
    for j in data:
        title = j.get("text", "")
        cats = j.get("categories", {}) or {}
        loc = cats.get("location", "") or ", ".join(cats.get("allLocations", []) or [])
        if not is_internship_title(title):
            continue
        tag = location_tag(loc)
        if not tag:
            continue
        text = j.get("descriptionPlain", "") or strip_html(j.get("description", ""))
        keep, stipend = parse_stipend(text)
        if not keep:
            continue
        out.append(
            make_job(
                "lever", company["name"], title, loc,
                j.get("hostedUrl", ""), text, stipend,
            )
        )
    return out


def _ashby_jobs(company):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{company['slug']}")
    if data is None:
        return None
    out = []
    for j in data.get("jobs", []):
        title = j.get("title", "")
        loc = j.get("location", "") or ""
        if not is_internship_title(title):
            continue
        tag = location_tag(loc)
        if not tag:
            continue
        text = j.get("descriptionPlain", "") or strip_html(j.get("descriptionHtml", ""))
        keep, stipend = parse_stipend(text)
        if not keep:
            continue
        out.append(
            make_job(
                "ashby", company["name"], title, loc,
                j.get("jobUrl", ""), text, stipend,
            )
        )
    return out


FETCHERS = {"greenhouse": _greenhouse_jobs, "lever": _lever_jobs, "ashby": _ashby_jobs}


def fetch():
    """Returns (jobs, meta). meta tracks which company feeds failed to respond."""
    jobs = []
    failed = []
    for company in load_companies():
        fn = FETCHERS.get(company.get("platform"))
        if fn is None:
            continue
        result = fn(company)
        if result is None:
            failed.append(company["name"])
        else:
            jobs.extend(result)
        time.sleep(0.3)
    meta = {"companies_checked": len(load_companies()), "companies_failed": failed}
    return jobs, meta
