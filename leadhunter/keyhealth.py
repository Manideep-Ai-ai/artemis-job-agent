"""Is each API key still working?

Problems are saved to key_status.json next to the database, so the dashboard and the terminal
(separate processes) both see a key that stopped working, e.g. during an overnight `leadhunter run`.
Only key problems are saved (rejected, out of credits); a network hiccup is not the key's fault.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from leadhunter.config import Config
from leadhunter.db import iso

log = logging.getLogger(__name__)

NAMES = {"gmail": "Gmail App Password", "search": "Web search key", "ai": "AI writing", "hunter": "Hunter.io key",
         "telegram": "Telegram bot"}
STEP = {"gmail": 3, "search": 4, "ai": 4, "hunter": 4, "telegram": 4}  # setup step where each key is entered


# Not every API answers 401 for a bad key: Gemini says 400 "Please pass a valid API key", Brave says 422 SUBSCRIPTION_TOKEN_INVALID
_BAD_KEY_TEXT = ("api key", "api_key", "subscription_token_invalid", "invalid_api_key", "token is invalid")


def explain(status: int, body: str = "") -> str | None:
    """What an API answer says about the key, or None when it isn't about the key."""
    if status in (401, 403) or (status in (400, 422) and any(t in body.lower() for t in _BAD_KEY_TEXT)):
        return "was rejected: it expired, was deleted, or is wrong"
    if status == 402:
        return "is out of credits"
    if status == 429:
        return "hit its limit: out of free credits, or too many requests (try again later)"
    return None


def _file(cfg: Config) -> Path:
    return Path(cfg.get("database") or "leadhunter.db").with_name("key_status.json")


def load(cfg: Config) -> dict[str, dict]:
    try:
        return json.loads(_file(cfg).read_text())
    except (OSError, ValueError):
        return {}


def record(cfg: Config, service: str, problem: str | None) -> None:
    """Save (or clear, with None) a key problem. Writes only when something changed."""
    data = load(cfg)
    if (data.get(service) or {}).get("problem") == problem:
        return
    if problem:
        data = {**data, service: {"problem": problem, "at": iso()}}
        log.warning("%s %s", NAMES[service], problem)
    else:
        data = {k: v for k, v in data.items() if k != service}
    try:
        _file(cfg).write_text(json.dumps(data, indent=1))
    except OSError as ex:
        log.warning("couldn't save key status: %s", ex)


def report_http(cfg: Config, service: str, status: int, body: str = "") -> None:
    """Call after any API response: saves a key problem, or clears an old one once a call works."""
    if status < 400:
        record(cfg, service, None)
    elif explain(status, body):
        record(cfg, service, explain(status, body))


def problems(cfg: Config) -> list[tuple[str, str, int]]:
    """(service, "Hunter.io key was rejected: ...", setup step) for every saved problem."""
    return [(k, f"{NAMES[k]} {v['problem']}", STEP[k]) for k, v in load(cfg).items() if k in NAMES]


def telegram_chat_id(token: str) -> str:
    """Your chat ID, read from the last private message you sent the bot (so nobody has to look it up)."""
    try:
        updates = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json().get("result") or []
    except (httpx.HTTPError, ValueError):
        return ""
    chats = [u["message"]["chat"]["id"] for u in updates if (u.get("message") or {}).get("chat", {}).get("type") == "private"]
    return str(chats[-1]) if chats else ""


# ---------------- active checks (cheap calls that send nothing) ----------------
def _status(method: str, url: str, **kw) -> tuple[int, str] | str:
    try:
        r = httpx.request(method, url, timeout=15, **kw)
    except httpx.HTTPError as ex:
        return f"couldn't reach it ({type(ex).__name__})"
    return r.status_code, r.text[:500]


def _hunter(key: str) -> tuple[int, str] | str:
    r = _status("GET", "https://api.hunter.io/v2/account", params={"api_key": key})
    if isinstance(r, tuple) and r[0] == 200:
        try:
            s = ((json.loads(r[1]).get("data") or {}).get("requests") or {}).get("searches") or {}
        except ValueError:
            s = {}
        if s.get("available") and s.get("used", 0) >= s["available"]:
            return 402, ""  # free monthly searches used up
    return r


def _ai_model(ai) -> tuple[int, str] | str:
    """(status, text) from the provider's model list, or a problem string when your model isn't on it any more."""
    try:
        r = httpx.get(f"{ai.base_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {ai.key}"}, timeout=15)
        ids = {m.get("id", "").replace("models/", "") for m in r.json().get("data") or []} if r.status_code == 200 else set()
    except (httpx.HTTPError, ValueError) as ex:
        return f"couldn't reach it ({type(ex).__name__})"
    if ids and ai.model not in ids:
        return f"can't run: the model {ai.model} is gone (retired by the provider). Pick another in setup"
    return r.status_code, r.text[:500]


def check_all(cfg: Config) -> dict[str, str]:
    """Test every key that is set. Returns {service: "ok" | what's wrong} and saves the results."""
    from leadhunter.ai import AI
    from leadhunter.mailer import Mailer

    results: dict[str, tuple[int, str] | str] = {}
    if cfg["mail"].get("from_address") and cfg.secret("GMAIL_APP_PASSWORD"):
        ok, msg = Mailer(cfg).test_login()
        results["gmail"] = (200, "") if ok else (401, "") if "refused" in msg else msg
    sp = (cfg["search"].get("provider") or "").lower()
    if sp == "brave" and cfg.secret("BRAVE_API_KEY"):  # uses 1 search from the free monthly quota
        results["search"] = _status("GET", "https://api.search.brave.com/res/v1/web/search", params={"q": "test", "count": 1},
                                    headers={"X-Subscription-Token": cfg.secret("BRAVE_API_KEY"), "Accept": "application/json"})
    elif sp == "tavily" and cfg.secret("TAVILY_API_KEY"):
        results["search"] = _status("POST", "https://api.tavily.com/search", json={"query": "test", "max_results": 1},
                                    headers={"Authorization": f"Bearer {cfg.secret('TAVILY_API_KEY')}"})
    ai = AI(cfg)
    if ai.enabled and ai.key:  # the model list is free and uses no tokens; it also shows if your model was retired
        results["ai"] = _ai_model(ai)
        if isinstance(results["ai"], str):
            record(cfg, "ai", results["ai"])
    if cfg["email_finder"].get("hunter") and cfg.secret("HUNTER_API_KEY"):
        results["hunter"] = _hunter(cfg.secret("HUNTER_API_KEY"))
    if cfg.secret("TELEGRAM_BOT_TOKEN"):
        r = _status("GET", f"https://api.telegram.org/bot{cfg.secret('TELEGRAM_BOT_TOKEN')}/getMe")
        results["telegram"] = (401, "") if isinstance(r, tuple) and r[0] == 404 else r  # 404 = not a token at all

    tok = cfg.secret("TELEGRAM_BOT_TOKEN")
    if results.get("telegram", (0,))[0] == 200 and cfg.secret("TELEGRAM_CHAT_ID") == tok.split(":")[0]:
        results["telegram"] = "can't message you: the chat ID is the bot's own number"
        record(cfg, "telegram", results["telegram"])
    out = {}
    for service, r in results.items():
        if isinstance(r, str):  # network trouble: say so, but don't blame the key
            out[service] = r
            continue
        report_http(cfg, service, *r)
        out[service] = "ok" if r[0] < 400 else (explain(*r) or f"answered HTTP {r[0]}")
    return out
