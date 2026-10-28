"""Freelance leads, modes, LinkedIn drafts, export, terminal + web onboarding."""
import csv
import sqlite3

from fastapi.testclient import TestClient

from leadhunter import freelance, sources
from leadhunter.config import load_config, save_env
from leadhunter.db import DB
from leadhunter.terminal import IO, Session, run_setup
from leadhunter.web import create_app

from conftest import fixture_json

HN_STORY = 49922569


def hn_hit(text, oid="50000001", parent=HN_STORY, author="founder42"):
    return {"comment_text": text, "parent_id": parent, "objectID": oid, "author": author,
            "created_at": "2026-10-02T10:00:00Z", "story_id": HN_STORY}


# ---------------- parsing ----------------
def test_deobfuscated_emails():
    assert freelance.find_emails("reach me: jobs [at] acme [dot] io") == ["jobs@acme.io"]
    assert freelance.find_emails("hiring(at)foo.com or ping") == ["hiring@foo.com"]
    assert freelance.find_emails("email priya at northwind dot co dot in today") == ["priya@northwind.co.in"]
    assert freelance.find_emails("<a href=\"mailto:x@y.com\">x&#x40;y.com</a>") == ["x@y.com"]


def test_hn_post_job_and_contract():
    job = freelance.parse_hn_post(hn_hit("Acme AI | Backend Engineer (Python) | Bangalore or REMOTE | Full-time<p>We use FastAPI. Email jobs [at] acme [dot] ai"), HN_STORY)
    assert job["kind"] == "job" and job["company"] == "Acme AI" and job["contact_email"] == "jobs@acme.ai"
    assert "Backend Engineer" in job["title"] and job["how_to_apply"] == "email"
    gig = freelance.parse_hn_post(hn_hit("Tiny Co | Contract React developer | REMOTE<p>3 month contract, $60/hr.", oid="2"), HN_STORY)
    assert gig["kind"] == "freelance"
    assert freelance.parse_hn_post(hn_hit("thanks!", parent=123), HN_STORY) is None   # a reply, not a post


def test_freelancer_projects_from_real_shape():
    ps = [freelance.freelancer_project(p) for p in fixture_json("freelancer_projects.json")["result"]["projects"]]
    hourly, fixed = ps
    assert hourly["budget"] == "$8–15/hr" and hourly["pay_type"] == "hourly" and hourly["how_to_apply"] == "platform"
    assert fixed["budget"] == "$250–750" and fixed["url"].startswith("https://www.freelancer.com/projects/")


def test_quote_respects_minimum():
    prefs = {"min_hourly": 20, "min_fixed": 100}
    q, below = freelance.suggest_quote({"budget_min": 8, "budget_max": 15, "pay_type": "hourly", "currency": "USD"}, prefs)
    assert below and q == "$20/hr"
    q, below = freelance.suggest_quote({"budget_min": 250, "budget_max": 750, "pay_type": "fixed", "currency": "USD"}, prefs)
    assert not below and q == "$550"


# ---------------- modes + collect ----------------
def test_collect_respects_mode(agent, cfg, monkeypatch):
    fixed = freelance.freelancer_project(fixture_json("freelancer_projects.json")["result"]["projects"][1])
    monkeypatch.setattr(sources, "remotive", lambda: [{"uid": "r1", "source": "remotive", "title": "Python Backend Engineer",
                                                       "company": "X", "location": "Remote", "url": "u", "description": "Python FastAPI AWS", "posted_at": ""}])
    monkeypatch.setattr(sources, "arbeitnow", lambda: [])
    monkeypatch.setattr(freelance, "hackernews", lambda: [])
    monkeypatch.setattr(freelance, "freelancer", lambda q: [fixed])
    cfg["mode"] = "freelance"
    r = agent.collect()
    assert r["fetched"] == 1
    row = agent.db.one("SELECT * FROM jobs")
    assert row["kind"] == "freelance" and row["quote"] and row["fit"] in ("strong", "good")
    cfg["mode"] = "both"
    assert agent.collect()["new"] == 1          # the remotive job now too


def test_leads_mode_drafts_nothing(agent, cfg):
    cfg["mode"] = "leads"
    jid, _ = agent.add_manual("Accenture", "Backend Engineer", "Hyderabad", description="Python")
    r = agent.reach_out(jid)
    assert r["added"] == 0 and "leads-only" in r["notes"][0]
    assert agent.db.q("SELECT * FROM messages") == []


# ---------------- freelance outreach ----------------
def test_freelance_with_email_gets_proposal_email_and_followups(agent, cfg):
    cfg["mode"] = "both"
    cfg["profile"].update(pitch="I build Python backends", min_fixed=100)
    jid, m = agent.add_manual("", "FastAPI backend for booking app", kind="freelance", budget="$250-750",
                              contact_email="founder@tinyco.io", description="FastAPI, Docker, AWS")
    j = agent.db.job(jid)
    assert j["quote"] == "$550" and j["budget_max"] == 750
    r = agent.reach_out(jid)
    assert r["contacts"][0]["email_confidence"] == "posted"
    d = agent.db.one("SELECT * FROM messages WHERE channel='email'")
    assert "$550" in d["body"] and d["body"].startswith("Hi,")
    agent.approve(d["id"])
    assert agent.send_due()["sent"] == 1


def test_freelance_platform_proposal(agent, cfg):
    cfg["mode"] = "freelance"
    p = freelance.freelancer_project(fixture_json("freelancer_projects.json")["result"]["projects"][1])
    jid, _, _ = agent._store_and_match(p)
    r = agent.reach_out(jid)
    assert r["proposal"] and "post" in r["notes"][0].lower()
    msg = agent.db.one("SELECT * FROM messages WHERE channel='platform'")
    assert msg["status"] == "draft" and "FastAPI" in msg["body"]
    agent.mark_done(msg["id"])
    assert agent.db.one("SELECT status FROM messages WHERE id=?", (msg["id"],))["status"] == "done"
    assert agent.send_due()["sent"] == 0        # platform proposals are never emailed


def test_hn_job_email_is_used_first(agent):
    job = freelance.parse_hn_post(hn_hit("Accenture | Backend Engineer | Hyderabad<p>Python, AWS. Mail hiring at accenture dot com"), HN_STORY)
    jid, _, _ = agent._store_and_match(job)
    r = agent.reach_out(jid)
    assert r["contacts"][0]["email"] == "hiring@accenture.com"
    assert len(r["contacts"]) == 3             # + recruiter + manager from search


# ---------------- LinkedIn drafts ----------------
def test_linkedin_drafts_for_found_people(agent):
    jid, _ = agent.add_manual("Accenture", "Backend Engineer - Python", "Hyderabad", description="Python FastAPI AWS")
    agent.reach_out(jid)
    notes = agent.db.q("SELECT * FROM messages WHERE channel='linkedin' AND kind='linkedin_note'")
    assert len(notes) == 2 and all(len(n["body"]) <= 200 for n in notes)
    assert agent.db.q("SELECT * FROM messages WHERE channel='linkedin' AND kind='linkedin_dm'")
    agent.cfg["linkedin"]["drafts"] = False
    j2, _ = agent.add_manual("Accenture", "Python Developer", "Hyderabad", description="Python")
    agent.add_contact(j2, "Meera Iyer", "meera.iyer@accenture.com", linkedin_url="https://linkedin.com/in/meera")
    assert len(agent.db.q("SELECT * FROM messages WHERE channel='linkedin' AND kind='linkedin_note'")) == 2


# ---------------- export + migration + env ----------------
def test_export_csv(agent, tmp_path):
    agent.add_manual("Accenture", "Backend Engineer", "Hyderabad", description="Python FastAPI")
    agent.add_manual("", "Logo design", kind="freelance", budget="$50")
    path = tmp_path / "leads.csv"
    assert agent.export_csv(str(path)) == 2
    rows = list(csv.DictReader(path.open()))
    assert {r["kind"] for r in rows} == {"job", "freelance"} and "Python" in rows[0]["matched"] + rows[1]["matched"]
    assert agent.export_csv(str(path), kind="freelance") == 1


def test_old_database_is_migrated(tmp_path):
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.executescript("CREATE TABLE jobs (id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL, title TEXT);"
                      "INSERT INTO jobs(uid,title) VALUES ('a','Old job');")
    con.commit(); con.close()
    db = DB(p)
    row = db.one("SELECT * FROM jobs")
    assert row["title"] == "Old job" and row["kind"] == "job"


def test_save_env_keeps_other_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nBRAVE_API_KEY=old\nOTHER=1\n")
    save_env({"BRAVE_API_KEY": "new", "GEMINI_API_KEY": "g"}, p)
    text = p.read_text()
    assert "BRAVE_API_KEY=new" in text and "OTHER=1" in text and "GEMINI_API_KEY=g" in text and "# comment" in text


# ---------------- terminal ----------------
def scripted(answers, secrets=()):
    a, s = iter(answers), iter(secrets)
    out = []

    class Out:
        def write(self, x): out.append(x)
        def flush(self): pass
    return IO(input_fn=lambda p: next(a), secret_fn=lambda p: next(s), out=Out()), out


def test_terminal_setup_freelance_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "cv.txt").write_text("Asha Rao\n3 years of experience. Python, Django, React.\n")
    io, out = scripted(["2", "cv.txt", "", "", "", "INR", "1500", "20000", "I ship Django apps fast",
                        "", "", "asha@gmail.com", "n", "n", "", "n", "5", "8"], ["pw pw pw pw"])
    cfg = run_setup("config.yaml", ".env", io, test_gmail=lambda c: (False, "bad password"))
    assert cfg.mode == "freelance" and cfg.lead_kinds == ["freelance"]
    p = cfg["profile"]
    assert p["name"] == "Asha Rao" and "Django" in p["skills"] and p["min_hourly"] == 1500 and p["currency"] == "INR"
    assert cfg["mail"]["dry_run"] is False and cfg["linkedin"]["drafts"] is False
    assert cfg["ai"]["provider"] == "none" and cfg.daily_limit == 8
    assert "GMAIL_APP_PASSWORD=pwpwpwpw" in (tmp_path / ".env").read_text()
    assert any("bad password" in x for x in out)


def test_terminal_session_flow(agent, cfg):
    """Add a job by hand -> reach out -> approve first draft, skip second -> LinkedIn later -> send -> quit."""
    opened = []
    answers = [
        "9",                                   # menu: Add a job / project from a link (jobs mode menu)
        "",                                    # no link -> manual
        "1",                                   # it's a job
        "Accenture", "Backend Engineer - Python", "Hyderabad", "", "",   # company, title, location, domain, email
        "1",                                   # lead detail: Reach out
        "y",                                   # review drafts now
        "1",                                   # draft 1: approve
        "4",                                   # draft 2: don't send
        "1", "n",                              # LinkedIn 1: open profile, not sent yet
        "5",                                   # stop LinkedIn review
        "6",                                   # menu: Send now
        "13",                                  # menu: Quit
    ]
    io, out = scripted(answers)
    Session(agent, io, open_url=opened.append).run()
    text = "".join(out)
    assert "Priya Sharma" in text and "Sent 1 new" in text
    assert opened == ["https://in.linkedin.com/in/priya-sharma-123"]
    assert agent.db.one("SELECT COUNT(*) n FROM messages WHERE status='sent'")["n"] == 1
    assert agent.db.one("SELECT COUNT(*) n FROM messages WHERE status='skipped'")["n"] == 1


# ---------------- web onboarding ----------------
def test_web_setup_wizard(tmp_path, monkeypatch, agent):
    monkeypatch.chdir(tmp_path)
    cfgp, envp = str(tmp_path / "config.yaml"), str(tmp_path / ".env")
    cfg = load_config(cfgp, envp)
    cfg["database"] = agent.cfg["database"]
    c = TestClient(create_app(cfg, agent, config_path=cfgp, env_path=envp))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/setup")
    assert "What are you here for?" in c.get("/setup").text
    c.post("/setup/1", data={"mode": "both"})
    r = c.post("/setup/resume", files={"file": ("cv.txt", b"Ravi Teja\n2 years of experience with Python, FastAPI and AWS", "text/plain")},
               follow_redirects=True)
    assert "skills found" in r.text and "Python" in r.text and "Lowest hourly rate" in r.text
    c.post("/setup/2", data={"name": "Ravi Teja", "years": "2", "skills": "Python, FastAPI, AWS", "roles": "Backend Engineer",
                             "locations": "Hyderabad, Remote", "min_hourly": "20", "min_fixed": "200", "pitch": "Python APIs"})
    c.post("/setup/3", data={"from_address": "ravi@gmail.com", "app_password": "abcd efgh", "dry_run": "on",
                             "linkedin_url": "https://linkedin.com/in/ravi", "li_drafts": "on"})
    r = c.post("/setup/4", data={"search": "brave", "search_key": "BK", "ai": "groq", "ai_key": "GK", "model": "", "daily_limit": "50"},
               follow_redirects=True)
    assert "Outreach throughput" in r.text and "Everything needed is set up" in r.text   # all required done: lands on Dashboard
    assert 'href="/start"' not in r.text.split("</nav>")[0]                          # ...and the Get started tab is gone
    r = c.get("/start")                                                               # still reachable from Settings
    assert "Connected ✓" in r.text and "Send for real" in r.text                    # practice mode is a choice, shown as optional
    saved = load_config(cfgp, envp)
    assert saved.mode == "both" and saved["profile"]["min_fixed"] == 200 and saved.daily_limit == 20
    assert saved["ai"]["model"] == "llama-3.3-70b-versatile"
    env = (tmp_path / ".env").read_text()
    assert "GMAIL_APP_PASSWORD=abcdefgh" in env and "BRAVE_API_KEY=BK" in env and "GROQ_API_KEY=GK" in env
    assert (tmp_path / "resume.txt").exists()


def test_web_manual_page_and_freelance_job(agent, cfg):
    cfg["mode"] = "both"
    p = freelance.freelancer_project(fixture_json("freelancer_projects.json")["result"]["projects"][1])
    jid, _, _ = agent._store_and_match(p)
    c = TestClient(create_app(cfg, agent))
    assert "$250–750" in c.get("/").text
    c.post(f"/job/{jid}/research")
    page = c.get(f"/job/{jid}").text
    assert "Proposal to post on the site" in page and "I posted it" in page
    assert "Proposal on freelancer.com" in c.get("/manual").text
    mid = agent.db.one("SELECT id FROM messages WHERE channel='platform'")["id"]
    c.post(f"/message/{mid}/done", data={"next": "/manual"})
    assert "Nothing waiting" in c.get("/manual").text
    assert c.get("/export.csv").text.startswith("id,kind,fit")


# ---------------- get started checklist ----------------
def test_checklist_depends_on_mode(cfg):
    from leadhunter.checklist import checklist, summary
    cfg["mode"] = "leads"
    names = [i["key"] for i in checklist(cfg)]
    assert "gmail" not in names and "search" not in names
    cfg["mode"] = "freelance"
    names = [i["key"] for i in checklist(cfg)]
    assert "gmail" in names and "reddit" in names and "search" not in names
    cfg["mode"] = "jobs"
    items = checklist(cfg)
    assert {i["key"] for i in items} >= {"profile", "gmail", "search", "ai", "hunter", "practice"}
    done, total = summary(items)
    assert total == 2 and done == 1       # profile done (skills set); gmail left; practice mode is optional


def test_terminal_checklist_and_web_start(agent, cfg):
    io, out = scripted(["11", "13"])
    Session(agent, io).run()
    text = "".join(out)
    assert "required setup step(s) left" in text and "myaccount.google.com/apppasswords" in text
    c = TestClient(create_app(cfg, agent))
    page = c.get("/start").text
    assert "Gmail App Password" in page and "brave.com" in page and "Hacker News" in page
