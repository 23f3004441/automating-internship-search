#!/usr/bin/env python3
"""Daily internship finder.

Pulls new internship listings from several sources, scores them against
Yumna's resume with Groq, and emails the best ones. Remembers what it
already sent in seen.json.

Sources, checked in this order:
1. Company career pages through Greenhouse, Lever and Ashby (companies.json)
2. JSearch on RapidAPI (budget limited, see sources/jsearch.py)
3. Adzuna for India
4. Remote boards: Remotive, RemoteOK, Himalayas
5. Internshala (minor source)

Run locally for a safe test:  python internship_finder.py --dry-run
"""
import argparse
import html as htmllib
import json
import os
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sources import adzuna, company_feeds, internshala, jsearch, remote_boards  # noqa: E402
from sources.common import dedup_key, passes_prefilter  # noqa: E402

MAX_JOBS = 100
BATCH_SIZE = 4
SECONDS_BETWEEN_BATCHES = 25
SEEN_DAYS = 60

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile on 16 Aug 2026. If a model stops working, the next one is tried.
MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
ACTIVE_MODELS = list(MODELS)
LAST_GROQ_ERROR = ""
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")

SOURCE_GROUPS = {
    "greenhouse": "Company career pages",
    "lever": "Company career pages",
    "ashby": "Company career pages",
    "jsearch": "JSearch (job boards)",
    "adzuna": "Adzuna",
    "remotive": "Remote boards",
    "remoteok": "Remote boards",
    "himalayas": "Remote boards",
    "internshala": "Internshala",
}
GROUP_ORDER = [
    "Company career pages",
    "JSearch (job boards)",
    "Adzuna",
    "Remote boards",
    "Internshala",
]

SYSTEM_PROMPT = """You score internship listings for one candidate.

Candidate: 4th year Computer Science student in Hyderabad, India. Skills: LLM prompting, n8n, Make, workflow automation, API integration, webhooks, evaluating AI outputs, Python, SQL, SQLAlchemy, Redis, Airtable, Google Sheets, Flask, Vue.js, HTML, CSS, JavaScript, Git. Projects: WhatsApp AI lead response agent (n8n, Groq), lead qualifier with a voice agent and proposal workflow (Make, Airtable, Gemini), prospect research tools (Relevance AI), a full stack placement portal (Flask, Vue).

She wants: AI agents, AI automation, workflow automation, n8n or Make, LLM and prompt work, generative AI, API integration, Python backend, full stack web development.
Location is fine if work from home, or on site or hybrid in South India (Hyderabad, Bangalore, Chennai, Coimbatore, Kochi and similar). Dubai or UAE on site roles are a welcome bonus. She is an Indian citizen studying in India, not eligible for roles that require a different work authorization.

Scoring:
9 to 10: the role is mainly AI agents, automation or LLM work that matches her skills.
7 to 8: good match, most duties fit her skills.
5 to 6: partial match.
0 to 4: weak or unrelated, such as medical role play, language training, content writing, video editing, marketing, sales, data entry or pure research.
Rules:
Score at most 3 if the role is on site outside South India and outside Dubai or the UAE.
Score at most 3 if a remote role is explicitly limited to the US, Europe, or any other country or region that excludes India, even if the rest of the role fits well.
Score at most 3 if the posting requires current enrollment in a US university or any non Indian university, unless it clearly says India based students or India enrolled students are allowed.
Score at most 5 if the role is mainly machine learning model building, deep learning or data science, because her resume has no such projects.
Score at most 4 if the company looks tiny, unknown, or like an individual recruiter rather than a real company, unless the listing clearly shows it is an established company.
Use only what the listing says. Do not guess about the company.

Return JSON only, in this exact shape:
{"results":[{"id":<number>,"score":<0 to 10>,"title":"<role title>","company":"<company name>","location":"<short location>","reason":"<at most 15 words on why it fits or not>"}]}
Give one result for every JOB id you receive. Do not use dashes as punctuation in any text."""


def extract_json(content):
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        pass
    try:
        start = content.index("{")
        end = content.rindex("}")
        return json.loads(content[start : end + 1])
    except (ValueError, AttributeError):
        return None


def call_groq(api_key, user_text):
    """Returns the model's text, or None. Falls back to the next model if one is retired."""
    global LAST_GROQ_ERROR
    for model in list(ACTIVE_MODELS):
        use_json_mode = True
        attempt = 0
        while attempt < 4:
            attempt += 1
            payload = {
                "model": model,
                "temperature": 0.1,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
            }
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}
            try:
                r = requests.post(
                    GROQ_URL,
                    headers={"Authorization": "Bearer " + api_key},
                    json=payload,
                    timeout=90,
                )
            except requests.RequestException as e:
                LAST_GROQ_ERROR = f"{model}: {e}"
                print("Groq error", LAST_GROQ_ERROR)
                time.sleep(20)
                continue
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"]
            LAST_GROQ_ERROR = f"{model}: HTTP {r.status_code} {r.text[:160]}"
            print("Groq problem", LAST_GROQ_ERROR)
            if r.status_code in (401, 403):
                return None
            if r.status_code in (429, 500, 502, 503):
                try:
                    wait = float(r.headers.get("retry-after", 20))
                except ValueError:
                    wait = 20
                time.sleep(min(wait + 1, 90))
                continue
            if r.status_code == 400 and use_json_mode:
                use_json_mode = False
                attempt -= 1
                continue
            break
        if len(ACTIVE_MODELS) > 1 and model in ACTIVE_MODELS:
            ACTIVE_MODELS.remove(model)
            print("Dropping model", model)
    return None


def score_jobs(api_key, jobs):
    scored = []
    failed = 0
    for i in range(0, len(jobs), BATCH_SIZE):
        chunk = jobs[i : i + BATCH_SIZE]
        user_text = "\n\n=====\n\n".join(
            f"JOB {j['id']}\nStipend: {j['stipend']}\nPosted: {j['posted']}\n"
            f"Company: {j['company'] or 'unknown'}\nLocation: {j['location'] or 'unknown'}\n{j['text']}"
            for j in chunk
        )
        content = call_groq(api_key, user_text)
        results = None
        if content:
            data = extract_json(content)
            results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            failed += 1
        else:
            for job in chunk:
                res = next((x for x in results if str(x.get("id")) == str(job["id"])), None)
                if not res:
                    continue
                try:
                    score = int(float(res.get("score", 0)))
                except (TypeError, ValueError):
                    score = 0
                scored.append(
                    {
                        "source": job["source"],
                        "group": SOURCE_GROUPS.get(job["source"], job["source"]),
                        "url": job["url"],
                        "stipend": job["stipend"],
                        "posted": job["posted"],
                        "score": score,
                        "title": job["title"] if job["title"] and job["title"] != "Internship" else (res.get("title") or "Internship"),
                        "company": job["company"] or res.get("company") or "",
                        "location": job["location"] or res.get("location") or "",
                        "reason": res.get("reason") or "",
                    }
                )
        if i + BATCH_SIZE < len(jobs):
            time.sleep(SECONDS_BETWEEN_BATCHES)
    return scored, failed


def esc(s):
    return htmllib.escape(str(s or ""))


def compose_email(meta, scored, failed_batches):
    scored = sorted(scored, key=lambda j: j["score"], reverse=True)
    top = [j for j in scored if j["score"] >= 7]
    maybe = [j for j in scored if 5 <= j["score"] < 7]

    warn_parts = []
    cf = meta.get("company_feeds", {})
    if cf.get("companies_failed"):
        warn_parts.append(
            f"{len(cf['companies_failed'])} company feed(s) did not respond today: "
            + ", ".join(cf["companies_failed"][:10])
        )
    js = meta.get("jsearch", {})
    for err in js.get("errors", []):
        warn_parts.append(f"JSearch: {err}")
    az = meta.get("adzuna", {})
    for err in az.get("errors", []):
        warn_parts.append(f"Adzuna: {err}")
    rb = meta.get("remote_boards", {})
    for err in rb.get("errors", []):
        warn_parts.append(f"Remote boards: {err}")
    ih = meta.get("internshala", {})
    if ih.get("pages_ok", 1) == 0:
        warn_parts.append("Could not load any Internshala page today.")
    elif ih.get("pages_failed", 0) > 0:
        warn_parts.append(f"{ih['pages_failed']} Internshala page(s) failed to load today.")
    if failed_batches:
        msg = f"{failed_batches} scoring batch(es) failed. Those jobs will be tried again tomorrow."
        if LAST_GROQ_ERROR:
            msg += " Last Groq error: " + LAST_GROQ_ERROR
        warn_parts.append(msg)
    if meta.get("overflow", 0) > 0:
        warn_parts.append(f"{meta['overflow']} extra new listings will be scored tomorrow.")
    warn = " ".join(warn_parts)

    if meta["new_count"] == 0:
        subject = "Internship agent needs attention"
    elif top:
        subject = f"Internships today: {len(top)} strong match" + ("es" if len(top) > 1 else "")
    else:
        subject = "Internships today: no strong matches"

    body = '<div style="font-family:Arial,sans-serif;max-width:640px">'
    body += f"<p>{meta['new_count']} new listings checked, {len(top)} scored 7 or higher.</p>"
    if warn:
        body += f"<p><b>{esc(warn)}</b></p>"

    for group in GROUP_ORDER:
        group_top = [j for j in top if j["group"] == group]
        group_maybe = [j for j in maybe if j["group"] == group]
        if not group_top and not group_maybe:
            continue
        body += f'<p style="margin-top:24px"><b>{esc(group)}</b></p>'
        for j in group_top:
            line = f"Score {j['score']}/10 | {esc(j['stipend'])}"
            if j["location"]:
                line += " | " + esc(j["location"])
            if j["posted"]:
                line += " | " + esc(j["posted"])
            body += (
                '<div style="margin:0 0 18px">'
                f"<div><b>{esc(j['title'])}</b></div>"
                f"<div>{esc(j['company'])}</div>"
                f"<div>{line}</div>"
                f"<div><i>{esc(j['reason'])}</i></div>"
                f'<div><a href="{esc(j["url"])}">Open listing</a></div></div>'
            )
        if group_maybe:
            body += '<p><i>Maybe (score 5 to 6)</i></p><ul style="padding-left:18px">'
            for j in group_maybe:
                body += f'<li><a href="{esc(j["url"])}">{esc(j["title"])}</a>, {esc(j["company"])}, {esc(j["stipend"])}</li>'
            body += "</ul>"

    funnel = meta.get("funnel", {})
    if funnel:
        body += '<p style="margin-top:28px"><i>Source counts today</i></p>'
        body += '<ul style="padding-left:18px">'
        for group in GROUP_ORDER:
            fetched = funnel.get("fetched", {}).get(group, 0)
            reached = funnel.get("reached_scoring", {}).get(group, 0)
            body += f"<li>{esc(group)}: {fetched} found, {reached} reached scoring</li>"
        body += "</ul>"
    body += "</div>"
    return subject, body


def send_email(subject, body_html):
    address = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = address
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
        server.login(address, password)
        server.sendmail(address, [address], msg.as_string())


def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_DAYS)).isoformat()
    return {k: v for k, v in data.items() if v >= cutoff}


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=1, sort_keys=True)


def collect_jobs(seen):
    """Fetches every source and returns (jobs, meta). Applies cross source
    dedup (same company and title) and the seen.json memory."""
    all_jobs = []
    meta = {}

    cf_jobs, cf_meta = company_feeds.fetch()
    all_jobs.extend(cf_jobs)
    meta["company_feeds"] = cf_meta

    js_jobs, js_meta, js_usage = jsearch.fetch()
    all_jobs.extend(js_jobs)
    meta["jsearch"] = js_meta
    jsearch.save_usage(js_usage)

    az_jobs, az_meta = adzuna.fetch()
    all_jobs.extend(az_jobs)
    meta["adzuna"] = az_meta

    rb_jobs, rb_meta = remote_boards.fetch()
    all_jobs.extend(rb_jobs)
    meta["remote_boards"] = rb_meta

    seen_urls = set(seen)
    ih_jobs, ih_meta = internshala.fetch(seen_urls)
    all_jobs.extend(ih_jobs)
    meta["internshala"] = ih_meta

    fetched_counts = _count_by_group(all_jobs)

    deduped = []
    seen_keys = set()
    for j in all_jobs:
        if j["url"] in seen:
            continue
        # Only dedup by company and title when the company is actually known.
        # Internshala jobs still have a placeholder title and no company at
        # this point, those are only told apart by Groq after scoring, so for
        # them we fall back to the URL based seen.json memory alone.
        if j["company"]:
            key = dedup_key(j["company"], j["title"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
        deduped.append(j)

    # Cheap pre filter: drops titles that show no tech work and anything
    # that fails the location or stipend rule, before the costly Groq calls.
    filtered = [j for j in deduped if passes_prefilter(j)]
    reached_counts = _count_by_group(filtered)

    overflow = max(0, len(filtered) - MAX_JOBS)
    filtered = filtered[:MAX_JOBS]
    for idx, j in enumerate(filtered, start=1):
        j["id"] = idx

    meta["new_count"] = len(filtered)
    meta["overflow"] = overflow
    meta["funnel"] = {"fetched": fetched_counts, "reached_scoring": reached_counts}
    return filtered, meta


def _count_by_group(jobs):
    counts = {group: 0 for group in GROUP_ORDER}
    for j in jobs:
        group = SOURCE_GROUPS.get(j["source"], j["source"])
        counts[group] = counts.get(group, 0) + 1
    return counts


def run(dry_run=False):
    seen = load_seen()
    all_jobs, meta = collect_jobs(seen)

    scored, failed_batches = [], 0
    if all_jobs:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise SystemExit("GROQ_API_KEY is not set")
        scored, failed_batches = score_jobs(api_key, all_jobs)

    print("All scored jobs (not in the email, for checking why things scored low):")
    for j in sorted(scored, key=lambda x: x["score"], reverse=True):
        print(f"  {j['score']:>2} | {j['source']:10s} | {j['company'][:30]:30s} | {j['title'][:60]:60s} | {j['reason']}")

    subject, body = compose_email(meta, scored, failed_batches)
    print(subject, {k: v for k, v in meta.items() if k not in ("company_feeds", "jsearch")})
    print("Company feeds failed:", meta["company_feeds"].get("companies_failed"))
    print("JSearch:", meta.get("jsearch"))
    print("Adzuna:", meta.get("adzuna"))
    print("Remote boards:", meta.get("remote_boards"))
    print("Funnel (found -> reached scoring):")
    for group in GROUP_ORDER:
        fetched = meta["funnel"]["fetched"].get(group, 0)
        reached = meta["funnel"]["reached_scoring"].get(group, 0)
        print(f"  {group}: {fetched} -> {reached}")

    if dry_run:
        print(body)
        return

    send_email(subject, body)
    now = datetime.now(timezone.utc).isoformat()
    for j in scored:
        seen[j["url"]] = now
    save_seen(seen)
    print("Email sent. Remembered", len(seen), "listings.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print the email, do not send or save")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
