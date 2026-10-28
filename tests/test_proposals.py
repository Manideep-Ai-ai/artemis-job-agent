"""Your projects and your own notes make emails and proposals specific."""
from fastapi.testclient import TestClient

from leadhunter.compose import best_project, platform_proposal, projects
from leadhunter.web import create_app

SIGNAI = "SignAI | https://signai.in | ERP for schools: fees, attendance, exams and reports"
OTHER = "ChatDesk | https://chatdesk.example | AI chatbot for customer support"


def test_projects_parse_and_best_one_fits_the_post():
    prof = {"projects": [OTHER, SIGNAI, "  ", "Just a name"]}
    assert [p["name"] for p in projects(prof)] == ["ChatDesk", "SignAI", "Just a name"]
    erp_post = {"title": "School ERP system", "description": "Need fees and attendance modules for our school ERP"}
    assert best_project(prof, erp_post)["name"] == "SignAI"
    assert best_project({}, erp_post) is None


def test_template_proposal_uses_notes_as_written_and_links_the_project():
    job = {"title": "ERP for coaching centre", "description": "attendance, fees, reports", "notes": "I can deliver in 3 weeks."}
    text = platform_proposal(job, {"name": "Manideep", "projects": [OTHER, SIGNAI]}, ["Python"], "$800")
    assert "I can deliver in 3 weeks." in text
    assert "Similar work I've done: SignAI (https://signai.in): ERP for schools" in text
    assert text.index("I can deliver") < text.index("How I'd approach it")      # notes come early, not buried


def test_notes_rewrite_unsent_drafts_only(agent, cfg):
    cfg["mode"] = "both"
    cfg["profile"].update(projects=[SIGNAI])
    jid, _ = agent.add_manual("", "School ERP in Django", kind="freelance", budget="$500-900",
                              contact_email="owner@school.in", description="ERP: fees, attendance, Django")
    agent.reach_out(jid)
    d = agent.db.one("SELECT * FROM messages WHERE channel='email'")
    assert "signai.in" in d["body"]                                             # project picked even before notes
    assert agent.set_notes(jid, "  Mention I speak Telugu.  ") == 1
    d = agent.db.one("SELECT * FROM messages WHERE id=?", (d["id"],))
    assert "Mention I speak Telugu." in d["body"] and agent.db.job(jid)["notes"] == "Mention I speak Telugu."
    agent.approve(d["id"])
    agent.send_due()
    assert agent.set_notes(jid, "changed my mind") == 0                        # sent emails are never rewritten


def test_web_projects_setting_and_notes_box(agent, cfg):
    cfg["mode"] = "both"
    c = TestClient(create_app(cfg, agent))
    c.post("/setup/2", data={"name": "Manideep", "skills": "Python", "projects": f"{SIGNAI}\n\n{OTHER}\n"})
    assert cfg["profile"]["projects"] == [SIGNAI, OTHER]
    assert "signai.in" in c.get("/setup?step=2").text
    jid, _ = agent.add_manual("", "School ERP", kind="freelance", contact_email="o@school.in", description="ERP fees")
    assert "Your notes for this one" in c.get(f"/job/{jid}").text
    r = c.post(f"/job/{jid}/notes", data={"notes": "Ask about Telugu UI"})
    assert "Notes saved" in r.text and "Ask about Telugu UI" in r.text
