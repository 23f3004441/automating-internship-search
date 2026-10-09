"""JSearch on RapidAPI. Surfaces LinkedIn, Indeed and Glassdoor listings
through Google for Jobs. The free plan is small, so this makes at most
JSEARCH_DAILY_LIMIT calls a day and tracks usage in jsearch_usage.json so
the monthly cap is never crossed.
"""
import json
import os
from datetime import datetime, timezone

import requests

from sources.common import is_internship_title, location_tag, make_job, parse_stipend

USAGE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jsearch_usage.json")
JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"
JSEARCH_HOST = "jsearch.p.rapidapi.com"

JSEARCH_DAILY_LIMIT = 3
# Free RapidAPI plans for JSearch are commonly 200 calls a month. Stop well
# before that so a slow day never pushes the account over the cap.
JSEARCH_MONTHLY_LIMIT = 150

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


def _search(api_key, query, country):
    try:
        r = requests.get(
            JSEARCH_URL,
            headers={"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": JSEARCH_HOST},
            params={
                "query": query,
                "country": country,
                "page": "1",
                "num_pages": "1",
                "date_posted": "week",
                "employment_types": "INTERN",
            },
            timeout=30,
        )
    except requests.RequestException as e:
        return None, str(e)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} {r.text[:160]}"
    try:
        data = r.json()
    except ValueError:
        return None, "bad JSON"
    return data.get("data", []), None


def _normalize(item):
    title = item.get("job_title", "") or ""
    company = item.get("employer_name", "") or ""
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
        usage["daily"][today] = usage["daily"].get(today, 0) + 1
        usage["monthly_count"] += 1
        meta["queries"] += 1
        if err:
            meta["errors"].append(f"{query}: {err}")
            continue
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
            jobs.append(make_job("jsearch", company, title, loc, url, desc, stipend, posted))

    meta["ran"] = True
    meta["monthly_count"] = usage["monthly_count"]
    return jobs, meta, usage
