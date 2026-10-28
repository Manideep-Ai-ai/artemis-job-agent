"""Configuration: config.yaml for settings, .env for secrets."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path

import yaml

HARD_DAILY_CEILING = 20  # no config value can push sending above this

MODES = {
    "jobs": "Job hunting: find jobs and email recruiters / hiring managers",
    "freelance": "Freelance: find client projects and send proposals",
    "both": "Both jobs and freelance projects",
    "leads": "Just collect leads (no emails sent)",
}

DEFAULTS: dict = {
    "mode": "jobs",                      # jobs | freelance | both | leads
    "lead_types": ["job"],               # used in 'leads' mode: job and/or freelance
    "profile": {
        "name": "", "resume_path": "", "skills": [], "years": 0, "roles": [],
        "locations": [], "linkedin_url": "", "portfolio_url": "", "phone": "",
        "pitch": "",                     # one line about you, used in freelance proposals
        "min_hourly": 0, "min_fixed": 0, "currency": "USD",
    },
    "sources": {
        "remotive": True, "arbeitnow": True, "greenhouse": [], "lever": [],
        "ashby": [], "smartrecruiters": [], "recruitee": [], "hackernews": True, "freelancer": True, "freelancer_queries": [],
        "reddit": False, "max_age_days": 7,
    },
    "linkedin": {"drafts": True},        # write LinkedIn notes/DMs for you to send yourself
    "ai": {"provider": "none", "model": "", "base_url": ""},
    "search": {"provider": "none", "searxng_url": "http://localhost:8080", "max_contacts_per_job": 2},
    "email_finder": {"hunter": False, "require_mx": True},
    "outreach": {
        "daily_limit": 10, "require_approval": True, "attach_resume": True,
        "followups": [4, 7], "send_window": ["09:30", "18:30"], "weekdays_only": True,
        "min_gap_minutes": 3, "max_gap_minutes": 9,
    },
    "mail": {
        "dry_run": True, "smtp_host": "smtp.gmail.com", "smtp_port": 465,
        "imap_host": "imap.gmail.com", "from_address": "",
    },
    "database": "leadhunter.db",
}

AI_DEFAULT_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "ollama": "http://localhost:11434/v1",
    "openai": "https://api.openai.com/v1",
}
AI_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_env(path: str | Path = ".env") -> None:
    """Minimal .env loader (KEY=value lines). Existing environment variables win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


class Config(dict):
    """Dict with a few helpers. Access sections like cfg['outreach']['daily_limit']."""

    @property
    def daily_limit(self) -> int:
        try:
            n = int(self["outreach"]["daily_limit"])
        except (TypeError, ValueError):
            n = 10
        return max(0, min(n, HARD_DAILY_CEILING))

    @property
    def followup_days(self) -> list[int]:
        days = [int(d) for d in self["outreach"].get("followups") or [] if int(d) > 0]
        return days[:3]

    @property
    def mode(self) -> str:
        m = (self.get("mode") or "jobs").lower()
        return m if m in MODES else "jobs"

    @property
    def lead_kinds(self) -> list[str]:
        m = self.mode
        if m == "jobs":
            return ["job"]
        if m == "freelance":
            return ["freelance"]
        if m == "both":
            return ["job", "freelance"]
        kinds = [k for k in (self.get("lead_types") or []) if k in ("job", "freelance")]
        return kinds or ["job", "freelance"]

    @property
    def outreach_enabled(self) -> bool:
        return self.mode != "leads"

    def secret(self, name: str) -> str:
        return os.environ.get(name, "").strip()

    def ai_settings(self) -> tuple[str, str, str, str]:
        """(provider, base_url, model, api_key)."""
        ai = self["ai"]
        provider = (ai.get("provider") or "none").lower()
        base = ai.get("base_url") or AI_DEFAULT_URLS.get(provider, "")
        key = self.secret(AI_KEY_ENV.get(provider, "")) if provider in AI_KEY_ENV else ""
        return provider, base, ai.get("model") or "", key


def save_config(cfg: dict, path: str | Path = "config.yaml") -> None:
    """Write settings back to config.yaml (used by the setup wizards). Secrets are never written here."""
    header = "# LeadHunter settings. Edit freely or re-run `leadhunter setup`. Secrets live in .env.\n"
    Path(path).write_text(header + yaml.safe_dump(dict(cfg), sort_keys=False, allow_unicode=True), encoding="utf-8")


def save_env(updates: dict[str, str], path: str | Path = ".env") -> None:
    """Set KEY=value lines in .env (creating it), keeping everything else. Also updates os.environ."""
    if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) for k in updates):
        raise ValueError("bad .env key")
    updates = {k: re.sub(r"[\r\n]", "", str(v)) for k, v in updates.items()}  # a pasted line break can't add lines
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    for k, v in updates.items():
        if k not in done:
            lines.append(f"{k}={v}")
        os.environ[k] = v
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        p.chmod(0o600)  # only you can read your keys
    except OSError:
        pass


def load_config(path: str | Path = "config.yaml", env_path: str | Path = ".env") -> Config:
    load_env(env_path)
    data = {}
    p = Path(path)
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config(_merge(DEFAULTS, data))
