"""Internshala search pages. Kept as a minor source now that company feeds,
JSearch, Adzuna and the remote boards do most of the work."""
import html as htmllib
import re
import time

import requests

from sources.common import is_recent, make_job, parse_stipend

BASE = "https://internshala.com/internships/"
PROFILES = "work-from-home-ai-agent-development,artificial-intelligence-ai-internships-in-"
CITIES = ["hyderabad", "bangalore", "chennai"]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _search_urls():
    urls = []
    for city in CITIES:
        root = BASE + PROFILES + city + "/stipend-10000/"
        urls.append(root)
        urls.append(root + "page-2/")
    return urls


def _fetch_page(url):
    for attempt in range(2):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"},
                timeout=30,
            )
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            print("Internshala page problem", r.status_code, url)
        except requests.RequestException as e:
            print("Internshala page error", e, url)
        time.sleep(3)
    return None


def _strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _parse_listings(page_html, seen_urls):
    page_html = re.sub(r"<script[\s\S]*?</script>", " ", page_html, flags=re.I)
    page_html = re.sub(r"<style[\s\S]*?</style>", " ", page_html, flags=re.I)

    runs = []
    for m in re.finditer(r"/internship/detail/[^\"'\s<>?#\\]+", page_html):
        if runs and runs[-1][0] == m.group(0):
            continue
        runs.append((m.group(0), m.start()))

    jobs = []
    for i, (path, start) in enumerate(runs):
        url = "https://internshala.com" + path
        if url in seen_urls:
            continue
        end = runs[i + 1][1] if i + 1 < len(runs) else min(len(page_html), start + 3500)
        cut = page_html.rfind("<", 0, end)
        if cut > start:
            end = cut
        seg = re.sub(r"^[^<>]*>", "", page_html[start:end], count=1)
        text = _strip_tags(seg)

        keep, label = parse_stipend(text)
        if not keep:
            continue
        title_match = re.match(r"^(.*?internship)", text, re.I)
        title = title_match.group(1) if title_match else "Internship"
        pm = re.search(
            r"(Just now|Few hours ago|Today|Yesterday|\d+\s+(?:hours?|days?|weeks?|months?)\s+ago)",
            text,
            re.I,
        )
        posted = pm.group(1) if pm else ""
        if not is_recent(posted):
            continue
        jobs.append(
            make_job(
                "internshala", "", title, "", url, text[:600], label, posted,
            )
        )
    return jobs


def fetch(seen_urls):
    """Returns (jobs, meta)."""
    jobs = []
    pages_ok = pages_failed = 0
    seen_in_run = set(seen_urls)
    for url in _search_urls():
        page = _fetch_page(url)
        if page is None:
            pages_failed += 1
        else:
            pages_ok += 1
            page_jobs = _parse_listings(page, seen_in_run)
            for j in page_jobs:
                seen_in_run.add(j["url"])
            jobs.extend(page_jobs)
        time.sleep(3)
    meta = {"pages_ok": pages_ok, "pages_failed": pages_failed}
    return jobs, meta
