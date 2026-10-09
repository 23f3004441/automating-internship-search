"""Free remote job boards: Remotive, RemoteOK and Himalayas. Only used if
each one responds reliably, each call is wrapped so one board failing does
not block the others."""
import requests

from sources.common import is_internship_title, is_recent, location_tag, make_job, parse_stipend, strip_html

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) internship-finder/1.0"}


def _remotive():
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": "internship"},
            headers=UA, timeout=20,
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        jobs = r.json().get("jobs", [])
    except (requests.RequestException, ValueError) as e:
        return None, str(e)

    out = []
    for j in jobs:
        title = j.get("title", "") or ""
        loc = j.get("candidate_required_location", "") or ""
        if not is_internship_title(title):
            continue
        desc = strip_html(j.get("description", ""))
        if not location_tag(loc) and not location_tag(desc[:300]):
            continue
        keep, stipend = parse_stipend(desc)
        if not keep:
            continue
        posted = j.get("publication_date", "")
        if not is_recent(posted):
            continue
        out.append(make_job(
            "remotive", j.get("company_name", ""), title, loc,
            j.get("url", ""), desc, stipend, posted,
        ))
    return out, None


def _remoteok():
    try:
        r = requests.get("https://remoteok.com/api", headers=UA, timeout=20)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        jobs = r.json()
    except (requests.RequestException, ValueError) as e:
        return None, str(e)

    out = []
    for j in jobs[1:]:
        title = j.get("position", "") or ""
        loc = j.get("location", "") or "Worldwide"  # RemoteOK leaves this blank for unrestricted roles
        if not is_internship_title(title):
            continue
        desc = strip_html(j.get("description", ""))
        if not location_tag(loc) and not location_tag(desc[:300]):
            continue
        keep, stipend = parse_stipend(desc)
        if not keep:
            continue
        posted = j.get("date", "")
        if not is_recent(posted):
            continue
        url = j.get("url", "") or j.get("apply_url", "")
        if url and url.startswith("/"):
            url = "https://remoteok.com" + url
        out.append(make_job(
            "remoteok", j.get("company", ""), title, loc,
            url, desc, stipend, posted,
        ))
    return out, None


def _himalayas():
    try:
        r = requests.get(
            "https://himalayas.app/jobs/api", params={"limit": 100}, headers=UA, timeout=20,
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        jobs = r.json().get("jobs", [])
    except (requests.RequestException, ValueError) as e:
        return None, str(e)

    out = []
    for j in jobs:
        title = j.get("title", "") or ""
        loc = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
        if not is_internship_title(title):
            continue
        desc = strip_html(j.get("description", "") or j.get("excerpt", ""))
        if not location_tag(loc) and not location_tag(desc[:300]):
            continue
        keep, stipend = parse_stipend(desc)
        if not keep:
            continue
        posted = j.get("pubDate", "")
        if not is_recent(posted):
            continue
        out.append(make_job(
            "himalayas", j.get("companyName", ""), title, loc,
            j.get("applicationLink", ""), desc, stipend, posted,
        ))
    return out, None


BOARDS = {"remotive": _remotive, "remoteok": _remoteok, "himalayas": _himalayas}


def fetch():
    jobs = []
    meta = {"errors": [], "boards_ok": []}
    for name, fn in BOARDS.items():
        result, err = fn()
        if err:
            meta["errors"].append(f"{name}: {err}")
        else:
            meta["boards_ok"].append(name)
            jobs.extend(result)
    return jobs, meta
