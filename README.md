# 12 O'Clock Boyz — MX Bikes AMA League System (Final Pack)

This repo contains:
- **Web app (Flask)**: signup, link Discord + Steam, profile (MXB in‑game name), events list, standings split by class, rules page, results upload.
- **Discord bot (discord.py)**: Race Director commands to create events, announce in `#race-announcements`, and apply penalties.
- **Local uploader (optional)**: a tiny script you run on your PC to automatically upload your **MX Bikes Export** HTML after each race.

> Important truth: the bot/web service running on Render cannot read your PC files automatically.
> To import results, you either upload the export file via the website, or run the included local uploader.

---

## Folder layout

- `web/` Flask app
- `bot/` Discord bot
- `shared/` DB + points + parsing helpers
- `scripts/` optional local uploader

---

## 1) Required environment variables (Render)

### Shared
- `DATABASE_URL`  (Render Postgres Internal URL recommended)
- `SECRET_KEY`    (random string)
- `PUBLIC_BASE_URL` (your web URL, e.g. https://12oclock-system.onrender.com)

### Discord OAuth (website login)
Create a Discord Application -> OAuth2
- Add Redirect URI: `https://YOUR_WEB_DOMAIN/auth/discord/callback`
Set:
- `DISCORD_CLIENT_ID`
- `DISCORD_CLIENT_SECRET`
- `DISCORD_GUILD_ID` (your server ID)

### Steam linking (website)
- `STEAM_WEB_API_KEY`  (optional for profile lookups; OpenID linking works without it)

### Bot
- `DISCORD_BOT_TOKEN`
- `RACE_ANNOUNCEMENTS_CHANNEL_ID`
- `RACE_DIRECTOR_ROLE_ID` (Role that can run director commands)
- Optional:
  - `CLIP_REVIEW_CHANNEL_ID`
  - `CLIP_APPROVAL_CHANNEL_ID`

### Results upload auth
- `RESULTS_UPLOAD_KEY` (random string)
This is used by the local uploader and the website upload endpoint.

---

## 2) Deploy on Render (recommended)

### A) Web Service
- Root Directory: `web`
- Build Command: `pip install -r ../requirements.txt`
- Start Command: `gunicorn app:app`

### B) Background Worker (Bot)
- Root Directory: `bot`
- Build Command: `pip install -r ../requirements.txt`
- Start Command: `python bot.py`

### C) Postgres
Create a Render Postgres database and set `DATABASE_URL` on both services.

---

## 3) First run / database
The apps auto-create tables on boot.

---

## 4) Using the system

### Website
- Visit `/` -> Click **Sign in with Discord**
- Go to **Profile** -> set your **MX Bikes In‑Game Name** (this is how results match to you)
- Link Steam on **Profile** if you want.

### Bot (Race Director)
- `/ping`
- `/create_event mode:MX class:450 title:"Round 1" track:"Appin" start:"2026-03-10 20:00" notes:"..."`

### Results importing (most important)
MX Bikes export gives you an `.html` file in **Documents\PiBoSo\MX Bikes\exports**
- Option 1: Website upload page: `/upload`
- Option 2: Local uploader script (auto):
  - `python scripts/local_uploader.py --export-dir "C:\Users\YOU\Documents\PiBoSo\MX Bikes\exports" --server https://YOUR_WEB_DOMAIN --key YOUR_RESULTS_UPLOAD_KEY`

---

## AMA Pro Points (included)
We included the standard 1st→22nd points table used in AMA-style series.
You can edit it in `shared/points.py`.

---

## Split standings by class
Standings pages:
- `/standings/450`
- `/standings/250`
- `/standings/250-2t`

---

## Troubleshooting

### "ModuleNotFoundError: requests"
Make sure your service installs `../requirements.txt` (web root dir is `web/` so path must be `../requirements.txt`)

### Discord says "application did not respond"
Your bot likely crashed or timed out. Check Render logs.

### I can't find results.txt
MX Bikes export writes an **HTML** file in `Documents\PiBoSo\MX Bikes\exports`.  
This system parses that HTML.

---

Enjoy — Devo 🔥
