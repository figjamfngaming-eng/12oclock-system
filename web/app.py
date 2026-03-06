import os
import secrets
import requests
from urllib.parse import urlencode

from flask import Flask, redirect, request, session, url_for, render_template, jsonify

from shared.db import init_db, exec_sql, q

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")  # must exactly match Discord Developer Portal
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/")
DATABASE_URL = os.getenv("DATABASE_URL")

# A strong secret is required for sessions
SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = SECRET_KEY

_db_ready = False


def ensure_db():
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True


def oauth_ready():
    return all([DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI])


@app.before_request
def _before_any_request():
    # Ensure DB tables exist for every request without using removed Flask hooks.
    if DATABASE_URL:
        try:
            ensure_db()
        except Exception:
            # Don't kill the whole site if DB is temporarily down
            pass


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.get("/")
def index():
    user = session.get("user")
    return render_template(
        "index.html",
        user=user,
        invite_url=DISCORD_INVITE_URL,
        oauth_ready=oauth_ready(),
    )


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.get("/auth/discord/login")
def discord_login():
    if not oauth_ready():
        return redirect(url_for("index"))

    # Discord OAuth2 Authorization
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "prompt": "none",
    }
    return redirect("https://discord.com/api/oauth2/authorize?" + urlencode(params))


@app.get("/auth/discord/callback")
def discord_callback():
    if not oauth_ready():
        return redirect(url_for("index"))

    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

    # Exchange code for token
    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify",
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    token_resp = requests.post(
        "https://discord.com/api/oauth2/token",
        data=token_data,
        headers=headers,
        timeout=20,
    )
    token_resp.raise_for_status()
    token_json = token_resp.json()
    access_token = token_json["access_token"]

    # Get Discord user
    user_resp = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    user_resp.raise_for_status()
    u = user_resp.json()

    discord_id = u.get("id")
    discord_name = f"{u.get('username', '')}#{u.get('discriminator', '0000')}".strip("#0000") if u.get("discriminator") else u.get("username")

    # Save session
    session["user"] = {"discord_id": discord_id, "discord_name": discord_name}

    # Save to DB
    if DATABASE_URL:
        ensure_db()
        exec_sql(
            """
            INSERT INTO users (discord_id, discord_name)
            VALUES (%s, %s)
            ON CONFLICT (discord_id)
            DO UPDATE SET discord_name = EXCLUDED.discord_name
            """,
            (discord_id, discord_name),
        )

    return redirect(url_for("index"))


@app.get("/api/discord_stats")
def api_discord_stats():
    # Simple stat example: number of registered users
    if not DATABASE_URL:
        return jsonify({"registered_racers": 0})

    ensure_db()
    rows = q("SELECT COUNT(*)::int AS c FROM users")
    return jsonify({"registered_racers": rows[0]["c"]})


@app.get("/standings")
def standings_page():
    # Show standings page using race_results table
    if not DATABASE_URL:
        return render_template("standings.html", rows=[], season="S1", round=1, class_name="450")

    ensure_db()
    season = request.args.get("season", "S1")
    round_str = request.args.get("round", "1")
    class_name = request.args.get("class", "450")

    try:
        rnd = int(round_str)
    except ValueError:
        rnd = 1

    rows = q(
        """
        SELECT rider_name, discord_id, SUM(points)::int AS points
        FROM race_results
        WHERE season = %s AND round = %s AND class_name = %s
        GROUP BY rider_name, discord_id
        ORDER BY points DESC, rider_name ASC
        """,
        (season, rnd, class_name),
    )
    return render_template("standings.html", rows=rows, season=season, round=rnd, class_name=class_name)
