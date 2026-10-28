from pathlib import Path

from leadhunter import emails, joblink, matching, resume, sources
from leadhunter.config import HARD_DAILY_CEILING, load_config
from leadhunter.research import build_queries, city_of, parse_profile_result, works_at
from leadhunter.util import html_to_text, is_company_host, registrable_domain

from conftest import fixture_json

ROOT = Path(__file__).resolve().parent.parent


# ---------- config ----------
def test_daily_limit_has_hard_ceiling(tmp_path):
    c = load_config(tmp_path / "x.yaml", tmp_path / "x.env")
    assert c.daily_limit == 10
    c["outreach"]["daily_limit"] = 500
    assert c.daily_limit == HARD_DAILY_CEILING
    c["outreach"]["followups"] = [3, 5, 7, 9, 11]
    assert c.followup_days == [3, 5, 7]


def test_packaged_templates_match_repo_copies():
    data = ROOT / "leadhunter" / "data"
    assert (data / "config.example.yaml").read_text() == (ROOT / "config.example.yaml").read_text()
    assert (data / "env.example").read_text() == (ROOT / ".env.example").read_text()


# ---------- util ----------
def test_domains():
    assert registrable_domain("https://careers.airbnb.com/positions/1") == "airbnb.com"
    assert registrable_domain("jobs.tcs.co.in") == "tcs.co.in"
    assert not is_company_host("https://boards.greenhouse.io/x")
    assert not is_company_host("https://t.me/jobs")
    assert is_company_host("https://www.accenture.com/in-en")


def test_html_to_text_handles_greenhouse_double_escaping():
    assert html_to_text("&lt;p&gt;Python &amp;amp; AWS&lt;/p&gt;") == "Python & AWS"


# ---------- resume + matching ----------
def test_find_skills_and_years():
    text = "Backend developer with 2.5 years of experience in Python, FastAPI, ReactJS, AWS (S3), k8s. Went to college."
    found = resume.find_skills(text)
    assert {"Python", "FastAPI", "React", "AWS", "Kubernetes"} <= set(found)
    assert "Go" not in found
    assert resume.guess_years(text) == 2.5


def test_score_job_fit_levels():
    profile = {"skills": ["Python", "FastAPI", "AWS", "Docker"], "years": 2, "roles": ["Backend Engineer"],
               "locations": ["Hyderabad"]}
    strong = {"title": "Backend Engineer (Python)", "location": "Hyderabad, India",
              "description": "2+ years of experience. Python, FastAPI, AWS, Docker."}
    r = matching.score_job(strong, profile)
    assert r["fit"] == "strong" and r["years_asked"] == 2
    senior = dict(strong, description="8+ years of experience with Python and AWS")
    assert matching.score_job(senior, profile)["fit"] in ("", "stretch")
    elsewhere = dict(strong, location="Berlin, Germany")
    assert matching.score_job(elsewhere, profile)["fit"] in ("", "stretch")
    unrelated = {"title": "Legal Counsel", "location": "Hyderabad", "description": "Contracts and compliance."}
    assert matching.score_job(unrelated, profile)["fit"] == ""


def test_location_aliases_and_remote():
    assert matching.location_ok("Bengaluru, KA", ["Bangalore"])
    assert matching.location_ok("United States (Remote)", ["Remote"])
    assert matching.location_ok("", ["Hyderabad"]) is None


# ---------- sources (real API shapes captured as fixtures) ----------
def test_lever_job_from_real_payload():
    j = sources.lever_job(fixture_json("lever_postings.json")[0], "palantir")
    assert j["uid"].startswith("lever:palantir:")
    assert j["url"].startswith("https://jobs.lever.co/palantir/")
    assert j["title"] and j["location"] and j["posted_at"].startswith("20")
    assert len(j["description"]) > 200


def test_greenhouse_job_from_real_payload():
    j = sources.greenhouse_job(fixture_json("greenhouse_jobs.json")["jobs"][0], "airbnb", "Airbnb")
    assert j["company"] == "Airbnb" and j["company_domain"] == "airbnb.com"
    assert j["title"] == "Associate Legal Counsel, Japan"


# ---------- job links ----------
def test_parse_ats_links():
    assert joblink.parse_ats("https://boards.greenhouse.io/airbnb/jobs/7789554") == ("greenhouse", "airbnb", "7789554")
    assert joblink.parse_ats("https://job-boards.greenhouse.io/acme/jobs/123?gh_src=x") == ("greenhouse", "acme", "123")
    assert joblink.parse_ats("https://jobs.lever.co/palantir/6ed76ce8-4156")[0] == "lever"
    assert joblink.parse_ats("https://jobs.ashbyhq.com/ramp/abc-123") == ("ashby", "ramp", "abc-123")
    assert joblink.parse_ats("https://careers.example.com/jobs/1") is None


def test_jobposting_jsonld():
    page = """<html><script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},
    {"@type":"JobPosting","title":"Python Developer","description":"&lt;p&gt;Python, AWS&lt;/p&gt;",
     "hiringOrganization":{"@type":"Organization","name":"Acme Tech","sameAs":"https://www.acmetech.io"},
     "jobLocation":{"@type":"Place","address":{"addressLocality":"Hyderabad","addressCountry":"IN"}},
     "datePosted":"2026-10-20"}]}</script></html>"""
    jp = joblink.find_jobposting(page)
    job = joblink.from_jobposting(jp, "https://jobs.acmetech.io/1")
    assert job["company"] == "Acme Tech" and job["company_domain"] == "acmetech.io"
    assert job["location"] == "Hyderabad, IN" and "Python" in job["description"]


# ---------- research ----------
def test_parse_profile_results():
    p = parse_profile_result({"title": "Priya Sharma - Talent Acquisition - Accenture | LinkedIn",
                              "url": "https://in.linkedin.com/in/priya?trk=1", "snippet": "Hyderabad"})
    assert p["name"] == "Priya Sharma" and p["linkedin_url"] == "https://in.linkedin.com/in/priya"
    assert parse_profile_result({"title": "Accenture Jobs", "url": "https://linkedin.com/company/x", "snippet": ""}) is None
    assert parse_profile_result({"title": "Top 10 Recruiters | LinkedIn", "url": "https://linkedin.com/in/x", "snippet": ""}) is None


def test_works_at_filters_former_and_other_companies():
    assert works_at({"text": "Talent Acquisition at Accenture Hyderabad"}, "Accenture")
    assert not works_at({"text": "Recruiter at Infosys"}, "Accenture")
    assert not works_at({"text": "Former recruiter at Accenture"}, "Accenture")


def test_queries_use_city_and_department():
    qs = build_queries("Accenture", "Senior Backend Engineer", "Hyderabad, Telangana, India")
    assert city_of("Hyderabad, Telangana") == "Hyderabad"
    assert all("site:linkedin.com/in" in q for _, q in qs)
    assert '"Hyderabad"' in qs[0][1] and "backend" in qs[1][1]


# ---------- emails ----------
def test_split_name_and_build():
    assert emails.split_name("Dr. Priya K. Sharma, SHRM-CP") == ("priya", "sharma")
    assert emails.split_name("Sérgio Ramos") == ("sergio", "ramos")
    assert emails.build("priya", "sharma", "first.last", "acme.com") == "priya.sharma@acme.com"
    assert emails.build("priya", "sharma", "flast", "acme.com") == "psharma@acme.com"
    assert emails.build("priya", "", "first.last", "acme.com") == ""


def test_pattern_from_public_examples():
    found = ["hr@acme.com", "ravi.kumar@acme.com", "neha.singh@acme.com", "j.doe@acme.com", "x@other.com"]
    assert emails.pattern_from_examples(found, "acme.com") == ("first.last", 2)


def test_finder_falls_back_to_guess(cfg):
    f = emails.EmailFinder(cfg, search=None)
    assert f.find("Priya Sharma", "acme.com") == ("priya.sharma@acme.com", "guessed")
