"""Score how well a job fits the user's profile. Transparent, keyword-based, no AI needed."""
from __future__ import annotations

import re

from leadhunter.resume import skill_patterns

FIT_THRESHOLDS = (("strong", 0.62), ("good", 0.40), ("stretch", 0.22))


def years_asked(text: str) -> float | None:
    """Smallest 'N+ years' / 'N-M years' requirement in the posting."""
    nums = []
    for a, b in re.findall(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", text, re.I):
        nums.append(float(a))
    for a in re.findall(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s*(?:of)?\s*(?:\w+\s){0,3}?experience", text, re.I):
        nums.append(float(a))
    nums = [n for n in nums if n <= 25]
    return min(nums) if nums else None


def location_ok(job_loc: str, wanted: list[str]) -> bool | None:
    """True/False if we can tell, None if the job lists no location."""
    if not wanted:
        return True
    jl = (job_loc or "").lower()
    if not jl:
        return None
    for w in wanted:
        w = w.lower().strip()
        if not w:
            continue
        if w == "remote" and any(k in jl for k in ("remote", "anywhere", "worldwide", "work from home", "wfh")):
            return True
        if w in jl:
            return True
        if w in ("bangalore", "bengaluru") and ("bangalore" in jl or "bengaluru" in jl):
            return True
        if w in ("gurgaon", "gurugram") and ("gurgaon" in jl or "gurugram" in jl):
            return True
    return False


def score_job(job: dict, profile: dict) -> dict:
    title = job.get("title") or ""
    text = f"{title}\n{job.get('description') or ''}"
    skills = profile.get("skills") or []
    matched, title_hits = [], 0
    for s in skills:
        pats = skill_patterns(s)
        if any(p.search(text) for p in pats):
            matched.append(s)
            if any(p.search(title) for p in pats):
                title_hits += 1

    # Skill coverage: 4+ matched skills is full marks; title mentions count extra.
    skill_score = min(1.0, (len(matched) + title_hits) / 4.0)

    role_score = 0.0
    tl = title.lower()
    for r in profile.get("roles") or []:
        words = [w for w in re.findall(r"[a-z0-9+#.]+", r.lower()) if w not in ("engineer", "developer", "senior", "junior")]
        if r.lower() in tl or (words and all(w in tl for w in words)):
            role_score = 1.0
            break
        if words and any(w in tl for w in words):
            role_score = max(role_score, 0.5)

    asked = years_asked(text)
    have = float(profile.get("years") or 0)
    if asked is None:
        exp_score = 0.8
    elif have + 0.5 >= asked:
        exp_score = 1.0
    elif have + 2 >= asked:
        exp_score = 0.5
    else:
        exp_score = 0.0

    freelance = job.get("kind") == "freelance"
    if freelance:
        # Projects are remote and rarely state years; judge on skills, with the title counting extra.
        loc = True
        exp_score, asked = 1.0, None
        role_score = max(role_score, min(1.0, title_hits / 2))
    else:
        loc = location_ok(job.get("location", ""), profile.get("locations") or [])
    loc_score = 1.0 if loc else (0.6 if loc is None else 0.0)

    score = 0.5 * skill_score + 0.2 * role_score + 0.15 * exp_score + 0.15 * loc_score
    below_min = False
    if freelance:
        from leadhunter.freelance import suggest_quote
        _, below_min = suggest_quote(job, profile)
        if below_min:
            score = min(score, 0.3)       # pays less than your minimum: stretch at best
    if not matched:
        score = min(score, 0.15)
    if loc is False:
        score = min(score, 0.35)          # wrong city: never better than a stretch
    if asked is not None and asked > have + 3:
        score = min(score, 0.3)           # far too senior

    fit = ""
    for name, t in FIT_THRESHOLDS:
        if score >= t:
            fit = name
            break
    return {"score": round(score, 3), "fit": fit, "matched": matched, "years_asked": asked,
            "location_ok": loc, "below_min": below_min}
