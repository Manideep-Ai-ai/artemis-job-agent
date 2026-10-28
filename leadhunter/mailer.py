"""Send email from the user's own Gmail (SMTP + app password) and read replies (IMAP).

dry_run (the default) writes each email to ./outbox/*.eml instead of sending, so you
can see exactly what would go out before turning sending on.
"""
from __future__ import annotations

import email
import email.header
import imaplib
import mimetypes
import quopri
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path

import certifi

from leadhunter.config import Config
from leadhunter.keyhealth import report_http
from leadhunter.util import html_to_text

AUTO_REPLY_RE = re.compile(r"(?i)(out of (the )?office|automatic reply|auto(matic)?[- ]?reply|on leave|vacation|away from)")
BOUNCE_FROM_RE = re.compile(r"(?i)(mailer-daemon|postmaster|mail delivery)")


# certifi's CA list: python.org builds on macOS ship without system certificates, so the default context can't verify Gmail
def tls() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, cfg: Config, outbox: str | Path = "outbox"):
        self.cfg = cfg
        m = cfg["mail"]
        self.dry_run = bool(m.get("dry_run", True))
        self.from_addr = m.get("from_address") or ""
        self.name = cfg["profile"].get("name") or ""
        self.smtp_host, self.smtp_port = m.get("smtp_host"), int(m.get("smtp_port") or 465)
        self.imap_host = m.get("imap_host")
        self.password = cfg.secret("GMAIL_APP_PASSWORD")
        self.outbox = Path(outbox)

    def ready(self) -> tuple[bool, str]:
        if self.dry_run:
            return True, "dry run: emails are written to ./outbox, not sent"
        if not self.from_addr or "@" not in self.from_addr:
            return False, "Set mail.from_address in config.yaml"
        if not self.password:
            return False, "Set GMAIL_APP_PASSWORD in .env (Google Account > Security > App passwords)"
        return True, f"sending as {self.from_addr}"

    def _refused(self) -> str:
        return (f"Gmail refused the login for {self.from_addr}. Check the address is spelled right, and use a 16-letter "
                "App Password (not your normal password): https://myaccount.google.com/apppasswords")

    def test_login(self) -> tuple[bool, str]:
        """Try to log in to Gmail SMTP and IMAP without sending anything."""
        if not self.from_addr or not self.password:
            return False, "Enter your Gmail address and app password first."
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=tls(), timeout=20) as s:
                s.login(self.from_addr, self.password)
            with imaplib.IMAP4_SSL(self.imap_host, ssl_context=tls()) as box:
                box.login(self.from_addr, self.password)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected):  # Gmail sometimes just hangs up on a bad login
            return False, self._refused()
        except imaplib.IMAP4.error as e:
            if "AUTHENTICATIONFAILED" in str(e):
                return False, self._refused()
            return False, f"Sending works, but reading replies failed ({e}). Turn on IMAP in Gmail settings."
        except (smtplib.SMTPException, OSError) as e:
            return False, f"Couldn't reach Gmail: {e}"
        return True, f"Connected to {self.from_addr}: sending and reply-checking both work."

    def build(self, to: str, subject: str, body: str, attachment: str | None = None,
              in_reply_to: str | None = None, references: str | None = None) -> EmailMessage:
        msg = EmailMessage()
        domain = self.from_addr.split("@")[-1] if "@" in self.from_addr else "leadhunter.local"
        msg["From"] = f"{self.name} <{self.from_addr}>" if self.name else self.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=domain)
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        msg.set_content(body)
        if attachment:
            p = Path(attachment)
            if p.exists() and p.stat().st_size < 8 * 1024 * 1024:
                ctype, _ = mimetypes.guess_type(p.name)
                maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
                msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
        return msg

    def send(self, msg: EmailMessage) -> str:
        """Send (or write in dry-run). Returns the Message-ID."""
        if self.dry_run:
            self.outbox.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9@._-]", "_", msg["To"])
            name = f"{datetime.now():%Y%m%d-%H%M%S}-{safe}.eml"
            (self.outbox / name).write_bytes(bytes(msg))
            return msg["Message-ID"]
        ok, why = self.ready()
        if not ok:
            raise MailError(why)
        try:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=tls(), timeout=30) as s:
                try:
                    s.login(self.from_addr, self.password)
                except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:  # Gmail may just hang up
                    report_http(self.cfg, "gmail", 401)
                    raise MailError(self._refused()) from e
                report_http(self.cfg, "gmail", 200)
                s.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            raise MailError(f"recipient refused: {e}") from e
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(str(e)) from e
        return msg["Message-ID"]

    # ---------------- replies ----------------
    def fetch_inbox(self, since: datetime) -> list[dict]:
        """Headers + short text of inbox mail since a date. Empty in dry-run."""
        if self.dry_run:
            return []
        ok, why = self.ready()
        if not ok:
            raise MailError(why)
        out = []
        try:
            with imaplib.IMAP4_SSL(self.imap_host, ssl_context=tls()) as box:
                box.login(self.from_addr, self.password)
                box.select("INBOX", readonly=True)
                typ, data = box.search(None, "SINCE", (since - timedelta(days=1)).strftime("%d-%b-%Y"))
                ids = (data[0] or b"").split()[-500:]
                for i in ids:
                    typ, parts = box.fetch(i, "(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.4000>)")
                    raw_head, raw_text = b"", b""
                    for p in parts:
                        if isinstance(p, tuple):
                            if b"HEADER" in p[0]:
                                raw_head = p[1]
                            else:
                                raw_text = p[1]
                    m = email.message_from_bytes(raw_head)
                    out.append({
                        "from": parseaddr(m.get("From", ""))[1].lower(),
                        "from_raw": m.get("From", ""),
                        "subject": str(email.header.make_header(email.header.decode_header(m.get("Subject", "")))),
                        "in_reply_to": m.get("In-Reply-To", "") or "",
                        "references": m.get("References", "") or "",
                        "text": raw_text.decode("utf-8", "ignore"),
                    })
        except (imaplib.IMAP4.error, OSError) as e:
            if "AUTHENTICATIONFAILED" in str(e):
                report_http(self.cfg, "gmail", 401)
            raise MailError(f"IMAP: {e}") from e
        return out


def match_inbox(messages: list[dict], contacts: list[dict]) -> dict[int, tuple[str, dict]]:
    """Map contact id -> ('replied' | 'bounced', the inbox message that decided it).

    contacts: dicts with id, email, first_message_id, last_message_id.
    Out-of-office auto-replies are ignored (follow-ups continue).
    """
    result: dict[int, tuple[str, dict]] = {}
    by_email = {c["email"].lower(): c for c in contacts if c.get("email")}
    by_msgid = {}
    for c in contacts:
        for k in ("first_message_id", "last_message_id"):
            if c.get(k):
                by_msgid[c[k].strip()] = c
    for m in messages:
        if BOUNCE_FROM_RE.search(m["from_raw"]) or BOUNCE_FROM_RE.search(m["from"]):
            low = m["text"].lower()
            for addr, c in by_email.items():
                if addr in low:
                    result[c["id"]] = ("bounced", m)
            continue
        c = by_email.get(m["from"])
        if not c:
            refs = f"{m['in_reply_to']} {m['references']}"
            c = next((by_msgid[mid] for mid in by_msgid if mid in refs), None)
        if not c:
            continue
        if AUTO_REPLY_RE.search(m["subject"]):
            continue
        result[c["id"]] = ("replied", m)
    return result


def classify_inbox(messages: list[dict], contacts: list[dict]) -> dict[int, str]:
    """Map contact id -> 'replied' | 'bounced'."""
    return {cid: status for cid, (status, _) in match_inbox(messages, contacts).items()}


_MIME_LINE = re.compile(r"(?i)^(--|content-[\w-]+:|mime-version:|charset=|boundary=)")
_QUOTE_START = re.compile(r"(?i)^(on .{0,200} wrote:|-----original message|from: )")


def reply_preview(text: str, limit: int = 240) -> str:
    """First lines of what they wrote, without MIME parts, quoted history or signature.
    ponytail: line heuristics on the first 4 KB of the body; parse the full MIME tree if previews look wrong."""
    if "quoted-printable" in text.lower():
        text = quopri.decodestring(text.encode("utf-8", "ignore")).decode("utf-8", "ignore")
    if "<" in text and ">" in text:
        text = html_to_text(text)
    kept = []
    for line in text.splitlines():
        line = line.strip()
        if _QUOTE_START.match(line) or line == "--":
            break
        if not line or line.startswith(">") or _MIME_LINE.match(line):
            continue
        kept.append(line)
    return " ".join(kept)[:limit]


SENTIMENT_RULES = [  # first match wins, so a polite "unfortunately" beats "interview" in the same email
    ("rejection", r"unfortunately|not (be )?moving forward|other candidates|(position|role) (has been|is) (filled|closed)|not a (good )?fit|decided not"),
    ("interview", r"interview|schedule (a|some) (call|time)|set up a (call|chat)|phone screen|calendly|your availability|available for a (call|chat)|next steps"),
    ("referral", r"forward(ed|ing)? (your|this)|pass(ed)? (it|this|your \w+) (on|along)|reach out to|cc'?d|copying|right person is|referr?(ed|al)"),
    ("question", r"\?"),
]


def reply_sentiment(text: str) -> str:
    """interview | referral | question | rejection | other.
    ponytail: keyword rules; swap in an AI call if too many land in 'other'."""
    low = text.lower()
    return next((label for label, rx in SENTIMENT_RULES if re.search(rx, low)), "other")
