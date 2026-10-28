"""Read-only views over the database for the Pipeline board and the Insights page.

Plain dicts and lists, so any frontend (server HTML today, a JSON API later) can use them.
"""
from __future__ import annotations

from datetime import timedelta

from leadhunter.db import DB, now, parse_iso

PIPELINE_COLUMNS = [
    ("review", "To review"),
    ("sent", "Sent"),
    ("replied", "Replied"),
    ("interview", "Interview"),
    ("closed", "Closed"),
]


def pipeline_column(c: dict) -> str:
    status, sentiment = c["status"], c.get("sentiment") or ""
    if status in ("found", "drafted"):
        return "review"
    if status == "active":
        return "sent"
    if status == "replied":
        return {"interview": "interview", "rejection": "closed"}.get(sentiment, "replied")
    return "closed"  # bounced | stopped | done


def pipeline(db: DB, kind: str = "") -> dict[str, list[dict]]:
    """Contacts grouped into board columns, newest activity first."""
    where, args = "j.status!='ignored'", ()
    if kind in ("job", "freelance"):
        where, args = where + " AND j.kind=?", (kind,)
    rows = db.q(f"""SELECT c.*, j.title job_title, j.kind job_kind FROM contacts c JOIN jobs j ON j.id=c.job_id
                    WHERE {where} ORDER BY COALESCE(c.reply_at, c.last_sent_at, c.created_at) DESC""", args)
    board: dict[str, list[dict]] = {k: [] for k, _ in PIPELINE_COLUMNS}
    for r in rows:
        c = dict(r)
        board[pipeline_column(c)].append(c)
    return board


# A contact counts as "contacted" once their first email went out.
_SENT = """FROM contacts c JOIN jobs j ON j.id=c.job_id
           JOIN messages m ON m.contact_id=c.id AND m.kind='initial' AND m.channel='email' AND m.status='sent'"""


def _rates(db: DB, key_sql: str) -> list[dict]:
    rows = db.q(f"""SELECT {key_sql} k, COUNT(*) sent, SUM(c.status='replied') replied {_SENT}
                    GROUP BY k ORDER BY sent DESC""")
    return [{"key": r["k"] or "unknown", "sent": r["sent"], "replied": r["replied"] or 0,
             "rate": round(100 * (r["replied"] or 0) / r["sent"]) if r["sent"] else 0} for r in rows]


WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def insights(db: DB) -> dict:
    n = lambda sql: db.one(sql)[0] or 0  # noqa: E731
    funnel = [
        ("Leads found", n("SELECT COUNT(*) FROM jobs WHERE status!='ignored'")),
        ("Matched", n("SELECT COUNT(*) FROM jobs WHERE status!='ignored' AND fit!=''")),
        ("Contacted", n(f"SELECT COUNT(*) {_SENT}")),
        ("Replied", n(f"SELECT COUNT(*) {_SENT} WHERE c.status='replied'")),
        ("Interview", n(f"SELECT COUNT(*) {_SENT} WHERE c.status='replied' AND c.sentiment='interview'")),
    ]
    # ponytail: weekday is taken from the stored UTC time; off by one only for sends near midnight
    by_weekday = _rates(db, "CAST(strftime('%w', m.sent_at) AS INTEGER)")
    for r in by_weekday:
        r["key"] = WEEKDAYS[r["key"]] if isinstance(r["key"], int) else r["key"]
    sentiment = [{"key": r["s"] or "other", "count": r["n"]} for r in db.q(
        "SELECT sentiment s, COUNT(*) n FROM contacts WHERE status='replied' GROUP BY s ORDER BY n DESC")]
    daily = [{"day": r["d"], "sent": r["n"]} for r in db.q(
        """SELECT date(sent_at) d, COUNT(*) n FROM messages WHERE status='sent' AND channel='email'
           AND sent_at >= date('now', '-30 days') GROUP BY d ORDER BY d""")]
    return {
        "funnel": [{"step": s, "count": c, "pct": round(100 * c / funnel[0][1]) if funnel[0][1] else 0} for s, c in funnel],
        "by_source": _rates(db, "j.source"),
        "by_weekday": by_weekday,
        "by_fit": _rates(db, "NULLIF(j.fit, '')"),
        "sentiment": sentiment,
        "daily": daily,
    }


STAGE = {"closed": 0, "review": 1, "sent": 2, "replied": 3, "interview": 4}  # how far a lead got, for the progress meter


def ago(ts: str | None) -> str:
    dt = parse_iso(ts)
    if not dt:
        return ""
    s = int((now() - dt).total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= size:
            return f"{s // size}{unit} ago"
    return "just now"


def overview(db: DB, weeks: int = 8) -> dict:
    """Numbers for the Dashboard: KPI tiles, weekly sent vs replied, leads in motion, recent activity."""
    n = lambda sql: db.one(sql)[0] or 0  # noqa: E731
    contacted = n(f"SELECT COUNT(*) {_SENT}")
    replied = n(f"SELECT COUNT(*) {_SENT} WHERE c.status='replied'")
    kpi = {
        "leads": n("SELECT COUNT(*) FROM jobs WHERE status!='ignored'"),
        "matched": n("SELECT COUNT(*) FROM jobs WHERE status!='ignored' AND fit!=''"),
        "leads_week": n("SELECT COUNT(*) FROM jobs WHERE status!='ignored' AND collected_at >= datetime('now','-7 days')"),
        "contacted": contacted,
        "contacted_week": n(f"SELECT COUNT(*) {_SENT} WHERE m.sent_at >= datetime('now','-7 days')"),
        "replied": replied,
        "unread": n("SELECT COUNT(*) FROM contacts WHERE reply_read=0"),
        "rate": round(100 * replied / contacted) if contacted else 0,
        "interviews": n("SELECT COUNT(*) FROM contacts WHERE status='replied' AND sentiment='interview'"),
    }

    # ponytail: weeks are rolling 7-day windows ending today, not calendar weeks
    today = now().date()
    chart = [{"week": (today - timedelta(days=7 * (weeks - i) - 1)).strftime("%b %d"), "sent": 0, "replied": 0} for i in range(weeks)]

    def bucket(rows: list, key: str) -> None:
        for r in rows:
            dt = parse_iso(r[0])
            i = weeks - 1 - (today - dt.date()).days // 7 if dt else -1
            if 0 <= i < weeks:
                chart[i][key] += 1
    since = f"date('now','-{7 * weeks} days')"
    bucket(db.q(f"SELECT sent_at FROM messages WHERE status='sent' AND channel='email' AND sent_at >= {since}"), "sent")
    bucket(db.q(f"SELECT reply_at FROM contacts WHERE reply_at >= {since}"), "replied")

    leads: dict[int, dict] = {}
    for r in db.q("SELECT c.*, j.title job_title, j.fit FROM contacts c JOIN jobs j ON j.id=c.job_id WHERE j.status!='ignored'"):
        c = dict(r)
        col = pipeline_column(c)
        lead = leads.setdefault(c["job_id"], {"id": c["job_id"], "title": c["job_title"], "company": c["company"], "fit": c["fit"],
                                              "stage": "closed", "people": [], "when": ""})
        lead["people"].append(c["name"])
        if STAGE[col] > STAGE[lead["stage"]]:
            lead["stage"] = col
        lead["when"] = max(lead["when"], c["reply_at"] or c["last_sent_at"] or c["created_at"] or "")
    active = sorted((x for x in leads.values() if x["stage"] != "closed"), key=lambda x: x["when"], reverse=True)[:5]

    activity = [dict(r) for r in db.q("""
        SELECT c.name, c.company, c.job_id, m.kind what, m.sent_at at FROM messages m JOIN contacts c ON c.id=m.contact_id
          WHERE m.status='sent' AND m.channel='email'
        UNION ALL SELECT name, company, job_id, 'reply:' || COALESCE(sentiment, 'other'), reply_at FROM contacts WHERE reply_at IS NOT NULL
        ORDER BY at DESC LIMIT 8""")]
    return {"kpi": kpi, "chart": chart, "active": active, "activity": activity}


def nav_counts(db: DB) -> dict[str, int]:
    """Badge numbers for the top tabs."""
    return {
        "unread": db.one("SELECT COUNT(*) FROM contacts WHERE reply_read=0")[0],
        "drafts": db.one("SELECT COUNT(*) FROM messages WHERE status='draft' AND channel='email'")[0],
    }
