# Getting started

This page lists everything you might connect, why, and exactly how. You can also see it inside the app:

- **Dashboard:** `leadhunter web` → **Get started** (shows what's connected ✓ and what's left)
- **Terminal:** `leadhunter` → **Get started checklist**, or `leadhunter doctor`

You never need to edit files by hand: `leadhunter setup` asks for each key and saves it in `.env` on your computer.

## What you need, by mode

| Mode | Required | Recommended | Optional |
|---|---|---|---|
| Job hunting | Profile/resume, Gmail App Password | Brave or Tavily search, Gemini/Groq AI | Hunter.io |
| Freelance | Profile/resume, Gmail App Password | Gemini/Groq AI | Reddit app |
| Both | Profile/resume, Gmail App Password | Search + AI | Hunter.io, Reddit |
| Just leads | Profile/resume | – | Reddit |

These lead sources need **no key at all**: Greenhouse, Lever and Ashby job boards, Remotive, Arbeitnow, Hacker News "Who is hiring?", Freelancer.com.

## 1. Your profile and resume (required)

Your skills decide what matches you.

1. Run `leadhunter setup` (or open the dashboard).
2. Give the path to your resume (PDF, Word or text), or upload it on the dashboard.
3. Check the skills it found; add or remove any.

## 2. Gmail App Password (required to send)

Sends emails from your own Gmail and reads replies so follow-ups stop on their own.

1. Turn on **2-Step Verification**: https://myaccount.google.com/security
2. Open https://myaccount.google.com/apppasswords, type the name `LeadHunter` and click **Create**.
3. Copy the 16-character code into setup. Setup tests the connection right away.
4. In Gmail → Settings → **Forwarding and POP/IMAP**, make sure IMAP is enabled.

Saved as `GMAIL_APP_PASSWORD`. Your normal Gmail password is never used.

## 3. Web search: Brave or Tavily (recommended for jobs)

Finds the recruiter and hiring manager for each job. Without it you only get emails written in the job post.

1. Create a free account at https://brave.com/search/api/ (or https://tavily.com).
2. Copy your API key.
3. Paste it in the **Power-ups** step of setup.

Saved as `BRAVE_API_KEY` or `TAVILY_API_KEY`.

## 4. AI writing: Gemini, Groq or Ollama (recommended)

Writes more personal emails and proposals. Without it, LeadHunter uses good templates.

- **Gemini (free tier):** https://aistudio.google.com/apikey → `GEMINI_API_KEY`
- **Groq (free tier):** https://console.groq.com/keys → `GROQ_API_KEY`
- **Ollama (free, offline, no key):** install from https://ollama.com, then run `ollama pull llama3.2`

Pick the provider in the **Power-ups** step of setup. Model names change over time; if one stops working, pick a current model from your provider's list.

## 5. Hunter.io (optional, fewer bounced emails)

Finds the exact email format a company uses.

1. Create a free account at https://hunter.io and copy your key from https://hunter.io/api-keys.
2. Paste it in **Settings → Power-ups** (step 4). Saving it switches Hunter on.

## 6. Reddit app (optional, needs Reddit's approval)

Reddit's [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
means new API apps are approved by hand, and personal projects are often refused. It's safe to skip: Freelancer.com
already gives you freelance leads.

1. Request API access from Reddit and wait for approval.
2. If approved, create a **script** app at https://www.reddit.com/prefs/apps and paste its ID and secret in **Settings → Power-ups**.

## 7. Turn off practice mode (when you're ready)

LeadHunter starts in practice mode: emails are saved to `./outbox/*.eml` instead of being sent.

1. Approve and "send" a few emails, then open the `.eml` files to check them.
2. When they look right, untick **Practice mode** in setup (Gmail step).

## LinkedIn

There's nothing to connect, on purpose. LeadHunter never logs into LinkedIn (LinkedIn bans automation). Add your own profile URL in setup for your email signature; LeadHunter writes connection notes and messages and opens each profile so you send them yourself.

## Where everything is saved

| File | What's in it |
|---|---|
| `config.yaml` | Your answers: mode, skills, cities, rates, limits |
| `.env` | Passwords and API keys (only readable by you; never commit it) |
| `leadhunter.db` | Your leads, contacts and message history |
| `outbox/` | Emails written in practice mode |
