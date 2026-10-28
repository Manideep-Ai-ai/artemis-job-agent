"""Terminal version: `leadhunter setup` (onboarding questions) and `leadhunter start` (guided session).

Everything the dashboard does can be done here with numbered menus.
"""
from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import webbrowser
from pathlib import Path

from leadhunter.config import MODES, Config, load_config, save_config, save_env
from leadhunter.mailer import Mailer
from leadhunter.resume import extract_profile, read_text

AI_MODELS = {"gemini": "gemini-flash-latest", "groq": "llama-3.3-70b-versatile", "ollama": "llama3.2", "openai": "gpt-4o-mini"}
AI_KEYS = {"gemini": ("GEMINI_API_KEY", "https://aistudio.google.com/apikey"),
           "groq": ("GROQ_API_KEY", "https://console.groq.com/keys"),
           "openai": ("OPENAI_API_KEY", "https://platform.openai.com/api-keys")}
SEARCH_KEYS = {"brave": ("BRAVE_API_KEY", "https://brave.com/search/api/"),
               "tavily": ("TAVILY_API_KEY", "https://tavily.com")}


def print_checklist(io: "IO", cfg: Config, full: bool = True) -> None:
    from leadhunter.checklist import FREE_SOURCES, checklist, summary
    items = checklist(cfg)
    done, total = summary(items)
    io.title(f"Get started · {done} of {total} required steps done")
    for n, i in enumerate(items, 1):
        mark = "✓" if i["done"] else "!" if i.get("problem") else ("✗" if i["level"] == "required" else "·")
        tag = "connected" if i["done"] else "KEY PROBLEM" if i.get("problem") else i["level"]
        io.say(f"  {mark} {n}. {i['name']}  ({tag})")
        if i.get("problem"):
            io.warn(f"       {i['problem']}. Make a new key and enter it with: leadhunter setup")
            continue
        if full and not i["done"]:
            io.dim(f"       Why: {i['why']}")
            for s in i["steps"]:
                io.dim(f"       - {s}")
            if i["link"]:
                io.dim(f"       Link: {i['link']}")
            if i["env"]:
                io.dim(f"       Saved as {i['env']} in .env")
    if full:
        io.dim("  Works with no key: " + "; ".join(FREE_SOURCES))
        io.dim("  Enter keys with: leadhunter setup  (or menu option 'Change my answers')")


class IO:
    """All input/output goes through here so it can be scripted in tests."""

    def __init__(self, input_fn=input, secret_fn=getpass.getpass, out=None):
        self.input_fn, self.secret_fn = input_fn, secret_fn
        self.out = out or sys.stdout
        self.color = hasattr(self.out, "isatty") and self.out.isatty() and os.environ.get("NO_COLOR") is None

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def say(self, s: str = "") -> None:
        print(s, file=self.out)

    def title(self, s: str) -> None:
        self.say()
        self.say(self._c("1;33", s))

    def good(self, s: str) -> None:
        self.say(self._c("32", "✓ " + s))

    def warn(self, s: str) -> None:
        self.say(self._c("33", "! " + s))

    def dim(self, s: str) -> None:
        self.say(self._c("2", s))

    def ask(self, prompt: str, default: str = "") -> str:
        hint = f" [{default}]" if default else ""
        v = self.input_fn(f"{prompt}{hint}: ").strip()   # EOF / Ctrl+D ends the session (see cli)
        return v or default

    def secret(self, prompt: str) -> str:
        return self.secret_fn(f"{prompt} (hidden, Enter to skip): ").strip()

    def yes(self, prompt: str, default: bool = True) -> bool:
        v = self.ask(f"{prompt} ({'Y/n' if default else 'y/N'})").lower()
        return default if not v else v.startswith("y")

    def choose(self, prompt: str, options: list[tuple[str, str]], default: int = 1) -> str:
        """options: [(value, label)] -> value"""
        if prompt:
            self.say(prompt)
        for i, (_, label) in enumerate(options, 1):
            self.say(f"  {i}) {label}")
        while True:
            v = self.ask(">", str(default))
            if v.isdigit() and 1 <= int(v) <= len(options):
                return options[int(v) - 1][0]
            self.warn(f"Type a number from 1 to {len(options)}.")

    def number(self, prompt: str, default: float) -> float:
        while True:
            v = self.ask(prompt, f"{default:g}")
            try:
                return float(v)
            except ValueError:
                self.warn("Type a number.")


def _list(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


# ======================================================================
# setup wizard
# ======================================================================
def run_setup(config_path: str = "config.yaml", env_path: str = ".env", io: IO | None = None,
              test_gmail=None) -> Config:
    io = io or IO()
    cfg = load_config(config_path, env_path)
    p = cfg["profile"]
    io.title("Welcome to LeadHunter")
    io.say("A few questions and you're set. Press Enter to keep the value in [brackets].")

    # ---- 1. goal
    io.title("Step 1 of 5 · What are you here for?")
    order = ["jobs", "freelance", "both", "leads"]
    cfg["mode"] = io.choose("", [(m, MODES[m]) for m in order], order.index(cfg.mode) + 1)
    if cfg["mode"] == "leads":
        cfg["lead_types"] = {"j": ["job"], "f": ["freelance"], "b": ["job", "freelance"]}[
            io.choose("Which leads do you want?", [("j", "Jobs"), ("f", "Freelance projects"), ("b", "Both")], 3)]
    kinds = cfg.lead_kinds

    # ---- 2. resume + profile
    io.title("Step 2 of 5 · About you")
    while True:
        path = io.ask("Path to your resume (PDF, DOCX or TXT)", p.get("resume_path") or "")
        if not path:
            io.warn("No resume: you can type your skills instead.")
            break
        rp = Path(os.path.expanduser(path.strip('"').strip("'")))
        if rp.exists():
            p["resume_path"] = str(rp.resolve())
            try:
                text = read_text(rp)
                found = extract_profile(text)
                io.good(f"Read your resume: {len(found['skills'])} skills found"
                        + (f", about {found['years']:g} years of experience" if found["years"] else ""))
                if found["skills"]:
                    p["skills"] = found["skills"]
                if found["years"] and not p.get("years"):
                    p["years"] = found["years"]
                first_line = next((l.strip() for l in text.splitlines() if l.strip()), "")
                if not p.get("name") and 1 < len(first_line.split()) <= 4 and not any(ch.isdigit() for ch in first_line):
                    p["name"] = first_line.title() if first_line.isupper() else first_line
            except Exception as e:
                io.warn(f"Couldn't read it ({e}). You can type your skills instead.")
            break
        io.warn(f"No file at {rp}. Check the path (tip: drag the file into this window).")
    p["skills"] = _list(io.ask("Your skills, comma separated", ", ".join(p.get("skills") or [])))
    p["name"] = io.ask("Your full name", p.get("name") or "")
    p["years"] = io.number("Years of experience", float(p.get("years") or 0))
    if "job" in kinds:
        p["roles"] = _list(io.ask("Roles you want, comma separated", ", ".join(p.get("roles") or []) or "Software Engineer"))
        p["locations"] = _list(io.ask("Cities you'd work in (add Remote if you like)", ", ".join(p.get("locations") or []) or "Remote"))
    if "freelance" in kinds:
        p["currency"] = io.ask("Currency for your rates", p.get("currency") or "USD").upper()
        p["min_hourly"] = io.number("Lowest hourly rate you'll accept", float(p.get("min_hourly") or 15))
        p["min_fixed"] = io.number("Smallest fixed-price project you'll take", float(p.get("min_fixed") or 100))
        p["pitch"] = io.ask("One line about you for proposals (e.g. 'I build fast Python backends and AI chatbots')", p.get("pitch") or "")
    p["portfolio_url"] = io.ask("Portfolio or GitHub link (optional)", p.get("portfolio_url") or "")
    p["phone"] = io.ask("Phone for your email signature (optional)", p.get("phone") or "")

    # ---- 3. Gmail
    io.title("Step 3 of 5 · Connect your Gmail")
    if not cfg.outreach_enabled:
        io.dim("Skipped: leads-only mode doesn't send email.")
    else:
        io.say("Emails go out from your own Gmail. Google needs an App Password for this:")
        io.say("  1. Turn on 2-Step Verification for your Google account")
        io.say("  2. Open https://myaccount.google.com/apppasswords and create one named 'LeadHunter'")
        io.say("  3. Paste the 16-character code below. It's saved only in .env on this computer.")
        addr = io.ask("Gmail address", cfg["mail"].get("from_address") or "")
        if addr:
            cfg["mail"]["from_address"] = addr
            while True:
                pw = io.secret("App password").replace(" ", "")
                if not pw:
                    io.warn("Skipped. Add GMAIL_APP_PASSWORD to .env later, or run setup again.")
                    break
                save_env({"GMAIL_APP_PASSWORD": pw}, env_path)
                io.say("Testing the connection…")
                ok, msg = (test_gmail or (lambda c: Mailer(c).test_login()))(cfg)
                (io.good if ok else io.warn)(msg)
                if ok or not io.yes("Try another password?"):
                    break
        cfg["mail"]["dry_run"] = io.yes("Start in practice mode? Emails are saved to ./outbox instead of sent "
                                        "until you turn it off", bool(cfg["mail"].get("dry_run", True)))

    # ---- 4. LinkedIn
    io.title("Step 4 of 5 · LinkedIn")
    io.dim("LeadHunter never logs into LinkedIn or sends messages there: LinkedIn bans automation.\n"
           "Instead it writes the connection note and message, and opens the profile for you to send in one click.")
    p["linkedin_url"] = io.ask("Your LinkedIn profile URL (shown in your email signature)", p.get("linkedin_url") or "")
    if cfg.outreach_enabled:
        cfg["linkedin"]["drafts"] = io.yes("Write LinkedIn notes for the people it finds?", bool(cfg["linkedin"].get("drafts", True)))

    # ---- 5. power-ups
    io.title("Step 5 of 5 · Free power-ups")
    if "job" in kinds and cfg.outreach_enabled:
        io.say("Web search finds the recruiter and hiring manager for each job (needed for job outreach).")
        sp = io.choose("Search provider:", [("brave", "Brave Search API (free tier)"), ("tavily", "Tavily (free tier)"),
                                            ("searxng", "My own SearXNG server"), ("none", "Skip for now")],
                       ["brave", "tavily", "searxng", "none"].index(cfg["search"].get("provider") or "none") + 1
                       if (cfg["search"].get("provider") or "none") in ("brave", "tavily", "searxng", "none") else 1)
        cfg["search"]["provider"] = sp
        if sp in SEARCH_KEYS:
            env, url = SEARCH_KEYS[sp]
            io.dim(f"Get a key at {url}")
            key = io.secret(f"{sp.title()} API key")
            if key:
                save_env({env: key}, env_path)
        elif sp == "searxng":
            cfg["search"]["searxng_url"] = io.ask("SearXNG URL", cfg["search"].get("searxng_url") or "http://localhost:8080")
    io.say("AI writes better, more personal emails and proposals (optional; templates work without it).")
    ai = io.choose("AI provider:", [("gemini", "Google Gemini (free tier)"), ("groq", "Groq (free tier)"),
                                    ("ollama", "Ollama on this computer (free, offline)"), ("openai", "OpenAI"),
                                    ("none", "No AI, use templates")], 1)
    cfg["ai"]["provider"] = ai
    if ai != "none":
        cfg["ai"]["model"] = io.ask("Model", cfg["ai"].get("model") if cfg["ai"].get("provider") == ai and cfg["ai"].get("model") else AI_MODELS[ai])
        cfg["ai"]["base_url"] = ""
        if ai in AI_KEYS:
            env, url = AI_KEYS[ai]
            io.dim(f"Get a key at {url}")
            key = io.secret(f"{ai.title()} API key")
            if key:
                save_env({env: key}, env_path)
    if cfg.outreach_enabled:
        n = int(io.number("Emails per day, including follow-ups (10 is safe, max 20)", float(cfg.daily_limit)))
        cfg["outreach"]["daily_limit"] = max(1, min(n, 20))

    save_config(cfg, config_path)
    if not Path(env_path).exists():
        save_env({}, env_path)
    io.title("Saved!")
    io.good(f"Saved {config_path} and {env_path}")
    print_checklist(io, load_config(config_path, env_path))
    io.say("Next: run `leadhunter start` for the guided terminal flow, or `leadhunter web` for the dashboard.")
    return load_config(config_path, env_path)


# ======================================================================
# guided session
# ======================================================================
def _wrap(s: str, indent: str = "    ") -> str:
    return "\n".join(textwrap.fill(line, 92, initial_indent=indent, subsequent_indent=indent) if line.strip() else ""
                     for line in (s or "").splitlines())


def _edit_text(io: IO, text: str) -> str:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or (shutil.which("nano") and "nano") or (shutil.which("notepad") and "notepad")
    if editor and sys.stdin.isatty():
        with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            subprocess.call([*editor.split(), path])
            return Path(path).read_text(encoding="utf-8").strip() or text
        finally:
            os.unlink(path)
    io.say("Type the new text. Finish with a line containing only a dot (.). Empty keeps the old text.")
    lines = []
    while True:
        try:
            line = io.input_fn("")
        except EOFError:
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip() or text


class Session:
    def __init__(self, agent, io: IO | None = None, open_url=webbrowser.open,
                 config_path: str = "config.yaml", env_path: str = ".env"):
        self.a, self.io, self.open_url = agent, io or IO(), open_url
        self.config_path, self.env_path = config_path, env_path

    # ---- helpers
    def _status_line(self) -> str:
        s = self.a.db.stats()
        parts = [f"{s['jobs']} jobs", f"{s['freelance']} freelance", f"{s['matched']} matches"]
        if self.a.cfg.outreach_enabled:
            parts += [f"{s['drafts']} drafts", f"{s['manual']} to send by hand",
                      f"sent today {self.a.sent_today()}/{self.a.cfg.daily_limit}",
                      f"{s['active']} waiting", f"{s['replied']} replied"]
        return " · ".join(parts)

    def run(self) -> None:
        io, a = self.io, self.a
        io.title(f"LeadHunter · {MODES[a.cfg.mode]}")
        if a.mailer.dry_run and a.cfg.outreach_enabled:
            io.dim("Practice mode: emails are saved to ./outbox, not sent. Turn it off in `leadhunter setup`.")
        from leadhunter.keyhealth import problems
        for _, msg, _ in problems(a.cfg):
            io.warn(f"{msg}. Make a new key and enter it with option 'Change my answers'.")
        while True:
            io.say()
            io.dim(self._status_line())
            options = [("collect", "Find new leads"), ("browse", "Browse my best leads")]
            from leadhunter.checklist import checklist, summary
            done, total = summary(checklist(a.cfg))
            if done < total:
                io.warn(f"{total - done} required setup step(s) left: choose 'Get started checklist'.")
            if a.cfg.outreach_enabled:
                options += [("drafts", "Review email drafts"), ("linkedin", "LinkedIn messages to send"),
                            ("proposals", "Freelance proposals to post"), ("send", "Send now (approved + follow-ups)"),
                            ("check", "Check Gmail for replies"), ("threads", "My conversations")]
            options += [("link", "Add a job / project from a link"), ("export", "Export leads to CSV"),
                        ("checklist", "Get started checklist (what to connect)"),
                        ("setup", "Change my answers (setup)"), ("quit", "Quit")]
            choice = io.choose("What next?", options, 1)
            if choice == "quit":
                io.say("Bye! Run `leadhunter start` any time.")
                return
            getattr(self, f"do_{choice}")()

    # ---- actions
    def do_collect(self):
        self.io.say("Collecting from all sources…")
        r = self.a.collect()
        self.io.good(f"{r['fetched']} leads checked, {r['new']} new, {r['new_good_matches']} strong/good matches.")
        for e in r["errors"]:
            self.io.warn(e)

    def _best(self, limit: int = 15):
        kinds = self.a.cfg.lead_kinds
        marks = ",".join("?" * len(kinds))
        return self.a.db.q(f"""SELECT * FROM jobs WHERE kind IN ({marks}) AND fit!='' AND status IN ('new','shortlisted')
                               ORDER BY (fit='strong') DESC, score DESC, id DESC LIMIT ?""", (*kinds, limit))

    def _line(self, j) -> str:
        money = f"  {j['budget']}" if j["kind"] == "freelance" and j["budget"] else ""
        who = j["company"] or ("Freelance" if j["kind"] == "freelance" else "")
        return f"[{j['fit']:7}] {j['title'][:52]} · {who[:24]} · {j['location'][:20]}{money}"

    def do_browse(self):
        io = self.io
        while True:
            rows = self._best()
            if not rows:
                io.warn("No new matches. Choose 'Find new leads' first, or add a link.")
                return
            pick = io.choose("Pick a lead (or the last option to go back):",
                             [(str(j["id"]), self._line(j)) for j in rows] + [("back", "Back")], 1)
            if pick == "back":
                return
            self.lead_detail(int(pick))

    def lead_detail(self, jid: int):
        io, a = self.io, self.a
        j = a.db.job(jid)
        io.title(f"{j['title']}  ({j['kind']}, {j['fit'] or 'weak'} fit)")
        io.say(f"  {j['company'] or '—'} · {j['location'] or '—'} · from {j['source']}")
        if j["kind"] == "freelance":
            io.say(f"  Budget: {j['budget'] or 'not stated'}" + (f" · suggested quote: {j['quote']}" if j["quote"] else ""))
        io.say(f"  Your skills it mentions: {', '.join(json.loads(j['matched'] or '[]')) or 'none'}")
        if j["contact_email"]:
            io.say(f"  Email in the post: {j['contact_email']}")
        io.say(f"  {j['url']}")
        io.say(_wrap((j["description"] or "")[:700]))
        opts = []
        if a.cfg.outreach_enabled:
            opts.append(("reach", "Reach out: find the people and write the messages"))
        opts += [("open", "Open the posting in my browser"), ("ignore", "Not interested (hide it)"), ("back", "Back")]
        c = io.choose("", opts, 1)
        if c == "reach":
            self._reach(jid)
        elif c == "open" and j["url"]:
            self.open_url(j["url"])
        elif c == "ignore":
            a.ignore(jid)
            io.good("Hidden.")

    def _reach(self, jid: int):
        io = self.io
        io.say("Working on it…")
        r = self.a.reach_out(jid)
        for c in r["contacts"]:
            io.good(f"{c['name']} · {c.get('title', '')[:50]} · {c.get('email') or 'no email'} ({c.get('email_confidence') or '-'})")
        for n in r["notes"]:
            io.warn(n)
        if r["added"] or r.get("proposal"):
            if io.yes("Review the drafts now?"):
                self.do_drafts(job_id=jid)
                self.do_linkedin(job_id=jid)
                self.do_proposals(job_id=jid)

    def do_drafts(self, job_id: int | None = None):
        io, a = self.io, self.a
        sql = """SELECT m.*, c.name, c.email, c.email_confidence, j.title jt, j.company FROM messages m
                 JOIN contacts c ON c.id=m.contact_id JOIN jobs j ON j.id=c.job_id
                 WHERE m.status='draft' AND m.channel='email'""" + (" AND c.job_id=?" if job_id else "") + " ORDER BY m.id"
        rows = a.db.q(sql, (job_id,) if job_id else ())
        if not rows:
            io.dim("No email drafts waiting.")
            return
        for m in rows:
            m = dict(m)
            while True:
                io.title(f"Email to {m['name']} <{m['email']}> ({m['email_confidence']})")
                io.say(f"  Re: {m['jt']} · {m['company']}")
                io.say(f"  Subject: {m['subject']}\n")
                io.say(_wrap(m["body"]))
                if m["email_confidence"] == "guessed":
                    io.warn("This address is a best guess and may bounce.")
                c = io.choose("", [("a", "Approve (sends with the next 'Send now')"), ("e", "Edit the text"),
                                   ("t", "Edit the subject"), ("s", "Don't send to this person"), ("n", "Decide later"),
                                   ("q", "Stop reviewing")], 1)
                if c == "e":
                    m["body"] = _edit_text(io, m["body"])
                    a.save_draft(m["id"], None, m["body"])
                    continue
                if c == "t":
                    m["subject"] = io.ask("New subject", m["subject"])
                    a.save_draft(m["id"], m["subject"], None)
                    continue
                if c == "a":
                    a.approve(m["id"])
                    io.good("Approved.")
                elif c == "s":
                    a.skip(m["id"])
                    io.good("Skipped.")
                elif c == "q":
                    return
                break

    def _manual_queue(self, channel: str, job_id: int | None):
        sql = """SELECT m.*, c.name, c.linkedin_url, j.title jt, j.company, j.url ju, j.id jid FROM messages m
                 LEFT JOIN contacts c ON c.id=m.contact_id JOIN jobs j ON j.id=m.job_id
                 WHERE m.status='draft' AND m.channel=?""" + (" AND m.job_id=?" if job_id else "") + " ORDER BY m.job_id, m.contact_id, m.seq"
        return self.a.db.q(sql, (channel, job_id) if job_id else (channel,))

    def do_linkedin(self, job_id: int | None = None):
        io, a = self.io, self.a
        rows = self._manual_queue("linkedin", job_id)
        if not rows:
            if not job_id:
                io.dim("No LinkedIn messages waiting.")
            return
        io.dim("Send these yourself: open the profile, click Connect → Add a note, paste. Send the message once they accept.")
        for m in rows:
            io.title(f"LinkedIn · {m['name']} · {m['subject']}")
            io.say(f"  {m['linkedin_url']}  (re: {m['jt']} at {m['company']})\n")
            io.say(_wrap(m["body"]))
            c = io.choose("", [("o", "Open their profile"), ("d", "I sent it"), ("s", "Skip"), ("n", "Later"), ("q", "Stop")], 1)
            if c == "o":
                self.open_url(m["linkedin_url"])
                if io.yes("Did you send it?", False):
                    a.mark_done(m["id"])
            elif c == "d":
                a.mark_done(m["id"])
            elif c == "s":
                a.db.run("UPDATE messages SET status='skipped' WHERE id=?", (m["id"],))
            elif c == "q":
                return

    def do_proposals(self, job_id: int | None = None):
        io, a = self.io, self.a
        rows = self._manual_queue("platform", job_id)
        if not rows:
            if not job_id:
                io.dim("No proposals waiting.")
            return
        for m in rows:
            io.title(f"Proposal · {m['jt']}")
            io.say(f"  Post it here: {m['ju']}\n")
            io.say(_wrap(m["body"]))
            c = io.choose("", [("o", "Open the project page"), ("e", "Edit"), ("d", "I posted it"), ("s", "Skip"), ("q", "Stop")], 1)
            if c == "o":
                self.open_url(m["ju"])
                if io.yes("Did you post it?", False):
                    a.mark_done(m["id"])
            elif c == "e":
                a.db.run("UPDATE messages SET body=? WHERE id=?", (_edit_text(io, m["body"]), m["id"]))
            elif c == "d":
                a.mark_done(m["id"])
            elif c == "s":
                a.db.run("UPDATE messages SET status='skipped' WHERE id=?", (m["id"],))
            elif c == "q":
                return

    def do_send(self):
        io, a = self.io, self.a
        approved = a.db.one("SELECT COUNT(*) n FROM messages WHERE status='approved' AND channel='email'")["n"]
        due = len(a.due_followups())
        io.say(f"{approved} approved email(s), {due} follow-up(s) due, {a.remaining_today()} left in today's limit.")
        if not approved and not due:
            return
        force = False
        if not a.in_send_window():
            io.warn("It's outside your send window (weekday working hours). Emails sent at night look automated.")
            force = io.yes("Send anyway?", False)
            if not force:
                return
        wait = not a.mailer.dry_run and io.yes("Space them a few minutes apart (recommended)?", True)
        if wait:
            io.say("Sending with gaps. Keep this window open…")
        r = a.send_due(wait=wait, force_window=force)
        io.good(f"Sent {r['sent']} new and {r['followups']} follow-ups. {a.remaining_today()} left today.")
        if r["skipped_reason"]:
            io.warn(r["skipped_reason"])
        for e in r["errors"]:
            io.warn(e)
        if a.mailer.dry_run:
            io.dim("Practice mode: open the .eml files in ./outbox to see them.")

    def do_check(self):
        try:
            r = self.a.check_replies()
            self.io.good(f"{r['replied']} new repl{'y' if r['replied'] == 1 else 'ies'}, {r['bounced']} bounce(s).")
        except Exception as e:
            self.io.warn(f"Couldn't check Gmail: {e}")

    def do_threads(self):
        rows = self.a.db.q("""SELECT c.*, j.title jt FROM contacts c JOIN jobs j ON j.id=c.job_id
                              WHERE c.status IN ('active','replied','bounced','done')
                              ORDER BY c.status='replied' DESC, c.last_sent_at DESC LIMIT 40""")
        if not rows:
            self.io.dim("No conversations yet.")
        for c in rows:
            self.io.say(f"  {c['status']:8} {c['name'][:22]:22} {c['email'][:32]:32} follow-ups {c['followups_sent']}  {c['jt'][:36]}")
        if rows and self.io.yes("Mark someone as replied (e.g. they answered on LinkedIn)?", False):
            name = self.io.ask("Their email")
            row = self.a.db.one("SELECT id FROM contacts WHERE lower(email)=lower(?)", (name,))
            if row:
                self.a.set_contact_status(row["id"], "replied")
                self.io.good("Marked replied. No more follow-ups to them.")

    def do_link(self):
        from leadhunter.joblink import JobLinkError
        io, a = self.io, self.a
        url = io.ask("Paste the job or project link (Enter to type it in by hand)")
        try:
            if url:
                jid, m = a.add_link(url)
            else:
                raise JobLinkError("")
        except JobLinkError as e:
            if str(e):
                io.warn(str(e))
            kind = "freelance" if a.cfg.lead_kinds == ["freelance"] else io.choose(
                "Is it a job or a freelance project?", [("job", "Job"), ("freelance", "Freelance project")], 1)
            company = io.ask("Company (or client)", "")
            title = io.ask("Title")
            if not title:
                return
            jid, m = a.add_manual(company, title, io.ask("Location", "Remote"), url,
                                  io.ask("Company email domain, e.g. acme.com (optional)", ""), kind=kind,
                                  budget=io.ask("Budget, e.g. $500 or $25-40/hr (optional)", "") if kind == "freelance" else "",
                                  contact_email=io.ask("Email to contact, if the post gives one (optional)", ""))
        io.good(f"Added: fit {m['fit'] or 'weak'}, matched {', '.join(m['matched']) or 'no skills'}")
        self.lead_detail(jid)

    def do_checklist(self):
        print_checklist(self.io, self.a.cfg)

    def do_export(self):
        path = self.io.ask("Save as", "leads.csv")
        n = self.a.export_csv(path)
        self.io.good(f"Saved {n} leads to {path}")

    def do_setup(self):
        cfg = run_setup(self.config_path, self.env_path, self.io)
        self.a.reload(cfg)
