"""Write the emails. Short, specific, honest. AI is optional; templates always work."""
from __future__ import annotations

import re

from leadhunter.ai import AI
from leadhunter.emails import split_name


def _signature(profile: dict) -> str:
    lines = [profile.get("name") or ""]
    extra = [x for x in (profile.get("phone"), profile.get("linkedin_url"), profile.get("portfolio_url")) if x]
    if extra:
        lines.append(" | ".join(extra))
    return "\n".join(l for l in lines if l)


def projects(profile: dict) -> list[dict]:
    """Projects you've built, from lines like "SignAI | https://signai.in | ERP for schools: fees, attendance"."""
    out = []
    for line in profile.get("projects") or []:
        name, url, what = ([x.strip() for x in line.split("|")] + ["", ""])[:3]
        if name:
            out.append({"name": name, "url": url, "what": what})
    return out


_WORD = re.compile(r"[a-z0-9+#]{3,}")


def best_project(profile: dict, job: dict) -> dict | None:
    """The project that shares the most words with the post (ties: the one listed first)."""
    post = set(_WORD.findall(f"{job.get('title', '')} {job.get('description', '')}".lower()))
    return max(projects(profile), key=lambda p: len(set(_WORD.findall(f"{p['name']} {p['what']}".lower())) & post), default=None)


def project_line(p: dict) -> str:
    return p["name"] + (f" ({p['url']})" if p["url"] else "") + (f": {p['what']}" if p["what"] else "")


def _for_ai(profile: dict, job: dict) -> str:
    """Projects and the user's own notes, for AI prompts. Notes win over everything else."""
    ps = "\n".join("- " + project_line(p) for p in projects(profile)) or "none"
    return (f"Projects I've built (use the most relevant one as proof, by name and with its link; never invent others):\n{ps}\n"
            f"My own notes for this one (follow them closely):\n{(job.get('notes') or '').strip() or 'none'}\n")


def _join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def template_initial(job: dict, contact: dict, profile: dict, matched: list[str]) -> tuple[str, str]:
    first = "there" if contact.get("kind") in ("posted", "client") else (split_name(contact.get("name", ""))[0].title() or "there")
    title, company = job.get("title", "the open role"), job.get("company", "your company")
    loc = f" ({job['location'].split(';')[0]})" if job.get("location") else ""
    skills = _join(matched[:4])
    years = profile.get("years") or 0
    yrs = f"{years:g} year{'s' if years != 1 else ''} of experience" if years else "hands-on project experience"
    is_recruiter = contact.get("kind") == "recruiter" or re.search(r"(?i)talent|recruit|hr\b|people", contact.get("title", ""))
    who = ("If you're not handling this role, I'd be grateful if you could point me to the right recruiter."
           if is_recruiter else
           "If this isn't your team, I'd be grateful if you could point me to the right person.")
    skill_line = (f"I have {yrs} working with {skills}, which the role asks for." if skills
                  else f"I have {yrs} and the role is a close match to what I've been building.")
    p = best_project(profile, job)
    extra = "".join(f"{x}\n\n" for x in ((job.get("notes") or "").strip(), f"One thing I built: {project_line(p)}." if p else "") if x)
    subject = f"{title} at {company}: {profile.get('name') or 'application'}"
    body = (
        f"Hi {first},\n\n"
        f"I saw the {title} opening at {company}{loc} and wanted to reach out directly. {skill_line}\n\n"
        + extra +
        f"I've attached my resume. Would you be open to a short call, or could you share it with the hiring team?\n"
        f"{who}\n\n"
        + (f"Job link: {job['url']}\n\n" if job.get("url") else "")
        + f"Thank you,\n{_signature(profile)}"
    )
    return subject, body


def ai_initial(ai: AI, job: dict, contact: dict, profile: dict, matched: list[str], resume_text: str) -> tuple[str, str] | None:
    if not ai.enabled:
        return None
    data = ai.json(
        "You write cold emails from a job seeker to a recruiter or hiring manager. Rules: under 120 words; "
        "plain, warm, specific; mention 1-2 concrete things from the resume that match the job; never invent "
        "facts, numbers, employers or degrees that are not in the resume or the projects list; no flattery, no buzzwords, no emojis; "
        "one clear ask (a short call or forwarding the resume); say the resume is attached; greet by first name.",
        f"Sender: {profile.get('name')}, {profile.get('years') or 0} years experience.\n"
        f"Matched skills: {', '.join(matched)}\n"
        f"Resume (excerpt):\n{resume_text[:3000]}\n\n"
        f"Job: {job.get('title')} at {job.get('company')} ({job.get('location')})\n"
        f"Job description (excerpt):\n{(job.get('description') or '')[:2500]}\n\n"
        f"Recipient: {contact.get('name')}, {contact.get('title')}\n" + _for_ai(profile, job) + "\n"
        'Return {"subject": "...", "body": "..."} — the body WITHOUT a signature.',
        max_tokens=700,
    )
    if not isinstance(data, dict) or not data.get("subject") or not data.get("body"):
        return None
    body = data["body"].strip()
    if job.get("url") and job["url"] not in body:
        body += f"\n\nJob link: {job['url']}"
    body += f"\n\nThank you,\n{_signature(profile)}"
    return data["subject"].strip()[:150], body


def compose_initial(job: dict, contact: dict, profile: dict, matched: list[str], ai: AI | None = None,
                    resume_text: str = "") -> tuple[str, str]:
    if ai is not None:
        got = ai_initial(ai, job, contact, profile, matched, resume_text)
        if got:
            return got
    return template_initial(job, contact, profile, matched)


# ---------------- freelance ----------------
def _proposal_core(job: dict, profile: dict, matched: list[str], quote: str) -> str:
    skills = _join(matched[:4]) or "the stack you mentioned"
    pitch = (profile.get("pitch") or "").strip()
    lines = [
        f"I read your post about \"{job.get('title', 'your project')}\" and I can help. "
        f"I work with {skills}" + (f", and {pitch[0].lower() + pitch[1:]}" if pitch else "") + ".",
        "",
        "How I'd approach it:",
        "1. A short call or a few questions to pin down scope and what \"done\" looks like.",
        "2. A first working version early, so you can give feedback before the details.",
        "3. Clean handover: code, short docs, and a walkthrough.",
    ]
    if (job.get("notes") or "").strip():  # the user's own words go in as written
        lines[1:1] = ["", job["notes"].strip()]
    p = best_project(profile, job)
    if p:
        lines += ["", f"Similar work I've done: {project_line(p)}."]
    if quote:
        lines += ["", f"Based on what you've described, I'd estimate around {quote}. I'm happy to adjust once the scope is clear."]
    if profile.get("portfolio_url"):
        lines += ["", f"More of my work: {profile['portfolio_url']}"]
    return "\n".join(lines)


def template_proposal_email(job: dict, contact: dict, profile: dict, matched: list[str], quote: str) -> tuple[str, str]:
    first = split_name(contact.get("name", ""))[0].title() if contact.get("kind") != "client" else ""
    hello = f"Hi {first}," if first and len(first) > 2 else "Hi,"
    subject = f"Re: {job.get('title', 'your project')}"[:120]
    body = (f"{hello}\n\n{_proposal_core(job, profile, matched, quote)}\n\n"
            f"Would you like to have a quick chat this week?\n\nThanks,\n{_signature(profile)}")
    return subject, body


def platform_proposal(job: dict, profile: dict, matched: list[str], quote: str) -> str:
    """Text to paste into Freelancer.com / Reddit. No email involved."""
    return f"Hi,\n\n{_proposal_core(job, profile, matched, quote)}\n\nHappy to answer any questions.\n{profile.get('name', '')}"


def ai_proposal(ai: AI, job: dict, profile: dict, matched: list[str], quote: str, resume_text: str) -> str | None:
    if not ai or not ai.enabled:
        return None
    try:
        return ai.chat(
            "You write short freelance proposals. Under 150 words. Open by restating the client's actual need in one "
            "sentence, then 2-3 concrete steps you'd take, then one relevant proof point taken ONLY from the resume or the projects list "
            "(name the project and include its link). If my notes say something, use it. "
            "Never invent experience, clients or numbers. Mention the quote if given. No greetings beyond 'Hi,', no "
            "emojis, no buzzwords. Do not add a signature.",
            f"Freelancer: {profile.get('name')}. {profile.get('pitch', '')}\nSkills that match: {', '.join(matched)}\n"
            f"Resume excerpt:\n{resume_text[:2500]}\n" + _for_ai(profile, job) + f"\nProject: {job.get('title')}\nBudget: {job.get('budget') or 'not stated'}\n"
            f"Suggested quote: {quote or 'none'}\nPost:\n{(job.get('description') or '')[:3000]}",
            max_tokens=500).strip()
    except Exception:
        return None


# ---------------- LinkedIn (drafts only; you send them yourself) ----------------
LINKEDIN_NOTE_LIMIT = 200   # free accounts allow 200 characters on a connection note


def linkedin_note(job: dict, contact: dict, profile: dict, matched: list[str]) -> str:
    first = split_name(contact.get("name", ""))[0].title() or "there"
    skills = _join(matched[:2])
    note = (f"Hi {first}, I'm interested in the {job.get('title', 'open')} role at {job.get('company', 'your team')}"
            + (f". I work with {skills}" if skills else "") + ". I'd love to connect.")
    if len(note) > LINKEDIN_NOTE_LIMIT:
        note = f"Hi {first}, I'm interested in the {job.get('title', 'open')} role at {job.get('company', 'your team')} and would love to connect."
    return note[:LINKEDIN_NOTE_LIMIT]


def linkedin_dm(job: dict, contact: dict, profile: dict, matched: list[str]) -> str:
    first = split_name(contact.get("name", ""))[0].title() or "there"
    skills = _join(matched[:3])
    return (f"Thanks for connecting, {first}! I'm interested in the {job.get('title')} role at {job.get('company')}"
            + (f"; I've been working with {skills}" if skills else "")
            + ". I also sent my resume by email. Would you be open to a quick chat, or could you point me to "
              "the right person on the hiring team?\n\n"
            + (f"Job: {job['url']}\n" if job.get("url") else "") + f"{profile.get('name', '')}")


FOLLOWUPS = [
    "Hi {first},\n\nJust bringing this back to the top of your inbox in case it got buried. "
    "I'm still very interested in the {title} role and happy to share anything else that would help.\n\n"
    "Thank you,\n{sig}",
    "Hi {first},\n\nA quick follow-up on the {title} role at {company}. If there's someone else I should "
    "speak with, a name would help a lot.\n\nThank you,\n{sig}",
    "Hi {first},\n\nThis is my last note so I don't crowd your inbox. If the {title} role or a similar one "
    "opens up, I'd love to be considered. Thanks for your time either way.\n\nBest,\n{sig}",
]


FREELANCE_FOLLOWUPS = [
    "Hi,\n\nJust checking whether you're still looking for help with \"{title}\". "
    "I'm available to start this week and happy to do a quick call.\n\nThanks,\n{sig}",
    "Hi,\n\nLast note from me on \"{title}\" so I don't crowd your inbox. If you need a hand with this "
    "or anything similar later, I'd be glad to help.\n\nBest,\n{sig}",
]


def compose_followup(n: int, job: dict, contact: dict, profile: dict) -> str:
    """n is 0-based follow-up number."""
    if job.get("kind") == "freelance":
        tpl = FREELANCE_FOLLOWUPS[min(n, len(FREELANCE_FOLLOWUPS) - 1)]
        return tpl.format(title=job.get("title", "your project"), sig=_signature(profile))
    tpl = FOLLOWUPS[min(n, len(FOLLOWUPS) - 1)]
    return tpl.format(first=split_name(contact.get("name", ""))[0].title() or "there",
                      title=job.get("title", "the"), company=job.get("company", "your company"),
                      sig=_signature(profile))
