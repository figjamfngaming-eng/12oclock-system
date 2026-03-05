import os
import time
from urllib.parse import urlencode

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, redirect, request, session, url_for, render_template, jsonify, abort

# ----------------------------
# Config / Env
# ----------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "")
SECRET_KEY = os.getenv("SECRET_KEY", os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me"))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")  # must match portal
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", os.getenv("DISCORD_INVITE", ""))

DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# ----------------------------
# App
# ----------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = SECRET_KEY

# ----------------------------
# DB helpers
# ----------------------------
def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL env var")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def db_exec(sql, params=None, fetch="none"):
    """
    fetch: 'none' | 'one' | 'all'
    """
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or {})
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return None

def ensure_schema():
    """
    Creates tables if missing and patches common missing columns.
    This avoids: psycopg2.errors.UndefinedColumn: column "user_id" does not exist
    """
    # create base tables
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            discord_name TEXT,
            steam_id TEXT,
            mxb_name TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

    db_exec(
        """
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            track TEXT,
            bike_class TEXT,
            start_time TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

    db_exec(
        """
        CREATE TABLE IF NOT EXISTS results (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            discord_id TEXT,
            rider_name TEXT,
            position INTEGER,
            points INTEGER,
            raw_html TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )

    # patch missing columns safely (Postgres 9.6+ supports IF NOT EXISTS on ADD COLUMN)
    db_exec("""ALTER TABLE users ADD COLUMN IF NOT EXISTS steam_id TEXT;""")
    db_exec("""ALTER TABLE users ADD COLUMN IF NOT EXISTS mxb_name TEXT;""")
    db_exec("""ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_name TEXT;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS raw_html TEXT;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS points INTEGER;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS discord_id TEXT;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS rider_name TEXT;""")
    db_exec("""ALTER TABLE results ADD COLUMN IF NOT EXISTS user_id INTEGER;""")

# Run schema once on first request (Flask 3 removed before_first_request)
_schema_done = False
@app.before_request
def _ensure_db_once():
    global _schema_done
    if not _schema_done:
        ensure_schema()
        _schema_done = True

# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def home():
    # Show homepage even if OAuth env vars are missing
    oauth_ok = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)

    # registered racers count
    try:
        row = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")
        racers = row["c"] if row else 0
    except Exception:
        racers = 0

    return render_template(
        "index.html",
        invite_url=DISCORD_INVITE_URL,
        oauth_ok=oauth_ok,
        racers=racers,
        me=session.get("me"),
    )

@app.get("/profile")
def profile():
    if not session.get("me"):
        return redirect(url_for("home"))
    # load user from db
    me = session["me"]
    u = db_exec("SELECT * FROM users WHERE discord_id=%(d)s;", {"d": me["id"]}, fetch="one")
    return render_template("profile.html", me=me, user=u, invite_url=DISCORD_INVITE_URL)

@app.post("/profile")
def profile_save():
    if not session.get("me"):
        return redirect(url_for("home"))
    me = session["me"]
    steam_id = request.form.get("steam_id", "").strip()
    mxb_name = request.form.get("mxb_name", "").strip()

    # upsert user
    db_exec(
        """
        INSERT INTO users (discord_id, discord_name, steam_id, mxb_name)
        VALUES (%(discord_id)s, %(discord_name)s, %(steam_id)s, %(mxb_name)s)
        ON CONFLICT (discord_id)
        DO UPDATE SET discord_name=EXCLUDED.discord_name,
                      steam_id=EXCLUDED.steam_id,
                      mxb_name=EXCLUDED.mxb_name;
        """,
        {
            "discord_id": me["id"],
            "discord_name": me.get("username"),
            "steam_id": steam_id,
            "mxb_name": mxb_name,
        },
    )
    return redirect(url_for("profile"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# ----------------------------
# Discord OAuth
# ----------------------------
@app.get("/auth/discord/login")
def discord_login():
    if not (DISCORD_CLIENT_ID and DISCORD_REDIRECT_URI):
        abort(500, "Discord OAuth env vars missing (DISCORD_CLIENT_ID / DISCORD_REDIRECT_URI).")

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
    if not (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI):
        abort(500, "Discord OAuth env vars missing (CLIENT_ID/SECRET/REDIRECT_URI).")

    code = request.args.get("code")
    if not code:
        return redirect(url_for("home"))

    # exchange code
    token_resp = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    token_resp.raise_for_status()
    token = token_resp.json()["access_token"]

    # get user
    me_resp = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    me_resp.raise_for_status()
    me = me_resp.json()

    session["me"] = {"id": str(me["id"]), "username": me.get("username", "")}

    # ensure user exists
    db_exec(
        """
        INSERT INTO users (discord_id, discord_name)
        VALUES (%(discord_id)s, %(discord_name)s)
        ON CONFLICT (discord_id)
        DO UPDATE SET discord_name=EXCLUDED.discord_name;
        """,
        {"discord_id": str(me["id"]), "discord_name": me.get("username", "")},
    )

    return redirect(url_for("profile"))

# ----------------------------
# League pages
# ----------------------------
@app.get("/schedule")
def schedule():
    events = db_exec(
        "SELECT * FROM events ORDER BY start_time NULLS LAST, id DESC;",
        fetch="all"
    ) or []
    return render_template("schedule.html", events=events, invite_url=DISCORD_INVITE_URL)

@app.get("/standings")
def standings():
    rows = db_exec(
        """
        SELECT
          COALESCE(u.mxb_name, u.discord_name, r.rider_name, r.discord_id) AS rider,
          SUM(COALESCE(r.points, 0))::int AS points,
          COUNT(*)::int AS motos
        FROM results r
        LEFT JOIN users u ON u.id = r.user_id OR u.discord_id = r.discord_id
        GROUP BY rider
        ORDER BY points DESC, motos DESC, rider ASC;
        """,
        fetch="all"
    ) or []
    return render_template("standings.html", rows=rows, invite_url=DISCORD_INVITE_URL)

@app.get("/riders")
def riders():
    users = db_exec("SELECT * FROM users ORDER BY created_at DESC LIMIT 500;", fetch="all") or []
    return render_template("riders.html", users=users, invite_url=DISCORD_INVITE_URL)

@app.get("/rules")
def rules():
    return render_template("rules.html", invite_url=DISCORD_INVITE_URL)

@app.get("/upload")
def upload_page():
    # optional: require login
    return render_template("upload.html", invite_url=DISCORD_INVITE_URL)

@app.post("/upload")
def upload_results():
    """
    Accepts either:
    - a file input named 'file'
    - or a text area named 'raw_html'
    """
    raw_html = ""
    if "file" in request.files and request.files["file"].filename:
        raw_html = request.files["file"].read().decode("utf-8", errors="ignore")
    else:
        raw_html = request.form.get("raw_html", "")

    raw_html = (raw_html or "").strip()
    if not raw_html:
        return "No HTML provided", 400

    # attach to active event if any
    ev = db_exec("SELECT * FROM events WHERE is_active=TRUE ORDER BY id DESC LIMIT 1;", fetch="one")
    event_id = ev["id"] if ev else None

    # store raw html
    me = session.get("me")
    discord_id = me["id"] if me else None

    db_exec(
        """
        INSERT INTO results (event_id, discord_id, rider_name, position, points, raw_html)
        VALUES (%(event_id)s, %(discord_id)s, %(rider_name)s, NULL, NULL, %(raw_html)s);
        """,
        {
            "event_id": event_id,
            "discord_id": discord_id,
            "rider_name": me.get("username") if me else None,
            "raw_html": raw_html,
        },
    )

    return redirect(url_for("standings"))

# ----------------------------
# API
# ----------------------------
@app.get("/api/discord_stats")
def api_discord_stats():
    # Basic health stats; optional guild member count if token + guild provided
    stats = {"ok": True, "guild_id": DISCORD_GUILD_ID or None}

    try:
        row = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")
        stats["registered_racers"] = row["c"] if row else 0
    except Exception:
        stats["registered_racers"] = 0

    if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID:
        try:
            r = requests.get(
                f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}?with_counts=true",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                stats["approx_members"] = data.get("approximate_member_count")
        except Exception:
            pass

    return jsonify(stats)

# alias your frontend was calling
@app.get("/api/stats")
def api_stats_alias():
    return api_discord_stats()

# ----------------------------
# Main
# ----------------------------
if __name__ == "__main__":
    # local dev only
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
