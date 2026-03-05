# AMA League System – Render Deploy Notes

## Why you saw `ModuleNotFoundError: requests`
That happens when the Render **Web Service Root Directory** is set to `/web` but the service is installing dependencies from the repo root `requirements.txt`.
To make it bulletproof, this pack includes **web/requirements.txt** and **bot/requirements.txt**.

## Render (recommended setup)

### Web service (one2oclock-web / 12oclock-web)
- Root Directory: `web`
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT`

### Bot worker (12oclock-system)
- Root Directory: `bot`
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

## Environment variables (minimum)
- `DATABASE_URL`  (use Render INTERNAL URL if bot+web are on Render)
- `DISCORD_BOT_TOKEN`
- `DISCORD_GUILD_ID`
- `RACE_ANNOUNCEMENTS_CHANNEL_ID`
- `DEFAULT_TZ_OFFSET_HOURS` (e.g. 11)

