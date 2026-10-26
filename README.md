# GPAT Quiz by Anees — Telegram quiz bot

Free daily GPAT / B.Pharm 3rd-sem MCQ practice on Telegram, using native quiz polls (correct answer + 1-line explanation).

Commands: `/start` · `/quiz` (10 random MCQs) · `/subject` (pick a subject) · `/daily` (10 questions every day at 8 PM IST, toggle) · `/score`.

After every quiz: *Poora 30-day exam plan + sab answers → https://pharma3sem.publicvm.com (₹49)*

- `questions.json` — 320 MCQs in 6 subjects (Pharmaceutics, Pharma Chemistry/Organic, Physical Pharmacy, Microbiology, Pharmaceutical Engineering, Pharmacology basics). 240 written for this bot + the 80 free-tier MCQs of pharma3sem. Options are shuffled each time.
- `bot.py` — no framework, only `requests`; webhook mode for Render's free plan. State (users, scores, daily opt-ins) lives in `/tmp` and is backed up as one pinned JSON file in the owner's chat with the bot, so it survives the free server sleeping.
- `deploy/github-daily-workflow.yml.example` — optional GitHub Action that wakes the server and triggers the daily quiz at 8 PM IST.

Env vars (set on the host, never in the repo): `TELEGRAM_BOT_TOKEN`, `OWNER_CHAT_ID`, `CRON_KEY`.
Repo secrets for the workflow: `BOT_URL`, `CRON_KEY`.

© Anees Reddy
