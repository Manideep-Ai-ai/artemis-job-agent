"""Find the right people for a job: a recruiter for the role's city and a hiring manager.

How it works (no LinkedIn login, no LinkedIn crawling):
 1. Ask a search engine for public profile results, e.g.
      site:linkedin.com/in "Accenture" "talent acquisition" Hyderabad
 2. Read name + headline from the search result titles/snippets.
 3. Keep people who currently work at the company, score them for the role and city,
    optionally let the AI re-rank, and pick the best recruiter + manager.
 4. Work out each person's work email (see emails.py).
"""
from __future__ import annotations

import logging
import re

from leadhunter.ai import AI
from leadhunter.emails import EmailFinder
from leadhunter.search import Search
from leadhunter.util import is_company_host, norm_company, registrable_domain

log = logging.getLogger("leadhunter.research")

RECRUITER_WORDS = ["talent acquisition", "recruiter", "recruitment", "recruiting", "hiring partner",
                   "human resources", "hr business partner", "hrbp", "people partner", "talent partner",
                   "staffing", "hr manager", "hr executive", "hr generalist", "sourcer", "head of talent"]
MANAGER_WORDS = ["engineering manager", "hiring manager", "head of engineering", "director of engineering",
                 "vp engineering", "vp of engineering", "tech lead", "technical lead", "team lead",
                 "lead engineer", "cto", "head of data", "head of product", "director", "head of"]
NOT_PEOPLE = {"jobs", "careers", "hiring", "linkedin", "people", "profiles", "top", "best", "the", "view"}

DEPARTMENTS = [
    (("data", "machine learning", "ml ", "ai ", "analytics"), "data"),
    (("frontend", "front-end", "react", "ui "), "frontend"),
    (("backend", "back-end", "platform", "api"), "backend"),
    (("devops", "sre", "cloud", "infrastructure"), "infrastructure"),
    (("mobile", "android", "ios", "flutter"), "mobile"),
    (("design", "ux"), "design"),
    (("product manager", "product owner"), "product"),
]


def city_of(location: str) -> str:
    loc = (location or "").split(";")[0].split("(")[0]
    first = loc.split(",")[0].strip()
    return "" if first.lower() in ("remote", "anywhere", "india", "") else first


def department_of(title: str) -> str:
    t = f" {title.lower()} "
    for keys, name in DEPARTMENTS:
        if any(k in t for k in keys):
            return name
    return "engineering"


def build_queries(company: str, title: str, location: str) -> list[tuple[str, str]]:
    """(kind, query) pairs, recruiter queries first."""
    city = city_of(location)
    dept = department_of(title)
    c = f'"{company}"'
    city_part = f' "{city}"' if city else ""
    return [
        ("recruiter", f'site:linkedin.com/in {c} ("talent acquisition" OR recruiter){city_part}'),
        ("recruiter", f'site:linkedin.com/in {c} (recruiter OR "talent acquisition") "{dept}"'),
        ("manager", f'site:linkedin.com/in {c} ("{dept} manager" OR "engineering manager" OR "head of {dept}"){city_part}'),
    ]


def parse_profile_result(hit: dict) -> dict | None:
    """Turn a search hit for a public profile into {name, headline, linkedin_url, text}."""
    url = hit.get("url", "")
    if not re.search(r"linkedin\.com/in/", url):
        return None
    title = re.sub(r"\s*\|\s*LinkedIn.*$", "", hit.get("title", ""), flags=re.I).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", title) if p.strip()]
    if not parts:
        return None
    name = re.sub(r"\(.*?\)", "", parts[0]).strip()
    words = name.split()
    if not (1 < len(words) <= 5) or any(w.lower() in NOT_PEOPLE for w in words):
        return None
    if not all(re.fullmatch(r"[A-Za-zÀ-ÿ.'’-]+", w) for w in words):
        return None
    headline = " - ".join(parts[1:])
    return {"name": name, "headline": headline, "linkedin_url": url.split("?")[0],
            "text": f"{headline} {hit.get('snippet', '')}"}


def works_at(person: dict, company: str) -> bool:
    text = person["text"]
    if re.search(r"(?i)\b(former|ex-|previously|past:|alumni)\b", text) and \
       not re.search(r"(?i)(current|present|experience:)", text):
        return False
    target = norm_company(company)
    if not target:
        return False
    return target in norm_company(text)


def score_person(person: dict, kind: str, city: str, dept: str) -> tuple[float, str]:
    t = person["text"].lower()
    words = RECRUITER_WORDS if kind == "recruiter" else MANAGER_WORDS
    score, why = 0.0, []
    hit = next((w for w in words if w in t), None)
    if hit:
        score += 0.5
        why.append(hit)
    if city and city.lower() in t:
        score += 0.25
        why.append(city)
    if dept and dept in t:
        score += 0.15
        why.append(dept)
    if kind == "recruiter" and re.search(r"(?i)\b(tech|technical|engineering|it)\b", t):
        score += 0.1
        why.append("tech hiring")
    return round(score, 2), ", ".join(why)


def find_company_domain(company: str, search: Search | None, hint_url: str = "") -> str:
    if hint_url and is_company_host(hint_url):
        return registrable_domain(hint_url)
    if not search or not search.enabled:
        return ""
    target = norm_company(company)
    try:
        hits = search(f'"{company}" official website', count=8)
    except Exception:
        return ""
    fallback = ""
    for h in hits:
        url = h.get("url", "")
        if not is_company_host(url):
            continue
        d = registrable_domain(url)
        label = re.sub(r"[^a-z0-9]", "", d.split(".")[0])
        if target and label and (label in target or target.startswith(label) or label.startswith(target[:6])):
            return d
        fallback = fallback or d
    return fallback


def ai_rerank(ai: AI, job: dict, people: list[dict], limit: int) -> list[dict]:
    if not ai.enabled or len(people) <= limit:
        return people
    listing = "\n".join(f"{i}. {p['name']} | {p['headline']} | {p['text'][:200]}" for i, p in enumerate(people))
    data = ai.json(
        "You pick who a job seeker should email about a job: ideally one recruiter who hires for this "
        "team/city and one hiring manager for the team. Only people who CURRENTLY work at the company.",
        f"Job: {job.get('title')} at {job.get('company')} ({job.get('location')})\n\nPeople:\n{listing}\n\n"
        f'Return {{"picks": [{{"index": n, "reason": "..."}}]}} with at most {limit} picks, best first.')
    if not isinstance(data, dict):
        return people
    picked = []
    for p in data.get("picks") or []:
        try:
            person = dict(people[int(p["index"])])
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        person["reason"] = f"AI: {p.get('reason', '')}".strip()
        picked.append(person)
    return picked or people


def find_contacts(job: dict, cfg, search: Search, ai: AI | None = None, finder: EmailFinder | None = None) -> dict:
    """Research one job. Returns {"domain", "contacts": [...], "notes": [...]}."""
    notes: list[str] = []
    company = (job.get("company") or "").strip()
    if not company:
        return {"domain": "", "contacts": [], "notes": ["The job has no company name. Edit the job and add it."]}
    if not search.enabled:
        return {"domain": "", "contacts": [], "notes": ["Web search isn't set up, so people can't be looked up. "
                                                         "Add a BRAVE_API_KEY or TAVILY_API_KEY (both have free tiers)."]}
    domain = job.get("company_domain") or find_company_domain(company, search, job.get("url", ""))
    if not domain:
        notes.append(f"Couldn't find {company}'s website, so no emails can be built. Set the domain by hand.")

    city, dept = city_of(job.get("location", "")), department_of(job.get("title", ""))
    seen, people = set(), []
    for kind, query in build_queries(company, job.get("title", ""), job.get("location", "")):
        try:
            hits = search(query, count=10)
        except Exception as e:
            notes.append(str(e))
            continue
        for h in hits:
            p = parse_profile_result(h)
            if not p or p["linkedin_url"] in seen or not works_at(p, company):
                continue
            seen.add(p["linkedin_url"])
            s, why = score_person(p, kind, city, dept)
            if s < 0.5:          # must at least have a matching role word
                continue
            p.update(kind=kind, score=s, reason=why, source_url=h.get("url", ""))
            people.append(p)

    people.sort(key=lambda p: p["score"], reverse=True)
    limit = int(cfg["search"].get("max_contacts_per_job") or 2)
    # Prefer a mix: best recruiter + best manager, then fill by score
    best = []
    for kind in ("recruiter", "manager"):
        p = next((x for x in people if x["kind"] == kind), None)
        if p:
            best.append(p)
    rest = [p for p in people if p not in best]
    ranked = (best + rest)
    if ai is not None:
        ranked = ai_rerank(ai, job, ranked[:12], limit)
    chosen = ranked[:limit]

    finder = finder or EmailFinder(cfg, search)
    for p in chosen:
        p["company"] = company
        p["title"] = p.pop("headline", "")
        if domain:
            p["email"], p["email_confidence"] = finder.find(p["name"], domain)
        else:
            p["email"], p["email_confidence"] = "", ""
        p.pop("text", None)
    if not chosen:
        notes.append("No recruiter or hiring manager found in search results. Try again later, or add a contact by hand.")
    return {"domain": domain, "contacts": chosen, "notes": notes}
