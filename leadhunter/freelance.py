"""Freelance / contract lead sources and pricing help. Official APIs only.

  hackernews : the monthly "Ask HN: Who is hiring?" thread (Algolia HN API). Posts that say
               contract / freelance / part-time become freelance leads, the rest job leads.
               Many posts include an email address to apply to.
  freelancer : Freelancer.com public project search API. You bid on the site itself;
               LeadHunter drafts the proposal for you to paste.
  reddit     : r/forhire [Hiring] posts through Reddit's official API (needs a free
               "script" app: REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET). Reply on Reddit yourself.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import httpx

from leadhunter.util import USER_AGENT, html_to_text, http_get

HN = "https://hn.algolia.com/api/v1"
FREELANCE_WORDS = re.compile(r"(?i)\b(contract(or)?|freelanc\w*|part[- ]time|consultant|consulting|fractional|short[- ]term|project[- ]based)\b")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


# ---------------- helpers ----------------
def deobfuscate(text: str) -> str:
    """'jobs [at] acme [dot] com' / 'jobs(at)acme.com' / 'jobs at acme dot com' -> jobs@acme.com"""
    t = html.unescape(text)
    t = re.sub(r"\s*[\[\(\{<]\s*(?:at|AT)\s*[\]\)\}>]\s*", "@", t)
    t = re.sub(r"\s*[\[\(\{<]\s*(?:dot|DOT)\s*[\]\)\}>]\s*", ".", t)
    t = re.sub(r"(\b[\w.+-]+)\s+(?:at|AT)\s+([\w-]+(?:\s+(?:dot|DOT)\s+[\w-]+)+)\b",
               lambda m: m.group(1) + "@" + re.sub(r"\s+(?:dot|DOT)\s+", ".", m.group(2)), t)
    return t


def find_emails(text: str) -> list[str]:
    out = []
    for e in EMAIL_RE.findall(deobfuscate(text)):
        e = e.strip(".").lower()
        if e not in out and not e.endswith((".png", ".jpg", ".gif")):
            out.append(e)
    return out


def budget_text(lo, hi, sign: str, pay_type: str) -> str:
    def f(x):
        return f"{x:,.0f}" if x is not None else ""
    if lo is None and hi is None:
        return ""
    rng = f"{sign}{f(lo)}–{f(hi)}" if lo is not None and hi is not None and lo != hi else f"{sign}{f(hi if hi is not None else lo)}"
    return rng + ("/hr" if pay_type == "hourly" else "")


def suggest_quote(job: dict, prefs: dict) -> tuple[str, bool]:
    """(quote text, below_your_minimum). Aims a little above the middle of the client's range,
    never below your own minimum."""
    lo, hi = job.get("budget_min"), job.get("budget_max")
    if lo is None and hi is None:
        return "", False
    lo = lo if lo is not None else hi
    hi = hi if hi is not None else lo
    target = lo + (hi - lo) * 0.6
    hourly = job.get("pay_type") == "hourly"
    minimum = float(prefs.get("min_hourly" if hourly else "min_fixed") or 0)
    below = minimum > 0 and hi < minimum
    target = max(target, minimum) if minimum else target
    step = 1 if hourly else (5 if target < 200 else 25)
    target = round(target / step) * step
    sign = job.get("currency_sign") or ("$" if (job.get("currency") or "USD") == "USD" else "")
    cur = "" if sign else f" {job.get('currency', '')}"
    return f"{sign}{target:,.0f}{cur}{'/hr' if hourly else ''}", below


# ---------------- Hacker News ----------------
def latest_hn_thread(title_prefix: str = "Ask HN: Who is hiring?") -> dict | None:
    data = http_get(f"{HN}/search_by_date", params={
        "tags": "story,author_whoishiring", "hitsPerPage": 10, "attributesToHighlight": "none"}).json()
    for h in data.get("hits", []):
        if (h.get("title") or "").startswith(title_prefix):
            return h
    return None


def parse_hn_post(hit: dict, story_id: int) -> dict | None:
    """Top-level 'Who is hiring' post -> lead. First line is usually 'Company | Role | Location | ...'."""
    raw = hit.get("comment_text") or ""
    if not raw or int(hit.get("parent_id") or 0) != int(story_id):
        return None
    text = html_to_text(raw)
    first = text.split("\n", 1)[0]
    parts = [p.strip() for p in re.split(r"\s+\|\s+|\s+\|\s*|\s*\|\s+", first) if p.strip()]
    if len(parts) < 2:
        return None
    company = re.sub(r"\(.*?\)", "", parts[0]).strip()[:80]
    role = next((p for p in parts[1:] if re.search(r"(?i)engineer|developer|designer|manager|scientist|lead|analyst|architect|devops|sre|founding|head|consultant|marketer|writer", p)), parts[1])
    loc = next((p for p in parts[1:] if re.search(r"(?i)remote|onsite|on-site|hybrid|\b[A-Z][a-z]+,? ?[A-Z]{2}\b|london|berlin|bangalore|bengaluru|hyderabad|india|nyc|sf\b|new york|san francisco", p)), "")
    emails = find_emails(raw)
    kind = "freelance" if FREELANCE_WORDS.search(first) or FREELANCE_WORDS.search(text[:600]) else "job"
    created = hit.get("created_at") or ""
    return {
        "uid": f"hn:{hit.get('objectID')}",
        "source": "hackernews",
        "kind": kind,
        "title": role[:120],
        "company": company,
        "location": loc[:80],
        "url": f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        "description": text[:8000],
        "posted_at": created,
        "contact_email": emails[0] if emails else "",
        "contact_name": hit.get("author", ""),
        "how_to_apply": "email" if emails else "link",
    }


def hackernews(max_posts: int = 1000) -> list[dict]:
    story = latest_hn_thread()
    if not story:
        return []
    sid = int(story["objectID"])
    out, page = [], 0
    while len(out) < max_posts and page < 10:
        data = http_get(f"{HN}/search", params={
            "tags": f"comment,story_{sid}", "hitsPerPage": 500, "page": page, "attributesToHighlight": "none"}).json()
        for h in data.get("hits", []):
            lead = parse_hn_post(h, sid)
            if lead:
                out.append(lead)
        page += 1
        if page >= int(data.get("nbPages") or 0):
            break
    return out


# ---------------- Freelancer.com ----------------
def freelancer_project(p: dict) -> dict:
    cur = p.get("currency") or {}
    b = p.get("budget") or {}
    pay = "hourly" if p.get("type") == "hourly" else "fixed"
    lo, hi = b.get("minimum"), b.get("maximum")
    skills = ", ".join(j.get("name", "") for j in p.get("jobs") or [])
    bids = (p.get("bid_stats") or {}).get("bid_count")
    desc = (p.get("description") or p.get("preview_description") or "")
    if skills:
        desc += f"\n\nSkills: {skills}"
    if bids is not None:
        desc += f"\nBids so far: {bids}"
    ts = p.get("time_submitted") or p.get("submitdate")
    return {
        "uid": f"freelancer:{p.get('id')}",
        "source": "freelancer.com",
        "kind": "freelance",
        "title": (p.get("title") or "").strip(),
        "company": "",
        "location": "Remote",
        "url": f"https://www.freelancer.com/projects/{p.get('seo_url')}" if p.get("seo_url") else "",
        "description": desc,
        "posted_at": datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat(timespec="seconds") if ts else "",
        "budget": budget_text(lo, hi, cur.get("sign") or "", pay) + ("" if cur.get("code") in (None, "USD") else f" {cur.get('code')}"),
        "budget_min": lo, "budget_max": hi, "currency": cur.get("code") or "", "currency_sign": cur.get("sign") or "",
        "pay_type": pay,
        "how_to_apply": "platform",
    }


def freelancer(queries: list[str], limit: int = 30) -> list[dict]:
    out, seen = [], set()
    for q in queries[:8]:
        data = http_get("https://www.freelancer.com/api/projects/0.1/projects/active/", params={
            "query": q, "limit": limit, "full_description": "true", "job_details": "true",
            "sort_field": "time_updated"}).json()
        for p in (data.get("result") or {}).get("projects") or []:
            if p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            out.append(freelancer_project(p))
    return out


# ---------------- Reddit (official API, app-only OAuth) ----------------
def reddit(client_id: str, client_secret: str, subreddits=("forhire",), limit: int = 50) -> list[dict]:
    tok = httpx.post("https://www.reddit.com/api/v1/access_token", auth=(client_id, client_secret),
                     data={"grant_type": "client_credentials"}, headers={"User-Agent": USER_AGENT}, timeout=20)
    tok.raise_for_status()
    headers = {"Authorization": f"Bearer {tok.json()['access_token']}", "User-Agent": USER_AGENT}
    out = []
    for sub in subreddits:
        r = httpx.get(f"https://oauth.reddit.com/r/{sub}/new", params={"limit": limit}, headers=headers, timeout=20)
        r.raise_for_status()
        for c in (r.json().get("data") or {}).get("children") or []:
            d = c.get("data") or {}
            title = d.get("title") or ""
            if not re.search(r"(?i)\[\s*hiring\s*\]", title):
                continue
            body = d.get("selftext") or ""
            m = re.search(r"(?i)(\$\s?\d[\d,]*)(?:\s*(?:-|–|to)\s*\$?\s?(\d[\d,]*))?\s*(/\s?h(ou)?r|per hour|hourly)?", f"{title} {body}")
            lo = hi = None
            pay = ""
            if m:
                lo = float(m.group(1).replace("$", "").replace(",", "").strip())
                hi = float(m.group(2).replace(",", "")) if m.group(2) else lo
                pay = "hourly" if m.group(3) else "fixed"
            emails = find_emails(body)
            out.append({
                "uid": f"reddit:{d.get('id')}", "source": f"reddit/r/{sub}", "kind": "freelance",
                "title": re.sub(r"(?i)\[\s*hiring\s*\]\s*", "", title).strip()[:150], "company": "",
                "location": "Remote", "url": f"https://www.reddit.com{d.get('permalink', '')}",
                "description": body[:8000],
                "posted_at": datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc).isoformat(timespec="seconds"),
                "budget": budget_text(lo, hi, "$", pay) if m else "", "budget_min": lo, "budget_max": hi,
                "currency": "USD" if m else "", "pay_type": pay,
                "contact_email": emails[0] if emails else "", "contact_name": d.get("author", ""),
                "how_to_apply": "email" if emails else "platform",
            })
    return out
