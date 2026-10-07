#!/usr/bin/env python3
"""Daily internship finder.

Reads Internshala search pages, scores new listings against Yumna's resume with Groq,
and emails the best ones. Remembers what it already sent in seen.json.

Run locally for a safe test:  python internship_finder.py --dry-run
"""
import argparse
import html as htmllib
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

BASE = "https://internshala.com/internships/"
PROFILES = "work-from-home-ai-agent-development,artificial-intelligence-ai-internships-in-"
CITIES = ["hyderabad", "bangalore", "chennai"]

MIN_STIPEND = 10000
MAX_JOBS = 80
BATCH_SIZE = 4
SECONDS_BETWEEN_BATCHES = 25
SEEN_DAYS = 60

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile on 16 Aug 2026. If a model stops working, the next one is tried.
MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
ACTIVE_MODELS = list(MODELS)
LAST_GROQ_ERROR = ""
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SYSTEM_PROMPT = """You score internship listings for one candidate.

Candidate: 4th year Computer Science student in Hyderabad. Skills: LLM prompting, n8n, Make, workflow automation, API integration, webhooks, evaluating AI outputs, Python, SQL, SQLAlchemy, Redis, Airtable, Google Sheets, Flask, Vue.js, HTML, CSS, JavaScript, Git. Projects: WhatsApp AI lead response agent (n8n, Groq), lead qualifier with a voice agent and proposal workflow (Make, Airtable, Gemini), prospect research tools (Relevance AI), a full stack placement portal (Flask, Vue).

She wants: AI agents, AI automation, workflow automation, n8n or Make, LLM and prompt work, generative AI, API integration, Python backend, full stack web development.
Location is fine if work from home, or on site or hybrid in South India (Hyderabad, Bangalore, Chennai, Coimbatore, Kochi and similar).

Scoring:
9 to 10: the role is mainly AI agents, automation or LLM work that matches her skills.
7 to 8: good match, most duties fit her skills.
5 to 6: partial match.
0 to 4: weak or unrelated, such as medical role play, language training, content writing, video editing, marketing, sales, data entry or pure research.
Rules: score at most 3 if the role is on site outside South India. Score at most 5 if the role is mainly machine learning model building, deep learning or data science, because her resume has no such projects. Use only what the listing says. Do not guess about the company.

Return JSON only, in this exact shape:
{"results":[{"id":<number>,"score":<0 to 10>,"title":"<role title>","company":"<company name>","location":"<short location>","reason":"<at most 15 words on why it fits or not>"}]}
Give one result for every JOB id you receive. Do not use dashes as punctuation in any text."""


def search_urls():
    urls = []
    for city in CITIES:
        root = BASE + PROFILES + city + "/stipend-10000/"
        urls.append(root)
        urls.append(root + "page-2/")
    return urls


def fetch_page(url):
    for attempt in range(2):
        try:
            r = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"},
                timeout=30,
            )
            if r.status_code == 200 and len(r.text) > 2000:
                return r.text
            print("Page problem", r.status_code, url)
        except requests.RequestException as e:
            print("Page error", e, url)
        time.sleep(3)
    return None


def strip_tags(s):
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_stipend(text):
    """Returns (keep, label). Drops stipends whose top amount is below MIN_STIPEND."""
    rs = re.search(r"₹\s*([\d,]+)(?:\s*-\s*([\d,]+))?\s*/\s*month", text, re.I)
    usd = re.search(r"\$\s*([\d,]+)(?:\s*-\s*([\d,]+))?\s*/\s*month", text, re.I)
    if rs:
        a = int(rs.group(1).replace(",", ""))
        b = int(rs.group(2).replace(",", "")) if rs.group(2) else a
        if max(a, b) < MIN_STIPEND:
            return False, ""
        if rs.group(2):
            return True, f"₹{a:,} to {b:,} per month"
        return True, f"₹{a:,} per month"
    if usd:
        top = int((usd.group(2) or usd.group(1)).replace(",", ""))
        if top * 85 < MIN_STIPEND:
            return False, ""
        return True, f"${top} per month"
    if re.search(r"\bunpaid\b", text, re.I):
        return False, ""
    return True, "stipend not listed"


def parse_listings(page_html, seen, in_run):
    """Returns (jobs, found). found counts every listing link on the page."""
    page_html = re.sub(r"<script[\s\S]*?</script>", " ", page_html, flags=re.I)
    page_html = re.sub(r"<style[\s\S]*?</style>", " ", page_html, flags=re.I)

    runs = []
    for m in re.finditer(r"/internship/detail/[^\"'\s<>?#\\]+", page_html):
        if runs and runs[-1][0] == m.group(0):
            continue
        runs.append((m.group(0), m.start()))

    jobs = []
    found = 0
    for i, (path, start) in enumerate(runs):
        found += 1
        url = "https://internshala.com" + path
        if url in seen or url in in_run:
            continue
        end = runs[i + 1][1] if i + 1 < len(runs) else min(len(page_html), start + 3500)
        cut = page_html.rfind("<", 0, end)
        if cut > start:
            end = cut
        seg = re.sub(r"^[^<>]*>", "", page_html[start:end], count=1)
        text = strip_tags(seg)

        keep, label = parse_stipend(text)
        if not keep:
            continue
        pm = re.search(
            r"(Just now|Few hours ago|Today|Yesterday|\d+\s+(?:hours?|days?|weeks?|months?)\s+ago)",
            text,
            re.I,
        )
        in_run.add(url)
        jobs.append({"url": url, "text": text[:600], "stipend": label, "posted": pm.group(1) if pm else ""})
    return jobs, found


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
            f"JOB {j['id']}\nStipend: {j['stipend']}\nPosted: {j['posted']}\n{j['text']}" for j in chunk
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
                        "url": job["url"],
                        "stipend": job["stipend"],
                        "posted": job["posted"],
                        "score": score,
                        "title": res.get("title") or "Internship",
                        "company": res.get("company") or "",
                        "location": res.get("location") or "",
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

    warn = ""
    if meta["pages_ok"] == 0:
        warn = "Could not load any Internshala page today. Check the workflow."
    elif meta["found"] == 0:
        warn = "Pages loaded but no listings were found. Internshala may have changed its layout, so the parser needs a look."
    elif meta["pages_failed"] > 0:
        warn = f"{meta['pages_failed']} page(s) failed to load today, so the list may be incomplete."
    if failed_batches:
        warn += (" " if warn else "") + f"{failed_batches} scoring batch(es) failed. Those jobs will be tried again tomorrow."
        if LAST_GROQ_ERROR:
            warn += " Last Groq error: " + LAST_GROQ_ERROR
    if meta["overflow"] > 0:
        warn += (" " if warn else "") + f"{meta['overflow']} extra new listings will be scored tomorrow."

    if meta["pages_ok"] == 0 or meta["found"] == 0:
        subject = "Internship agent needs attention"
    elif top:
        subject = f"Internships today: {len(top)} strong match" + ("es" if len(top) > 1 else "")
    else:
        subject = "Internships today: no strong matches"

    body = '<div style="font-family:Arial,sans-serif;max-width:640px">'
    body += f"<p>{meta['new_count']} new listings checked, {len(top)} scored 7 or higher.</p>"
    if warn:
        body += f"<p><b>{esc(warn)}</b></p>"
    for j in top:
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
    if maybe:
        body += '<p style="margin-top:20px"><b>Maybe (score 5 to 6)</b></p><ul style="padding-left:18px">'
        for j in maybe:
            body += f'<li><a href="{esc(j["url"])}">{esc(j["title"])}</a>, {esc(j["company"])}, {esc(j["stipend"])}</li>'
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


def run(dry_run=False):
    seen = load_seen()
    in_run = set()
    all_jobs = []
    pages_ok = pages_failed = found = 0

    for url in search_urls():
        page = fetch_page(url)
        if page is None:
            pages_failed += 1
        else:
            pages_ok += 1
            jobs, n = parse_listings(page, seen, in_run)
            all_jobs.extend(jobs)
            found += n
        time.sleep(3)

    overflow = max(0, len(all_jobs) - MAX_JOBS)
    all_jobs = all_jobs[:MAX_JOBS]
    for idx, j in enumerate(all_jobs, start=1):
        j["id"] = idx

    scored, failed_batches = [], 0
    if all_jobs:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise SystemExit("GROQ_API_KEY is not set")
        scored, failed_batches = score_jobs(api_key, all_jobs)

    meta = {
        "pages_ok": pages_ok,
        "pages_failed": pages_failed,
        "found": found,
        "new_count": len(all_jobs),
        "overflow": overflow,
    }
    subject, body = compose_email(meta, scored, failed_batches)
    print(subject, meta)

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
