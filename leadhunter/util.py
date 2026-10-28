"""Small shared helpers."""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlparse

import httpx

from leadhunter import __version__

USER_AGENT = f"LeadHunter/{__version__} (+https://github.com/; open-source personal job agent)"

# Hosts that belong to job boards / ATS vendors, not to the hiring company.
NOT_COMPANY_HOSTS = {
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
    "myworkdayjobs.com", "linkedin.com", "indeed.com", "naukri.com", "glassdoor.com",
    "remotive.com", "arbeitnow.com", "wellfound.com", "instahyre.com", "cutshort.io",
    "foundit.in", "monster.com", "google.com", "bit.ly", "t.me", "telegram.me",
    "eightfold.ai", "icims.com", "taleo.net", "successfactors.com", "freshteam.com",
    "keka.com", "zohorecruit.com", "darwinbox.in", "hirist.tech", "unstop.com",
    "github.com", "wikipedia.org", "crunchbase.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "ambitionbox.com", "zaubacorp.com", "tracxn.com",
}


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: float = 20, follow: bool = True) -> httpx.Response:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
    h.update(headers or {})
    return httpx.get(url, params=params, headers=h, timeout=timeout, follow_redirects=follow)


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)              # Greenhouse double-escapes its HTML
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return s.strip()


def make_uid(*parts: str) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:16]


def registrable_domain(host_or_url: str) -> str:
    """'careers.acme.co.in' -> 'acme.co.in', 'https://www.acme.com/x' -> 'acme.com'."""
    host = host_or_url
    if "//" in host_or_url:
        host = urlparse(host_or_url).hostname or ""
    host = (host or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two_level = {"co.in", "co.uk", "com.au", "co.jp", "com.br", "com.sg", "co.nz", "org.in", "net.in", "ac.in", "gov.in", "com.mx"}
    if ".".join(parts[-2:]) in two_level:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_company_host(host_or_url: str) -> bool:
    d = registrable_domain(host_or_url)
    return bool(d) and d not in NOT_COMPANY_HOSTS and "." in d


def norm_company(name: str) -> str:
    name = re.sub(r"(?i)\b(pvt|private|ltd|limited|llp|inc|llc|corp|corporation|technologies|technology|solutions|software|india|labs?|gmbh|co)\b\.?", " ", name or "")
    return re.sub(r"[^a-z0-9]+", "", name.lower())
