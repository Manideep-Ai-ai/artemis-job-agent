"""SmartRecruiters + Recruitee sources, reply previews and tags, Telegram alerts, Pipeline, Insights, Sources page."""
import logging
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from leadhunter import sources
from leadhunter.insights import insights, pipeline
from leadhunter.mailer import reply_preview, reply_sentiment
from leadhunter.web import create_app

from conftest import fixture_json


# ---------------- new sources (real API shapes captured as fixtures) ----------------
def test_smartrecruiters_job_from_real_payload():
    j = sources.smartrecruiters_job(fixture_json("smartrecruiters_posting.json"), "Ubisoft2")
    assert j["uid"].startswith("smartrecruiters:Ubisoft2:") and j["company"] == "Ubisoft"
    assert j["url"].startswith("https://jobs.smartrecruiters.com/Ubisoft2/")
    assert j["location"] == "Paris, IDF, France" and len(j["description"]) > 500
    assert "<p>" not in j["description"]


def test_smartrecruiters_fetches_details_for_listed_postings(monkeypatch):
    class R:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data
    urls = []

    def fake_get(url, params=None):
        urls.append(url)
        return R(fixture_json("smartrecruiters_posting.json" if url.split("/")[-1].isdigit() else "smartrecruiters_postings.json"))
    monkeypatch.setattr(sources, "http_get", fake_get)
    jobs = sources.smartrecruiters("Ubisoft2")
    assert len(jobs) == 1 and len(urls) == 2


def test_recruitee_job_from_real_payload():
    o = fixture_json("recruitee_offers.json")["offers"][0]
    j = sources.recruitee_job(o, "bunq")
    assert j["uid"] == f"recruitee:bunq:{o['id']}" and j["company"] == "bunq"
    assert j["company_domain"] == "bunq.com"          # careers.bunq.com
    assert datetime.fromisoformat(j["posted_at"]).tzinfo is not None   # "… UTC" made readable for the age filter
    assert len(j["description"]) > 500


@pytest.mark.parametrize("bad", ["evil.com/x#", "a b", "..", "", "x" * 81, "../etc"])
def test_company_ids_cannot_change_the_url(bad):
    with pytest.raises(ValueError):
        sources.check_slug(bad)
    with pytest.raises(ValueError):
        sources.recruitee(bad)


def test_collect_all_runs_new_boards(cfg, monkeypatch):
    cfg["sources"].update(remotive=False, arbeitnow=False, hackernews=False, smartrecruiters=["Acme"], recruitee=["bunq"])
    monkeypatch.setattr(sources, "smartrecruiters", lambda c: [{"uid": "s1", "title": "A", "posted_at": ""}])
    monkeypatch.setattr(sources, "recruitee", lambda c: (_ for _ in ()).throw(RuntimeError("404")))
    jobs, errors = sources.collect_all(cfg)
    assert [j["uid"] for j in jobs] == ["s1"] and errors == ["recruitee/bunq: 404"]


# ---------------- replies ----------------
MIME_REPLY = """--000abc
Content-Type: text/plain; charset="UTF-8"
Content-Transfer-Encoding: quoted-printable

Hi Test, thanks for reaching out! Could we schedule a call on Thursday? Please s=
hare your availability.

Best, Priya

On Mon, Oct 26, 2026 at 10:00 Test User wrote:
> Hi Priya, I saw the Backend Engineer opening"""


def test_reply_preview_drops_mime_and_quoted_history():
    p = reply_preview(MIME_REPLY)
    assert p.startswith("Hi Test, thanks") and "Please share your availability" in p
    assert "Content-Type" not in p and "wrote:" not in p and "opening" not in p


@pytest.mark.parametrize("text,label", [
    ("Could we schedule a call next week?", "interview"),
    ("Unfortunately we're moving forward with other candidates, good luck with your interview process", "rejection"),
    ("I've forwarded your resume to the hiring manager", "referral"),
    ("What is your notice period?", "question"),
    ("Thanks!", "other"),
])
def test_reply_sentiment(text, label):
    assert reply_sentiment(text) == label


def sent_to_priya(agent):
    jid, _ = agent.add_manual("Accenture", "Backend Engineer", "Hyderabad", description="Python FastAPI AWS")
    agent.research(jid)
    for m in agent.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'"):
        agent.approve(m["id"])
    agent.send_due()
    return agent.db.one("SELECT * FROM contacts WHERE name='Priya Sharma'")


def priya_replies(agent, priya, text=MIME_REPLY):
    agent.mailer.fetch_inbox = lambda since: [
        {"from": priya["email"], "from_raw": f"Priya <{priya['email']}>", "subject": "Re: Backend Engineer",
         "in_reply_to": "", "references": "", "text": text}]


def test_reply_is_stored_tagged_and_alerted(agent, monkeypatch):
    priya = sent_to_priya(agent)
    priya_replies(agent, priya)
    posts = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: posts.append((url, json)) or httpx.Response(200, request=httpx.Request("POST", url)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert agent.check_replies() == {"replied": 1, "bounced": 0}
    c = agent.db.contact(priya["id"])
    assert c["status"] == "replied" and c["sentiment"] == "interview" and c["reply_read"] == 0
    assert c["reply_preview"].startswith("Hi Test") and c["reply_at"]
    assert posts[0][0] == "https://api.telegram.org/bot123:abc/sendMessage" and posts[0][1]["chat_id"] == "42"
    assert "Priya Sharma (Accenture), interview" in posts[0][1]["text"]


def test_no_telegram_without_keys(agent, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("should not post"))
    assert agent.notify("hi") is False


def test_telegram_failure_never_logs_the_token(agent, monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def boom(url, json, timeout):
        return httpx.Response(401, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        assert agent.notify("hi") is False
    assert "HTTP 401" in caplog.text and "SECRET" not in caplog.text


# ---------------- pipeline + insights ----------------
def test_pipeline_columns_and_moves(agent):
    priya = sent_to_priya(agent)
    board = pipeline(agent.db)
    assert any(c["id"] == priya["id"] for c in board["sent"])
    priya_replies(agent, priya, "Thanks, what's your notice period?")
    agent.check_replies()
    assert any(c["id"] == priya["id"] for c in pipeline(agent.db)["replied"])
    agent.set_sentiment(priya["id"], "interview")
    assert any(c["id"] == priya["id"] for c in pipeline(agent.db)["interview"])
    agent.set_contact_status(priya["id"], "stopped")
    assert any(c["id"] == priya["id"] for c in pipeline(agent.db)["closed"])


def test_insights_numbers(agent):
    priya = sent_to_priya(agent)
    priya_replies(agent, priya)
    agent.check_replies()
    d = insights(agent.db)
    f = {x["step"]: x["count"] for x in d["funnel"]}
    assert f["Leads found"] == 1 and f["Contacted"] == 2 and f["Replied"] == 1 and f["Interview"] == 1
    assert d["by_source"] == [{"key": "manual", "sent": 2, "replied": 1, "rate": 50}]
    assert d["sentiment"] == [{"key": "interview", "count": 1}]
    assert sum(x["sent"] for x in d["daily"]) >= 0       # dates are relative to today, so only the shape is checked


# ---------------- web pages ----------------
def test_web_replies_badge_filters_and_mark_read(agent, cfg):
    priya = sent_to_priya(agent)
    priya_replies(agent, priya)
    agent.check_replies()
    c = TestClient(create_app(cfg, agent))
    home = c.get("/").text
    assert 'Conversations<span class="badge">1</span>' in home
    t = c.get("/threads?show=interview").text
    assert "Priya Sharma" in t and "Mark read" in t and "Hi Test, thanks" in t
    assert "Priya Sharma" not in c.get("/threads?show=rejection").text
    assert "They replied" in c.get(f"/job/{priya['job_id']}").text
    c.post(f"/contact/{priya['id']}/read")
    assert 'class="badge"' not in c.get("/threads").text.split("</nav>")[0].split("Conversations")[1][:40]


def test_web_pipeline_insights_and_why_fit(agent, cfg):
    priya = sent_to_priya(agent)
    c = TestClient(create_app(cfg, agent))
    assert "Pipeline" in c.get("/pipeline").text and "Priya Sharma" in c.get("/pipeline").text
    c.post(f"/contact/{priya['id']}/move", data={"to": "interview"})
    assert agent.db.contact(priya["id"])["sentiment"] == "interview"
    assert "Reply rate by source" in c.get("/insights").text
    assert "Why you fit" in c.get(f"/job/{priya['job_id']}").text


def test_web_sources_page(agent, cfg, monkeypatch):
    c = TestClient(create_app(cfg, agent))
    assert "SmartRecruiters" in c.get("/sources").text
    r = c.post("/sources", data={"remotive": "on", "b_recruitee": "bunq, evil.com/x#", "b_greenhouse": "airbnb\nstripe",
                                 "max_age_days": "14"})
    assert "Skipped invalid ids: evil.com/x#" in r.text
    s = cfg["sources"]
    assert s["recruitee"] == ["bunq"] and s["greenhouse"] == ["airbnb", "stripe"]
    assert s["remotive"] is True and s["arbeitnow"] is False and s["max_age_days"] == 14
    monkeypatch.setattr(sources, "recruitee", lambda slug: [{}, {}, {}])
    r = c.post("/sources/test?board=recruitee", data={"t_recruitee": "bunq", "b_recruitee": "bunq, personio"})
    assert "3 open jobs" in r.text and cfg["sources"]["recruitee"] == ["bunq", "personio"]
    assert "didn" in c.post("/sources/test?board=recruitee", data={"t_recruitee": "../x"}).text


def test_web_telegram_setup_validates_chat_id(agent, cfg, tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.setenv(k, "")  # save_env writes os.environ directly; this makes pytest restore it afterwards
    c = TestClient(create_app(cfg, agent, env_path=str(tmp_path / ".env")))
    assert "Telegram alerts" in c.get("/setup?step=4").text
    assert "is a number" in c.post("/setup/4", data={"tg_chat": "@me"}).text
    assert "full bot token" in c.post("/setup/4", data={"tg_token": "AAHfakefakefakefakefakefakefake12"}).text   # colon part missing
    tok = "123456789:AAHfakefakefakefakefakefakefake12"
    c.post("/setup/4", data={"tg_token": tok, "tg_chat": "42"})
    env = (tmp_path / ".env").read_text()
    assert f"TELEGRAM_BOT_TOKEN={tok}" in env and "TELEGRAM_CHAT_ID=42" in env


def test_dashboard_overview(agent, cfg):
    priya = sent_to_priya(agent)
    priya_replies(agent, priya)
    agent.check_replies()
    from leadhunter.insights import overview
    d = overview(agent.db)
    assert d["kpi"]["contacted"] == 2 and d["kpi"]["replied"] == 1 and d["kpi"]["rate"] == 50
    assert d["chart"][-1] == {**d["chart"][-1], "sent": 2, "replied": 1}       # this week's bucket
    assert d["active"][0]["stage"] == "interview" and "reply:interview" in [r["what"] for r in d["activity"]]
    html = TestClient(create_app(cfg, agent)).get("/dashboard").text
    assert "Outreach throughput" in html and "Priya Sharma" in html and "<svg viewBox" in html


def test_web_hunter_and_reddit_keys_saved_and_masked(agent, cfg, tmp_path, monkeypatch):
    for k in ("HUNTER_API_KEY", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.setenv(k, "")
    c = TestClient(create_app(cfg, agent, env_path=str(tmp_path / ".env")))
    c.post("/setup/4", data={"hunter_key": "hk_secret_1234", "reddit_id": "rid", "reddit_secret": "rs_secret_9876"})
    env = (tmp_path / ".env").read_text()
    assert "HUNTER_API_KEY=hk_secret_1234" in env and "REDDIT_CLIENT_SECRET=rs_secret_9876" in env
    assert cfg["email_finder"]["hunter"] is True and cfg["sources"]["reddit"] is True
    page = c.get("/setup?step=4").text
    assert "hk_secret_1234" not in page and "····1234" in page


def test_gmail_login_hangup_reads_as_refused_and_verifies_certs(cfg, monkeypatch):
    import smtplib
    from leadhunter import mailer
    seen = {}

    class HangUp:
        def __init__(self, host, port, context=None, timeout=None):
            seen["verify"] = context.verify_mode

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, user, pw):
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", HangUp)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcdabcdabcdabcd")
    cfg["mail"]["from_address"] = "me@gmail.com"
    ok, msg = mailer.Mailer(cfg).test_login()
    assert not ok and "refused the login for me@gmail.com" in msg
    assert seen["verify"] == mailer.ssl.CERT_REQUIRED
