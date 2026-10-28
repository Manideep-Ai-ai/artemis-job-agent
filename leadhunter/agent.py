"""The agent: collect -> match -> research -> draft -> (you approve) -> send -> follow up -> stop on reply."""
from __future__ import annotations

import json
import logging
import random
import re
import time

import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path

from leadhunter import sources
from leadhunter.ai import AI
import csv

from leadhunter.compose import (ai_proposal, compose_followup, compose_initial, linkedin_dm, linkedin_note,
                                platform_proposal, template_proposal_email)
from leadhunter.freelance import suggest_quote
from leadhunter.config import Config
from leadhunter.db import DB, iso, parse_iso
from leadhunter.joblink import fetch_job, manual_job
from leadhunter.keyhealth import report_http
from leadhunter.mailer import MailError, Mailer, match_inbox, reply_preview, reply_sentiment
from leadhunter.matching import score_job
from leadhunter.research import find_contacts
from leadhunter.resume import extract_profile, read_text
from leadhunter.search import Search

log = logging.getLogger("leadhunter")


class Agent:
    def __init__(self, cfg: Config, db: DB | None = None, mailer: Mailer | None = None,
                 search: Search | None = None, ai: AI | None = None, sleep=time.sleep, clock=None):
        self.cfg = cfg
        self.db = db or DB(cfg["database"])
        self.mailer = mailer or Mailer(cfg)
        self.search = search or Search(cfg)
        self.ai = ai or AI(cfg)
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._profile: dict | None = None
        self._resume_text = ""

    # ---------------- profile ----------------
    @property
    def profile(self) -> dict:
        if self._profile is None:
            p = dict(self.cfg["profile"])
            path = p.get("resume_path")
            if path and Path(path).exists():
                try:
                    self._resume_text = read_text(path)
                except Exception as e:  # unreadable PDF etc.
                    log.warning("Couldn't read resume %s: %s", path, e)
                if self._resume_text:
                    found = extract_profile(self._resume_text)
                    if not p.get("skills"):
                        p["skills"] = found["skills"]
                    if not p.get("years"):
                        p["years"] = found["years"]
            self._profile = p
        return self._profile

    def reload(self, cfg: Config) -> None:
        """Apply new settings (after the setup wizard) without restarting."""
        self.cfg = cfg
        self.mailer, self.search, self.ai = Mailer(cfg, self.mailer.outbox), Search(cfg), AI(cfg)
        self._profile, self._resume_text = None, ""

    # ---------------- leads ----------------
    def _store_and_match(self, job: dict, status: str = "new") -> tuple[int, dict, bool]:
        jid, created = self.db.upsert_job(job)
        m = score_job(job, self.profile)
        self.db.set_match(jid, m["score"], m["fit"], m["matched"])
        if job.get("kind") == "freelance":
            quote, _ = suggest_quote(job, self.profile)
            self.db.run("UPDATE jobs SET quote=? WHERE id=?", (quote, jid))
        if status != "new":
            self.db.run("UPDATE jobs SET status=? WHERE id=? AND status='new'", (status, jid))
        return jid, m, created

    def collect(self) -> dict:
        jobs, errors = sources.collect_all(self.cfg, self.profile.get("skills"))
        new = good = 0
        for j in jobs:
            _, m, created = self._store_and_match(j)
            new += created
            good += created and m["fit"] in ("strong", "good")
        return {"fetched": len(jobs), "new": new, "new_good_matches": good, "errors": errors}

    def add_link(self, url: str) -> tuple[int, dict]:
        job = fetch_job(url)
        jid, m, _ = self._store_and_match(job, status="shortlisted")
        return jid, m

    def add_manual(self, company: str, title: str, location: str = "", url: str = "", domain: str = "",
                   description: str = "", kind: str = "job", budget: str = "", contact_email: str = "") -> tuple[int, dict]:
        job = manual_job(company, title, location, url, description, domain)
        job["kind"] = kind if kind in ("job", "freelance") else "job"
        if budget:
            job["budget"] = budget
            nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", budget)]
            if nums:
                job["budget_min"], job["budget_max"] = min(nums), max(nums)
                job["pay_type"] = "hourly" if re.search(r"(?i)/\s*h|hour", budget) else "fixed"
                job["currency"] = "USD" if "$" in budget else ("INR" if re.search(r"₹|inr|\brs\b", budget, re.I) else "")
        if contact_email:
            job["contact_email"] = contact_email.strip().lower()
            job["how_to_apply"] = "email"
        jid, m, _ = self._store_and_match(job, status="shortlisted")
        return jid, m

    def ignore(self, job_id: int) -> None:
        self.db.run("UPDATE jobs SET status='ignored' WHERE id=?", (job_id,))

    # ---------------- reach out: research + drafts ----------------
    def reach_out(self, job_id: int) -> dict:
        """Prepare outreach for one lead: find people (jobs) or the client (freelance), then draft
        emails / LinkedIn notes / platform proposals. Nothing is sent here."""
        row = self.db.job(job_id)
        if not row:
            raise ValueError(f"No lead with id {job_id}")
        job = dict(row)
        if not self.cfg.outreach_enabled:
            return {"domain": "", "contacts": [], "added": 0, "proposal": None,
                    "notes": ["You're in leads-only mode, so nothing is drafted. Change the mode in setup to reach out."]}
        res = {"domain": job.get("company_domain") or "", "contacts": [], "added": 0, "proposal": None, "notes": []}

        # 1) The post itself names an email: that's the best contact there is.
        if job.get("contact_email") and not self.db.is_suppressed(job["contact_email"]):
            freelance = job.get("kind") == "freelance"
            by = f" (posted by {job['contact_name']})" if job.get("contact_name") else ""
            c = {"company": job.get("company", ""), "name": "Client" if freelance else "Hiring team",
                 "title": ("Posted this project" if freelance else "Contact from the job post") + by, "email": job["contact_email"],
                 "email_confidence": "posted", "reason": "email given in the post", "score": 1.0,
                 "kind": "client" if freelance else "posted", "source_url": job.get("url", "")}
            cid = self.db.add_contact(job_id, c)
            res["contacts"].append(c)
            if cid:
                res["added"] += 1
                self.draft(cid)

        # 2) Freelance without an email: proposal to paste on the platform.
        if job.get("kind") == "freelance":
            if not job.get("contact_email"):
                res["proposal"] = self.draft_platform_proposal(job_id)
                res["notes"].append("This client is contacted on the site itself. Copy the proposal and post it there.")
        # 3) Jobs: find recruiter + hiring manager by web search.
        else:
            found = find_contacts(job, self.cfg, self.search, self.ai)
            res["notes"] += [n for n in found["notes"] if not (res["contacts"] and "No recruiter" in n)]
            if found["domain"] and not job.get("company_domain"):
                self.db.run("UPDATE jobs SET company_domain=? WHERE id=?", (found["domain"], job_id))
                res["domain"] = found["domain"]
            for c in found["contacts"]:
                cid = self.db.add_contact(job_id, c)
                res["contacts"].append(c)
                if cid:
                    res["added"] += 1
                    self.draft(cid)
        self.db.run("UPDATE jobs SET status='researched' WHERE id=?", (job_id,))
        return res

    research = reach_out  # v0.2 name

    def draft_platform_proposal(self, job_id: int) -> int | None:
        if self.db.one("SELECT 1 FROM messages WHERE job_id=? AND channel='platform'", (job_id,)):
            return None
        job = dict(self.db.job(job_id))
        text = self._platform_text(job)
        return self.db.add_message(None, "proposal", 0, job.get("title", ""), text, "draft", channel="platform", job_id=job_id)

    def draft_linkedin(self, contact_id: int) -> None:
        c = dict(self.db.contact(contact_id))
        if not c.get("linkedin_url") or not self.cfg["linkedin"].get("drafts", True):
            return
        if self.db.one("SELECT 1 FROM messages WHERE contact_id=? AND channel='linkedin'", (contact_id,)):
            return
        job = dict(self.db.job(c["job_id"]))
        matched = json.loads(job.get("matched") or "[]")
        self.db.add_message(contact_id, "linkedin_note", 0, "Connection note", linkedin_note(job, c, self.profile, matched),
                            "draft", channel="linkedin")
        self.db.add_message(contact_id, "linkedin_dm", 1, "Message after they accept", linkedin_dm(job, c, self.profile, matched),
                            "draft", channel="linkedin")

    def mark_done(self, message_id: int) -> None:
        """You posted the proposal / sent the LinkedIn message yourself."""
        self.db.run("UPDATE messages SET status='done', sent_at=? WHERE id=? AND channel!='email'",
                    (iso(self.clock()), message_id))

    def export_csv(self, path: str, kind: str = "", fit: str = "") -> int:
        where, args = ["status!='ignored'"], []
        if kind:
            where.append("kind=?"); args.append(kind)
        if fit:
            where.append("fit=?"); args.append(fit)
        rows = self.db.q(f"SELECT * FROM jobs WHERE {' AND '.join(where)} ORDER BY score DESC", tuple(args))
        cols = ["id", "kind", "fit", "score", "title", "company", "location", "budget", "quote", "contact_email",
                "url", "source", "posted_at", "matched", "status"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for r in rows:
                w.writerow([", ".join(json.loads(r["matched"] or "[]")) if c == "matched" else r[c] for c in cols])
        return len(rows)

    def add_contact(self, job_id: int, name: str, email: str, title: str = "", linkedin_url: str = "") -> int | None:
        job = self.db.job(job_id)
        email = email.strip().lower()
        if email and "@" in email and not job["company_domain"]:
            self.db.run("UPDATE jobs SET company_domain=? WHERE id=?", (email.split("@")[1], job_id))
        cid = self.db.add_contact(job_id, {
            "company": job["company"], "name": name, "title": title,
            "linkedin_url": linkedin_url, "email": email, "email_confidence": "yours" if email else "",
            "reason": "added by you", "score": 1.0})
        if cid:
            self.draft(cid)
        return cid

    def draft(self, contact_id: int) -> int | None:
        self.draft_linkedin(contact_id)        # LinkedIn drafts don't need an email address
        c = dict(self.db.contact(contact_id))
        if not c.get("email"):
            return None
        if self.db.is_suppressed(c["email"]) or self.db.emailed_before(c["email"]):
            self.db.run("UPDATE contacts SET status='stopped' WHERE id=?", (contact_id,))
            return None
        if self.db.one("SELECT 1 FROM messages WHERE contact_id=? AND kind='initial'", (contact_id,)):
            return None
        job = dict(self.db.job(c["job_id"]))
        subject, body = self._first_email(job, c)
        status = "draft" if self.cfg["outreach"].get("require_approval", True) or c.get("email_confidence") == "guessed" else "approved"
        mid = self.db.add_message(contact_id, "initial", 0, subject, body, status)
        self.db.run("UPDATE contacts SET status='drafted', subject=? WHERE id=?", (subject, contact_id))
        return mid

    def _platform_text(self, job: dict) -> str:
        matched = json.loads(job.get("matched") or "[]")
        return ai_proposal(self.ai, job, self.profile, matched, job.get("quote") or "", self._resume_text) \
            or platform_proposal(job, self.profile, matched, job.get("quote") or "")

    def _first_email(self, job: dict, c: dict) -> tuple[str, str]:
        matched = json.loads(job.get("matched") or "[]")
        if job.get("kind") != "freelance":
            return compose_initial(job, c, self.profile, matched, self.ai, self._resume_text)
        quote = job.get("quote") or ""
        subject, body = template_proposal_email(job, c, self.profile, matched, quote)
        ai_text = ai_proposal(self.ai, job, self.profile, matched, quote, self._resume_text)
        return subject, (f"Hi,\n\n{ai_text}\n\nThanks,\n{self.profile.get('name', '')}" if ai_text else body)

    def set_notes(self, job_id: int, notes: str) -> int:
        """Save your notes for a lead and rewrite its unsent drafts with them. Returns how many were rewritten."""
        self.db.run("UPDATE jobs SET notes=? WHERE id=?", (notes.strip()[:2000], job_id))
        job, n = dict(self.db.job(job_id)), 0
        for m in self.db.q("""SELECT * FROM messages WHERE job_id=? AND status='draft'
                              AND (channel='platform' OR (channel='email' AND kind='initial'))""", (job_id,)):
            subject, body = (m["subject"], self._platform_text(job)) if m["channel"] == "platform" \
                else self._first_email(job, dict(self.db.contact(m["contact_id"])))
            self.db.run("UPDATE messages SET subject=?, body=? WHERE id=?", (subject, body, m["id"]))
            n += 1
        return n

    def approve(self, message_id: int, subject: str | None = None, body: str | None = None) -> None:
        if subject is not None or body is not None:
            self.save_draft(message_id, subject, body)
        self.db.run("UPDATE messages SET status='approved' WHERE id=? AND status='draft' AND channel='email'", (message_id,))

    def save_draft(self, message_id: int, subject: str | None, body: str | None) -> None:
        row = self.db.one("SELECT * FROM messages WHERE id=?", (message_id,))
        if not row or row["status"] not in ("draft", "approved"):
            return
        self.db.run("UPDATE messages SET subject=?, body=? WHERE id=?",
                    (subject if subject is not None else row["subject"], body if body is not None else row["body"], message_id))
        if subject is not None:
            self.db.run("UPDATE contacts SET subject=? WHERE id=?", (subject, row["contact_id"]))

    def skip(self, message_id: int) -> None:
        row = self.db.one("SELECT contact_id FROM messages WHERE id=?", (message_id,))
        self.db.run("UPDATE messages SET status='skipped' WHERE id=? AND status IN ('draft','approved')", (message_id,))
        if row:
            self.db.run("UPDATE contacts SET status='stopped' WHERE id=? AND status='drafted'", (row["contact_id"],))

    def set_contact_status(self, contact_id: int, status: str) -> None:
        """Manually mark replied / stopped (e.g. they answered on LinkedIn)."""
        assert status in ("replied", "stopped", "done", "active")
        self.db.run("UPDATE contacts SET status=? WHERE id=?", (status, contact_id))
        if status in ("replied", "stopped", "done"):
            self.db.run("UPDATE messages SET status='skipped' WHERE contact_id=? AND status IN ('draft','approved')", (contact_id,))

    def set_sentiment(self, contact_id: int, sentiment: str) -> None:
        """Hand-tag a reply (e.g. move a card to Interview on the pipeline board)."""
        assert sentiment in ("interview", "referral", "question", "rejection", "other")
        self.set_contact_status(contact_id, "replied")
        self.db.run("UPDATE contacts SET sentiment=?, reply_read=1 WHERE id=?", (sentiment, contact_id))

    def mark_read(self, contact_id: int | None = None) -> None:
        """Mark one reply as read, or all of them when contact_id is None."""
        if contact_id is None:
            self.db.run("UPDATE contacts SET reply_read=1 WHERE reply_read=0")
        else:
            self.db.run("UPDATE contacts SET reply_read=1 WHERE id=?", (contact_id,))

    # ---------------- limits ----------------
    def _local_now(self) -> datetime:
        return self.clock().astimezone()

    def sent_today(self) -> int:
        local = self._local_now()
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return self.db.sent_since(midnight.astimezone(timezone.utc))

    def remaining_today(self) -> int:
        return max(0, self.cfg.daily_limit - self.sent_today())

    def in_send_window(self) -> bool:
        win = self.cfg["outreach"].get("send_window") or []
        local = self._local_now()
        if self.cfg["outreach"].get("weekdays_only", True) and local.weekday() >= 5:
            return False
        if len(win) != 2:
            return True
        hhmm = local.strftime("%H:%M")
        return win[0] <= hhmm <= win[1]

    # ---------------- sending ----------------
    def due_followups(self) -> list[tuple[dict, int]]:
        days = self.cfg.followup_days
        out = []
        for c in self.db.q("SELECT * FROM contacts WHERE status='active' ORDER BY last_sent_at"):
            c = dict(c)
            n = c["followups_sent"] or 0
            if n >= len(days):
                continue
            last = parse_iso(c["last_sent_at"])
            if last and self.clock() - last >= timedelta(days=days[n]):
                out.append((c, n))
        return out

    def finish_exhausted(self) -> int:
        """Contacts who got every follow-up and still no reply after the last wait -> done."""
        days = self.cfg.followup_days
        wait = days[-1] if days else 7
        n = 0
        for c in self.db.q("SELECT * FROM contacts WHERE status='active' AND followups_sent>=?", (len(days),)):
            last = parse_iso(c["last_sent_at"])
            if last and self.clock() - last >= timedelta(days=wait):
                self.db.run("UPDATE contacts SET status='done' WHERE id=?", (c["id"],))
                n += 1
        return n

    def _attachment(self) -> str | None:
        if self.cfg["outreach"].get("attach_resume", True):
            path = self.profile.get("resume_path")
            if path and Path(path).exists():
                return path
        return None

    def _gap(self) -> None:
        if self.mailer.dry_run:
            return
        o = self.cfg["outreach"]
        lo, hi = float(o.get("min_gap_minutes") or 0), float(o.get("max_gap_minutes") or 0)
        if hi > 0:
            self.sleep(random.uniform(lo, max(lo, hi)) * 60)

    def send_due(self, wait: bool = True, force_window: bool = False) -> dict:
        """Send follow-ups that are due, then approved first emails, within today's limit."""
        report = {"sent": 0, "followups": 0, "failed": 0, "skipped_reason": "", "errors": []}
        ok, why = self.mailer.ready()
        if not ok:
            report["skipped_reason"] = why
            return report
        if not force_window and not self.in_send_window():
            report["skipped_reason"] = "outside the send window (working hours on weekdays)"
            return report
        try:
            self.check_replies()          # never follow up someone who already answered
        except MailError as e:
            report["errors"].append(f"reply check: {e}")
        self.finish_exhausted()

        queue: list[tuple[str, dict, int | None]] = []
        for c, n in self.due_followups():
            queue.append(("followup", c, n))
        for m in self.db.q("""SELECT m.id mid, c.* FROM messages m JOIN contacts c ON c.id=m.contact_id
                              JOIN jobs j ON j.id=c.job_id
                              WHERE m.status='approved' AND m.kind='initial' ORDER BY j.score DESC, m.id"""):
            queue.append(("initial", dict(m), m["mid"]))

        first = True
        for kind, c, extra in queue:
            if self.remaining_today() <= 0:
                report["skipped_reason"] = f"daily limit of {self.cfg.daily_limit} reached"
                break
            if self.db.is_suppressed(c["email"]):
                self.set_contact_status(c["id"], "stopped")
                continue
            if not first and wait:
                self._gap()
            first = False
            try:
                if kind == "initial":
                    self._send_initial(c, extra)
                    report["sent"] += 1
                else:
                    self._send_followup(c, extra)
                    report["followups"] += 1
            except MailError as e:
                report["failed"] += 1
                report["errors"].append(f"{c['email']}: {e}")
                if "refused" in str(e):
                    self.db.suppress(c["email"], "refused")
                    self.db.run("UPDATE contacts SET status='bounced' WHERE id=?", (c["id"],))
        return report

    def _send_initial(self, c: dict, message_id: int) -> None:
        m = self.db.one("SELECT * FROM messages WHERE id=?", (message_id,))
        msg = self.mailer.build(c["email"], m["subject"], m["body"], self._attachment())
        try:
            msgid = self.mailer.send(msg)
        except MailError as e:
            self.db.run("UPDATE messages SET status='failed', error=? WHERE id=?", (str(e), message_id))
            raise
        t = iso(self.clock())
        self.db.run("UPDATE messages SET status='sent', sent_at=?, message_id=? WHERE id=?", (t, msgid, message_id))
        self.db.run("""UPDATE contacts SET status='active', last_sent_at=?, first_message_id=?, last_message_id=?,
                       subject=? WHERE id=?""", (t, msgid, msgid, m["subject"], c["id"]))

    def _send_followup(self, c: dict, n: int) -> None:
        job = dict(self.db.job(c["job_id"]))
        body = compose_followup(n, job, c, self.profile)
        subject = c["subject"] or ""
        subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        ids = [c["first_message_id"], c["last_message_id"]]
        refs = " ".join(dict.fromkeys(x for x in ids if x))  # unique, keeps order
        msg = self.mailer.build(c["email"], subject, body, None, in_reply_to=c["last_message_id"], references=refs)
        mid = self.db.add_message(c["id"], "followup", n + 1, subject, body, "approved")
        try:
            msgid = self.mailer.send(msg)
        except MailError as e:
            self.db.run("UPDATE messages SET status='failed', error=? WHERE id=?", (str(e), mid))
            raise
        t = iso(self.clock())
        self.db.run("UPDATE messages SET status='sent', sent_at=?, message_id=? WHERE id=?", (t, msgid, mid))
        self.db.run("UPDATE contacts SET followups_sent=followups_sent+1, last_sent_at=?, last_message_id=? WHERE id=?",
                    (t, msgid, c["id"]))

    # ---------------- replies ----------------
    def check_replies(self) -> dict:
        active = [dict(r) for r in self.db.q("SELECT * FROM contacts WHERE status='active'")]
        if not active:
            return {"replied": 0, "bounced": 0}
        since = min(parse_iso(c["last_sent_at"]) or self.clock() for c in active) - timedelta(days=30)
        since = max(since, self.clock() - timedelta(days=60))
        inbox = self.mailer.fetch_inbox(since)
        result = match_inbox(inbox, active)
        counts = {"replied": 0, "bounced": 0}
        news = []
        for cid, (status, m) in result.items():
            c = next(x for x in active if x["id"] == cid)
            if status == "bounced":
                self.db.run("UPDATE contacts SET status='bounced' WHERE id=?", (cid,))
                self.db.suppress(c["email"], "bounced")
            else:
                preview = reply_preview(m["text"])
                sentiment = reply_sentiment(f"{m['subject']} {preview}")
                self.db.run("UPDATE contacts SET status='replied', reply_at=?, reply_preview=?, sentiment=?, reply_read=0 WHERE id=?",
                            (iso(self.clock()), preview, sentiment, cid))
                news.append(f"{c['name']} ({c['company']}), {sentiment}: {preview[:120]}")
            counts[status] += 1
        if news:
            self.notify("New replies:\n" + "\n".join(news))
        return counts

    def notify(self, text: str) -> bool:
        """Telegram alert when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Never raises."""
        token, chat = self.cfg.secret("TELEGRAM_BOT_TOKEN"), self.cfg.secret("TELEGRAM_CHAT_ID")
        if not (token and chat):
            return False
        try:
            httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": text[:4000]}, timeout=15).raise_for_status()
            report_http(self.cfg, "telegram", 200)
            return True
        except httpx.HTTPStatusError as e:  # log the status only: the URL holds the bot token
            report_http(self.cfg, "telegram", 401 if e.response.status_code == 404 else e.response.status_code)  # 404 = not a token
            log.warning("Telegram alert failed: HTTP %s", e.response.status_code)
        except httpx.HTTPError as e:
            log.warning("Telegram alert failed: %s", type(e).__name__)
        return False

    # ---------------- loop ----------------
    def tick(self) -> dict:
        return self.send_due(wait=True)

    def run_forever(self, collect_every_hours: float = 6, tick_minutes: float = 15) -> None:
        last_collect = 0.0
        while True:
            if time.time() - last_collect >= collect_every_hours * 3600:
                try:
                    log.info("collect: %s", self.collect())
                except Exception as e:
                    log.error("collect failed: %s", e)
                last_collect = time.time()
            try:
                log.info("tick: %s", self.tick())
            except Exception as e:
                log.error("tick failed: %s", e)
            self.sleep(tick_minutes * 60)
