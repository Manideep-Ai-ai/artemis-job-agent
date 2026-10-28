"""The "Get started" checklist: what to connect, why, where to get it, and whether it's done.

Shared by the dashboard (/start), the terminal session and `leadhunter doctor`.
"""
from __future__ import annotations

from pathlib import Path

from leadhunter.config import Config
from leadhunter.keyhealth import problems

# level: required | recommended | optional | none
def checklist(cfg: Config) -> list[dict]:
    kinds = cfg.lead_kinds
    outreach = cfg.outreach_enabled
    p = cfg["profile"]
    sp = (cfg["search"].get("provider") or "none").lower()
    ai = (cfg["ai"].get("provider") or "none").lower()

    search_ok = (sp == "brave" and bool(cfg.secret("BRAVE_API_KEY"))) or \
                (sp == "tavily" and bool(cfg.secret("TAVILY_API_KEY"))) or \
                (sp == "searxng" and bool(cfg["search"].get("searxng_url")))
    ai_ok = ai == "ollama" or (ai in ("gemini", "groq", "openai") and bool(
        cfg.secret({"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}[ai])))
    gmail_ok = bool(cfg["mail"].get("from_address") and "@" in cfg["mail"]["from_address"]
                    and cfg.secret("GMAIL_APP_PASSWORD"))

    items = [
        {
            "key": "profile", "name": "Your profile and resume",
            "level": "required",
            "why": "Your skills decide which jobs and projects match you.",
            "done": bool(p.get("name") and (p.get("skills") or (p.get("resume_path") and Path(p["resume_path"]).exists()))),
            "steps": ["Upload your resume in setup (step 2).",
                      "Check the skills it found."],
            "link": "", "env": "",
        },
        {
            "key": "gmail", "name": "Gmail App Password",
            "level": "required" if outreach else "none",
            "why": "Sends your emails from your own Gmail and reads replies so follow-ups stop.",
            "done": gmail_ok,
            "steps": ["Turn on 2-Step Verification: https://myaccount.google.com/signinoptions/twosv",
                      "Create an app password named Artemis: https://myaccount.google.com/apppasswords",
                      "Paste the 16-letter code in setup (step 3). It is not your Gmail password.",
                      "\"Setting not available\"? 2-Step is off, or it's a work/school account. Use a personal Gmail."],
            "link": "https://myaccount.google.com/apppasswords", "env": "GMAIL_APP_PASSWORD",
        },
        {
            "key": "search", "name": "Web search: Brave or Tavily",
            "level": "recommended" if ("job" in kinds and outreach) else "none",
            "why": "Finds the recruiter and hiring manager for each job. Without it you only get emails written in the post.",
            "done": search_ok,
            "steps": ["Get a free key from Brave Search API (or Tavily).",
                      "Paste it in setup (step 4)."],
            "link": "https://brave.com/search/api/", "env": "BRAVE_API_KEY / TAVILY_API_KEY",
        },
        {
            "key": "ai", "name": "AI writing: Gemini, Groq or Ollama",
            "level": "recommended" if outreach else "optional",
            "why": "Writes more personal emails and proposals. Without it, good templates are used.",
            "done": ai_ok,
            "steps": ["Get a free Gemini key from Google AI Studio.",
                      "Paste it in setup (step 4)."],
            "link": "https://aistudio.google.com/apikey", "env": "GEMINI_API_KEY / GROQ_API_KEY",
        },
        {
            "key": "hunter", "name": "Hunter.io (better HR emails)",
            "level": "optional" if ("job" in kinds and outreach) else "none",
            "why": "Finds the exact email format a company uses, so fewer emails bounce.",
            "done": bool(cfg["email_finder"].get("hunter") and cfg.secret("HUNTER_API_KEY")),
            "steps": ["Get a free key at hunter.io.",
                      "Paste it in setup (step 4)."],
            "link": "https://hunter.io/api-keys", "env": "HUNTER_API_KEY",
        },
        {
            "key": "reddit", "name": "Reddit (needs Reddit's approval)",
            "level": "optional" if "freelance" in kinds else "none",
            "why": "Adds [Hiring] posts from r/forhire. Reddit now approves every app by hand and often says no "
                   "to personal projects. Safe to skip: Freelancer.com already gives you freelance leads.",
            "done": bool(cfg["sources"].get("reddit") and cfg.secret("REDDIT_CLIENT_ID") and cfg.secret("REDDIT_CLIENT_SECRET")),
            "steps": ["Request API access from Reddit and wait for approval (can take weeks).",
                      "If approved, create a 'script' app and paste its ID and secret in setup (step 4)."],
            "link": "https://www.reddit.com/prefs/apps", "env": "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET",
        },
        {
            "key": "telegram", "name": "Telegram alerts for replies",
            "level": "optional" if outreach else "none",
            "why": "Pings your phone the moment someone replies, with what kind of reply it is.",
            "done": bool(cfg.secret("TELEGRAM_BOT_TOKEN") and cfg.secret("TELEGRAM_CHAT_ID")),
            "steps": ["In Telegram, send /newbot to @BotFather and copy the whole token (with the colon).",
                      "Send your new bot any message, like \"hi\".",
                      "Paste the token in setup (step 4) and click Finish. Your chat ID is found for you."],
            "link": "https://t.me/BotFather", "env": "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID",
        },
        {
            "key": "practice", "name": "Send for real (practice mode off)",
            "level": "optional" if outreach else "none",  # a choice, not a missing setup step
            "why": "While practice mode is on, emails are saved to ./outbox instead of sent, so you can check them first.",
            "done": not cfg["mail"].get("dry_run", True),
            "steps": ["Check a few emails in ./outbox.",
                      "When they look right, untick Practice mode in setup (step 3)."],
            "link": "", "env": "",
        },
    ]
    # a key that is set but stopped working (expired, deleted, out of credits) is not done
    bad = {k: msg for k, msg, _ in problems(cfg)}
    return [{**i, "done": False, "problem": bad[i["key"]]} if i["key"] in bad else i for i in items if i["level"] != "none"]


FREE_SOURCES = ["Greenhouse, Lever, Ashby, SmartRecruiters and Recruitee company job boards", "Remotive and Arbeitnow job lists",
                "Hacker News 'Who is hiring?' (jobs and contract work)", "Freelancer.com projects"]


def summary(items: list[dict]) -> tuple[int, int]:
    must = [i for i in items if i["level"] == "required"]
    return sum(i["done"] for i in must), len(must)
