"""The dashboard can't be driven by other websites, outside data can't plant scripts, keys can't break .env."""
import csv

import pytest
from fastapi.testclient import TestClient

from leadhunter.config import save_env
from leadhunter.theme import href
from leadhunter.web import create_app


def test_other_websites_cannot_post_or_rebind(agent, cfg):
    c = TestClient(create_app(cfg, agent))
    assert c.post("/send", headers={"origin": "https://evil.example"}).status_code == 403          # cross-site form
    assert c.post("/send", headers={"referer": "https://evil.example/page"}).status_code == 403
    assert c.post("/send", headers={"origin": "null"}).status_code == 403                          # sandboxed iframe
    assert c.get("/dashboard", headers={"host": "evil.example"}).status_code == 403                # DNS rebinding
    ok = c.post("/collect", headers={"origin": "http://testserver"}, follow_redirects=False)
    assert ok.status_code == 303                                                                     # our own forms still work


def test_only_http_links_become_hrefs():
    assert href("https://jobs.lever.co/acme/1") == "https://jobs.lever.co/acme/1"
    for bad in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "", None):
        assert href(bad) == "#"


def test_job_with_javascript_link_is_not_clickable(agent, cfg):
    jid, _ = agent.add_manual("Acme", "Engineer", url="javascript:alert(document.cookie)")
    page = TestClient(create_app(cfg, agent)).get(f"/job/{jid}").text
    assert "javascript:alert" not in page


def test_env_values_cannot_add_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "")
    p = tmp_path / ".env"
    save_env({"BRAVE_API_KEY": "abc\nGMAIL_APP_PASSWORD=stolen\r"}, p)
    assert p.read_text() == "BRAVE_API_KEY=abcGMAIL_APP_PASSWORD=stolen\n"
    with pytest.raises(ValueError):
        save_env({"BAD\nKEY": "x"}, p)


def test_csv_cells_cannot_be_formulas(agent, tmp_path):
    agent.add_manual("=HYPERLINK(\"http://evil\")", "@SUM(A1)", location="-1+1")
    path = tmp_path / "leads.csv"
    agent.export_csv(str(path))
    row = next(r for r in csv.DictReader(path.open()) if r["title"].endswith("SUM(A1)"))
    assert row["title"] == "'@SUM(A1)" and row["company"].startswith("'=") and row["location"] == "'-1+1"
