# LeadHunter dashboard: pages and contents

This is a reference for designing the frontend. It lists every page, what is
on it, what users can do there, and every status value that needs a visual
style. Items marked **NEW** are being added now.

The dashboard runs locally at `http://127.0.0.1:8765`.

## Global layout

- **Top bar:** the LeadHunter logo and the main tabs.
- **Tabs, in order:** Get started · Leads · **Pipeline (NEW)** · Outbox ·
  Send by hand · Conversations · **Insights (NEW)** · Settings
- **Badges on tabs (NEW):**
  - Conversations shows the number of unread replies.
  - Outbox shows the number of drafts waiting for approval.
- **Flash message:** a one-line notice under the top bar after any action
  (for example "Approved. It goes out with the next send."). It can be
  success, info or error.
- **Stats strip:** shown on Leads and Outbox. Tiles: Jobs · Freelance ·
  Matches · Drafts · Send by hand · Sent today (`n/10`) · Waiting reply ·
  Replied. In "Just leads" mode only the first three tiles appear.

## 1. Setup wizard (`/setup?step=1..4`)

Four steps with a progress bar: **Goal → About you → Gmail & LinkedIn →
Power-ups**. The top tabs are hidden until setup is finished.

| Step | Fields | Actions |
|---|---|---|
| 1. Goal | 4 radio cards: Job hunting · Freelance clients · Both · Just leads. For "Just leads": checkboxes for Jobs and Freelance | Next |
| 2. About you | Resume upload (PDF/DOCX/TXT, max 5 MB) · Full name · Years of experience · Skills (comma tags). **Jobs:** roles you want, cities. **Freelance:** lowest hourly rate, smallest fixed project, one-line pitch. Portfolio URL · Phone | Read my resume · Next · Back |
| 3. Gmail & LinkedIn | Gmail address · App password (shows "saved ✓") · Practice-mode checkbox · LinkedIn profile URL · "Write LinkedIn notes" checkbox | Save and test connection · Next · Back |
| 4. Power-ups | Search provider (Brave/Tavily/SearXNG/Skip) + API key + SearXNG URL · AI provider (Gemini/Groq/Ollama/OpenAI/None) + key + model · Emails per day (1–20) · **Telegram alerts (NEW):** bot token + chat ID | Finish · Back |

States: resume read OK ("12 skills found"), resume unreadable, Gmail test
passed or failed.

## 2. Get started (`/start`)

A checklist of what's connected.

- **Header card:** current mode (with a "change" link), "X of Y required
  steps done", the list of free sources that need no key, and a "Go to my
  leads" button.
- **One card per item:** Profile · Gmail · Practice mode · Web search · AI ·
  Hunter · Reddit · **Telegram (NEW)**. Each card has:
  - a numbered title
  - a badge: Done ✓ / Connected ✓ / Required / Recommended / Optional
  - "why you need it"
  - numbered steps (hidden once done)
  - the env variable name
  - buttons: "Open <provider site>" and "Enter it in setup"

## 3. Leads (`/`), the home page

- Stats strip.
- Warning notes for anything still to set up.
- **Find leads card:**
  - "Find new leads" button · "Export CSV" button
  - a paste-a-link field with an Add button
  - a collapsible "Add it by hand" form: Job/Freelance select, title,
    company, city, email domain, budget, email from the post, link
- **Matches card:**
  - filters: All · Jobs · Freelance (tabs) · Strong · Good · Stretch
  - a table (up to 100 rows) with these columns:

| Column | Content |
|---|---|
| Lead | Title (links to the detail page), company |
| Fit | Pill strong/good/stretch/weak. For freelance, the budget and "quote ~$X" |
| Your skills | Up to 5 highlighted skill chips |
| Where | City, source, plus "email in post" |
| Action | Button: "Reach out" / "Open" / "Details" |

Empty state: "No leads yet. Click Find new leads or paste a link."

## 4. Lead detail (`/job/{id}`)

- **Header card:**
  - title, company, city, fit pill
  - for freelance: budget and suggested quote
  - for jobs: email domain
  - "email in post", "Open posting" link
  - **NEW, "Why you fit" box:** all matched-skill chips, plus a note on which
    ones the email leads with
  - buttons: "Find recruiter & hiring manager" or "Write proposal" ·
    "Not interested"
  - a "fix domain" input with a Save button
  - a collapsible full description
- **Proposal card** (freelance on a platform): the proposal text in a copy
  box · Copy · Open project · "I posted it".
- **One card per contact:**
  - name, status pill, title, LinkedIn link
  - email with a confidence pill (verified/likely/guessed/posted), plus "why"
    this person was picked
  - **Email draft:** subject and body inputs · "Approve email" ·
    "Save draft" · "Don't email". After sending, it becomes a line such as
    "Email sent on … Follow-ups sent: n."
  - **LinkedIn messages:** connection note (≤200 characters) and DM, each in
    a copy box · Copy · Open profile · "I sent it"
  - **NEW, reply box** (once they reply): reply date, tag pill, preview text
  - "They answered somewhere else?" · Mark replied · Stop
- **Add a contact you found yourself:** name, email, title, LinkedIn →
  "Add and draft email".

## 5. Pipeline (`/pipeline`), NEW

A Kanban board with one card per contact you've reached out to.

| Column | Contact status |
|---|---|
| To review | `found`, `drafted` (draft not yet approved) |
| Sent | `active` (waiting, follow-ups running) |
| Replied | `replied` |
| Interview | `replied` + sentiment `interview` |
| Closed | `bounced`, `stopped`, `done`, or sentiment `rejection` |

- **Card:** person, company, lead title, last activity date, follow-up count
  (`1/2`), and a sentiment tag if they replied. Clicking a card opens the
  lead detail page.
- **Card action:** a "Move to…" menu (Interview / Closed) that sets the
  status or sentiment by hand. Not shown on "To review" cards.
- **Filters:** Jobs / Freelance.
- **Empty state:** "Reach out to a lead to see it here."

## 6. Outbox (`/outbox`)

- Stats strip.
- **Emails waiting card:**
  - "Approve all drafts" · "Send now (up to N left today)"
  - a note: "n follow-ups due. Follow-ups go first… working hours only."
  - a table: Status (draft/approved) · To (name, email, confidence) ·
    Lead (title, company) · Subject
- Empty state: "Nothing waiting."
- Send result flash: "Sent 3 new and 1 follow-up. 6 left today. Stopped:
  outside working hours."

## 7. Send by hand (`/manual`)

LinkedIn notes and freelance proposals that the user sends themselves.

- **One card per item:**
  - heading: "LinkedIn · Name · Connection note" or "Proposal on
    freelancer.com"
  - "For <lead>" link
  - the text in a copy box · Copy · Open profile/project · "I sent it"
- Empty state: "Nothing waiting."

## 8. Conversations (`/threads`)

- "Check Gmail for replies" button · **NEW** "Mark all read" button.
- Unread replies are listed first.
- **Filter chips (NEW):** All · Interview · Referral · Question · Rejection ·
  Waiting · Bounced.
- **Table:**

| Column | Content |
|---|---|
| Status | Pill active/replied/bounced/done. **NEW:** sentiment tag, and a "new" dot for unread replies |
| Person | Name, email |
| Lead | Title, company |
| Follow-ups | `1/2 follow-ups`, last sent date |
| **Reply (NEW)** | Reply date, a one-line preview of the reply, and a "Mark read" action |

## 9. Insights (`/insights`), NEW

- **Funnel:** leads found → matched → contacted → replied → interview, with a
  count and a percentage at each step.
- **Reply rate by source:** a bar for each source (greenhouse, lever,
  remotive, hackernews, …) showing sent vs replied.
- **Reply rate by weekday sent:** Mon–Fri bars.
- **Reply rate by fit:** strong / good / stretch.
- **Sentiment breakdown:** interview / referral / question / rejection /
  other.
- **Last 30 days:** emails sent per day (small bar chart).
- Empty state: "Send a few emails to see what works."

## 10. Settings

The setup wizard reopened with all tabs visible. Each step links to the
**NEW Lead sources page** (`/sources`), which was only editable in
`config.yaml` before:

- **Toggles:** Remotive · Arbeitnow · Hacker News · Freelancer.com · Reddit.
- **Company boards** (one list each; add or remove company slugs):
  Greenhouse · Lever · Ashby · **SmartRecruiters (NEW)** · **Recruitee
  (NEW)**. Each board has a text box of company ids, a hint showing where the
  id comes from (`boards.greenhouse.io/<slug>`), a one-off id field and a
  "Test" button. Test saves the form, then reports "N open jobs" or why it
  failed. Invalid ids are dropped on save, and the flash message lists them.
- **Freelancer search words**, and **max age of leads (days)**.

## Status values that need a visual style

| Thing | Values |
|---|---|
| Fit | strong · good · stretch · weak |
| Email confidence | posted · verified · likely · guessed |
| Contact status | found · drafted · active · replied · bounced · stopped · done |
| Message status | draft · approved · sent · skipped · failed |
| Reply sentiment (NEW) | interview · referral · question · rejection · other |
| Checklist badge | Done · Connected · Required · Recommended · Optional |

## Not part of this round

- **Gmail sign-in with Google (OAuth):** needs a Google Cloud app to be set
  up first.
- **Workable:** its public API answered, but none of the companies tried had
  open jobs, so the data format couldn't be checked.
- **Browser extension:** a separate project. It will call the existing
  `POST /add` route.
