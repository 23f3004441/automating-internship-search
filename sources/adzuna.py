"""Adzuna job search for India."""
import os

import requests

from sources.common import is_internship_title, location_tag, make_job, parse_stipend

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/in/search/1"
CITIES = ["Hyderabad", "Bangalore", "Chennai"]


def _search(app_id, app_key, where=None):
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": "internship",
        "results_per_page": 50,
        "content-type": "application/json",
    }
    if where:
        params["where"] = where
    try:
        r = requests.get(ADZUNA_URL, params=params, timeout=30)
    except requests.RequestException as e:
        return None, str(e)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code} {r.text[:160]}"
    try:
        data = r.json()
    except ValueError:
        return None, "bad JSON"
    return data.get("results", []), None


def fetch():
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    meta = {"ran": False, "errors": []}
    if not app_id or not app_key:
        meta["errors"].append("ADZUNA_APP_ID or ADZUNA_APP_KEY is not set, Adzuna skipped")
        return [], meta

    jobs = []
    seen_urls = set()
    for where in CITIES + [None]:
        results, err = _search(app_id, app_key, where)
        if err:
            meta["errors"].append(f"{where or 'India'}: {err}")
            continue
        for item in results or []:
            title = item.get("title", "") or ""
            company = (item.get("company") or {}).get("display_name", "") or ""
            loc = (item.get("location") or {}).get("display_name", "") or ""
            url = item.get("redirect_url", "") or ""
            desc = item.get("description", "") or ""
            posted = item.get("created", "") or ""
            if not url or url in seen_urls:
                continue
            if not is_internship_title(title):
                continue
            if not location_tag(loc) and not location_tag(desc[:300]):
                continue
            keep, stipend = parse_stipend(desc)
            if not keep:
                continue
            seen_urls.add(url)
            jobs.append(make_job("adzuna", company, title, loc, url, desc, stipend, posted))

    meta["ran"] = True
    return jobs, meta
