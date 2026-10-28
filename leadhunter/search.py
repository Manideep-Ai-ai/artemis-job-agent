"""Web search providers. LeadHunter searches the open web through a search engine API;
it never logs into or crawls LinkedIn itself.

Each provider returns a list of {"title", "url", "snippet"}.
"""
from __future__ import annotations

import httpx

from leadhunter.config import Config
from leadhunter.keyhealth import report_http
from leadhunter.util import USER_AGENT


class SearchError(Exception):
    pass


class Search:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.provider = (cfg["search"].get("provider") or "none").lower()
        self.searxng_url = cfg["search"].get("searxng_url") or ""
        self.brave_key = cfg.secret("BRAVE_API_KEY")
        self.tavily_key = cfg.secret("TAVILY_API_KEY")

    @property
    def enabled(self) -> bool:
        return (self.provider == "brave" and bool(self.brave_key)) or \
               (self.provider == "tavily" and bool(self.tavily_key)) or \
               (self.provider == "searxng" and bool(self.searxng_url))

    def __call__(self, query: str, count: int = 10) -> list[dict]:
        if not self.enabled:
            raise SearchError("Web search isn't set up. Add BRAVE_API_KEY or TAVILY_API_KEY to .env, or run SearXNG.")
        try:
            if self.provider == "brave":
                r = httpx.get("https://api.search.brave.com/res/v1/web/search",
                              params={"q": query, "count": min(count, 20)},
                              headers={"X-Subscription-Token": self.brave_key, "Accept": "application/json"},
                              timeout=20)
                r.raise_for_status()
                report_http(self.cfg, "search", r.status_code)
                items = (r.json().get("web") or {}).get("results") or []
                return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")} for i in items]
            if self.provider == "tavily":
                r = httpx.post("https://api.tavily.com/search",
                               json={"query": query, "max_results": min(count, 20), "search_depth": "basic"},
                               headers={"Authorization": f"Bearer {self.tavily_key}"}, timeout=30)
                r.raise_for_status()
                report_http(self.cfg, "search", r.status_code)
                return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                        for i in r.json().get("results") or []]
            r = httpx.get(f"{self.searxng_url.rstrip('/')}/search", params={"q": query, "format": "json"},
                          headers={"User-Agent": USER_AGENT}, timeout=20)
            r.raise_for_status()
            report_http(self.cfg, "search", r.status_code)
            return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                    for i in (r.json().get("results") or [])[:count]]
        except httpx.HTTPStatusError as e:
            report_http(self.cfg, "search", e.response.status_code, e.response.text)
            raise SearchError(f"{self.provider} search failed: HTTP {e.response.status_code}") from e
        except httpx.HTTPError as e:
            raise SearchError(f"{self.provider} search failed: {e}") from e
