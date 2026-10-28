"""Work out a person's work email: company domain + the company's address pattern.

Confidence levels shown to the user before anything is sent:
  verified : an email-finder API (Hunter) found or verified this exact address
  likely   : built from a pattern seen in real public addresses at this domain
  guessed  : built from the most common pattern (first.last); may bounce
  yours    : you typed it in yourself
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

import httpx

from leadhunter.config import Config
from leadhunter.keyhealth import report_http

PATTERNS = {
    "first.last": "{first}.{last}",
    "first": "{first}",
    "firstlast": "{first}{last}",
    "f.last": "{f}.{last}",
    "flast": "{f}{last}",
    "first_last": "{first}_{last}",
    "first.l": "{first}.{l}",
    "last": "{last}",
}
GENERIC_LOCALS = {
    "hr", "careers", "career", "jobs", "job", "info", "contact", "support", "hello", "sales", "admin",
    "noreply", "no-reply", "recruitment", "recruiting", "talent", "hiring", "press", "media", "team",
    "help", "office", "privacy", "legal", "security", "billing", "marketing", "partners", "apply",
}
TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof", "er", "ca", "cs", "mba", "phr", "shrm", "cp", "scp", "pmp", "jr", "sr"}


def ascii_name(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def split_name(full: str) -> tuple[str, str]:
    """'Dr. Priya K. Sharma, SHRM-CP' -> ('priya', 'sharma')."""
    full = ascii_name(full.split(",")[0])
    words = [w.lower() for w in re.findall(r"[A-Za-z]+", full)]
    words = [w for w in words if w not in TITLES]
    if not words:
        return "", ""
    first = words[0]
    rest = [w for w in words[1:] if len(w) > 1]
    last = rest[-1] if rest else ""
    return first, last


def build(first: str, last: str, pattern: str, domain: str) -> str:
    if not first or not domain:
        return ""
    if not last and "last" in pattern and pattern not in ("first",):
        return ""
    tpl = PATTERNS.get(pattern, PATTERNS["first.last"])
    local = tpl.format(first=first, last=last, f=first[:1], l=last[:1])
    return f"{local}@{domain}".lower()


def classify(local: str) -> str | None:
    """Guess which pattern produced a local part like 'priya.sharma' (no name needed)."""
    local = local.lower()
    if local in GENERIC_LOCALS or any(ch.isdigit() for ch in local):
        return None
    m = re.fullmatch(r"([a-z]+)([._])([a-z]+)", local)
    if m:
        a, sep, b = m.groups()
        if sep == "_":
            return "first_last"
        if len(a) == 1:
            return "f.last"
        if len(b) == 1:
            return "first.l"
        return "first.last"
    return None  # 'priya' / 'psharma' / 'priyasharma' are ambiguous without the name


def pattern_from_examples(emails: list[str], domain: str) -> tuple[str | None, int]:
    votes = Counter()
    for e in emails:
        local, _, d = e.lower().partition("@")
        if d != domain.lower():
            continue
        p = classify(local)
        if p:
            votes[p] += 1
    if not votes:
        return None, 0
    p, n = votes.most_common(1)[0]
    return p, n


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def has_mx(domain: str) -> bool | None:
    """True/False when DNS answers; None if DNS lookup itself isn't possible."""
    try:
        import dns.resolver
        import dns.exception
    except ImportError:
        return None
    try:
        return len(dns.resolver.resolve(domain, "MX", lifetime=8)) > 0
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return False
    except dns.exception.DNSException:
        return None


class EmailFinder:
    def __init__(self, cfg: Config, search=None):
        self.cfg = cfg
        self.search = search
        self.hunter_key = cfg.secret("HUNTER_API_KEY") if cfg["email_finder"].get("hunter") else ""
        self._pattern_cache: dict[str, tuple[str, str]] = {}

    def domain_pattern(self, domain: str) -> tuple[str, str]:
        """(pattern, confidence) for a domain."""
        if domain in self._pattern_cache:
            return self._pattern_cache[domain]
        result = ("first.last", "guessed")
        if self.hunter_key:
            try:
                r = httpx.get("https://api.hunter.io/v2/domain-search",
                              params={"domain": domain, "api_key": self.hunter_key, "limit": 10}, timeout=20)
                report_http(self.cfg, "hunter", r.status_code, r.text)
                if r.status_code == 200:
                    pat = (r.json().get("data") or {}).get("pattern") or ""
                    pat = pat.replace("{first}", "first").replace("{last}", "last").replace("{f}", "f").replace("{l}", "l")
                    if pat in PATTERNS:
                        result = (pat, "likely")
            except httpx.HTTPError:
                pass
        if result[1] == "guessed" and self.search is not None and getattr(self.search, "enabled", False):
            try:
                hits = self.search(f'"@{domain}" email', count=10)
                found = EMAIL_RE.findall(" ".join(h["snippet"] + " " + h["title"] for h in hits))
                pat, n = pattern_from_examples(found, domain)
                if pat and n >= 2:
                    result = (pat, "likely")
            except Exception:
                pass
        self._pattern_cache[domain] = result
        return result

    def hunter_find(self, first: str, last: str, domain: str) -> str:
        if not self.hunter_key or not last:
            return ""
        try:
            r = httpx.get("https://api.hunter.io/v2/email-finder",
                          params={"domain": domain, "first_name": first, "last_name": last, "api_key": self.hunter_key},
                          timeout=20)
            report_http(self.cfg, "hunter", r.status_code, r.text)
            if r.status_code != 200:
                return ""
            data = r.json().get("data") or {}
            if data.get("email") and (data.get("score") or 0) >= 70:
                return data["email"].lower()
        except httpx.HTTPError:
            pass
        return ""

    def find(self, full_name: str, domain: str) -> tuple[str, str]:
        """Return (email, confidence) or ('', reason)."""
        first, last = split_name(full_name)
        if not first or not domain:
            return "", "no name or domain"
        if self.cfg["email_finder"].get("require_mx", True) and has_mx(domain) is False:
            return "", f"{domain} has no mail server"
        found = self.hunter_find(first, last, domain)
        if found:
            return found, "verified"
        pattern, conf = self.domain_pattern(domain)
        email = build(first, last, pattern, domain)
        if not email:  # e.g. single-word name and a pattern that needs a last name
            email, conf = build(first, last, "first", domain), "guessed"
        return email, conf
