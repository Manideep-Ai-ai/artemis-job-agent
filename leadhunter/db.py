"""Local SQLite storage. Everything stays on the user's computer."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    uid TEXT UNIQUE NOT NULL,
    source TEXT, title TEXT, company TEXT, company_domain TEXT,
    location TEXT, url TEXT, description TEXT,
    posted_at TEXT, collected_at TEXT,
    score REAL DEFAULT 0, fit TEXT DEFAULT '', matched TEXT DEFAULT '[]',
    status TEXT DEFAULT 'new'          -- new | shortlisted | researched | ignored
);
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    job_id INTEGER REFERENCES jobs(id),
    company TEXT, name TEXT, title TEXT,
    linkedin_url TEXT, source_url TEXT,
    email TEXT, email_confidence TEXT,   -- verified | likely | guessed
    score REAL DEFAULT 0, reason TEXT,
    status TEXT DEFAULT 'found',         -- found | drafted | active | replied | bounced | stopped | done
    followups_sent INTEGER DEFAULT 0,
    last_sent_at TEXT, first_message_id TEXT, last_message_id TEXT, subject TEXT,
    created_at TEXT,
    UNIQUE(job_id, name)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    kind TEXT,                           -- initial | followup
    seq INTEGER DEFAULT 0,
    subject TEXT, body TEXT,
    status TEXT DEFAULT 'draft',         -- draft | approved | sent | skipped | failed
    message_id TEXT, created_at TEXT, sent_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS suppression (
    email TEXT PRIMARY KEY, reason TEXT, at TEXT
);
"""

# Columns added after v0.2. Applied with ALTER TABLE so old databases keep working.
MIGRATIONS = [
    ("jobs", "kind", "TEXT DEFAULT 'job'"),            # job | freelance
    ("jobs", "budget", "TEXT DEFAULT ''"),             # human text, e.g. "$25–50/hr"
    ("jobs", "budget_min", "REAL"),
    ("jobs", "budget_max", "REAL"),
    ("jobs", "currency", "TEXT DEFAULT ''"),
    ("jobs", "pay_type", "TEXT DEFAULT ''"),           # fixed | hourly | ''
    ("jobs", "quote", "TEXT DEFAULT ''"),              # suggested quote for freelance leads
    ("jobs", "contact_email", "TEXT DEFAULT ''"),      # email published in the post itself
    ("jobs", "contact_name", "TEXT DEFAULT ''"),
    ("jobs", "how_to_apply", "TEXT DEFAULT ''"),       # email | platform | link
    ("contacts", "kind", "TEXT DEFAULT ''"),           # recruiter | manager | client | posted | yours
    ("messages", "job_id", "INTEGER"),
    ("messages", "channel", "TEXT DEFAULT 'email'"),   # email | linkedin | platform
    ("contacts", "reply_at", "TEXT"),
    ("contacts", "reply_preview", "TEXT DEFAULT ''"),  # first lines of their reply
    ("contacts", "sentiment", "TEXT DEFAULT ''"),      # interview | referral | question | rejection | other
    ("contacts", "reply_read", "INTEGER DEFAULT 1"),   # 0 = new reply the user hasn't opened
    ("jobs", "notes", "TEXT DEFAULT ''"),              # the user's own notes, used when writing the email / proposal
]


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class DB:
    def __init__(self, path: str | Path = "leadhunter.db"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        for table, col, decl in MIGRATIONS:
            cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ---------- generic ----------
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def run(self, sql: str, args: tuple = ()) -> int:
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    # ---------- jobs ----------
    def upsert_job(self, job: dict) -> tuple[int, bool]:
        """Insert a job if new. Returns (id, created)."""
        row = self.one("SELECT id FROM jobs WHERE uid=?", (job["uid"],))
        if row:
            return row["id"], False
        jid = self.run(
            """INSERT INTO jobs(uid, source, title, company, company_domain, location, url,
               description, posted_at, collected_at, kind, budget, budget_min, budget_max, currency,
               pay_type, contact_email, contact_name, how_to_apply)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job["uid"], job.get("source", ""), job.get("title", ""), job.get("company", ""),
             job.get("company_domain", ""), job.get("location", ""), job.get("url", ""),
             (job.get("description") or "")[:20000], job.get("posted_at") or "", iso(),
             job.get("kind") or "job", job.get("budget") or "", job.get("budget_min"), job.get("budget_max"),
             job.get("currency") or "", job.get("pay_type") or "", (job.get("contact_email") or "").lower(),
             job.get("contact_name") or "", job.get("how_to_apply") or ""),
        )
        return jid, True

    def set_match(self, job_id: int, score: float, fit: str, matched: list[str]) -> None:
        self.run("UPDATE jobs SET score=?, fit=?, matched=? WHERE id=?",
                 (score, fit, json.dumps(matched), job_id))

    def job(self, job_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM jobs WHERE id=?", (job_id,))

    # ---------- contacts ----------
    def add_contact(self, job_id: int, c: dict) -> int | None:
        try:
            return self.run(
                """INSERT INTO contacts(job_id, company, name, title, linkedin_url, source_url,
                   email, email_confidence, score, reason, created_at, kind)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, c.get("company", ""), c["name"], c.get("title", ""), c.get("linkedin_url", ""),
                 c.get("source_url", ""), c.get("email", ""), c.get("email_confidence", ""),
                 c.get("score", 0), c.get("reason", ""), iso(), c.get("kind", "")),
            )
        except sqlite3.IntegrityError:
            return None

    def contact(self, cid: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM contacts WHERE id=?", (cid,))

    def emailed_before(self, email: str) -> bool:
        """True if we've already started a thread with this address (any job)."""
        if not email:
            return False
        return bool(self.one(
            "SELECT 1 FROM contacts WHERE lower(email)=lower(?) AND status IN ('active','replied','bounced','stopped','done')",
            (email,)))

    # ---------- suppression ----------
    def suppress(self, email: str, reason: str) -> None:
        self.run("INSERT OR REPLACE INTO suppression(email, reason, at) VALUES (lower(?),?,?)",
                 (email, reason, iso()))

    def is_suppressed(self, email: str) -> bool:
        return bool(self.one("SELECT 1 FROM suppression WHERE email=lower(?)", (email or "",)))

    # ---------- messages ----------
    def add_message(self, contact_id: int | None, kind: str, seq: int, subject: str, body: str,
                    status: str = "draft", channel: str = "email", job_id: int | None = None) -> int:
        if job_id is None and contact_id is not None:
            row = self.one("SELECT job_id FROM contacts WHERE id=?", (contact_id,))
            job_id = row["job_id"] if row else None
        return self.run(
            """INSERT INTO messages(contact_id, kind, seq, subject, body, status, created_at, channel, job_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (contact_id, kind, seq, subject, body, status, iso(), channel, job_id))

    def sent_since(self, since: datetime) -> int:
        row = self.one("SELECT COUNT(*) n FROM messages WHERE status='sent' AND sent_at>=?", (iso(since),))
        return row["n"] if row else 0

    def stats(self) -> dict:
        def n(sql: str) -> int:
            return self.one(sql)[0]
        return {
            "jobs": n("SELECT COUNT(*) FROM jobs WHERE kind='job'"),
            "freelance": n("SELECT COUNT(*) FROM jobs WHERE kind='freelance'"),
            "matched": n("SELECT COUNT(*) FROM jobs WHERE fit!=''"),
            "contacts": n("SELECT COUNT(*) FROM contacts"),
            "drafts": n("SELECT COUNT(*) FROM messages WHERE status='draft' AND channel='email'"),
            "approved": n("SELECT COUNT(*) FROM messages WHERE status='approved' AND channel='email'"),
            "manual": n("SELECT COUNT(*) FROM messages WHERE status='draft' AND channel!='email'"),
            "sent": n("SELECT COUNT(*) FROM messages WHERE status='sent'"),
            "active": n("SELECT COUNT(*) FROM contacts WHERE status='active'"),
            "replied": n("SELECT COUNT(*) FROM contacts WHERE status='replied'"),
            "bounced": n("SELECT COUNT(*) FROM contacts WHERE status='bounced'"),
        }
