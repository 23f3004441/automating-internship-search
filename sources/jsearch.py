"""JSearch on RapidAPI. Surfaces LinkedIn, Indeed and Glassdoor listings
through Google for Jobs. The free plan is small, so this makes at most
JSEARCH_DAILY_LIMIT calls a day and tracks usage in jsearch_usage.json so
the monthly cap is never crossed. Only calls that actually succeed count
against that budget, a failed call (wrong endpoint, timeout, rate limit)
is not spent quota.
"""
import json
import os
from datetime import datetime, timezone

import requests

from sources.common import is_internship_title, is_recent, location_tag, make_job, parse_stipend

USAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jsearch_usage.json")
JSEARCH_HOST = "jsearch.p.rapidapi.com"
# JSearch v5 (2026) searches at /search-v2 with cursor based paging, older
# plans used /search with page numbers. Try the current path first, and
# fall back to the other one on a 404 so a plan change on either side does
# not take this source down.
SEARCH_PATHS = ["search-v2", "search"]

JSEARCH_DAILY_LIMIT = 3
# At most 3 calls a day, so 90 is already a hard ceiling over 30 days, this
# just makes that explicit and keeps a bit of room below whatever the real
# RapidAPI plan limit turns out to be.
JSEARCH_MONTHLY_LIMIT = 90

# (query, country) pairs. country is the JSearch / Google for Jobs two
# letter country code, it narrows results to that country instead of
# relying on wording alone. Two India searches and one Dubai or UAE search,
# using all 3 of the daily searches the free plan can afford.
QUERIES = [
    ("AI agent or AI automation or LLM internship", "in"),
    ("software engineer or full stack developer internship", "in"),
    ("software engineer or business internship in Dubai UAE", "ae"),
]


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def load_usage():
    try:
        with open(USAGE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    month = _month()
    if data.get("month") != month:
        data = {"month": month, "monthly_count": 0, "daily": {}}
    data.setdefault("monthly_count", 0)
    data.setdefault("daily", {})
    return data


def save_usage(usage):
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(usage, f, indent=1, sort_keys=True)


def _call(api_key, path, query, country):
    try:
        r = requests.get(
            f"https://{JSEARCH_HOST}/{path}",
            headers={"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": JSEARCH_HOST},
            params={
                "query": query,
                "country": country,
                "num_pages": "1",
                "date_posted": "week",
                "employment_types": "INTERN",
            },
            timeout=30,
        )
    except requests.RequestException as e:
        return None, None, str(e)
    return r, path, None


def _search(api_key, query, country):
    """Tries the configured search paths in order, retrying on a 404 so a
    path change on either side does not take this source down. Returns
    (jobs, error)."""
    last_err = None
    for path in SEARCH_PATHS:
        r, used_path, err = _call(api_key, path, query, country)
        if err:
            last_err = err
            continue
        if r.status_code == 404:
            last_err = f"HTTP 404 at /{path}"
            continue
        if r.status_code != 200:
            return None, f"HTTP {r.status_code} {r.text[:160]}"
        try:
            payload = r.json()
        except ValueError:
            return None, "bad JSON"
        data = payload.get("data", [])
        # v5 nests the list under data.jobs alongside a cursor, older plans
        # returned the list directly as data.
        jobs = data.get("jobs", []) if isinstance(data, dict) else data
        return jobs, None
    return None, last_err or "all search paths failed"


def _normalize(item):
    title = item.get("job_title", "") or ""
    company = item.get("employer_name", "") or ""
    loc = item.get("job_location") or ""
    if not loc:
        city = item.get("job_city") or ""
        state = item.get("job_state") or ""
        country = item.get("job_country") or ""
        if item.get("job_is_remote"):
            loc = "Remote" + (f", {country}" if country else "")
        else:
            loc = ", ".join(p for p in [city, state, country] if p)
    url = item.get("job_apply_link") or item.get("job_google_link") or ""
    desc = item.get("job_description", "") or ""
    posted = item.get("job_posted_at_datetime_utc", "") or item.get("job_posted_at", "") or ""
    return title, company, loc, url, desc, posted


def fetch():
    """Returns (jobs, meta, usage). Skips cleanly if RAPIDAPI_KEY is not set."""
    api_key = os.environ.get("RAPIDAPI_KEY")
    usage = load_usage()
    meta = {"ran": False, "queries": 0, "errors": [], "monthly_count": usage["monthly_count"]}
    if not api_key:
        meta["errors"].append("RAPIDAPI_KEY is not set, JSearch skipped")
        return [], meta, usage

    today = _today()
    used_today = usage["daily"].get(today, 0)
    remaining_today = max(0, JSEARCH_DAILY_LIMIT - used_today)
    remaining_month = max(0, JSEARCH_MONTHLY_LIMIT - usage["monthly_count"])
    allowed = min(remaining_today, remaining_month, len(QUERIES))

    if allowed <= 0:
        meta["errors"].append("JSearch daily or monthly budget already used, skipped today")
        return [], meta, usage

    jobs = []
    for query, country in QUERIES[:allowed]:
        results, err = _search(api_key, query, country)
        meta["queries"] += 1
        if err:
            meta["errors"].append(f"{query}: {err}")
            continue
        # Only a call that actually succeeded counts against the budget.
        usage["daily"][today] = usage["daily"].get(today, 0) + 1
        usage["monthly_count"] += 1
        for item in results or []:
            title, company, loc, url, desc, posted = _normalize(item)
            if not url or not is_internship_title(title):
                continue
            if not location_tag(loc) and not location_tag(desc[:300]):
                continue
            # parse_stipend also drops anything under MIN_STIPEND and
            # anything the description calls unpaid.
            keep, stipend = parse_stipend(desc)
            if not keep:
                continue
            if not is_recent(posted):
                continue
            jobs.append(make_job("jsearch", company, title, loc, url, desc, stipend, posted))

    meta["ran"] = True
    meta["monthly_count"] = usage["monthly_count"]
    return jobs, meta, usage
