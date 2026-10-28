import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from leadhunter import emails
from leadhunter.agent import Agent
from leadhunter.ai import AI
from leadhunter.config import load_config
from leadhunter.db import DB
from leadhunter.mailer import Mailer

FIX = Path(__file__).parent / "fixtures"


def fixture_json(name):
    return json.loads((FIX / name).read_text())


class FakeSearch:
    """Answers like a search engine API would, from canned results."""
    enabled = True
    provider = "fake"

    def __init__(self, results=None):
        self.queries = []
        self.results = results or {}

    def __call__(self, query, count=10):
        self.queries.append(query)
        for key, hits in self.results.items():
            if key in query:
                return hits
        return []


ACCENTURE_HITS = {
    "official website": [
        {"title": "Accenture | Let there be change", "url": "https://www.accenture.com/in-en", "snippet": ""}],
    '"talent acquisition" OR recruiter': [
        {"title": "Priya Sharma - Talent Acquisition Specialist - Accenture | LinkedIn",
         "url": "https://in.linkedin.com/in/priya-sharma-123?trk=x",
         "snippet": "Hyderabad, Telangana · Talent Acquisition at Accenture · hiring for technology roles"},
        {"title": "Rahul Verma - Recruiter - Infosys | LinkedIn",
         "url": "https://in.linkedin.com/in/rahul-verma", "snippet": "Hyderabad · Recruiter at Infosys"},
        {"title": "Accenture Jobs - LinkedIn", "url": "https://www.linkedin.com/company/accenture/jobs", "snippet": ""},
        {"title": "Anita Rao - Former Recruiter - Accenture | LinkedIn",
         "url": "https://in.linkedin.com/in/anita-rao", "snippet": "Former recruiter at Accenture, now at Wipro"},
    ],
    '"engineering manager"': [
        {"title": "Karthik Reddy - Engineering Manager, Backend - Accenture | LinkedIn",
         "url": "https://in.linkedin.com/in/karthik-reddy", "snippet": "Hyderabad · Engineering Manager at Accenture · backend platforms"},
    ],
}


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr(emails, "has_mx", lambda d: True)


@pytest.fixture
def cfg(tmp_path):
    c = load_config(tmp_path / "none.yaml", tmp_path / "none.env")
    c["database"] = str(tmp_path / "t.db")
    c["profile"].update(name="Test User", skills=["Python", "FastAPI", "AWS", "Docker"], years=2,
                        roles=["Backend Engineer"], locations=["Hyderabad", "Remote"])
    c["search"]["max_contacts_per_job"] = 2
    c["outreach"]["send_window"] = ["00:00", "23:59"]
    c["outreach"]["weekdays_only"] = False
    return c


class Clock:
    def __init__(self, t=None):
        self.t = t or datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def agent(cfg, tmp_path, clock):
    mailer = Mailer(cfg, outbox=tmp_path / "outbox")
    ai = AI(cfg)  # provider none -> disabled, templates used
    return Agent(cfg, db=DB(cfg["database"]), mailer=mailer, search=FakeSearch(ACCENTURE_HITS), ai=ai,
                 sleep=lambda s: None, clock=clock)
