"""End-to-end: job -> research -> drafts -> approve -> send (dry run) -> follow-ups -> reply stops it."""
import email
from datetime import timedelta

from fastapi.testclient import TestClient

from leadhunter.web import create_app


def _setup(agent):
    jid, m = agent.add_manual("Accenture", "Backend Engineer - Python", "Hyderabad",
                              description="2+ years of experience with Python, FastAPI and AWS.")
    return jid, m


def test_research_finds_recruiter_and_manager(agent):
    jid, m = _setup(agent)
    assert m["fit"] == "strong"
    r = agent.research(jid)
    assert r["domain"] == "accenture.com"
    names = [c["name"] for c in r["contacts"]]
    assert names == ["Priya Sharma", "Karthik Reddy"]          # recruiter first, then manager
    assert "Rahul Verma" not in names and "Anita Rao" not in names  # other company / former employee
    assert r["contacts"][0]["email"] == "priya.sharma@accenture.com"
    drafts = agent.db.q("SELECT * FROM messages WHERE status='draft' AND channel='email'")
    assert len(drafts) == 2
    assert "Hi Priya" in drafts[0]["body"] and "Python" in drafts[0]["body"]


def test_nothing_sends_without_approval(agent):
    jid, _ = _setup(agent)
    agent.research(jid)
    r = agent.send_due()
    assert r["sent"] == 0 and agent.db.stats()["sent"] == 0


def test_send_followups_and_stop_on_reply(agent, clock, tmp_path):
    jid, _ = _setup(agent)
    agent.research(jid)
    for m in agent.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'"):
        agent.approve(m["id"])
    r = agent.send_due()
    assert r["sent"] == 2
    files = sorted((tmp_path / "outbox").glob("*.eml"))
    assert len(files) == 2
    msg = email.message_from_bytes(files[0].read_bytes())
    assert msg["To"].endswith("@accenture.com")
    priya = agent.db.one("SELECT * FROM contacts WHERE name='Priya Sharma'")
    assert priya["status"] == "active" and priya["first_message_id"]

    # Day 2: nothing due yet
    clock.t += timedelta(days=2)
    assert agent.send_due()["followups"] == 0

    # Day 4: first follow-up for both, threaded as a reply
    clock.t += timedelta(days=2)
    assert agent.send_due()["followups"] == 2
    fu = agent.db.one("SELECT * FROM messages WHERE kind='followup' AND contact_id=?", (priya["id"],))
    assert fu["subject"].startswith("Re: ")
    last = sorted((tmp_path / "outbox").glob("*.eml"))[-1]
    assert email.message_from_bytes(last.read_bytes())["In-Reply-To"]

    # Priya replies; Karthik's address bounces
    karthik = agent.db.one("SELECT * FROM contacts WHERE name='Karthik Reddy'")
    agent.mailer.fetch_inbox = lambda since: [
        {"from": "priya.sharma@accenture.com", "from_raw": "Priya <priya.sharma@accenture.com>",
         "subject": "Re: Backend", "in_reply_to": "", "references": "", "text": "Sure, let's talk"},
        {"from": "mailer-daemon@googlemail.com", "from_raw": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>",
         "subject": "Delivery Status Notification (Failure)", "in_reply_to": "", "references": "",
         "text": f"Address not found: {karthik['email']}"},
    ]
    clock.t += timedelta(days=7)
    r = agent.send_due()
    assert r["followups"] == 0                    # nobody gets a second follow-up
    assert agent.db.contact(priya["id"])["status"] == "replied"
    assert agent.db.contact(karthik["id"])["status"] == "bounced"
    assert agent.db.is_suppressed(karthik["email"])


def test_out_of_office_is_not_a_reply(agent):
    from leadhunter.mailer import classify_inbox
    contacts = [{"id": 1, "email": "a@x.com", "first_message_id": "<m1@x>", "last_message_id": "<m1@x>"}]
    ooo = [{"from": "a@x.com", "from_raw": "a@x.com", "subject": "Automatic reply: Backend role",
            "in_reply_to": "<m1@x>", "references": "", "text": "I'm on leave"}]
    assert classify_inbox(ooo, contacts) == {}


def test_daily_limit_counts_everything(agent, cfg):
    cfg["outreach"]["daily_limit"] = 3
    for i in range(3):
        jid, _ = agent.add_manual("Accenture", f"Backend Engineer {i}", "Hyderabad", description="Python FastAPI AWS")
        agent.research(jid)
    # Same people for every job -> only the first job's drafts are kept (never email a person twice in parallel)
    for m in agent.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'"):
        agent.approve(m["id"])
    agent.add_contact(jid, "Meera Iyer", "meera.iyer@accenture.com")
    agent.add_contact(jid, "Arjun Nair", "arjun.nair@accenture.com")
    for m in agent.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'"):
        agent.approve(m["id"])
    r = agent.send_due()
    assert r["sent"] == 3 and "daily limit" in r["skipped_reason"]
    assert agent.remaining_today() == 0


def test_no_duplicate_outreach_to_same_person(agent):
    j1, _ = _setup(agent)
    agent.research(j1)
    for m in agent.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'"):
        agent.approve(m["id"])
    agent.send_due()
    j2, _ = agent.add_manual("Accenture", "Python Developer", "Hyderabad", description="Python")
    agent.research(j2)
    assert agent.db.q("SELECT * FROM messages m JOIN contacts c ON c.id=m.contact_id WHERE c.job_id=? AND m.channel='email'", (j2,)) == []


def test_send_window(agent, cfg, clock):
    cfg["outreach"]["send_window"] = ["09:30", "18:30"]
    cfg["outreach"]["weekdays_only"] = True
    clock.t = clock.t.replace(hour=2)  # 02:00 UTC on a Tuesday
    import os, time
    os.environ["TZ"] = "UTC"; time.tzset()
    assert not agent.in_send_window()
    clock.t = clock.t.replace(hour=11)
    assert agent.in_send_window()
    clock.t = clock.t + timedelta(days=4)  # Saturday
    assert not agent.in_send_window()


def test_dashboard_pages(agent, cfg):
    jid, _ = _setup(agent)
    agent.research(jid)
    c = TestClient(create_app(cfg, agent))
    assert c.get("/").status_code == 200
    page = c.get(f"/job/{jid}").text
    assert "Priya Sharma" in page and "Approve" in page
    mid = agent.db.one("SELECT id FROM messages WHERE channel='email' LIMIT 1")["id"]
    r = c.post(f"/message/{mid}/save", data={"subject": "Hello", "body": "Edited body", "approve": "1"}, follow_redirects=False)
    assert r.status_code == 303
    m = agent.db.one("SELECT * FROM messages WHERE id=?", (mid,))
    assert m["status"] == "approved" and m["body"] == "Edited body"
    assert c.get("/outbox").status_code == 200
    r = c.post("/send", follow_redirects=True)
    assert r.status_code == 200 and "Sent 1 new" in r.text
    assert "Priya" in c.get("/threads").text
