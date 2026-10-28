# Contributing

Thanks for helping! Artemis is small on purpose: plain Python, SQLite, server-rendered HTML, no JS build.

## Setup

```bash
git clone https://github.com/Manideep-Ai-ai/artemis.git && cd artemis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Tests must run **offline**. Use the fakes in `tests/conftest.py` (`FakeSearch`, dry-run `Mailer`, fixed `Clock`) and save real API responses under `tests/fixtures/`.

## Good first issues

- Add a job source (Workable, RemoteOK). Copy the pattern in `sources.py` and add a fixture test.
- Add skills and aliases to `resume.py`.
- Improve name / headline parsing in `research.py` for more search-result formats.
- Translate the email templates in `compose.py`.

## Rules for new features

1. **Use official APIs or public feeds.** No logged-in scraping, no LinkedIn automation, no CAPTCHA solving.
2. **Never weaken the safety rails:** daily limit and hard ceiling, approval before first emails, send window, stop-on-reply, suppression list.
3. **AI is optional.** Every AI feature needs a non-AI fallback.
4. **Keep data local.** No telemetry, no central server.

## Pull requests

- One change per PR, with a test.
- Run `pytest` before pushing; CI runs it on Python 3.10–3.13.
- Describe what you changed and how you tested it.
