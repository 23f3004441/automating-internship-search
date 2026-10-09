"""Shared helpers used by every source module."""
import html as htmllib
import re

MIN_STIPEND = 10000

INDIA_WORDS = [
    "india", "bangalore", "bengaluru", "hyderabad", "chennai", "pune",
    "mumbai", "gurugram", "gurgaon", "noida", "delhi", "kolkata", "coimbatore",
    "kochi",
]
UAE_WORDS = ["dubai", "abu dhabi", "uae", "united arab emirates", "sharjah"]
REMOTE_WORDS = ["remote", "work from home", "wfh", "worldwide", "anywhere", "global"]

INTERN_TITLE_RE = re.compile(r"\bintern(s|ship|ships)?\b|\btrainee(s)?\b|\bapprentice(ship|s)?\b", re.I)

# A cheap, deliberately generous check used only to skip the obviously non
# technical titles before they reach Groq. It is not the real relevance
# judgement, that still happens in scoring, this just saves API calls.
TECH_TITLE_RE = re.compile(
    r"\bsoftware\b|\bengineer\w*|\bdevelop\w*|\bprogram\w*|\bcoding\b|\bdata\b|"
    r"\banalyst\b|\banalytics\b|\bai\b|artificial intelligence|\bml\b|machine learning|"
    r"\bautomation\b|\bdevops\b|\bcloud\b|\bback[\s-]?end\b|\bfront[\s-]?end\b|"
    r"\bfull[\s-]?stack\b|\bweb\b|\bapp\b|\bapplication\b|information technology|"
    r"\bit\b|\btechnical\b|\btechnology\b|\bcomputer\w*|\bqa\b|quality assurance|"
    r"\btesting\b|\bproduct\b|\bapi\b|\bdatabase\b|\bsql\b|\bpython\b|\bjava\w*|"
    r"\bcyber\w*|\bsecurity\b|\bnetwork\w*|\bsystems\b|\binfrastructure\b|\bux\b|"
    r"\bui\b|\brobotics\b|\bhardware\b|\bembedded\b|\bblockchain\b|\bplatform\b|"
    r"\bresearch\b|\bscientist\b|\bstatistic\w*|\bquant\w*|\bmath\w*",
    re.I,
)
UNKNOWN_TITLES = {"", "internship"}


def is_internship_title(title):
    return bool(INTERN_TITLE_RE.search(title or ""))


def is_tech_title(title):
    """True if the title looks like tech work, or if the title is still the
    generic placeholder used before Groq has read the full listing."""
    title = (title or "").strip()
    if title.lower() in UNKNOWN_TITLES:
        return True
    return bool(TECH_TITLE_RE.search(title))


def location_tag(loc_text):
    """Returns 'india', 'uae', 'remote' or None based on a free text location string."""
    loc = (loc_text or "").lower()
    if any(w in loc for w in INDIA_WORDS):
        return "india"
    if any(w in loc for w in UAE_WORDS):
        return "uae"
    if any(w in loc for w in REMOTE_WORDS):
        return "remote"
    return None


def strip_html(s):
    """Strips HTML tags, unescaping entities first since some ATS systems
    double encode their rich text content (literal &lt;div&gt; in the JSON)."""
    if not s:
        return ""
    for _ in range(2):
        unescaped = htmllib.unescape(s)
        if unescaped == s:
            break
        s = unescaped
    s = re.sub(r"<[^>]+>", " ", s)
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


def passes_prefilter(job):
    """The last, cheap check before a job is sent to Groq. Drops anything
    with a title that shows no tech work, anything whose location does not
    match the India, remote India allowed, or UAE rule, and anything that
    fails the stipend rule. This repeats checks each source module already
    makes, it is a safety net so nothing slips through regardless of source."""
    if not is_tech_title(job.get("title", "")):
        return False
    if not location_tag(job.get("location", "")) and not location_tag((job.get("text") or "")[:300]):
        return False
    keep, _ = parse_stipend(job.get("text", ""))
    if not keep:
        return False
    return True


def dedup_key(company, title):
    def norm(s):
        s = (s or "").lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()
    return norm(company) + "|" + norm(title)


def make_job(source, company, title, location, url, text, stipend="stipend not listed", posted=""):
    return {
        "source": source,
        "company": company or "",
        "title": title or "Internship",
        "location": location or "",
        "url": url,
        "stipend": stipend,
        "posted": posted,
        "text": text[:1500],
    }
