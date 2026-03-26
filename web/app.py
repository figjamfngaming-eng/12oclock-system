import os
import random
from flask import Flask, jsonify, redirect, render_template, request, session, url_for, abort
import psycopg2
import psycopg2.extras
import requests

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-secret-key-now")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/")
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")
ADMIN_DISCORD_IDS = {x.strip() for x in os.getenv("ADMIN_DISCORD_IDS", "").split(",") if x.strip()}
_db_ready = False


def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL env var")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_exec(sql: str, params=None, fetch: str = "none"):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == "one":
                row = cur.fetchone(); conn.commit(); return row
            if fetch == "all":
                rows = cur.fetchall(); conn.commit(); return rows
            conn.commit(); return None


def init_db_once():
    global _db_ready
    if _db_ready or not DATABASE_URL:
        return
    db_exec("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        discord_id TEXT UNIQUE,
        discord_name TEXT,
        mxb_name TEXT,
        steam_id TEXT,
        guid TEXT,
        guid_status TEXT DEFAULT 'pending',
        guid_note TEXT,
        team_name TEXT,
        rider_number TEXT,
        rider_class TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS guid TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS guid_status TEXT DEFAULT 'pending';")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS guid_note TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS rider_class TEXT;")
    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_guid_unique ON users(guid) WHERE guid IS NOT NULL;")

    db_exec("""
    CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        name TEXT,
        track TEXT,
        class_name TEXT,
        season TEXT DEFAULT 'S1',
        round_number INTEGER DEFAULT 1,
        event_type TEXT DEFAULT 'practice',
        guid_lock_required BOOLEAN DEFAULT FALSE,
        start_time TIMESTAMP NULL,
        status TEXT DEFAULT 'open',
        created_by_discord_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS event_type TEXT DEFAULT 'practice';")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS guid_lock_required BOOLEAN DEFAULT FALSE;")

    db_exec("""
    CREATE TABLE IF NOT EXISTS registrations (
        id SERIAL PRIMARY KEY,
        event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
        discord_id TEXT,
        rider_name TEXT,
        rider_guid TEXT,
        class_name TEXT,
        team_name TEXT,
        gate_pick INTEGER,
        status TEXT DEFAULT 'registered',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("ALTER TABLE registrations ADD COLUMN IF NOT EXISTS rider_guid TEXT;")

    db_exec("""
    CREATE TABLE IF NOT EXISTS protests (
        id SERIAL PRIMARY KEY,
        event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
        discord_id TEXT,
        rider_name TEXT,
        against_rider TEXT,
        reason TEXT,
        status TEXT DEFAULT 'open',
        admin_note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    db_exec("""
    CREATE TABLE IF NOT EXISTS qualifying_times (
        id SERIAL PRIMARY KEY,
        event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
        discord_id TEXT,
        rider_name TEXT,
        rider_guid TEXT,
        best_lap_ms INTEGER NOT NULL,
        lap_source TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(event_id, rider_guid)
    );
    """)
    _db_ready = True


@app.before_request
def ensure_db():
    init_db_once()


def current_user():
    return session.get("user")


def oauth_ready() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)


def is_admin_user() -> bool:
    user = current_user()
    return bool(user and str(user.get("discord_id")) in ADMIN_DISCORD_IDS)


def require_admin():
    if not is_admin_user():
        abort(403)


@app.context_processor
def inject_globals():
    return {"invite_url": DISCORD_INVITE_URL, "user": current_user(), "oauth_ready": oauth_ready(), "default_season": DEFAULT_SEASON, "is_admin_user": is_admin_user()}


@app.route("/")
def index():
    recent_events = db_exec("SELECT id, name, track, class_name, season, round_number, event_type, guid_lock_required, status FROM events ORDER BY id DESC LIMIT 6;", fetch="all")
    rider_count = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")["c"]
    reg_count = db_exec("SELECT COUNT(*)::int AS c FROM registrations;", fetch="one")["c"]
    return render_template("index.html", recent_events=recent_events, rider_count=rider_count, reg_count=reg_count, standings_450=[], standings_250=[])


@app.route("/riders")
def riders_page():
    rows = db_exec("SELECT discord_name, mxb_name, steam_id, guid, guid_status, team_name, rider_number, rider_class, created_at FROM users ORDER BY created_at DESC, discord_name ASC;", fetch="all")
    return render_template("riders.html", riders=rows)


@app.route("/events")
def events_page():
    rows = db_exec("SELECT * FROM events ORDER BY round_number ASC, id ASC;", fetch="all")
    return render_template("events.html", events=rows, season=DEFAULT_SEASON)


@app.route("/event/<int:event_id>")
def event_page(event_id: int):
    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    if not event:
        return "Event not found", 404
    results = db_exec("SELECT rider_name, rider_guid, best_lap_ms FROM qualifying_times WHERE event_id = %s ORDER BY best_lap_ms ASC, created_at ASC;", (event_id,), fetch="all")
    regs = db_exec("SELECT rider_name, rider_guid, gate_pick, status FROM registrations WHERE event_id = %s ORDER BY gate_pick ASC NULLS LAST, rider_name ASC;", (event_id,), fetch="all")
    return render_template("event.html", event=event, results=results, regs=regs)


@app.route("/register", methods=["GET", "POST"])
def register_page():
    user = current_user()
    if not user:
        return redirect(url_for("index"))
    message = None
    if request.method == "POST":
        event_id = request.form.get("event_id", type=int)
        rider_name = request.form.get("rider_name", "").strip()
        event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
        profile = db_exec("SELECT * FROM users WHERE discord_id = %s LIMIT 1;", (user["discord_id"],), fetch="one")
        if not event:
            message = "Event not found."
        elif not profile:
            message = "Create your rider profile first."
        elif event["guid_lock_required"] and (not profile.get("guid") or profile.get("guid_status") != "approved"):
            status = profile.get("guid_status") or "missing"
            message = f"GUID mismatch: You are not approved for this event. Your current GUID status is {status}. Update your GUID in your profile and try again."
        else:
            existing = db_exec("SELECT id FROM registrations WHERE event_id = %s AND discord_id = %s LIMIT 1;", (event_id, user["discord_id"]), fetch="one")
            if existing:
                message = "You are already registered for this event."
            else:
                db_exec("INSERT INTO registrations (event_id, discord_id, rider_name, rider_guid, class_name, team_name, status) VALUES (%s,%s,%s,%s,%s,%s,'registered');", (event_id, user["discord_id"], rider_name or profile.get("mxb_name"), profile.get("guid"), event.get("class_name"), profile.get("team_name")))
                message = "Registration successful."
    events = db_exec("SELECT id, name, class_name, event_type, guid_lock_required, status FROM events WHERE status = 'open' ORDER BY id DESC;", fetch="all")
    return render_template("register.html", events=events, message=message)


@app.route("/profile", methods=["GET", "POST"])
def profile_page():
    user = current_user()
    if not user:
        return redirect(url_for("index"))
    profile = db_exec("SELECT * FROM users WHERE discord_id = %s LIMIT 1;", (user["discord_id"],), fetch="one")
    if request.method == "POST":
        mxb_name = request.form.get("mxb_name", "").strip()
        steam_id = request.form.get("steam_id", "").strip() or None
        guid = request.form.get("guid", "").strip() or None
        team_name = request.form.get("team_name", "").strip() or None
        rider_number = request.form.get("rider_number", "").strip() or None
        rider_class = request.form.get("rider_class", "").strip() or None
        dup = None
        if guid:
            dup = db_exec("SELECT discord_name FROM users WHERE guid = %s AND discord_id <> %s LIMIT 1;", (guid, user["discord_id"]), fetch="one")
        if dup:
            profile = dict(profile or {})
            profile.update({"discord_name": user["username"], "mxb_name": mxb_name, "steam_id": steam_id, "guid": guid, "team_name": team_name, "rider_number": rider_number, "rider_class": rider_class, "guid_status": "mismatch", "guid_note": f"GUID already used by {dup['discord_name']}"})
            return render_template("profile.html", profile=profile, message=f"GUID already used by {dup['discord_name']}. Pick the correct GUID.")
        db_exec("""
            INSERT INTO users (discord_id, discord_name, mxb_name, steam_id, guid, guid_status, guid_note, team_name, rider_number, rider_class)
            VALUES (%s,%s,%s,%s,%s,'pending',NULL,%s,%s,%s)
            ON CONFLICT (discord_id) DO UPDATE SET
                discord_name = EXCLUDED.discord_name,
                mxb_name = EXCLUDED.mxb_name,
                steam_id = EXCLUDED.steam_id,
                guid = EXCLUDED.guid,
                guid_status = 'pending',
                guid_note = NULL,
                team_name = EXCLUDED.team_name,
                rider_number = EXCLUDED.rider_number,
                rider_class = EXCLUDED.rider_class;
        """, (user["discord_id"], user["username"], mxb_name, steam_id, guid, team_name, rider_number, rider_class))
        return redirect(url_for("profile_page"))
    if not profile:
        profile = {"discord_name": user["username"], "mxb_name": None, "steam_id": None, "guid": None, "guid_status": "missing", "guid_note": None, "team_name": None, "rider_number": None, "rider_class": None}
    return render_template("profile.html", profile=profile, message=None)


@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    require_admin()
    message = None
    if request.method == "POST":
        action = request.form.get("action", "").strip()
        if action == "create_event":
            name = request.form.get("name", "").strip()
            track = request.form.get("track", "").strip()
            class_name = request.form.get("class_name", "").strip()
            season = request.form.get("season", DEFAULT_SEASON).strip() or DEFAULT_SEASON
            round_number = request.form.get("round_number", type=int) or 1
            event_type = request.form.get("event_type", "practice").strip()
            guid_lock_required = event_type in {"qualifier", "finals"}
            if name and track and class_name:
                db_exec("INSERT INTO events (name, track, class_name, season, round_number, event_type, guid_lock_required, status, created_by_discord_id) VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s);", (name, track, class_name, season, round_number, event_type, guid_lock_required, current_user()["discord_id"]))
                message = "Event created."
        elif action == "set_guid_status":
            discord_id = request.form.get("discord_id", "").strip()
            status = request.form.get("guid_status", "pending").strip()
            note = request.form.get("guid_note", "").strip() or None
            db_exec("UPDATE users SET guid_status = %s, guid_note = %s WHERE discord_id = %s;", (status, note, discord_id))
            message = "GUID status updated."
        elif action == "draw_gates_fastest_lap":
            event_id = request.form.get("event_id", type=int)
            rows = db_exec("SELECT rider_guid FROM qualifying_times WHERE event_id = %s ORDER BY best_lap_ms ASC, created_at ASC;", (event_id,), fetch="all")
            for idx, row in enumerate(rows, start=1):
                db_exec("UPDATE registrations SET gate_pick = %s WHERE event_id = %s AND rider_guid = %s;", (idx, event_id, row["rider_guid"]))
            message = "Gate pick order updated from fastest lap."
    events = db_exec("SELECT * FROM events ORDER BY id DESC LIMIT 50;", fetch="all")
    riders = db_exec("SELECT discord_id, discord_name, mxb_name, guid, guid_status, guid_note, rider_class FROM users ORDER BY created_at DESC LIMIT 100;", fetch="all")
    return render_template("admin.html", message=message, events=events, protests=[], riders=riders)


@app.route("/auth/discord/login")
def discord_login():
    if not oauth_ready():
        return redirect(url_for("index"))
    return redirect(f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify")


@app.route("/auth/discord/callback")
def discord_callback():
    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))
    token_resp = requests.post("https://discord.com/api/oauth2/token", data={"client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET, "grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        return redirect(url_for("index"))
    me = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"}, timeout=20).json()
    session["user"] = {"discord_id": me["id"], "username": me.get("global_name") or me["username"]}
    return redirect(url_for("profile_page"))


@app.route("/auth/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

