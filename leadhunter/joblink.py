"""Turn a pasted job link into a job record.

Greenhouse / Lever / Ashby links use their official APIs. Any other page is fetched
once (like opening it in a browser) and read from its schema.org JobPosting data,
which most career sites publish for Google Jobs. If nothing can be read, the user
fills in company and title by hand.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

from leadhunter import sources
from leadhunter.util import html_to_text, http_get, is_company_host, make_uid, registrable_domain


class JobLinkError(Exception):
    pass


def parse_ats(url: str) -> tuple[str, str, str] | None:
    """Return (ats, company_token, job_id) for known ATS links."""
    u = urlparse(url.strip())
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)
    if host.endswith("greenhouse.io"):
        if "jobs" in parts:
            i = parts.index("jobs")
            if i >= 1 and i + 1 < len(parts):
                return "greenhouse", parts[i - 1], re.sub(r"\D", "", parts[i + 1])
        if "for" in qs and "token" in qs:  # embed links
            return "greenhouse", qs["for"][0], qs["token"][0]
    if "gh_jid" in qs and parts:
        # company career page that embeds Greenhouse; token unknown, handled generically
        return None
    if host == "jobs.lever.co" and len(parts) >= 2:
        return "lever", parts[0], parts[1]
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return "ashby", parts[0], parts[1]
    return None


def find_jobposting(html: str) -> dict | None:
    """Find a schema.org JobPosting inside <script type="application/ld+json">."""
    for block in re.findall(r'(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                t = d.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return d
                if "@graph" in d:
                    stack.append(d["@graph"])
    return None


def from_jobposting(jp: dict, url: str) -> dict:
    org = jp.get("hiringOrganization") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    if isinstance(org, str):
        org = {"name": org}
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    names = []
    for loc in locs:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, dict):
            names.append(", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else ""] if x))
    if jp.get("jobLocationType") == "TELECOMMUTE":
        names.append("Remote")
    domain = ""
    for key in ("sameAs", "url"):
        v = org.get(key)
        if isinstance(v, list):
            v = v[0] if v else ""
        if v and is_company_host(v):
            domain = registrable_domain(v)
            break
    if not domain and is_company_host(url):
        domain = registrable_domain(url)
    return {
        "uid": f"link:{make_uid(url)}",
        "source": "link",
        "title": html_to_text(jp.get("title", "")),
        "company": html_to_text(org.get("name", "")),
        "company_domain": domain,
        "location": "; ".join(n for n in names if n),
        "url": url,
        "description": html_to_text(jp.get("description", "")),
        "posted_at": jp.get("datePosted", ""),
    }


def fetch_job(url: str) -> dict:
    url = url.strip()
    if not url.startswith("http"):
        raise JobLinkError("That doesn't look like a link. Paste the full job URL starting with https://")
    ats = parse_ats(url)
    try:
        if ats:
            kind, token, jid = ats
            if kind == "greenhouse":
                j = http_get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}").json()
                job = sources.greenhouse_job(j, token, sources.greenhouse_company_name(token))
            elif kind == "lever":
                job = sources.lever_job(http_get(f"https://api.lever.co/v0/postings/{token}/{jid}").json(), token)
            else:
                data = http_get(f"https://api.ashbyhq.com/posting-api/job-board/{token}").json()
                match = next((j for j in data.get("jobs", []) if j.get("id") == jid or jid in (j.get("jobUrl") or "")), None)
                if not match:
                    raise JobLinkError("This Ashby job is no longer listed.")
                job = sources.ashby_job(match, token)
            job["url"] = job.get("url") or url
            return job
        r = http_get(url)
    except JobLinkError:
        raise
    except Exception as e:
        raise JobLinkError(f"Couldn't open the link ({e}). Add the job by hand with company and title.") from e

    if r.status_code >= 400:
        raise JobLinkError(f"The site answered {r.status_code}. It may need a login. Add the job by hand with company and title.")
    jp = find_jobposting(r.text)
    if jp:
        return from_jobposting(jp, str(r.url))
    # Last resort: page title only
    m = re.search(r'(?is)<meta[^>]+property="og:title"[^>]+content="([^"]+)"', r.text) or re.search(r"(?is)<title>(.*?)</title>", r.text)
    title = html_to_text(m.group(1)) if m else ""
    if not title:
        raise JobLinkError("Couldn't read job details from that page. Add the job by hand with company and title.")
    return {
        "uid": f"link:{make_uid(url)}", "source": "link", "title": title, "company": "",
        "company_domain": registrable_domain(str(r.url)) if is_company_host(str(r.url)) else "",
        "location": "", "url": url, "description": html_to_text(r.text)[:6000], "posted_at": "",
    }


def manual_job(company: str, title: str, location: str = "", url: str = "", description: str = "",
               domain: str = "") -> dict:
    return {
        "uid": f"manual:{make_uid(company.lower(), title.lower(), location.lower(), url)}",
        "source": "manual", "title": title.strip(), "company": company.strip(),
        "company_domain": registrable_domain(domain) if domain else "",
        "location": location.strip(), "url": url.strip(), "description": description, "posted_at": "",
    }
