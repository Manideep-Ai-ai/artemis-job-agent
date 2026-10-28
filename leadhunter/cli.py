"""Command line: `leadhunter <command>` (or `python -m leadhunter <command>`)."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

from leadhunter.agent import Agent
from leadhunter.config import load_config
from leadhunter.joblink import JobLinkError


def _agent(args) -> Agent:
    return Agent(load_config(args.config, args.env))


def cmd_init(args):
    data = Path(__file__).resolve().parent / "data"
    for src, dst in (("config.example.yaml", "config.yaml"), ("env.example", ".env")):
        s = data / src
        if Path(dst).exists():
            print(f"{dst} already exists, left as is")
        elif s.exists():
            shutil.copy(s, dst)
            print(f"created {dst}")
    print("\nNext: run `leadhunter setup` to answer a few questions (or edit config.yaml and .env by hand).")


def cmd_setup(args):
    from leadhunter.terminal import run_setup
    run_setup(args.config, args.env)


def cmd_start(args):
    from leadhunter.terminal import Session, run_setup
    if not Path(args.config).exists():
        print("First time here? Let's set you up.")
        run_setup(args.config, args.env)
    Session(_agent(args), config_path=args.config, env_path=args.env).run()


def cmd_export(args):
    n = _agent(args).export_csv(args.path, args.kind or "", args.fit or "")
    print(f"Saved {n} leads to {args.path}")


def cmd_manual(args):
    """Print LinkedIn notes / platform proposals that you send yourself."""
    a = _agent(args)
    rows = a.db.q("""SELECT m.*, c.name, c.linkedin_url, j.title jt, j.company, j.url ju FROM messages m
                     LEFT JOIN contacts c ON c.id=m.contact_id JOIN jobs j ON j.id=m.job_id
                     WHERE m.status='draft' AND m.channel=? ORDER BY m.job_id, m.seq""", (args.channel,))
    for m in rows:
        print("=" * 72)
        where = m["linkedin_url"] if args.channel == "linkedin" else m["ju"]
        print(f"#{m['id']} {m['subject']} · {m['name'] or ''} · {m['jt']} · {where}\n")
        print(m["body"])
    print("=" * 72 if rows else "Nothing waiting.")
    if rows:
        print("When you've sent one: leadhunter done <id> [<id> ...]")


def cmd_done(args):
    a = _agent(args)
    for i in args.ids:
        a.mark_done(int(i))
    print("Marked as sent.")


def cmd_doctor(args):
    a = _agent(args)
    p = a.profile
    from leadhunter.config import MODES
    print(f"Mode         : {MODES[a.cfg.mode]}")
    print(f"Profile      : {p.get('name') or '(name not set)'}, {p.get('years') or 0} yrs, {len(p.get('skills') or [])} skills")
    print(f"  skills     : {', '.join(p.get('skills') or []) or '(none: set profile.skills or resume_path)'}")
    print(f"Resume       : {'found' if p.get('resume_path') and Path(p['resume_path']).exists() else 'NOT FOUND'} ({p.get('resume_path')})")
    print(f"AI           : {'on (' + a.ai.provider + ' / ' + a.ai.model + ')' if a.ai.enabled else 'off (templates will be used)'}")
    print(f"Web search   : {'on (' + a.search.provider + ')' if a.search.enabled else 'OFF: needed to find HR contacts'}")
    ok, why = a.mailer.ready()
    print(f"Email        : {'ready' if ok else 'NOT READY'}: {why}")
    print(f"Daily limit  : {a.cfg.daily_limit} emails/day (sent today: {a.sent_today()})")
    print(f"Follow-ups   : after {', '.join(str(d) for d in a.cfg.followup_days)} days")
    from leadhunter.keyhealth import NAMES, check_all
    print("Testing keys :" + ("" if a.cfg.secret("GMAIL_APP_PASSWORD") else " (none entered yet)"))
    for k, v in check_all(a.cfg).items():
        print(f"  {'✓' if v == 'ok' else '!'} {NAMES[k]:20} {'works' if v == 'ok' else v}")
    from leadhunter.terminal import IO, print_checklist
    print_checklist(IO(), a.cfg)


def cmd_collect(args):
    r = _agent(args).collect()
    print(f"Fetched {r['fetched']} jobs, {r['new']} new, {r['new_good_matches']} new strong/good matches.")
    for e in r["errors"]:
        print(f"  ! {e}")


def cmd_add(args):
    a = _agent(args)
    try:
        if args.company and args.title:
            jid, m = a.add_manual(args.company, args.title, args.location or "", args.url or "", args.domain or "")
        elif args.url:
            jid, m = a.add_link(args.url)
            if args.domain:
                a.db.run("UPDATE jobs SET company_domain=? WHERE id=?", (args.domain, jid))
        else:
            sys.exit("Give a job URL, or --company and --title.")
    except JobLinkError as e:
        sys.exit(f"{e}\nExample: leadhunter add --company Accenture --title 'Python Developer' --location Hyderabad")
    j = a.db.job(jid)
    print(f"Job #{jid}: {j['title']} at {j['company'] or '?'} ({j['location'] or 'location n/a'})")
    print(f"  fit: {m['fit'] or 'weak'} ({m['score']}), matched: {', '.join(m['matched']) or 'none'}")
    if not j["company"]:
        print("  ! No company found. Re-add with --company and --title.")
    print(f"Next: leadhunter research {jid}")


def cmd_jobs(args):
    a = _agent(args)
    where = "WHERE fit!=''" if not args.all else ""
    if args.fit:
        where = "WHERE fit=?"
    rows = a.db.q(f"SELECT * FROM jobs {where} ORDER BY score DESC, id DESC LIMIT ?",
                  ((args.fit, args.limit) if args.fit else (args.limit,)))
    for j in rows:
        print(f"#{j['id']:<5} {j['fit'] or '-':8} {j['score']:.2f}  {j['title'][:55]:55}  {j['company'][:22]:22}  {j['location'][:25]}  [{j['status']}]")
    if not rows:
        print("No matching jobs yet. Run: leadhunter collect   or   leadhunter add <job link>")


def cmd_research(args):
    a = _agent(args)
    for jid in args.job_ids:
        r = a.research(jid)
        print(f"Job #{jid}: domain {r['domain'] or '?'}, {r['added']} new contact(s)")
        for c in r["contacts"]:
            print(f"  - {c['name']} | {c['title'][:60]} | {c['email'] or 'no email'} ({c['email_confidence'] or '-'})")
            print(f"    why: {c.get('reason', '')}  {c.get('linkedin_url', '')}")
        for n in r["notes"]:
            print(f"  ! {n}")
    print("Next: leadhunter drafts   (review, then approve)")


def cmd_contact(args):
    a = _agent(args)
    cid = a.add_contact(args.job_id, args.name, args.email, args.title or "", args.linkedin or "")
    print(f"Added contact #{cid}" if cid else "That contact already exists for this job.")


def cmd_drafts(args):
    a = _agent(args)
    rows = a.db.q("""SELECT m.*, c.name, c.email, c.email_confidence, j.title jt, j.company FROM messages m
                     JOIN contacts c ON c.id=m.contact_id JOIN jobs j ON j.id=c.job_id
                     WHERE m.status IN ('draft','approved') AND m.channel='email' ORDER BY m.id""")
    for m in rows:
        print("=" * 72)
        print(f"Draft #{m['id']} [{m['status']}] to {m['name']} <{m['email']}> ({m['email_confidence']})")
        print(f"Job: {m['jt']} at {m['company']}")
        print(f"Subject: {m['subject']}\n")
        print(m["body"] if args.full else m["body"][:600] + ("..." if len(m["body"]) > 600 else ""))
    if not rows:
        print("No drafts. Research a job first: leadhunter research <job id>")
    else:
        print("=" * 72 + "\nApprove: leadhunter approve <id> [<id> ...]   Skip: leadhunter skip <id>   Edit in the web app: leadhunter web")


def cmd_approve(args):
    a = _agent(args)
    ids = [r["id"] for r in a.db.q("SELECT id FROM messages WHERE status='draft' AND channel='email'")] if args.ids == ["all"] else [int(i) for i in args.ids]
    for i in ids:
        a.approve(i)
    print(f"Approved {len(ids)}. They go out with `leadhunter send` (max {a.cfg.daily_limit}/day).")


def cmd_skip(args):
    a = _agent(args)
    for i in args.ids:
        a.skip(int(i))
    print("Skipped.")


def cmd_send(args):
    a = _agent(args)
    r = a.send_due(wait=not args.no_wait, force_window=args.now)
    print(f"Sent {r['sent']} new, {r['followups']} follow-ups, {r['failed']} failed. Left today: {a.remaining_today()}.")
    if r["skipped_reason"]:
        print(f"  stopped: {r['skipped_reason']}")
    for e in r["errors"]:
        print(f"  ! {e}")
    if a.mailer.dry_run:
        print("  (dry run: see ./outbox. Set mail.dry_run: false to really send.)")


def cmd_check(args):
    r = _agent(args).check_replies()
    print(f"Replies: {r['replied']}, bounces: {r['bounced']}")


def cmd_mark(args):
    _agent(args).set_contact_status(args.contact_id, args.status)
    print("Updated.")


def cmd_status(args):
    a = _agent(args)
    s = a.db.stats()
    print(json.dumps({**s, "sent_today": a.sent_today(), "daily_limit": a.cfg.daily_limit}, indent=2))
    rows = a.db.q("""SELECT c.*, j.title jt FROM contacts c JOIN jobs j ON j.id=c.job_id
                     WHERE c.status IN ('active','replied','bounced') ORDER BY c.last_sent_at DESC LIMIT 30""")
    for c in rows:
        print(f"  #{c['id']:<4} {c['status']:8} {c['name'][:24]:24} {c['email'][:34]:34} follow-ups {c['followups_sent']}  {c['jt'][:40]}")


def cmd_run(args):
    a = _agent(args)
    print(f"LeadHunter agent running. Collect every {args.collect_hours}h, check every {args.tick_minutes} min. Ctrl+C to stop.")
    a.run_forever(args.collect_hours, args.tick_minutes)


def cmd_web(args):
    import uvicorn
    from leadhunter.web import create_app
    app = create_app(load_config(args.config, args.env), config_path=args.config, env_path=args.env)
    print(f"Open http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="leadhunter", description="Open-source job agent: leads -> HR contacts -> emails -> follow-ups.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--env", default=".env")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("start", help="guided terminal session (default)").set_defaults(fn=cmd_start)
    sub.add_parser("setup", help="answer the onboarding questions (mode, resume, Gmail, LinkedIn, keys)").set_defaults(fn=cmd_setup)
    sub.add_parser("init", help="create config.yaml and .env from templates").set_defaults(fn=cmd_init)
    sub.add_parser("doctor", help="check your setup").set_defaults(fn=cmd_doctor)
    sub.add_parser("collect", help="fetch new jobs from all sources and score them").set_defaults(fn=cmd_collect)

    p = sub.add_parser("add", help="add a job from a link (or by hand)")
    p.add_argument("url", nargs="?")
    p.add_argument("--company"); p.add_argument("--title"); p.add_argument("--location"); p.add_argument("--domain", help="company email domain, e.g. accenture.com")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("jobs", help="list matched jobs")
    p.add_argument("--fit", choices=["strong", "good", "stretch"]); p.add_argument("--all", action="store_true"); p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_jobs)

    p = sub.add_parser("research", help="find recruiter / hiring manager + emails for job(s) and draft emails")
    p.add_argument("job_ids", type=int, nargs="+"); p.set_defaults(fn=cmd_research)

    p = sub.add_parser("contact", help="add a contact you found yourself")
    p.add_argument("job_id", type=int); p.add_argument("name"); p.add_argument("email")
    p.add_argument("--title"); p.add_argument("--linkedin"); p.set_defaults(fn=cmd_contact)

    p = sub.add_parser("drafts", help="show drafts waiting for approval"); p.add_argument("--full", action="store_true"); p.set_defaults(fn=cmd_drafts)
    p = sub.add_parser("approve", help="approve drafts by id, or 'all'"); p.add_argument("ids", nargs="+"); p.set_defaults(fn=cmd_approve)
    p = sub.add_parser("skip", help="skip drafts by id"); p.add_argument("ids", nargs="+"); p.set_defaults(fn=cmd_skip)

    p = sub.add_parser("send", help="send due follow-ups + approved emails (within the daily limit)")
    p.add_argument("--no-wait", action="store_true", help="no random gap between emails")
    p.add_argument("--now", action="store_true", help="ignore the working-hours window")
    p.set_defaults(fn=cmd_send)

    sub.add_parser("check", help="check Gmail for replies and bounces").set_defaults(fn=cmd_check)
    p = sub.add_parser("mark", help="mark a contact replied/stopped (e.g. they answered on LinkedIn)")
    p.add_argument("contact_id", type=int); p.add_argument("status", choices=["replied", "stopped", "done", "active"]); p.set_defaults(fn=cmd_mark)
    sub.add_parser("status", help="pipeline summary").set_defaults(fn=cmd_status)

    p = sub.add_parser("run", help="run the agent continuously")
    p.add_argument("--collect-hours", type=float, default=6); p.add_argument("--tick-minutes", type=float, default=15)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("web", help="open the dashboard"); p.add_argument("--port", type=int, default=8765); p.set_defaults(fn=cmd_web)

    p = sub.add_parser("export", help="save leads to a CSV file")
    p.add_argument("path", nargs="?", default="leads.csv"); p.add_argument("--kind", choices=["job", "freelance"])
    p.add_argument("--fit", choices=["strong", "good", "stretch"]); p.set_defaults(fn=cmd_export)
    p = sub.add_parser("linkedin", help="LinkedIn notes/messages for you to send"); p.set_defaults(fn=cmd_manual, channel="linkedin")
    p = sub.add_parser("proposals", help="freelance proposals for you to post"); p.set_defaults(fn=cmd_manual, channel="platform")
    p = sub.add_parser("done", help="mark LinkedIn messages / proposals as sent"); p.add_argument("ids", nargs="+"); p.set_defaults(fn=cmd_done)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        args.fn = cmd_start
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        args.fn(args)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Run `leadhunter` again any time to continue.")


if __name__ == "__main__":
    main()
