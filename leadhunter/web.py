"""Local dashboard (http://127.0.0.1:8765). Server-rendered, no JavaScript build step.

First visit shows the setup wizard: what you're here for, your resume, Gmail, LinkedIn, keys.
"""
from __future__ import annotations

import html
import json
import re
import threading
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from leadhunter.agent import Agent
from leadhunter.checklist import FREE_SOURCES, checklist, summary
from leadhunter.config import MODES, Config, load_config, save_config, save_env
from leadhunter.insights import nav_counts
from leadhunter.keyhealth import check_all, problems, record, telegram_chat_id
from leadhunter.joblink import JobLinkError
from leadhunter.mailer import Mailer
from leadhunter.resume import extract_profile, read_text
from leadhunter.terminal import AI_KEYS, AI_MODELS, SEARCH_KEYS
from leadhunter.theme import STATIC, href, meter, page, win

e = html.escape

base_page = page  # create_app wraps it to add the tab badges and practice-mode tag


def back(url: str, msg: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    return RedirectResponse(f"{url}{sep}msg={quote(msg)}" if msg else url, status_code=303)


def _list(s: str) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}  # testserver: FastAPI's TestClient
_URL = re.compile(r"https://[\w./-]+[\w/]")


def linkify(text: str) -> str:
    """Escape text and make its https:// links clickable."""
    return _URL.sub(lambda m: f'<a href="{m[0]}" target="_blank" rel="noopener">{m[0]}</a>', e(text))


def create_app(cfg: Config, agent: Agent | None = None, config_path: str | None = None, env_path: str = ".env") -> FastAPI:
    app = FastAPI(title="LeadHunter", docs_url=None, redoc_url=None)
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.middleware("http")
    async def local_only(request, call_next):
        """Other websites must not drive this dashboard. Blocks DNS rebinding (wrong Host) and
        cross-site form posts (Origin/Referer from another site), so a page you visit can't send emails or change keys."""
        host = request.headers.get("host") or ""
        if host.rsplit(":", 1)[0] not in LOCAL_HOSTS:
            return PlainTextResponse("Artemis only answers on 127.0.0.1.", status_code=403)
        src = request.headers.get("origin") or request.headers.get("referer")
        if request.method != "GET" and src and urlparse(src).netloc != host:
            return PlainTextResponse("Blocked a form sent from another website.", status_code=403)
        return await call_next(request)
    a = agent or Agent(cfg)
    state = {"cfg": cfg}

    def C() -> Config:
        return state["cfg"]

    def page(title: str, body: str, flash: str = "", nav: bool = True) -> HTMLResponse:  # noqa: F811
        return base_page(title, body, flash, nav, badges=nav_counts(a.db) if nav else None,
                         practice=C().outreach_enabled and bool(C()["mail"].get("dry_run", True)), setup_done=setup_done(),
                         alerts=[(msg, step) for _, msg, step in problems(C())] if nav else None)

    def setup_done() -> bool:
        """All required checklist steps done: the Get started tab is no longer needed."""
        done, total = summary(checklist(C()))
        return done == total

    def not_found(path: str) -> HTMLResponse:
        body = f"""<section class="lost"><div><span class="label">404</span><h1>This page isn't here.</h1>
          <p class="muted">The link may be old, or the lead was removed when you clicked Not interested.</p><code>{e(path)}</code>
          <div class="row"><a class="btn" href="/">Go to Leads</a><a class="btn quiet" href="/dashboard">Dashboard</a></div></div>
          <div><img class="dk" src="/assets/n404-dark.png" alt=""><img class="lt" src="/assets/n404-light.png" alt=""></div></section>"""
        r = page("Not found", body)
        r.status_code = 404
        return r

    from starlette.exceptions import HTTPException

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        if exc.status_code == 404:
            return not_found(request.url.path)
        return HTMLResponse(e(str(exc.detail)), status_code=exc.status_code)

    def save(cfg_: Config) -> None:
        if config_path:
            save_config(cfg_, config_path)
            cfg_ = load_config(config_path, env_path)
        state["cfg"] = cfg_
        a.reload(cfg_)

    def saved(name: str) -> str:
        """Label hint for a secret field: shows only the last 4 characters of what's in .env."""
        v = C().secret(name)
        return f"(saved ····{e(v[-4:])}, leave empty to keep)" if v else ""

    def needs_setup() -> bool:
        return bool(config_path) and (not Path(config_path).exists() or not C()["profile"].get("name"))

    def stats_html() -> str:
        s = a.db.stats()
        items = [("Jobs", s["jobs"]), ("Freelance", s["freelance"]), ("Matches", s["matched"])]
        if C().outreach_enabled:
            items += [("Drafts", s["drafts"]), ("Send by hand", s["manual"]),
                      ("Sent today", f"{a.sent_today()}/{C().daily_limit}"), ("Waiting reply", s["active"]), ("Replied", s["replied"])]
        bars = {"Sent today": meter(a.sent_today(), C().daily_limit)}
        return '<div class="stats">' + "".join(f'<div class="stat"><b>{e(str(v))}</b><span>{k}</span>{bars.get(k, "")}</div>' for k, v in items) + "</div>"

    # ================================================================ setup wizard
    def steps_bar(n: int) -> str:
        names = ["Goal", "About you", "Gmail & LinkedIn", "Power-ups"]
        return '<nav class="steps">' + "".join(f'<a href="/setup?step={i + 1}" class="{"on" if i + 1 == n else ""}"><small>0{i + 1}</small>{x}</a>'
                                              for i, x in enumerate(names)) + "</nav>"

    @app.get("/setup", response_class=HTMLResponse)
    def setup(step: int = 1, msg: str = ""):
        c, p = C(), C()["profile"]
        kinds = c.lead_kinds
        if step == 1:
            goals = "".join(f"""<label class="goal"><div class="pic" style="background-image:url(/assets/card-{pic}.png)"></div>
                <div><input type="radio" name="mode" value="{m}" {'checked' if c.mode == m else ''}><b>{t}</b><br>
                <span class="muted">{e(MODES[m].split(':', 1)[-1].strip())}</span></div></label>"""
                            for m, t, pic in (("jobs", "Job hunting", "eagle"), ("freelance", "Freelance clients", "parrot"),
                                              ("both", "Both", "dove"), ("leads", "Just leads", "crow")))
            lt = c.get("lead_types") or ["job", "freelance"]
            body = f"""{steps_bar(1)}<div class="card"><h1>What are you here for?</h1>
              <form method="post" action="/setup/1"><div class="goals">{goals}</div>
              <p class="muted">Just leads? Pick which:
                <label style="display:inline"><input type="checkbox" name="lead_job" {'checked' if 'job' in lt else ''}> Jobs</label>
                <label style="display:inline"><input type="checkbox" name="lead_freelance" {'checked' if 'freelance' in lt else ''}> Freelance projects</label></p>
              <button>Next</button></form></div>"""
        elif step == 2:
            jobs = f"""<div class="grid2"><label><span>Roles you want</span><input type="text" name="roles" value="{e(', '.join(p.get('roles') or []))}" placeholder="Backend Engineer, AI Engineer"></label>
                <label><span>Cities (add Remote if you like)</span><input type="text" name="locations" value="{e(', '.join(p.get('locations') or []))}" placeholder="Hyderabad, Remote"></label></div>""" if "job" in kinds else ""
            free = f"""<div class="grid2"><label><span>Lowest hourly rate ({e(p.get('currency') or 'USD')})</span><input type="number" step="any" name="min_hourly" value="{p.get('min_hourly') or ''}"></label>
                <label><span>Smallest fixed project ({e(p.get('currency') or 'USD')})</span><input type="number" step="any" name="min_fixed" value="{p.get('min_fixed') or ''}"></label></div>
                <label><span>One line about you, for proposals</span><input type="text" name="pitch" value="{e(p.get('pitch') or '')}" placeholder="I build fast Python backends and AI chatbots"></label>""" if "freelance" in kinds else ""
            have = f'<p class="muted">Current resume: {e(Path(p["resume_path"]).name)}</p>' if p.get("resume_path") else ""
            body = f"""{steps_bar(2)}<div class="card"><h1>About you</h1>
              <form method="post" action="/setup/resume" enctype="multipart/form-data" class="row">
                <input type="file" name="file" accept=".pdf,.docx,.txt" class="grow"><button>Read my resume</button></form>{have}
              <p class="muted">Your resume stays on this computer. Reading it fills in your skills below.</p>
              <form method="post" action="/setup/2">
              <div class="grid2"><label><span>Your full name</span><input type="text" name="name" value="{e(p.get('name') or '')}" required></label>
                <label><span>Years of experience</span><input type="number" step="0.5" name="years" value="{p.get('years') or 0}"></label></div>
              <label><span>Skills (these decide your matches)</span><input type="text" name="skills" value="{e(', '.join(p.get('skills') or []))}"></label>
              {jobs}{free}
              <div class="grid2"><label><span>Portfolio / GitHub (optional)</span><input type="url" name="portfolio_url" value="{e(p.get('portfolio_url') or '')}"></label>
                <label><span>Phone for signature (optional)</span><input type="text" name="phone" value="{e(p.get('phone') or '')}"></label></div>
              <label><span>Projects you've built (one per line: name | link | what it does)</span>
                <textarea name="projects" style="min-height:90px" placeholder="SignAI | https://signai.in | AI recruitment ERP: candidate matching, pipelines, placements, invoicing">{e(chr(10).join(p.get('projects') or []))}</textarea></label>
              <p class="muted">Emails and proposals mention the project that best fits each post, with its link, as proof you've done similar work.</p>
              <button>Next</button> <a class="btn quiet" href="/setup?step=1">Back</a></form></div>"""
        elif step == 3:
            gmail = f"""<h2>Gmail</h2><p class="muted">Emails go out from your own Gmail. Create an App Password at
              <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener">myaccount.google.com/apppasswords</a>
              (needs 2-Step Verification). This is <b>not</b> your Gmail password: it's a separate 16-letter code Google makes
              just for this app, which you can delete any time. It's saved only in .env on this computer.</p>
              <div class="grid2"><label><span>Gmail address</span><input type="email" name="from_address" value="{e(c['mail'].get('from_address') or '')}"></label>
              <label><span>App password {saved("GMAIL_APP_PASSWORD")}</span><input type="password" name="app_password" autocomplete="off"></label></div>
              <label><input type="checkbox" name="dry_run" {'checked' if c['mail'].get('dry_run', True) else ''}> Practice mode: save emails to ./outbox instead of sending</label>
              <button name="test" value="1" class="quiet">Save and test the connection</button>""" if c.outreach_enabled else '<p class="muted">Leads-only mode doesn\'t send email.</p>'
            body = f"""{steps_bar(3)}<div class="card"><form method="post" action="/setup/3">{gmail}
              <h2 style="margin-top:18px">LinkedIn</h2>
              <p class="muted">Artemis never logs into LinkedIn or sends messages there (LinkedIn bans automation). It writes the
              connection note and message and opens the profile, so you send each in one click.</p>
              <label><span>Your LinkedIn profile URL (for your signature)</span><input type="url" name="linkedin_url" value="{e(p.get('linkedin_url') or '')}"></label>
              <label><input type="checkbox" name="li_drafts" {'checked' if c['linkedin'].get('drafts', True) else ''}> Write LinkedIn notes for the people it finds</label>
              <button>Next</button> <a class="btn quiet" href="/setup?step=2">Back</a></form></div>"""
        else:
            sp = c["search"].get("provider") or "none"
            ai = c["ai"].get("provider") or "none"
            sopts = "".join(f'<option value="{v}" {"selected" if sp == v else ""}>{t}</option>' for v, t in
                            (("brave", "Brave Search API (free tier)"), ("tavily", "Tavily (free tier)"), ("searxng", "My SearXNG server"), ("none", "Skip")))
            aopts = "".join(f'<option value="{v}" {"selected" if ai == v else ""}>{t}</option>' for v, t in
                            (("gemini", "Google Gemini (free tier)"), ("groq", "Groq (free tier)"), ("ollama", "Ollama, offline"), ("openai", "OpenAI"), ("none", "No AI, templates")))
            search = f"""<h2>Web search</h2><p class="muted">Finds the recruiter and hiring manager for each job. Keys:
              <a href="https://brave.com/search/api/" target="_blank" rel="noopener">Brave</a> or <a href="https://tavily.com" target="_blank" rel="noopener">Tavily</a>.</p>
              <div class="grid2"><label><span>Provider</span><select name="search">{sopts}</select></label>
              <label><span>API key {saved(SEARCH_KEYS[sp][0]) if sp in SEARCH_KEYS else ""}</span><input type="password" name="search_key" autocomplete="off"></label></div>
              <label><span>SearXNG URL (only for SearXNG)</span><input type="text" name="searxng_url" value="{e(c['search'].get('searxng_url') or '')}"></label>""" if "job" in c.lead_kinds and c.outreach_enabled else ""
            limit = f"""<label><span>Emails per day, including follow-ups (10 is safe, max 20)</span><input type="number" name="daily_limit" min="1" max="20" value="{c.daily_limit}"></label>""" if c.outreach_enabled else ""
            telegram = f"""<h2>Telegram alerts (optional)</h2><p class="muted">A message on your phone when someone replies.
              1. Make a bot with <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> (send /newbot) and paste its token.
              2. Send your new bot any message, like "hi". 3. Click Finish: Artemis finds your chat ID by itself.</p>
              <div class="grid2"><label><span>Bot token {saved("TELEGRAM_BOT_TOKEN")}</span><input type="password" name="tg_token" autocomplete="off"></label>
              <label><span>Your chat ID (found for you, leave empty)</span><input type="text" name="tg_chat" value="{e(c.secret('TELEGRAM_CHAT_ID'))}"></label></div>""" if c.outreach_enabled else ""
            hunter = f"""<h2>Hunter.io (optional)</h2><p class="muted">Finds the email format each company uses, so fewer emails bounce.
              Free key at <a href="https://hunter.io/api-keys" target="_blank" rel="noopener">hunter.io/api-keys</a>.</p>
              <label><span>Hunter API key {saved("HUNTER_API_KEY")}</span><input type="password" name="hunter_key" autocomplete="off"></label>""" \
                if "job" in c.lead_kinds and c.outreach_enabled else ""
            reddit = f"""<h2>Reddit (optional)</h2><p class="muted">Adds [Hiring] posts from r/forhire. At
              <a href="https://www.reddit.com/prefs/apps" target="_blank" rel="noopener">reddit.com/prefs/apps</a> click "create another app",
              pick "script", redirect URL http://localhost. The client ID is the short code under the app name.</p>
              <div class="grid2"><label><span>Client ID</span><input type="text" name="reddit_id" value="{e(c.secret('REDDIT_CLIENT_ID'))}"></label>
              <label><span>Secret {saved("REDDIT_CLIENT_SECRET")}</span><input type="password" name="reddit_secret" autocomplete="off"></label></div>""" \
                if "freelance" in c.lead_kinds else ""
            body = f"""{steps_bar(4)}<div class="card"><form method="post" action="/setup/4">{search}
              <h2>AI writing (optional)</h2><p class="muted">Better, more personal emails and proposals. Keys:
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener">Gemini</a>, <a href="https://console.groq.com/keys" target="_blank" rel="noopener">Groq</a>.</p>
              <div class="grid2"><label><span>Provider</span><select name="ai">{aopts}</select></label>
              <label><span>API key {saved(AI_KEYS[ai][0]) if ai in AI_KEYS else ""}</span><input type="password" name="ai_key" autocomplete="off"></label></div>
              <label><span>Model (empty = recommended default)</span><input type="text" name="model" value="{e(c['ai'].get('model') or '')}"></label>
              {hunter}{reddit}{limit}{telegram}<button>Finish</button> <a class="btn quiet" href="/setup?step=3">Back</a></form></div>"""
        if not needs_setup():
            body += '<p class="muted">Pick which job boards and lists to search on <a href="/sources">Lead sources</a>. See every key and what it does on <a href="/start">What&rsquo;s connected</a>.</p>'
        return page("Setup", body, msg, nav=not needs_setup())

    @app.post("/setup/1")
    def setup1(mode: str = Form("jobs"), lead_job: str = Form(""), lead_freelance: str = Form("")):
        c = C()
        c["mode"] = mode if mode in MODES else "jobs"
        c["lead_types"] = [k for k, on in (("job", lead_job), ("freelance", lead_freelance)) if on] or ["job", "freelance"]
        save(c)
        return back("/setup?step=2")

    @app.post("/setup/resume")
    async def setup_resume(file: UploadFile = File(...)):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".pdf", ".docx", ".txt"):
            return back("/setup?step=2", "Upload a PDF, Word (.docx) or text file.")
        data = await file.read()
        if len(data) > 5 * 1024 * 1024:
            return back("/setup?step=2", "That file is over 5 MB.")
        dest = Path(f"resume{suffix}").resolve()
        dest.write_bytes(data)
        c = C()
        c["profile"]["resume_path"] = str(dest)
        try:
            text = read_text(dest)
            found = extract_profile(text)
            if found["skills"]:
                c["profile"]["skills"] = found["skills"]
            if found["years"]:
                c["profile"]["years"] = found["years"]
            first = next((l.strip() for l in text.splitlines() if l.strip()), "")
            if not c["profile"].get("name") and 1 < len(first.split()) <= 4 and not any(ch.isdigit() for ch in first):
                c["profile"]["name"] = first.title() if first.isupper() else first
            msg = f"Read your resume: {len(found['skills'])} skills found. Check them below."
        except Exception as ex:
            msg = f"Saved, but couldn't read the text ({ex}). Type your skills below."
        save(c)
        return back("/setup?step=2", msg)

    @app.post("/setup/2")
    def setup2(name: str = Form(...), years: float = Form(0), skills: str = Form(""), roles: str = Form(""),
               locations: str = Form(""), min_hourly: str = Form(""), min_fixed: str = Form(""), pitch: str = Form(""),
               portfolio_url: str = Form(""), phone: str = Form(""), projects: str = Form("")):
        c = C()
        p = c["profile"]
        p.update(name=name.strip(), years=years, skills=_list(skills), portfolio_url=portfolio_url.strip(), phone=phone.strip())
        if "job" in c.lead_kinds:
            p.update(roles=_list(roles), locations=_list(locations))
        if "freelance" in c.lead_kinds:
            p.update(min_hourly=float(min_hourly or 0), min_fixed=float(min_fixed or 0), pitch=pitch.strip())
        p["projects"] = [x.strip()[:400] for x in projects.splitlines() if x.strip()][:20]
        save(c)
        return back("/setup?step=3")

    @app.post("/setup/3")
    def setup3(from_address: str = Form(""), app_password: str = Form(""), dry_run: str = Form(""),
               linkedin_url: str = Form(""), li_drafts: str = Form(""), test: str = Form("")):
        c = C()
        c["profile"]["linkedin_url"] = linkedin_url.strip()
        c["linkedin"]["drafts"] = bool(li_drafts)
        if c.outreach_enabled:
            c["mail"]["from_address"] = from_address.strip()
            c["mail"]["dry_run"] = bool(dry_run)
            if app_password.strip():
                save_env({"GMAIL_APP_PASSWORD": app_password.replace(" ", "").strip()}, env_path)
        save(c)
        if test:
            ok, msg = Mailer(C()).test_login()
            return back("/setup?step=3", ("✓ " if ok else "") + msg)
        return back("/setup?step=4")

    @app.post("/setup/4")
    def setup4(search: str = Form("none"), search_key: str = Form(""), searxng_url: str = Form(""),
               ai: str = Form("none"), ai_key: str = Form(""), model: str = Form(""), daily_limit: int = Form(10),
               tg_token: str = Form(""), tg_chat: str = Form(""), hunter_key: str = Form(""),
               reddit_id: str = Form(""), reddit_secret: str = Form("")):
        c = C()
        secrets = {}
        if tg_token.strip():
            if not re.fullmatch(r"\d{5,}:[\w-]{30,}", tg_token.strip()):
                return back("/setup?step=4", "! That isn't a full bot token. Copy the whole line from @BotFather, like 123456789:AAH... (with the colon).")
            secrets["TELEGRAM_BOT_TOKEN"] = tg_token.strip()
        if tg_chat.strip() and not tg_chat.strip().lstrip("-").isdigit():
            return back("/setup?step=4", "The Telegram chat ID is a number, like 123456789.")
        token = secrets.get("TELEGRAM_BOT_TOKEN") or c.secret("TELEGRAM_BOT_TOKEN")
        chat = tg_chat.strip() or c.secret("TELEGRAM_CHAT_ID")
        tg_note = ""
        if token and chat == token.split(":")[0]:  # the bot's own number: Telegram refuses "bot to bot"
            chat = ""
        if token and not chat:
            chat = telegram_chat_id(token)
            tg_note = "" if chat else " To finish Telegram: send your bot any message in Telegram, then click Finish again."
        if chat and chat != c.secret("TELEGRAM_CHAT_ID"):
            secrets["TELEGRAM_CHAT_ID"] = chat
        if hunter_key.strip():
            secrets["HUNTER_API_KEY"] = hunter_key.strip()
            c["email_finder"]["hunter"] = True
        for k, v in (("REDDIT_CLIENT_ID", reddit_id), ("REDDIT_CLIENT_SECRET", reddit_secret)):
            if v.strip():
                secrets[k] = v.strip()
        if reddit_id.strip() or reddit_secret.strip():
            c["sources"]["reddit"] = True
        if search in ("brave", "tavily", "searxng", "none"):
            c["search"]["provider"] = search
            if search in SEARCH_KEYS and search_key.strip():
                secrets[SEARCH_KEYS[search][0]] = search_key.strip()
            if searxng_url.strip():
                c["search"]["searxng_url"] = searxng_url.strip()
        if ai in ("gemini", "groq", "ollama", "openai", "none"):
            if ai != c["ai"].get("provider"):
                c["ai"]["model"] = ""
            c["ai"]["provider"] = ai
            c["ai"]["model"] = model.strip() or c["ai"].get("model") or AI_MODELS.get(ai, "")
            c["ai"]["base_url"] = ""
            if ai in AI_KEYS and ai_key.strip():
                secrets[AI_KEYS[ai][0]] = ai_key.strip()
        c["outreach"]["daily_limit"] = max(1, min(int(daily_limit or 10), 20))
        if secrets:
            save_env(secrets, env_path)
        save(c)
        if secrets.keys() & {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
            record(C(), "telegram", None)  # new details: the old warning no longer applies; the next use or check re-tests them
        if setup_done():
            return back("/dashboard", "Saved. Everything needed is set up." + tg_note)
        return back("/start", "Saved. Here's what's connected and what's left." + tg_note)

    # ================================================================ get started
    @app.get("/start", response_class=HTMLResponse)
    def start(msg: str = ""):
        c = C()
        items = checklist(c)
        done, total = summary(items)
        badge = {"required": "Required", "recommended": "Recommended", "optional": "Optional"}
        cards = ""
        for n, i in enumerate(items, 1):
            word = "Done" if i["key"] in ("profile", "practice") else "Connected"
            state = f'<span class="pill {word}">{word} ✓</span>' if i["done"] else f'<span class="pill {badge[i["level"]]}">{badge[i["level"]]}</span>'
            steps = "".join(f"<li>{linkify(s)}</li>" for s in i["steps"])
            if i.get("problem"):  # set, but stopped working
                state = '<span class="pill failed">Key problem</span>'
                steps = f"<li>{e(i['problem'])}. Make a new key and paste it in setup.</li>"
            link = f'<a class="btn" href="{e(i["link"])}" target="_blank" rel="noopener">Open {e(i["link"].split("/")[2])}</a>' if i["link"] else ""
            env = f'<p class="muted">Saved as <code>{e(i["env"])}</code> in .env on your computer.</p>' if i["env"] else ""
            step = 3 if i["key"] in ("gmail", "practice") else 4 if i["key"] in ("search", "ai", "hunter", "reddit", "telegram") else 2
            setup_link = f'<a class="btn quiet" href="/setup?step={step}">Enter it in setup</a>'
            cards += f"""<div class="card"><div class="row"><h2 style="margin-right:auto">{n}. {e(i['name'])}</h2>{state}</div>
              <p>{e(i['why'])}</p>{'' if i['done'] else f'<ol>{steps}</ol>'}{env if not i['done'] else ''}
              <div class="row">{'' if i['done'] else link}{setup_link}</div></div>"""
        free = "".join(f"<li>{e(s)}</li>" for s in FREE_SOURCES)
        name = (c["profile"].get("name") or "").split(" ")[0]
        body = f"""<section class="hero"><div class="art"><img src="/assets/banner-get-started.png" alt=""></div><div class="in">
          <span class="label">Get started</span><h1>{f"Welcome back, {e(name)}." if name else "Welcome."}</h1>
          <p>Mode: <b>{e(MODES[c.mode])}</b> · <a href="/setup">change</a></p>
          <p><span class="money">{done} / {total} required</span> · {'You can start finding leads now.' if done == total else 'Finish the required steps below.'}</p>
          {meter(done, total)}
          <p class="muted">These leads work with no key at all:</p><ul class="muted">{free}</ul>
          <div class="row"><a class="btn" href="/">Go to my leads →</a><a class="btn quiet" href="/setup">Open setup</a>
          <form method="post" action="/keys/check"><button class="quiet">Check all keys now</button></form></div></div></section>
          <h2>What's connected</h2>{cards}
          <p class="muted">Keys and passwords stay in the .env file on your computer and are never sent anywhere except to that service.</p>"""
        return page("Get started", body, msg)

    # ================================================================ leads
    @app.get("/", response_class=HTMLResponse)
    def home(msg: str = "", fit: str = "", kind: str = ""):
        if needs_setup():
            return back("/setup")
        c = C()
        items = checklist(c)
        todo = [i["name"] for i in items if not i["done"] and i["level"] in ("required", "recommended")]
        warn = [f"Still to set up: {', '.join(todo)}."] if todo else []
        kinds = [kind] if kind in ("job", "freelance") else c.lead_kinds
        where = [f"kind IN ({','.join('?' * len(kinds))})", "status!='ignored'", "(fit!='' OR status!='new')"]
        args: list = list(kinds)
        if fit:
            where.append("fit=?")
            args.append(fit)
        rows = a.db.q(f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY (status='shortlisted') DESC, (fit='strong') DESC, score DESC, id DESC LIMIT 100", tuple(args))
        label = "Reach out" if c.outreach_enabled else "Details"

        def money(j):
            if j["kind"] != "freelance":
                return ""
            q = f'<br><span class="muted">quote ~{e(j["quote"])}</span>' if j["quote"] else ""
            return f'<span class="money">{e(j["budget"] or "budget n/a")}</span>{q}'

        trs = "".join(
            f"""<tr><td><a href="/job/{j['id']}">{e(j['title'])}</a><br><span class="muted">{e(j['company'] or ('Freelance project' if j['kind'] == 'freelance' else '?'))}</span></td>
            <td><span class="pill {e(j['fit'] or 'weak')}">{e(j['fit'] or 'weak')}</span><br>{money(j)}</td>
            <td>{' '.join(f'<mark>{e(m)}</mark>' for m in json.loads(j['matched'] or '[]')[:5])}</td>
            <td class="muted">{e(j['location'] or '')}<br>{e(j['source'])}{' · email in post' if j['contact_email'] else ''}</td>
            <td><a class="btn" href="/job/{j['id']}">{'Open' if j['status'] == 'researched' else label}</a></td></tr>"""
            for j in rows)
        tabs = "".join(f'<a href="/?kind={k}" class="{"on" if kind == k else ""}">{t}</a>' for k, t in (("", "All"), ("job", "Jobs"), ("freelance", "Freelance"))
                       if not k or k in c.lead_kinds)
        filters = "".join(f'<a href="/?fit={f}{"&kind=" + kind if kind else ""}" class="{"on" if fit == f else ""}">{f.title()}</a>' for f in ("strong", "good", "stretch"))
        find = f"""<div class="row"><form method="post" action="/collect"><button>Find new leads</button></form>
          <a class="btn quiet" href="/export.csv">Export CSV</a><span class="muted">{e(MODES[c.mode])}</span></div><br>
          <form method="post" action="/add" class="row"><input class="grow" type="url" name="url" placeholder="…or paste a job / project link (Greenhouse, Lever, Ashby, careers page, HN)">
          <button>Add</button></form>
          <details><summary class="muted">Link won't open? Add it by hand</summary><br>
          <form method="post" action="/add-manual" class="grid2">
            <select name="kind"><option value="job">Job</option><option value="freelance">Freelance project</option></select>
            <input type="text" name="title" placeholder="Title" required>
            <input type="text" name="company" placeholder="Company or client">
            <input type="text" name="location" placeholder="City, e.g. Hyderabad">
            <input type="text" name="domain" placeholder="Email domain, e.g. accenture.com (optional)">
            <input type="text" name="budget" placeholder="Budget, e.g. $500 or $25-40/hr (freelance)">
            <input type="email" name="contact_email" placeholder="Email from the post (optional)">
            <input type="url" name="url" placeholder="Link (optional)"><button>Add lead</button></form></details>"""
        notes = "".join(f'<div class="note"><p>{e(w)} <a href="/start">Get started</a></p></div>' for w in warn)
        body = f"""{stats_html()}{notes}{win("Find leads", find)}
        <div class="card"><div class="row"><h2 style="margin-right:auto">Matches <span class="money muted">{len(rows)}</span></h2>
          <div class="chips">{tabs}</div><div class="chips">{filters}</div></div><br>
        <table><tr><th>Lead</th><th>Fit</th><th>Your skills it mentions</th><th>Where</th><th></th></tr>{trs or '<tr><td colspan=5 class="muted">No leads yet. Click “Find new leads” or paste a link.</td></tr>'}</table></div>"""
        return page("Leads", body, msg)

    @app.get("/export.csv")
    def export_csv(kind: str = "", fit: str = ""):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        a.export_csv(path, kind, fit)
        data = Path(path).read_bytes()
        os.unlink(path)
        return Response(data, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="leads.csv"'})

    @app.post("/collect")
    def collect():
        r = a.collect()
        return back("/", f"Checked {r['fetched']} leads: {r['new']} new, {r['new_good_matches']} strong/good matches."
                    + (f" Some sources failed: {'; '.join(r['errors'])[:300]}" if r["errors"] else ""))

    @app.post("/add")
    def add(url: str = Form(...)):
        try:
            jid, m = a.add_link(url)
        except JobLinkError as ex:
            return back("/", str(ex))
        return back(f"/job/{jid}", f"Added. Fit: {m['fit'] or 'weak'}.")

    @app.post("/add-manual")
    def add_manual(title: str = Form(...), company: str = Form(""), location: str = Form(""), domain: str = Form(""),
                   url: str = Form(""), kind: str = Form("job"), budget: str = Form(""), contact_email: str = Form("")):
        jid, m = a.add_manual(company, title, location, url, domain, kind=kind, budget=budget, contact_email=contact_email)
        return back(f"/job/{jid}", "Added.")

    @app.get("/job/{jid}", response_class=HTMLResponse)
    def job(jid: int, msg: str = ""):
        j = a.db.job(jid)
        if not j:
            return not_found(f"/job/{jid}")
        c = C()
        free = j["kind"] == "freelance"
        contacts = a.db.q("SELECT * FROM contacts WHERE job_id=? ORDER BY score DESC", (jid,))
        cards = ""
        for ct in contacts:
            m = a.db.one("SELECT * FROM messages WHERE contact_id=? AND kind='initial'", (ct["id"],))
            if m and m["status"] in ("draft", "approved"):
                draft = f"""<form method="post" action="/message/{m['id']}/save">
                  <p><input type="text" name="subject" value="{e(m['subject'])}"></p>
                  <p><textarea name="body">{e(m['body'])}</textarea></p>
                  <div class="row"><button name="approve" value="1">{'Approved ✓ (save)' if m['status'] == 'approved' else 'Approve email'}</button>
                  <button class="quiet" name="approve" value="0">Save draft</button>
                  <button class="quiet" formaction="/message/{m['id']}/skip">Don't email</button></div></form>"""
            elif m:
                draft = f'<p class="muted">Email {e(m["status"])}{" on " + e(m["sent_at"][:16]) if m["sent_at"] else ""}. Follow-ups sent: {ct["followups_sent"]}.</p>'
            else:
                draft = '<p class="muted">No email address. Add one below if you know it, or use LinkedIn.</p>'
            li = ""
            for lm in a.db.q("SELECT * FROM messages WHERE contact_id=? AND channel='linkedin' ORDER BY seq", (ct["id"],)):
                li += f"""<div style="margin-top:14px"><b>LinkedIn · {e(lm['subject'])}</b> <span class="pill {e(lm['status'])}">{e(lm['status'])}</span>
                  <pre class="copy" id="li{lm['id']}">{e(lm['body'])}</pre>
                  <div class="row"><button type="button" class="quiet" data-copy="li{lm['id']}">Copy</button>
                  <a class="btn quiet" href="{href(ct['linkedin_url'])}" target="_blank" rel="noopener">Open profile</a>
                  {'' if lm['status'] != 'draft' else f'<form method="post" action="/message/{lm["id"]}/done"><button class="quiet">I sent it</button></form>'}</div></div>"""
            prof = f' · <a href="{href(ct["linkedin_url"])}" target="_blank" rel="noopener">LinkedIn</a>' if ct["linkedin_url"] else ""
            replied = (f'<div class="note"><div><b>They replied {e(ct["reply_at"][:10])}</b> <span class="pill {e(ct["sentiment"])}">{e(ct["sentiment"])}</span>'
                       f'<br>“{e(ct["reply_preview"] or "")}”</div></div>') if ct["reply_at"] else ""
            cards += f"""<div class="card"><h2>{e(ct['name'])} <span class="pill {e(ct['status'])}">{e(ct['status'])}</span></h2>
              <p class="muted">{e(ct['title'] or '')}{prof}<br>
              {e(ct['email'] or 'no email')} <span class="pill {e(ct['email_confidence'] or 'found')}">{e(ct['email_confidence'] or '-')}</span>
              · why: {e(ct['reason'] or '')}</p>{draft}{li}{replied}
              <form method="post" action="/contact/{ct['id']}/mark" class="row"><span class="muted">They answered somewhere else?</span>
              <button class="quiet" name="status" value="replied">Mark replied</button><button class="quiet" name="status" value="stopped">Stop</button></form></div>"""
        prop = ""
        for pm in a.db.q("SELECT * FROM messages WHERE job_id=? AND channel='platform'", (jid,)):
            prop += f"""<div class="card"><h2>Proposal to post on the site <span class="pill {e(pm['status'])}">{e(pm['status'])}</span></h2>
              <p class="muted">This client is contacted on {e(j['source'])}. Copy this, open the project and paste it into your bid.</p>
              <form method="post" action="/message/{pm['id']}/save"><input type="hidden" name="subject" value="{e(pm['subject'])}">
              <textarea name="body" id="pp{pm['id']}" style="min-height:260px">{e(pm['body'])}</textarea>
              <div class="row"><button class="quiet" name="approve" value="0">Save my edits</button></div></form>
              <div class="row"><button type="button" class="quiet" data-copy="pp{pm['id']}">Copy</button>
              <a class="btn quiet" href="{href(j['url'])}" target="_blank" rel="noopener">Open project</a>
              {'' if pm['status'] != 'draft' else f'<form method="post" action="/message/{pm["id"]}/done"><button class="quiet">I posted it</button></form>'}</div></div>"""
        skills = json.loads(j["matched"] or "[]")
        matched = " ".join(f"<mark>{e(x)}</mark>" for x in skills)
        why_fit = (f"""<div class="note"><div><b>Why you fit:</b> the post asks for {len(skills)} of your skills: {matched}<br>
          <span class="muted">The template email leads with the first four ({e(", ".join(skills[:4]))}); AI emails pick the 1–2 strongest.</span></div></div>"""
                   if skills else '<p class="muted">None of your listed skills appear in this post. Check the description before reaching out.</p>')
        money = f' · <span class="money">{e(j["budget"] or "budget n/a")}</span>' + (f' · suggested quote <b>{e(j["quote"])}</b>' if j["quote"] else "") if free else ""
        reach = ""
        if c.outreach_enabled:
            what = "Write proposal" if free else "Find recruiter & hiring manager"
            reach = f'<form method="post" action="/job/{jid}/research"><button>{what}</button></form>'
            if not free:
                reach += f"""<form method="post" action="/job/{jid}/domain" class="row"><input type="text" name="domain" placeholder="fix domain e.g. acme.com" style="width:200px"><button class="quiet">Save domain</button></form>"""
        people = "<h2>People to contact</h2>" + cards if cards else ""
        has_drafts = a.db.one("""SELECT 1 FROM messages WHERE job_id=? AND status='draft'
                                 AND (channel='platform' OR (channel='email' AND kind='initial'))""", (jid,))
        notes_card = f"""<form method="post" action="/job/{jid}/notes" class="card"><h2>Your notes for this one</h2>
          <p class="muted">Tell Artemis what to say: a similar project you did, why you want this, a question for them.
          Your words are used when writing the {'proposal' if free else 'email'}.</p>
          <textarea name="notes" style="min-height:90px" placeholder="e.g. Mention SignAI: I built its AI candidate matching. Offer a short paid discovery call first.">{e(j['notes'] or '')}</textarea>
          <div class="row"><button class="quiet">{'Rewrite my drafts with these notes' if has_drafts else 'Save notes'}</button></div></form>""" if c.outreach_enabled else ""
        body = f"""<p><a href="/" class="muted">← Leads</a></p><div class="card"><h1>{e(j['title'])}</h1>
          <p>{e(j['company'] or ('Freelance client' if free else '?'))} · {e(j['location'] or 'location n/a')} · <span class="pill {e(j['fit'] or 'weak')}">{e(j['fit'] or 'weak')}</span>{money}
          {'' if free else ' · email domain: <b>' + e(j['company_domain'] or 'unknown') + '</b>'}
          {f' · email in post: <b>{e(j["contact_email"])}</b>' if j['contact_email'] else ''}
          {f' · <a href="{href(j["url"])}" target="_blank" rel="noopener">Open posting</a>' if j['url'] else ''}</p>
          {why_fit}<div class="row">{reach}<form method="post" action="/job/{jid}/ignore"><button class="quiet">Not interested</button></form></div>
          <details><summary class="muted">Full description</summary><p style="white-space:pre-wrap">{e((j['description'] or '')[:8000])}</p></details></div>
          {notes_card}{prop}{people}
          {'' if not c.outreach_enabled else f'''<div class="card"><h2>Add a contact you found yourself</h2>
          <form method="post" action="/job/{jid}/contact" class="grid2"><input type="text" name="name" placeholder="Full name" required>
          <input type="email" name="email" placeholder="Work email" required><input type="text" name="title" placeholder="Their title (optional)">
          <input type="url" name="linkedin" placeholder="LinkedIn URL (optional)"><button>Add and draft email</button></form></div>'''}"""
        return page(j["title"], body, msg)

    @app.post("/job/{jid}/notes")
    def job_notes(jid: int, notes: str = Form("")):
        if not a.db.job(jid):
            return not_found(f"/job/{jid}")
        n = a.set_notes(jid, notes)
        how = "" if a.ai.enabled else " AI is off, so your notes were added to the template as written."
        return back(f"/job/{jid}", (f"Notes saved. Rewrote {n} draft{'s' * (n != 1)}." if n else "Notes saved. They'll be used when the draft is written.") + how)

    @app.post("/job/{jid}/research")
    def research(jid: int):
        r = a.reach_out(jid)
        parts = []
        if r["contacts"]:
            parts.append(f"Found {len(r['contacts'])} contact(s), {r['added']} new.")
        if r.get("proposal"):
            parts.append("Proposal drafted.")
        return back(f"/job/{jid}", " ".join(parts + r["notes"]) or "Nothing new found.")

    @app.post("/job/{jid}/ignore")
    def ignore(jid: int):
        a.ignore(jid)
        return back("/", "Hidden.")

    @app.post("/job/{jid}/domain")
    def set_domain(jid: int, domain: str = Form(...)):
        from leadhunter.util import registrable_domain
        a.db.run("UPDATE jobs SET company_domain=? WHERE id=?", (registrable_domain(domain), jid))
        return back(f"/job/{jid}", "Domain saved. Find contacts again to rebuild emails.")

    @app.post("/job/{jid}/contact")
    def add_contact(jid: int, name: str = Form(...), email: str = Form(...), title: str = Form(""), linkedin: str = Form("")):
        a.add_contact(jid, name, email, title, linkedin)
        return back(f"/job/{jid}", "Contact added with a draft email.")

    def _job_of_message(mid: int) -> int:
        r = a.db.one("SELECT job_id FROM messages WHERE id=?", (mid,))
        return r["job_id"] if r else 0

    @app.post("/message/{mid}/save")
    def save_msg(mid: int, subject: str = Form(...), body: str = Form(...), approve: str = Form("0")):
        if approve == "1":
            a.approve(mid, subject, body)
            a.db.run("UPDATE messages SET subject=?, body=? WHERE id=? AND status='approved'", (subject, body, mid))
            msg = f"Approved. It goes out with the next send (max {C().daily_limit}/day, working hours)."
        else:
            a.save_draft(mid, subject, body)
            msg = "Draft saved."
        return back(f"/job/{_job_of_message(mid)}", msg)

    @app.post("/message/{mid}/skip")
    def skip(mid: int):
        a.skip(mid)
        return back(f"/job/{_job_of_message(mid)}", "Skipped. This person won't be emailed.")

    @app.post("/message/{mid}/done")
    def done(mid: int, next: str = Form("")):
        a.mark_done(mid)
        return back(next if next.startswith("/") else f"/job/{_job_of_message(mid)}", "Marked as sent.")

    @app.post("/contact/{cid}/mark")
    def mark(cid: int, status: str = Form(...)):
        a.set_contact_status(cid, status if status in ("replied", "stopped") else "stopped")
        ct = a.db.contact(cid)
        return back(f"/job/{ct['job_id']}" if ct else "/threads", "Updated. No more follow-ups to this person.")

    # ================================================================ outbox / manual / threads
    @app.get("/outbox", response_class=HTMLResponse)
    def outbox(msg: str = ""):
        rows = a.db.q("""SELECT m.*, c.name, c.email, c.email_confidence, c.job_id, j.title jt, j.company FROM messages m
                         JOIN contacts c ON c.id=m.contact_id JOIN jobs j ON j.id=c.job_id
                         WHERE m.status IN ('draft','approved') AND m.channel='email' ORDER BY m.status, m.id""")
        trs = "".join(f"""<tr><td><span class="pill {e(m['status'])}">{e(m['status'])}</span></td>
            <td>{e(m['name'])}<br><span class="muted">{e(m['email'])}</span> <span class="pill {e(m['email_confidence'])}">{e(m['email_confidence'])}</span></td>
            <td><a href="/job/{m['job_id']}">{e(m['jt'])}</a><br><span class="muted">{e(m['company'])}</span></td><td>{e(m['subject'])}</td></tr>""" for m in rows)
        due = len(a.due_followups())
        inner = f"""<div class="row">
          <form method="post" action="/approve-all"><button class="quiet">Approve all drafts</button></form>
          <form method="post" action="/send"><button>Send now (up to {a.remaining_today()} left today)</button></form></div>
          <p class="muted">{due} follow-up(s) due. Follow-ups go first, then approved emails, best matches first. Emails only go out in working hours.</p>
          <table><tr><th>Status</th><th>To</th><th>Lead</th><th>Subject</th></tr>{trs or '<tr><td colspan=4 class="muted">[ ] Nothing waiting.</td></tr>'}</table>"""
        body = stats_html() + win("Emails waiting", inner)
        return page("Outbox", body, msg)

    @app.get("/manual", response_class=HTMLResponse)
    def manual(msg: str = ""):
        rows = a.db.q("""SELECT m.*, c.name, c.linkedin_url, j.title jt, j.company, j.url ju, j.source FROM messages m
                         LEFT JOIN contacts c ON c.id=m.contact_id JOIN jobs j ON j.id=m.job_id
                         WHERE m.status='draft' AND m.channel IN ('linkedin','platform') ORDER BY m.channel, m.job_id, m.seq""")
        items = ""
        for m in rows:
            where = m["linkedin_url"] if m["channel"] == "linkedin" else m["ju"]
            head = f"LinkedIn · {e(m['name'] or '')} · {e(m['subject'])}" if m["channel"] == "linkedin" else f"Proposal on {e(m['source'])}"
            items += f"""<div class="card"><h2>{head}</h2><p class="muted">For <a href="/job/{m['job_id']}">{e(m['jt'])}</a> {e(m['company'] or '')}</p>
              <pre class="copy" id="m{m['id']}">{e(m['body'])}</pre>
              <div class="row"><button type="button" class="quiet" data-copy="m{m['id']}">Copy</button>
              <a class="btn quiet" href="{href(where)}" target="_blank" rel="noopener">Open {'profile' if m['channel'] == 'linkedin' else 'project'}</a>
              <form method="post" action="/message/{m['id']}/done"><input type="hidden" name="next" value="/manual"><button class="quiet">I sent it</button></form></div></div>"""
        body = f"""<span class="label">Send by hand</span><h1>{len(rows)} message{'' if len(rows) == 1 else 's'} for you to paste and send</h1><p class="muted">LinkedIn notes and freelance proposals you send yourself, so your accounts stay safe.
          Copy, open, paste, then click “I sent it”.</p>{items or '<div class="card muted">[ ] Nothing waiting.</div>'}"""
        return page("Send by hand", body, msg)

    @app.post("/approve-all")
    def approve_all():
        ids = [r["id"] for r in a.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'")]
        for i in ids:
            a.approve(i)
        return back("/outbox", f"Approved {len(ids)} draft(s).")

    @app.post("/send")
    def send():
        r = a.send_due(wait=False, force_window=False)
        msg = f"Sent {r['sent']} new and {r['followups']} follow-ups. {a.remaining_today()} left today."
        if r["skipped_reason"]:
            msg += f" Stopped: {r['skipped_reason']}."
        if r["errors"]:
            msg += " Errors: " + "; ".join(r["errors"])[:300]
        return back("/outbox", msg)

    THREAD_FILTERS = {"": "All", "interview": "Interview", "referral": "Referral", "question": "Question",
                      "rejection": "Rejection", "active": "Waiting", "bounced": "Bounced"}

    @app.get("/threads", response_class=HTMLResponse)
    def threads(msg: str = "", show: str = ""):
        where, args = "c.status IN ('active','replied','bounced','done')", ()
        if show in ("active", "bounced"):
            where, args = "c.status=?", (show,)
        elif show in THREAD_FILTERS:
            where, args = (where + " AND c.sentiment=?", (show,)) if show else (where, ())
        rows = a.db.q(f"""SELECT c.*, j.title jt FROM contacts c JOIN jobs j ON j.id=c.job_id
                         WHERE {where} ORDER BY c.reply_read ASC, c.status='replied' DESC, COALESCE(c.reply_at, c.last_sent_at) DESC""", args)
        days = C().followup_days

        def reply(c) -> str:
            if not c["reply_at"]:
                return '<span class="muted">-</span>'
            read = "" if c["reply_read"] else f"""<form method="post" action="/contact/{c['id']}/read"><button class="quiet">Mark read</button></form>"""
            return f"""<span class="muted">{e(c['reply_at'][:10])}</span> {e(c['reply_preview'] or '')}{read}"""

        trs = "".join(f"""<tr><td>{'' if c['reply_read'] else '<span class="dot" title="New reply"></span>'}<span class="pill {e(c['status'])}">{e(c['status'])}</span>
            {f'<br><span class="pill {e(c["sentiment"])}">{e(c["sentiment"])}</span>' if c['sentiment'] else ''}</td>
            <td>{e(c['name'])}<br><span class="muted">{e(c['email'])}</span></td>
            <td><a href="/job/{c['job_id']}">{e(c['jt'])}</a><br><span class="muted">{e(c['company'])}</span></td>
            <td>{c['followups_sent']}/{len(days)} follow-ups<br><span class="muted">last {e((c['last_sent_at'] or '')[:10])}</span></td>
            <td>{reply(c)}</td></tr>""" for c in rows)
        chips = "".join(f'<a href="/threads{"?show=" + k if k else ""}" class="{"on" if show == k else ""}">{t}</a>' for k, t in THREAD_FILTERS.items())
        unread = sum(1 for c in rows if not c["reply_read"])
        body = f"""<span class="label">Conversations</span><div class="row"><h1 style="margin-right:auto">{f"{unread} new repl{'y' if unread == 1 else 'ies'}" if unread else "Your conversations"}</h1>
          <form method="post" action="/read-all"><button class="quiet">Mark all read</button></form>
          <form method="post" action="/check"><button class="quiet">Check Gmail for replies</button></form></div>
          <br><div class="card"><div class="chips">{chips}</div><br>
          <table><tr><th>Status</th><th>Person</th><th>Lead</th><th>Follow-ups</th><th>Reply</th></tr>{trs or '<tr><td colspan=5 class="muted">— No conversations match this filter.</td></tr>'}</table></div>"""
        return page("Conversations", body, msg)

    @app.post("/contact/{cid}/read")
    def read_one(cid: int):
        a.mark_read(cid)
        return back("/threads")

    @app.post("/read-all")
    def read_all():
        a.mark_read()
        return back("/threads", "All replies marked as read.")

    @app.post("/check")
    def check():
        try:
            r = a.check_replies()
            return back("/threads", f"{r['replied']} new repl{'y' if r['replied'] == 1 else 'ies'}, {r['bounced']} bounce(s).")
        except Exception as ex:
            return back("/threads", f"Couldn't check Gmail: {ex}")

    from leadhunter.web_pages import register  # local import: web_pages imports helpers from this module
    @app.post("/keys/check")
    def keys_check():
        res = check_all(C())
        if not res:
            return back("/start", "No keys entered yet.")
        bad = [f"{k}: {v}" for k, v in res.items() if v != "ok"]
        return back("/start", ("! " + "; ".join(bad)) if bad else f"✓ All {len(res)} keys work.")

    if config_path:  # the real app (not tests): check every key once in the background when the dashboard starts
        threading.Thread(target=check_all, args=(C(),), daemon=True).start()

    register(app, a, C, save, page)
    return app
