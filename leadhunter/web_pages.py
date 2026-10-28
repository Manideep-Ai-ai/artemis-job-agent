"""Dashboard pages: Pipeline board, Insights and Sources. Registered by web.create_app."""
from __future__ import annotations

from typing import Callable

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from leadhunter import sources
from leadhunter.agent import Agent
from leadhunter.config import Config
from leadhunter.insights import PIPELINE_COLUMNS, STAGE, ago, insights, overview, pipeline
from leadhunter.theme import area_chart, icon, initials, meter
from leadhunter.web import back, e

TOGGLES = [("remotive", "Remotive (remote jobs)"), ("arbeitnow", "Arbeitnow"), ("hackernews", "Hacker News: Who is hiring?"),
           ("freelancer", "Freelancer.com projects"), ("reddit", "Reddit r/forhire (needs a Reddit app)")]


def _slugs(text: str) -> tuple[list[str], list[str]]:
    """Split a comma/newline list into (valid slugs, rejected entries)."""
    items = [x.strip() for x in text.replace("\n", ",").split(",") if x.strip()]
    good = [x for x in items if sources.SLUG_RE.match(x) and ".." not in x]
    return list(dict.fromkeys(good)), [x for x in items if x not in good]


def _bars(rows: list[dict], label: str = "key", value: str = "rate", suffix: str = "%") -> str:
    if not rows:
        return '<p class="muted">No data yet.</p>'
    top = max(r[value] for r in rows) or 1
    return "".join(f"""<div class="bar"><span>{e(str(r[label]))}</span><b class="track"><i style="width:{max(2, 100 * r[value] // top)}%"></i></b>
        <span>{r[value]}{suffix}{f' · {r["replied"]}/{r["sent"]}' if 'sent' in r and 'replied' in r else ''}</span></div>""" for r in rows)


def register(app: FastAPI, a: Agent, C: Callable[[], Config], save: Callable[[Config], None],
             page: Callable[..., HTMLResponse]) -> None:

    # ================================================================ dashboard
    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(msg: str = ""):
        d = overview(a.db)
        k = d["kpi"]
        tiles = [("Matches", k["matched"], f"of {k['leads']} collected · +{k['leads_week']} this week", "leads"),
                 ("People contacted", k["contacted"], f"+{k['contacted_week']} this week", "people"),
                 ("Replies", k["replied"], f"{k['unread']} unread", "reply"),
                 ("Reply rate", f"{k['rate']}%", f"{k['interviews']} interview{'s' * (k['interviews'] != 1)}", "insights")]
        kpis = "".join(f'<div class="kpi"><div>{t}{icon(i, 20)}</div><b>{e(str(v))}</b><small>{e(h)}</small></div>' for t, v, h, i in tiles)
        stage = dict(PIPELINE_COLUMNS)
        look = {"review": "draft", "sent": "active", "replied": "replied", "interview": "interview"}

        def lead(x: dict) -> str:
            people = "".join(f'<span class="ava" title="{e(p)}">{e(initials(p))}</span>' for p in x["people"][:4])
            return f"""<div class="proj"><div><h3><a href="/job/{x['id']}">{e(x['title'] or '')}</a></h3>
                <span class="pill {look[x['stage']]}">{stage[x['stage']]}</span></div>
                <span class="muted">{e(x['company'] or '')}{f" · {e(x['fit'])} fit" if x['fit'] else ''}</span>
                <div>{meter(STAGE[x['stage']], 4)}<span class="money">{STAGE[x['stage']]}/4</span></div>
                <div><span>{people}</span><span class="muted">{e(ago(x['when']))}</span></div></div>"""

        def event(r: dict) -> str:
            if r["what"].startswith("reply:"):
                what = f'replied <span class="pill {e(r["what"][6:])}">{e(r["what"][6:])}</span>'
            else:
                what = "got your first email" if r["what"] == "initial" else "got a follow-up"
            return f"""<li><span class="ava">{e(initials(r['name']))}</span><div><p><a href="/job/{r['job_id']}"><b>{e(r['name'])}</b></a>
                <span class="muted">({e(r['company'] or '')})</span> {what}</p><small>{e(ago(r['at']))}</small></div></li>"""

        leads = "".join(lead(x) for x in d["active"]) or '<p class="muted">No one contacted yet. <a href="/">Pick a lead</a> and reach out.</p>'
        feed = f'<ol class="feed">{"".join(event(r) for r in d["activity"])}</ol>' if d["activity"] else '<p class="muted">Nothing sent yet.</p>'
        first = (C()["profile"].get("name") or "").split(" ")[0]
        body = f"""<span class="label">Dashboard</span><h1>Welcome back{f", {e(first)}" if first else ""}.</h1>
          <div class="kpis">{kpis}</div>
          <div class="card"><div class="ch"><h2>Outreach throughput</h2><p>Emails sent against replies received, last eight weeks</p></div>
            {area_chart(d["chart"], "week", "sent", "replied")}</div>
          <div class="cards2"><div class="card"><div class="ch"><h2>Active leads</h2><p>How far each conversation has got</p></div>{leads}
            <p><a href="/pipeline" class="muted">Open the pipeline →</a></p></div>
            <div class="card"><div class="ch"><h2>Recent activity</h2><p>Latest emails and replies</p></div>{feed}</div></div>"""
        return page("Dashboard", body, msg)

    # ================================================================ pipeline
    @app.get("/pipeline", response_class=HTMLResponse)
    def pipeline_page(msg: str = "", kind: str = ""):
        board = pipeline(a.db, kind)
        days = len(C().followup_days)

        def tile(c: dict) -> str:
            when = (c["reply_at"] or c["last_sent_at"] or c["created_at"] or "")[:10]
            tag = f' <span class="pill {e(c["sentiment"])}">{e(c["sentiment"])}</span>' if c["sentiment"] else ""
            move = "" if c["status"] in ("found", "drafted") else f"""<form method="post" action="/contact/{c['id']}/move" class="row">
                <select name="to" style="width:auto"><option value="">Move to…</option><option value="interview">Interview</option>
                <option value="closed">Closed</option></select><button class="quiet">Move</button></form>"""
            return f"""<div class="tile"><div><a href="/job/{c['job_id']}"><b>{e(c['name'])}</b></a>{tag}</div>
                <span class="muted">{e(c['company'] or '')} · {e(c['job_title'] or '')}</span>
                <small class="muted">{e(when)} · {c['followups_sent']}/{days} follow-ups</small>{move}</div>"""

        cols = "".join(f"""<div class="col"><h2>{t} <span class="muted">{len(board[k])}</span></h2>
            {''.join(tile(c) for c in board[k]) or '<p class="muted">Nothing here yet</p>'}</div>""" for k, t in PIPELINE_COLUMNS)
        chips = "".join(f'<a href="/pipeline{"?kind=" + k if k else ""}" class="{"on" if kind == k else ""}">{t}</a>'
                        for k, t in (("", "All"), ("job", "Jobs"), ("freelance", "Freelance")))
        empty = "" if any(board.values()) else '<div class="note"><p>Reach out to a lead to see it here.</p></div>'
        body = f"""<span class="label">Pipeline</span><div class="row"><h1 style="margin-right:auto">Everyone you've reached out to</h1><div class="chips">{chips}</div></div>
          {empty}<div class="board">{cols}</div>"""
        return page("Pipeline", body, msg)

    @app.post("/contact/{cid}/move")
    def move(cid: int, to: str = Form("")):
        if to == "interview":
            a.set_sentiment(cid, "interview")
        elif to == "closed":
            a.set_contact_status(cid, "stopped")
        else:
            return back("/pipeline", "Pick a column to move it to.")
        return back("/pipeline", "Moved.")

    # ================================================================ insights
    @app.get("/insights", response_class=HTMLResponse)
    def insights_page(msg: str = ""):
        d = insights(a.db)
        funnel = "".join(f"""<div class="stat"><b>{f['count']}</b><span>{e(f['step'])} · {f['pct']}%</span></div>""" for f in d["funnel"])
        empty = "" if d["funnel"][2]["count"] else '<div class="note"><p>Send a few emails to see what works.</p></div>'
        body = f"""<span class="label">Insights · last 30 days</span><h1>What's working</h1>{empty}
          <div class="stats">{funnel}</div>
          <div class="grid2">
            <div class="card"><span class="label">Reply rate by source</span>{_bars(d['by_source'])}</div>
            <div class="card"><span class="label">Reply rate by weekday sent</span>{_bars(d['by_weekday'])}</div>
            <div class="card"><span class="label">Reply rate by fit</span>{_bars(d['by_fit'])}</div>
            <div class="card"><span class="label">What replies say</span>{_bars(d['sentiment'], value='count', suffix='')}</div>
          </div>
          <div class="card"><span class="label">Emails sent, last 30 days</span>{_bars(d['daily'], label='day', value='sent', suffix='')}</div>"""
        return page("Insights", body, msg)

    # ================================================================ sources
    @app.get("/sources", response_class=HTMLResponse)
    def sources_page(msg: str = ""):
        s = C()["sources"]
        toggles = "".join(f'<label><input type="checkbox" name="{k}" {"checked" if s.get(k) else ""}> {t}</label>' for k, t in TOGGLES)
        boards = "".join(f"""<div class="card"><h2>{label}</h2><p class="muted">Company ids from links like <code>{e(where)}</code>, separated by commas.</p>
            <textarea name="b_{k}" style="min-height:60px">{e(', '.join(s.get(k) or []))}</textarea>
            <div class="row"><input type="text" name="t_{k}" placeholder="Try one company id" style="width:220px">
            <button class="quiet" formaction="/sources/test?board={k}">Test</button></div></div>""" for k, (label, where) in sources.BOARDS.items())
        body = f"""<p><a href="/setup" class="muted">← Settings</a></p><span class="label">Lead sources</span><h1>Where Artemis looks</h1>
          <p class="muted">Turn sources on and off and add company boards. No need to edit config.yaml.</p><form method="post" action="/sources">
          <div class="card"><h2>Free lists</h2>{toggles}
            <div class="grid2"><label><span>Freelancer search words (empty = your top skills)</span>
              <input type="text" name="freelancer_queries" value="{e(', '.join(s.get('freelancer_queries') or []))}"></label>
            <label><span>Skip leads older than (days)</span><input type="number" min="1" max="90" name="max_age_days" value="{int(s.get('max_age_days') or 7)}"></label></div></div>
          <h2>Company job boards</h2>{boards}<button>Save sources</button></form>"""
        return page("Sources", body, msg)

    def apply(form) -> str:
        """Save the sources form. Returns a note about anything that was rejected."""
        c = C()
        s = c["sources"]
        for k, _ in TOGGLES:
            s[k] = bool(form.get(k))
        s["freelancer_queries"] = [x.strip() for x in str(form.get("freelancer_queries", "")).split(",") if x.strip()]
        try:
            s["max_age_days"] = max(1, min(int(form.get("max_age_days") or 7), 90))
        except ValueError:
            pass  # keep the old value
        rejected = []
        for k in sources.BOARDS:
            s[k], bad = _slugs(str(form.get(f"b_{k}", "")))
            rejected += bad
        save(c)
        return f" Skipped invalid ids: {', '.join(rejected)[:200]}" if rejected else ""

    @app.post("/sources")
    async def save_sources(request: Request):
        return back("/sources", "Saved." + apply(await request.form()))

    @app.post("/sources/test")
    async def test_source(request: Request, board: str = ""):
        form = await request.form()
        apply(form)  # so typing in the lists isn't lost when testing
        slug = str(form.get(f"t_{board}", "")).strip()
        if board not in sources.BOARDS or not slug:
            return back("/sources", "Type a company id next to Test first.")
        try:
            jobs = getattr(sources, board)(sources.check_slug(slug))
        except Exception as ex:  # wrong id (404), network down, bad JSON...
            return back("/sources", f"{sources.BOARDS[board][0]} '{slug}' didn't work: {str(ex)[:150]}")
        return back("/sources", f"{sources.BOARDS[board][0]} '{slug}': {len(jobs)} open jobs. Add it to the list and save.")
