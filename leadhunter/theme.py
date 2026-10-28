"""Artemis look for the dashboard: tokens, page shell, flash bar, window frame.

Spec: "LeadHunter UI design system/Artemis Design System.dc.html". Two colors, no radius, no shadows;
status is shown by fill / border style / a word, never by hue.
"""
from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlparse

from fastapi.responses import HTMLResponse

from leadhunter import __version__

e = html.escape
STATIC = Path(__file__).parent / "static"

_LIGHT = "--bg:#fff;--sf:#f4f4f4;--sf2:#e9e9e9;--ink:#000;--soft:#595959;--rule:#d4d4d4;--inv:none;--invr:invert(1);--dk:none;--lt:block;color-scheme:light"
_DARK = "--bg:#000;--sf:#0d0d0d;--sf2:#1a1a1a;--ink:#fff;--soft:#a3a3a3;--rule:#2a2a2a;--inv:invert(1);--invr:none;--dk:block;--lt:none;color-scheme:dark"
MONO = "'Geist Mono',ui-monospace,monospace"

CSS = f"""
:root{{{_LIGHT}}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{{_DARK}}}}}
:root[data-theme=dark]{{{_DARK}}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--ink)}}
body{{font:400 14px/1.5 Geist,system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column}}
a{{color:var(--ink);text-underline-offset:3px}}a:hover{{color:var(--soft)}}
.dk{{display:var(--dk)}}.lt{{display:var(--lt)}}
code{{font:500 12px {MONO};background:var(--sf2);padding:2px 6px}}
.app{{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}}.app.solo{{grid-template-columns:minmax(0,1fr)}}
.side{{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;background:var(--sf);border-right:1px solid var(--rule)}}
.side nav{{display:flex;flex-direction:column;gap:2px;padding:12px 10px;overflow-y:auto}}
.side nav a{{display:flex;align-items:center;gap:10px;padding:9px 10px;text-decoration:none;color:var(--soft);white-space:nowrap}}
.side nav a svg{{flex:none}}.side nav a:hover{{color:var(--ink);background:var(--sf2)}}
.side nav a.on{{color:var(--bg);background:var(--ink);font-weight:500}}.side nav .badge{{margin-left:auto}}.side nav a.on .badge{{background:var(--bg);color:var(--ink)}}
.side .foot{{margin-top:auto;padding:14px 18px;border-top:1px solid var(--rule);color:var(--soft);font:500 11px {MONO}}}
.inset{{display:flex;flex-direction:column;min-width:0}}
.top{{position:sticky;top:0;z-index:20;height:56px;padding:0 28px;display:flex;align-items:center;gap:12px;background:var(--bg);border-bottom:1px solid var(--rule)}}
.where{{margin-right:auto;font:600 16px Geist,sans-serif;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.logo{{display:flex;align-items:center;gap:10px;height:56px;padding:0 18px;flex:none;text-decoration:none;border-bottom:1px solid var(--rule)}}.logo img{{display:block;filter:var(--inv)}}
.top .logo{{border:0;padding:0;margin-right:auto}}
.practice{{display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px dashed var(--ink);font:500 11px/1 {MONO};white-space:nowrap}}
.practice::before{{content:"";width:6px;height:6px;background:var(--ink)}}
.theme{{width:36px;height:36px;display:grid;place-items:center;border:1px solid var(--rule);background:transparent;color:var(--ink);cursor:pointer;padding:0}}
.theme:hover{{border-color:var(--ink);background:transparent;color:var(--ink)}}
.theme .moon{{display:var(--dk)}}.theme .sun{{display:var(--lt)}}
.badge{{min-width:18px;height:18px;padding:0 5px;display:inline-grid;place-items:center;background:var(--ink);color:var(--bg);font:500 10px/1 {MONO}}}
.flash{{border-bottom:1px solid var(--rule);background:var(--sf)}}
.flash>div{{padding:10px 28px;display:flex;align-items:center;gap:12px}}
.flash b{{font:500 12px {MONO};white-space:nowrap}}.flash span{{flex:1;font-weight:500}}
.flash.alert{{background:var(--ink);color:var(--bg);border-color:var(--ink)}}.flash.alert a{{color:var(--bg);font-weight:500;white-space:nowrap}}
.flash button{{background:none;border:0;color:var(--soft);padding:0 4px;font:400 16px/1 {MONO}}}
main{{flex:1;width:100%;max-width:1600px;padding:28px 28px 72px}}
h1{{margin:0 0 14px;font:600 28px/1.1 Geist,sans-serif;letter-spacing:-.02em}}
h2{{margin:0 0 12px;font:600 18px/1.2 Geist,sans-serif;letter-spacing:-.01em}}
.label{{display:block;font:400 10px/1.4 Silkscreen,monospace;letter-spacing:.1em;color:var(--soft);text-transform:uppercase;margin:0 0 8px}}
.card{{border:1px solid var(--rule);padding:20px;margin:0 0 24px}}
.win{{border:1px solid var(--ink);margin:0 0 24px}}
.win>.bar{{display:flex;align-items:center;gap:3px;height:30px;padding:0 12px;border-bottom:1px solid var(--ink);font:400 10px/1 Silkscreen,monospace;letter-spacing:.08em;text-transform:uppercase}}
.win>.bar span{{flex:1}}.win>.bar i{{width:9px;height:9px;border:1px solid var(--ink)}}.win>.bar i:last-child{{background:var(--ink)}}
.win>.in{{padding:20px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:0 0 24px}}
.stat{{padding:14px 16px;border:1px solid var(--rule);display:flex;flex-direction:column;gap:6px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:16px;margin:0 0 24px}}
.kpi{{border:1px solid var(--rule);padding:18px 20px;display:flex;flex-direction:column;gap:6px}}.kpi:hover{{border-color:var(--ink)}}
.kpi>div{{display:flex;justify-content:space-between;align-items:center;font-size:13px;font-weight:500}}.kpi svg{{color:var(--soft)}}
.kpi b{{font:500 30px/1.1 {MONO};letter-spacing:-.02em}}.kpi small{{color:var(--soft);font-size:12px}}
.ch{{margin:0 0 18px}}.ch h2{{margin:0 0 4px}}.ch p{{margin:0;color:var(--soft);font-size:13px}}
.chart{{padding-left:28px}}.plot{{position:relative;height:240px}}.plot svg{{display:block;width:100%;height:100%;overflow:visible}}
.ys span{{position:absolute;left:-28px;transform:translateY(-50%);color:var(--soft);font:500 11px {MONO}}}
.xs{{display:flex;justify-content:space-between;margin-top:8px;color:var(--soft);font:500 11px {MONO}}}
.legend{{display:flex;gap:20px;justify-content:center;font-size:12px;margin-top:10px}}
.legend i{{display:inline-block;width:18px;border-top:2px dashed var(--ink);vertical-align:middle;margin-right:6px}}.legend i.solid{{border-top-style:solid}}
.cards2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:24px}}.cards2>.card{{margin:0}}
.proj{{display:flex;flex-direction:column;gap:8px;padding:14px 0;border-top:1px solid var(--rule)}}
.proj>div{{display:flex;justify-content:space-between;align-items:center;gap:10px}}.proj .meter{{flex:1}}.proj .meter i{{height:8px}}
.proj h3{{margin:0;font-size:14px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.ava{{display:inline-grid;place-items:center;width:26px;height:26px;flex:none;border:1px solid var(--ink);background:var(--bg);font:500 10px {MONO}}}
.ava+.ava{{margin-left:-6px}}.feed .ava{{width:32px;height:32px;font-size:11px}}
.feed{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:16px}}.feed li{{display:flex;gap:12px;align-items:flex-start}}
.feed p{{margin:0;line-height:1.4}}.feed small{{color:var(--soft);font:500 11px {MONO}}}
.stat b{{font:500 26px/1 {MONO};letter-spacing:-.02em}}.stat span{{font-size:12px;color:var(--soft)}}
.meter{{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:2px}}.meter i{{height:6px;border:1px solid var(--ink)}}.meter i.on{{background:var(--ink)}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:12px 10px;border-bottom:1px solid var(--rule);vertical-align:top}}
th{{font:400 10px Silkscreen,monospace;letter-spacing:.08em;color:var(--soft);text-transform:uppercase}}
.pill{{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;font:500 11px/16px {MONO};white-space:nowrap;color:var(--ink);border:1px solid var(--ink)}}
.pill.strong,.pill.posted,.pill.verified,.pill.replied,.pill.sent,.pill.interview,.pill.referral,.pill.Done,.pill.Connected{{background:var(--ink);color:var(--bg)}}
.pill.stretch,.pill.guessed,.pill.drafted,.pill.draft,.pill.Recommended{{border-style:dashed}}
.pill.weak,.pill.found,.pill.stopped,.pill.done,.pill.skipped,.pill.rejection,.pill.other,.pill.Optional{{color:var(--soft);border:1px dotted var(--soft)}}
.pill.bounced{{color:var(--soft);border:1px dashed var(--soft);text-decoration:line-through}}
.pill.strong::before{{content:"■"}}.pill.good::before{{content:"□"}}.pill.stretch::before{{content:"◌"}}.pill.weak::before{{content:"·"}}
.pill.verified::before,.pill.done::before,.pill.approved::before{{content:"✓"}}.pill.guessed::before{{content:"?"}}
.pill.active::before{{content:"●"}}.pill.replied::before{{content:"↩"}}.pill.failed::before{{content:"!"}}
input[type=text],input[type=email],input[type=url],input[type=password],input[type=number],input[type=file],select,textarea{{width:100%;font:400 14px Geist,system-ui,sans-serif;color:var(--ink);background:var(--bg);border:1px solid var(--rule);border-radius:0;padding:9px 10px}}
.pw{{display:flex}}.pw input{{flex:1;min-width:0}}
.peek{{min-height:0;padding:0 12px;background:var(--bg);color:var(--soft);border:1px solid var(--rule);border-left:0}}.peek:hover{{background:var(--sf);color:var(--ink)}}
input:focus,select:focus,textarea:focus{{outline:1px solid var(--ink);border-color:var(--ink)}}
input[type=checkbox],input[type=radio]{{accent-color:var(--ink)}}
textarea{{min-height:200px}}label{{display:block;margin:0 0 14px}}label>span{{display:block;font-weight:500;margin-bottom:6px}}
button,.btn{{font:500 14px/1 Geist,system-ui,sans-serif;min-height:40px;padding:12px 18px;border:1px solid var(--ink);border-radius:0;background:var(--ink);color:var(--bg);cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:6px}}
button:hover,.btn:hover{{background:var(--bg);color:var(--ink)}}
button.quiet,.btn.quiet{{background:transparent;color:var(--ink);border-color:var(--rule)}}button.quiet:hover,.btn.quiet:hover{{border-color:var(--ink)}}
button:disabled{{background:var(--sf2);color:var(--soft);border-color:var(--sf2);cursor:default}}
form{{margin:0}}.row{{display:flex;flex-wrap:wrap;gap:10px;align-items:center}}.grow{{flex:1 1 260px}}
.muted{{color:var(--soft)}}.money{{font:500 13px {MONO};white-space:nowrap}}
.note{{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;border:1px dashed var(--ink);margin:0 0 24px}}
.note::before{{content:"[i]";font:500 12px/20px {MONO}}}.note>*{{margin:0}}
mark{{background:var(--sf2);color:var(--ink);font:500 12px {MONO};padding:2px 6px}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.goals{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:0 0 18px}}
.goal{{border:1px solid var(--rule);cursor:pointer;margin:0}}.goal:hover{{border-color:var(--ink)}}
.goal:has(input:checked){{outline:1px solid var(--ink);border-color:var(--ink)}}
.goal .pic{{aspect-ratio:4/3;border-bottom:1px solid var(--rule);background:center/cover no-repeat;filter:var(--invr);image-rendering:pixelated}}
.goal div:last-child{{padding:12px 14px}}.goal input{{margin:0 8px 0 0}}.goal b{{font-size:15px}}
.steps{{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--ink);margin:0 0 24px}}
.steps a{{display:flex;flex-direction:column;gap:2px;padding:12px 14px;text-decoration:none;color:var(--soft);border-right:1px solid var(--rule)}}
.steps a small{{font:500 11px {MONO}}}.steps a.on{{background:var(--ink);color:var(--bg)}}
.hero{{border:1px solid var(--ink);margin:0 0 24px}}.hero .art{{aspect-ratio:3/1;overflow:hidden;border-bottom:1px solid var(--ink)}}
.hero .art img,.lost img{{width:100%;height:100%;object-fit:cover;image-rendering:pixelated;display:block}}.hero .art img{{filter:var(--inv)}}
.hero .in{{padding:28px}}
.lost{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);border:1px solid var(--ink)}}.lost>div:first-child{{padding:36px;display:flex;flex-direction:column;gap:16px;justify-content:center}}
.lost img{{min-height:360px;max-height:560px;object-position:center 45%}}
pre.copy{{white-space:pre-wrap;background:var(--sf);border:1px solid var(--rule);padding:12px;margin:8px 0;font:400 14px/1.5 Geist,system-ui,sans-serif}}
.dot{{display:inline-block;width:8px;height:8px;background:var(--ink);margin-right:6px}}
.chips{{display:flex;flex-wrap:wrap;gap:6px}}.chips a{{padding:4px 10px;border:1px solid var(--rule);text-decoration:none;font-size:13px}}
.chips a:hover{{border-color:var(--ink);color:var(--ink)}}.chips a.on{{background:var(--ink);color:var(--bg);border-color:var(--ink)}}
.board{{display:grid;grid-template-columns:repeat(5,minmax(220px,1fr));gap:12px;overflow-x:auto;padding-bottom:6px}}
.col{{border:1px solid var(--rule);min-height:160px}}.col h2{{display:flex;justify-content:space-between;padding:12px;margin:0;border-bottom:1px solid var(--rule);font:400 11px Silkscreen,monospace;letter-spacing:.06em;text-transform:uppercase}}
.col>p,.col>.tile{{margin:10px}}
.tile{{display:flex;flex-direction:column;gap:6px;border:1px solid var(--rule);padding:12px;background:var(--bg)}}.tile:hover{{border-color:var(--ink)}}
.tile>div{{display:flex;flex-wrap:wrap;align-items:center;gap:6px}}.tile small{{font:500 11px {MONO}}}.tile select{{width:auto;padding:6px}}.tile button{{min-height:0;padding:7px 10px;font-size:13px}}
.bar{{display:grid;grid-template-columns:120px minmax(0,1fr) 120px;gap:12px;align-items:center;margin:8px 0}}
.bar>span{{font:500 12px {MONO}}}.bar>span:last-child{{text-align:right}}.bar .track{{display:block;height:20px;background:var(--sf)}}.bar .track i{{display:block;height:100%;background:var(--ink)}}
details summary{{cursor:pointer;color:var(--soft)}}
::view-transition-old(root),::view-transition-new(root){{animation:none;mix-blend-mode:normal}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important;transition:none!important}}}}
@media(max-width:900px){{.app{{grid-template-columns:minmax(0,1fr)}}.side{{z-index:30;height:auto;border:0;border-bottom:1px solid var(--rule)}}
.side nav{{flex-direction:row;overflow-x:auto;scrollbar-width:none;padding:6px}}.side .foot{{display:none}}.top{{position:static}}}}
@media(max-width:640px){{.top,main,.flash>div{{padding-left:16px;padding-right:16px}}.plot{{height:180px}}.xs span:nth-child(even){{visibility:hidden}}.grid2,.lost{{grid-template-columns:1fr}}.steps{{grid-template-columns:1fr 1fr}}
td:nth-child(4),th:nth-child(4){{display:none}}.bar{{grid-template-columns:80px minmax(0,1fr) 90px}}}}
"""

HEAD_JS = """<script>try{var t=localStorage.getItem('lh-theme');if(t)document.documentElement.dataset.theme=t}catch(e){}</script>"""

BODY_JS = """<script>
const EYE='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
EYE_OFF='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/><path d="m3 3 18 18"/></svg>';
document.querySelectorAll('input[type=password]').forEach(i=>{const w=document.createElement('span'),b=document.createElement('button');
w.className='pw';b.type='button';b.className='peek';b.innerHTML=EYE;b.setAttribute('aria-label','Show what you typed');
b.onclick=()=>{const show=i.type==='password';i.type=show?'text':'password';b.innerHTML=show?EYE_OFF:EYE;b.setAttribute('aria-label',show?'Hide':'Show what you typed')};
i.before(w);w.append(i,b);});
document.addEventListener('click',ev=>{const b=ev.target.closest('[data-copy]');if(!b)return;
const el=document.getElementById(b.dataset.copy),t=el.tagName==='TEXTAREA'?el.value:el.innerText;navigator.clipboard.writeText(t).then(()=>{b.textContent='Copied ✓'});});
document.querySelector('.theme').addEventListener('click',ev=>{
const d=document.documentElement,dark=(d.dataset.theme||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'))==='dark',next=dark?'light':'dark';
const apply=()=>{d.dataset.theme=next;try{localStorage.setItem('lh-theme',next)}catch(e){}};
if(!document.startViewTransition||matchMedia('(prefers-reduced-motion: reduce)').matches)return apply();
const r=ev.currentTarget.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2,rad=Math.hypot(Math.max(x,innerWidth-x),Math.max(y,innerHeight-y));
document.startViewTransition(apply).ready.then(()=>d.animate({clipPath:[`circle(0px at ${x}px ${y}px)`,`circle(${rad}px at ${x}px ${y}px)`]},{duration:650,easing:'cubic-bezier(.4,0,.2,1)',pseudoElement:'::view-transition-new(root)'})).catch(()=>{});
});
</script>"""

THEME_ICON = """<svg width="20" height="20" viewBox="0 0 25 25" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
<g class="sun"><circle cx="12.4" cy="12.8" r="5"/><path d="M12.4 1.8v2M12.4 21.8v2M4.6 5l1.4 1.4M18.8 19.1l1.4 1.4M1.4 12.8h2M21.4 12.8h2M4.6 20.5l1.4-1.4M18.8 6.4l1.4-1.4"/></g>
<path class="moon" d="M21.2 13.2A8.9 8.9 0 1 1 11.4 3.4a6.9 6.9 0 0 0 9.8 9.8Z"/></svg>"""

# Lucide icon paths (lucide.dev, ISC), square corners to match the system
_ICONS = {
    "start": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><path d="M4 22v-7"/>',
    "dashboard": '<rect width="7" height="9" x="3" y="3"/><rect width="7" height="5" x="14" y="3"/><rect width="7" height="9" x="14" y="12"/><rect width="7" height="5" x="3" y="16"/>',
    "leads": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "pipeline": '<path d="M6 5v11M12 5v6M18 5v14"/>',
    "outbox": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "manual": '<rect width="8" height="4" x="8" y="2"/><path d="M16 4h4v18H4V4h4"/>',
    "threads": '<path d="M21 15V3H3v18l4-4h12a2 2 0 0 0 2-2z"/>',
    "insights": '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
    "settings": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "people": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
    "reply": '<path d="M9 17 4 12l5-5"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
}


def icon(name: str, size: int = 18) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="square" aria-hidden="true">{_ICONS[name]}</svg>')


TABS = [("/start", "Get started", "", "start"), ("/dashboard", "Dashboard", "", "dashboard"), ("/", "Leads", "", "leads"),
        ("/pipeline", "Pipeline", "", "pipeline"), ("/outbox", "Outbox", "drafts", "outbox"), ("/manual", "Send by hand", "", "manual"),
        ("/threads", "Conversations", "unread", "threads"), ("/insights", "Insights", "", "insights"), ("/setup", "Settings", "", "settings")]
_TAB_OF = {"Setup": "Settings", "Sources": "Settings"}  # page title -> tab, where they differ

# ponytail: flash kind guessed from the wording; pass "✓ "/"! " as the first characters to force one
_BAD = ("fail", "couldn", "didn", "error", "stopped", "invalid", "upload a", "over 5", "is a number", "pick a", "type a", "no such")
_GOOD = ("saved", "approved", "added", "sent ", "marked", "moved", "updated", "hidden", "read your resume", "open jobs", "found")


def flash_kind(msg: str) -> tuple[str, str]:
    """Return (glyph, text) for the flash bar: [✓] done, [!] needs you, [i] info."""
    if msg[:1] in "✓!":
        return f"[{msg[0]}]", msg[1:].strip()
    low = msg.lower()
    if any(w in low for w in _BAD):
        return "[!]", msg
    return ("[✓]" if any(w in low for w in _GOOD) else "[i]"), msg


def href(url: str | None) -> str:
    """A link from outside data (job posts, profiles): only http(s), so a post can't plant a javascript: link."""
    return e(url) if url and urlparse(url.strip()).scheme in ("http", "https") else "#"


def win(title: str, body: str) -> str:
    """The window frame: marks the one area that matters most on a page."""
    return f'<section class="win"><div class="bar"><span>{e(title)}</span><i></i><i></i><i></i></div><div class="in">{body}</div></section>'


def meter(n: int, total: int) -> str:
    """Block progress, never a smooth bar."""
    total = max(1, min(total, 20))
    return '<div class="meter">' + "".join(f'<i class="{"on" if i < n else ""}"></i>' for i in range(total)) + "</div>"


def page(title: str, body: str, flash: str = "", nav: bool = True, badges: dict | None = None, practice: bool = False,
         setup_done: bool = False, alerts: list[tuple[str, int]] | None = None) -> HTMLResponse:
    f = ""
    if flash:
        glyph, text = flash_kind(flash)
        f = f"""<div class="flash" role="status"><div><b>{e(glyph)}</b><span>{e(text)}</span>
          <button type="button" aria-label="Dismiss" onclick="this.closest('.flash').remove()">×</button></div></div>"""
    f = "".join(f"""<div class="flash alert" role="alert"><div><b>[!]</b><span>{e(msg)}.</span>
          <a href="/setup?step={step}">Fix it</a></div></div>""" for msg, step in alerts or []) + f
    b = badges or {}
    badge = lambda k: f'<span class="badge">{b[k]}</span>' if b.get(k) else ""
    tab = _TAB_OF.get(title, title)
    logo = """<a class="logo" href="/dashboard" aria-label="Artemis home"><img src="/assets/mark.png" alt="" height="28">
<img src="/assets/wordmark.png" alt="Artemis" height="13"></a>"""
    side = f"""<aside class="side">{logo}<nav>{"".join(f'<a href="{u}" class="{"on" if t == tab else ""}">{icon(i)}{t}{badge(k)}</a>' for u, t, k, i in TABS if not (setup_done and u == "/start"))}</nav>
<div class="foot">Artemis {__version__} · runs on this computer</div></aside>""" if nav else ""
    mode = '<span class="practice" title="Emails are saved to ./outbox instead of being sent">practice mode</span>' if practice else ""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · Artemis</title><link rel="icon" href="/assets/mark.png">{HEAD_JS}
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&family=Silkscreen&display=swap">
<style>{CSS}</style></head><body><div class="app{"" if nav else " solo"}">{side}<div class="inset">
<header class="top">{f'<span class="where">{e(tab)}</span>' if nav else logo}{mode}
<button type="button" class="theme" aria-label="Toggle light and dark theme">{THEME_ICON}</button></header>
{f}<main>{body}</main></div></div>{BODY_JS}</body></html>""")


def initials(name: str) -> str:
    return "".join(w[0] for w in (name or "?").split()[:2]).upper()


def area_chart(rows: list[dict], x: str, dashed: str, solid: str) -> str:
    """Two series told apart by line style, not color: `dashed` as a dashed line, `solid` as a line over a grey fill.
    The SVG stretches to any width; axis labels are HTML so they stay readable on a phone."""
    step = max(1, -(-max([r[dashed] for r in rows] + [r[solid] for r in rows] + [4]) // 4))  # ceil(max / 4)
    ymax, dx = step * 4, 1000 / max(1, len(rows) - 1)
    pts = lambda k: " ".join(f"{i * dx:.1f},{200 - 200 * r[k] / ymax:.1f}" for i, r in enumerate(rows))  # noqa: E731
    ticks = range(0, ymax + 1, step)
    grid = "".join(f'<line x1="0" x2="1000" y1="{200 - 200 * v / ymax:.1f}" y2="{200 - 200 * v / ymax:.1f}"/>' for v in ticks)
    ys = "".join(f'<span style="top:{100 - 100 * v / ymax:.1f}%">{v}</span>' for v in ticks)
    xs = "".join(f"<span>{e(r[x])}</span>" for r in rows)
    label = ", ".join(f"{r[x]}: {r[dashed]} {dashed}, {r[solid]} {solid}" for r in rows)
    ns = 'fill="none" stroke="var(--ink)" stroke-width="2" vector-effect="non-scaling-stroke"'
    return f"""<div class="chart" role="img" aria-label="{e(label)}"><div class="plot"><div class="ys">{ys}</div>
<svg viewBox="0 0 1000 200" preserveAspectRatio="none" aria-hidden="true"><g stroke="var(--rule)" stroke-dasharray="3 3" vector-effect="non-scaling-stroke">{grid}</g>
<polygon points="0,200 {pts(solid)} 1000,200" fill="var(--sf2)"/><polyline points="{pts(solid)}" {ns}/>
<polyline points="{pts(dashed)}" {ns} stroke-dasharray="6 5"/></svg></div><div class="xs">{xs}</div>
<div class="legend"><span><i></i>{e(dashed.capitalize())}</span><span><i class="solid"></i>{e(solid.capitalize())}</span></div></div>"""
