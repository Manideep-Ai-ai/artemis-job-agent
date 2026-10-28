"""Job sources. Only official public APIs / feeds: no logins, no page scraping.

Each collector returns a list of job dicts:
  uid, source, title, company, company_domain, location, url, description, posted_at
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from leadhunter.config import Config
from leadhunter.util import http_get, html_to_text, is_company_host, make_uid, registrable_domain

log = logging.getLogger("leadhunter.sources")

# Company board ids go into API URLs (Recruitee even into the hostname), so only plain slugs are allowed.
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
BOARDS = {  # config key -> (label, where the slug comes from)
    "greenhouse": ("Greenhouse", "boards.greenhouse.io/<slug>"),
    "lever": ("Lever", "jobs.lever.co/<slug>"),
    "ashby": ("Ashby", "jobs.ashbyhq.com/<slug>"),
    "smartrecruiters": ("SmartRecruiters", "jobs.smartrecruiters.com/<slug>"),
    "recruitee": ("Recruitee", "<slug>.recruitee.com"),
}


def check_slug(slug: str) -> str:
    if not SLUG_RE.match(slug or "") or ".." in slug:
        raise ValueError(f"not a valid company id: {slug!r}")
    return slug


def _ts_ms(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def _ts_s(s) -> str:
    try:
        return datetime.fromtimestamp(int(s), tz=timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


# ---------------- Greenhouse ----------------
def greenhouse_company_name(token: str) -> str:
    try:
        return http_get(f"https://boards-api.greenhouse.io/v1/boards/{token}").json().get("name") or token
    except Exception:
        return token


def greenhouse_job(j: dict, token: str, company: str) -> dict:
    url = j.get("absolute_url", "")
    return {
        "uid": f"greenhouse:{token}:{j.get('id')}",
        "source": "greenhouse",
        "title": (j.get("title") or "").strip(),
        "company": j.get("company_name") or company,
        # absolute_url is often the company's own careers site (careers.acme.com) -> email domain hint
        "company_domain": registrable_domain(url) if is_company_host(url) else "",
        "location": (j.get("location") or {}).get("name", "").strip(),
        "url": url,
        "description": html_to_text(j.get("content")),
        "posted_at": j.get("first_published") or j.get("updated_at") or "",
    }


def greenhouse(token: str) -> list[dict]:
    company = greenhouse_company_name(token)
    data = http_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"}).json()
    return [greenhouse_job(j, token, company) for j in data.get("jobs", [])]


# ---------------- Lever ----------------
def lever_job(j: dict, company: str) -> dict:
    cats = j.get("categories") or {}
    # Lever splits the posting: intro + bullet lists (requirements live here) + closing text
    parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
    for lst in j.get("lists") or []:
        parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
    parts.append(j.get("additionalPlain") or "")
    loc = cats.get("location", "") or ", ".join(cats.get("allLocations") or [])
    if (j.get("workplaceType") or "").lower() == "remote" and "remote" not in loc.lower():
        loc = f"{loc} (Remote)" if loc else "Remote"
    return {
        "uid": f"lever:{company}:{j.get('id')}",
        "source": "lever",
        "title": (j.get("text") or "").strip(),
        "company": company.replace("-", " ").title(),
        "location": loc,
        "url": j.get("hostedUrl", ""),
        "description": "\n".join(p for p in parts if p).strip(),
        "posted_at": _ts_ms(j.get("createdAt")),
    }


def lever(company: str) -> list[dict]:
    data = http_get(f"https://api.lever.co/v0/postings/{company}", params={"mode": "json"}).json()
    return [lever_job(j, company) for j in data] if isinstance(data, list) else []


# ---------------- Ashby ----------------
def ashby_job(j: dict, org: str) -> dict:
    return {
        "uid": f"ashby:{org}:{j.get('id')}",
        "source": "ashby",
        "title": (j.get("title") or "").strip(),
        "company": org.replace("-", " ").title(),
        "location": (j.get("location") or "") + (" (Remote)" if j.get("isRemote") else ""),
        "url": j.get("jobUrl", ""),
        "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
        "posted_at": j.get("publishedAt", ""),
    }


def ashby(org: str) -> list[dict]:
    data = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{org}").json()
    return [ashby_job(j, org) for j in data.get("jobs", [])]


# ---------------- SmartRecruiters ----------------
def smartrecruiters_job(p: dict, company_id: str) -> dict:
    """p: a posting detail (GET /postings/{id}); the list endpoint has no description."""
    loc = p.get("location") or {}
    where = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
    if loc.get("remote") and "remote" not in where.lower():
        where = f"{where} (Remote)" if where else "Remote"
    sections = ((p.get("jobAd") or {}).get("sections") or {})
    text = "\n\n".join(html_to_text(sections.get(k, {}).get("text"))
                       for k in ("jobDescription", "qualifications", "additionalInformation"))
    return {
        "uid": f"smartrecruiters:{company_id}:{p.get('id')}",
        "source": "smartrecruiters",
        "title": (p.get("name") or "").strip(),
        "company": (p.get("company") or {}).get("name") or company_id,
        "location": where,
        "url": p.get("postingUrl") or f"https://jobs.smartrecruiters.com/{company_id}/{p.get('id')}",
        "description": text.strip(),
        "posted_at": p.get("releasedDate", ""),
    }


def smartrecruiters(company_id: str, limit: int = 25) -> list[dict]:
    # ponytail: one request per posting, so only the newest `limit` get fetched; raise it if you miss jobs
    base = f"https://api.smartrecruiters.com/v1/companies/{check_slug(company_id)}/postings"
    listing = http_get(base, params={"limit": limit}).json().get("content", [])
    return [smartrecruiters_job(http_get(f"{base}/{p['id']}").json(), company_id) for p in listing]


# ---------------- Recruitee ----------------
def recruitee_job(o: dict, company: str) -> dict:
    where = o.get("location") or ", ".join(x for x in (o.get("city"), o.get("country")) if x)
    if o.get("remote") and "remote" not in where.lower():
        where = f"{where} (Remote)" if where else "Remote"
    # "2026-10-15 09:31:36 UTC" -> ISO, so collect_all's age filter can read it
    posted = (o.get("published_at") or o.get("created_at") or "").replace(" UTC", "+00:00").replace(" ", "T", 1)
    url = o.get("careers_url", "")
    return {
        "uid": f"recruitee:{company}:{o.get('id')}",
        "source": "recruitee",
        "title": (o.get("title") or "").strip(),
        "company": o.get("company_name") or company,
        "company_domain": registrable_domain(url) if is_company_host(url) else "",
        "location": where,
        "url": url,
        "description": f"{html_to_text(o.get('description'))}\n\n{html_to_text(o.get('requirements'))}".strip(),
        "posted_at": posted,
    }


def recruitee(company: str) -> list[dict]:
    check_slug(company)
    data = http_get(f"https://{company}.recruitee.com/api/offers/").json()
    return [recruitee_job(o, company) for o in data.get("offers", [])]


# ---------------- Remotive (remote jobs) ----------------
# Remotive asks API users to link back to the job and not call more than a few times a day.
def remotive(limit: int = 100) -> list[dict]:
    data = http_get("https://remotive.com/api/remote-jobs", params={"category": "software-dev", "limit": limit}).json()
    out = []
    for j in data.get("jobs", []):
        out.append({
            "uid": f"remotive:{j.get('id')}",
            "source": "remotive",
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": j.get("candidate_required_location", "") or "Remote",
            "url": j.get("url", ""),
            "description": html_to_text(j.get("description")),
            "posted_at": j.get("publication_date", ""),
        })
    return out


# ---------------- Arbeitnow ----------------
def arbeitnow() -> list[dict]:
    data = http_get("https://www.arbeitnow.com/api/job-board-api").json()
    out = []
    for j in data.get("data", []):
        loc = j.get("location", "")
        if j.get("remote"):
            loc = f"{loc} (Remote)" if loc else "Remote"
        out.append({
            "uid": f"arbeitnow:{j.get('slug') or make_uid(j.get('url', ''))}",
            "source": "arbeitnow",
            "title": j.get("title", ""),
            "company": j.get("company_name", ""),
            "location": loc,
            "url": j.get("url", ""),
            "description": html_to_text(j.get("description")),
            "posted_at": _ts_s(j.get("created_at")),
        })
    return out


def collect_all(cfg: Config, skills: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Run every enabled source for the user's mode. Returns (leads, errors).
    One failing source never stops the rest."""
    from leadhunter import freelance  # local import keeps module load light

    s = cfg["sources"]
    kinds = cfg.lead_kinds
    tasks: list[tuple[str, callable]] = []
    if "job" in kinds:
        if s.get("remotive"):
            tasks.append(("remotive", remotive))
        if s.get("arbeitnow"):
            tasks.append(("arbeitnow", arbeitnow))
        for t in s.get("greenhouse") or []:
            tasks.append((f"greenhouse/{t}", lambda t=t: greenhouse(t)))
        for c in s.get("lever") or []:
            tasks.append((f"lever/{c}", lambda c=c: lever(c)))
        for o in s.get("ashby") or []:
            tasks.append((f"ashby/{o}", lambda o=o: ashby(o)))
        for c in s.get("smartrecruiters") or []:
            tasks.append((f"smartrecruiters/{c}", lambda c=c: smartrecruiters(c)))
        for c in s.get("recruitee") or []:
            tasks.append((f"recruitee/{c}", lambda c=c: recruitee(c)))
    if s.get("hackernews"):
        tasks.append(("hackernews", freelance.hackernews))
    if "freelance" in kinds:
        if s.get("freelancer"):
            queries = s.get("freelancer_queries") or (skills or [])[:5] or ["python"]
            tasks.append(("freelancer.com", lambda q=queries: freelance.freelancer(q)))
        if s.get("reddit"):
            cid, secret = cfg.secret("REDDIT_CLIENT_ID"), cfg.secret("REDDIT_CLIENT_SECRET")
            if cid and secret:
                tasks.append(("reddit", lambda: freelance.reddit(cid, secret)))

    max_age = timedelta(days=int(s.get("max_age_days") or 30))
    cutoff = datetime.now(timezone.utc) - max_age
    jobs, errors = [], []
    for name, fn in tasks:
        try:
            got = fn()
        except Exception as e:  # network, JSON, 404 for a wrong token...
            errors.append(f"{name}: {e}")
            continue
        for j in got:
            j.setdefault("kind", "job")
            if j["kind"] not in kinds:
                continue
            posted = j.get("posted_at")
            try:
                dt = datetime.fromisoformat(posted.replace("Z", "+00:00")) if posted else None
                if dt and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except ValueError:
                dt = None
            if dt and dt < cutoff:
                continue
            jobs.append(j)
        log.info("%s: %d jobs", name, len(got))
    return jobs, errors
