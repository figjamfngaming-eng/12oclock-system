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
ADMIN_DISCORD_IDS = {
    x.strip() for x in os.getenv("ADMIN_DISCORD_IDS", "").split(",") if x.strip()
}


def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL env var")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_exec(sql: str, params=None, fetch: str = "none"):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == "one":
                row = cur.fetchone()
                conn.commit()
                return row
            if fetch == "all":
                rows = cur.fetchall()
                conn.commit()
                return rows
            conn.commit()
            return None


def init_db():
    db_exec("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            discord_name TEXT,
            mxb_name TEXT,
            steam_id TEXT,
            team_name TEXT,
            rider_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            name TEXT,
            track TEXT,
            class_name TEXT,
            season TEXT DEFAULT 'S1',
            round_number INTEGER DEFAULT 1,
            start_time TIMESTAMP NULL,
            status TEXT DEFAULT 'open',
            created_by_discord_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS race_results (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            season TEXT DEFAULT 'S1',
            round_number INTEGER DEFAULT 1,
            class_name TEXT DEFAULT '450',
            discord_id TEXT,
            rider_name TEXT,
            team_name TEXT,
            position INTEGER,
            points INTEGER DEFAULT 0,
            penalty_points INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS registrations (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            discord_id TEXT,
            rider_name TEXT,
            class_name TEXT,
            team_name TEXT,
            gate_pick INTEGER,
            status TEXT DEFAULT 'registered',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

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

    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS team_name TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS rider_number TEXT;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS team_name TEXT;")

    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_event ON race_results(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_class ON race_results(class_name);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_reg_event ON registrations(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_protest_event ON protests(event_id);")


@app.before_request
def ensure_db():
    if DATABASE_URL:
        init_db()


def current_user():
    return session.get("user")


def oauth_ready() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)


def is_admin_user() -> bool:
    user = current_user()
    if not user:
        return False
    return str(user.get("discord_id")) in ADMIN_DISCORD_IDS


def require_admin():
    if not is_admin_user():
        abort(403)


def get_points_for_position(pos: int) -> int:
    ama = {
        1: 26, 2: 23, 3: 21, 4: 19, 5: 18, 6: 17, 7: 16, 8: 15, 9: 14, 10: 13,
        11: 12, 12: 11, 13: 10, 14: 9, 15: 8, 16: 7, 17: 6, 18: 5, 19: 4, 20: 3,
        21: 2, 22: 1
    }
    return ama.get(pos, 0)


@app.context_processor
def inject_globals():
    return {
        "invite_url": DISCORD_INVITE_URL,
        "user": current_user(),
        "oauth_ready": oauth_ready(),
        "default_season": DEFAULT_SEASON,
        "is_admin_user": is_admin_user(),
    }


@app.route("/")
def index():
    recent_events = db_exec("""
        SELECT id, name, track, class_name, season, round_number, start_time, status
        FROM events
        ORDER BY id DESC
        LIMIT 6;
    """, fetch="all")

    standings_450 = db_exec("""
        SELECT rider_name,
               SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = '450' AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC
        LIMIT 5;
    """, (DEFAULT_SEASON,), fetch="all")

    standings_250 = db_exec("""
        SELECT rider_name,
               SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = '250' AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC
        LIMIT 5;
    """, (DEFAULT_SEASON,), fetch="all")

    rider_count = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")["c"]
    reg_count = db_exec("SELECT COUNT(*)::int AS c FROM registrations;", fetch="one")["c"]

    return render_template(
        "index.html",
        recent_events=recent_events,
        standings_450=standings_450,
        standings_250=standings_250,
        rider_count=rider_count,
        reg_count=reg_count,
    )


@app.route("/events")
def events_page():
    season = request.args.get("season", DEFAULT_SEASON)
    rows = db_exec("""
        SELECT id, name, track, class_name, season, round_number, start_time, status
        FROM events
        WHERE season = %s
        ORDER BY round_number ASC, id ASC;
    """, (season,), fetch="all")
    return render_template("events.html", events=rows, season=season)


@app.route("/event/<int:event_id>")
def event_page(event_id: int):
    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    if not event:
        return "Event not found", 404

    results = db_exec("""
        SELECT rider_name, team_name, position, points, penalty_points, notes
        FROM race_results
        WHERE event_id = %s
        ORDER BY position ASC NULLS LAST, rider_name ASC;
    """, (event_id,), fetch="all")

    regs = db_exec("""
        SELECT rider_name, team_name, gate_pick, status
        FROM registrations
        WHERE event_id = %s
        ORDER BY gate_pick ASC NULLS LAST, rider_name ASC;
    """, (event_id,), fetch="all")

    return render_template("event.html", event=event, results=results, regs=regs)


@app.route("/standings")
def standings_page():
    season = request.args.get("season", DEFAULT_SEASON)
    class_name = request.args.get("class", "450")

    rows = db_exec("""
        SELECT rider_name,
               SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = %s AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC;
    """, (class_name, season), fetch="all")

    return render_template("standings.html", rows=rows, season=season, class_name=class_name)


@app.route("/team-standings")
def team_standings_page():
    season = request.args.get("season", DEFAULT_SEASON)
    rows = db_exec("""
        SELECT COALESCE(team_name, 'Independent') AS team_name,
               SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE season = %s
        GROUP BY COALESCE(team_name, 'Independent')
        ORDER BY total_points DESC, team_name ASC;
    """, (season,), fetch="all")
    return render_template("teams.html", rows=rows, season=season)


@app.route("/riders")
def riders_page():
    rows = db_exec("""
        SELECT discord_name, mxb_name, steam_id, team_name, rider_number, created_at
        FROM users
        ORDER BY created_at DESC, discord_name ASC;
    """, fetch="all")
    return render_template("riders.html", riders=rows)


@app.route("/schedule")
def schedule_page():
    season = request.args.get("season", DEFAULT_SEASON)
    rows = db_exec("""
        SELECT id, name, track, class_name, season, round_number, start_time, status
        FROM events
        WHERE season = %s
        ORDER BY round_number ASC, id ASC;
    """, (season,), fetch="all")
    return render_template("schedule.html", schedule=rows, season=season)


@app.route("/rules")
def rules_page():
    rules = [
        "Respect all riders, staff, and officials at all times.",
        "No intentional cutting, ramming, brake-checking, or dirty riding.",
        "Race Director decisions are final unless formally reviewed.",
        "All riders must use their registered MX Bikes rider name.",
        "Penalties may be applied for unsafe riding or breaking league rules.",
        "Standings are calculated from race points minus penalty points.",
        "False result submissions or impersonation can lead to removal.",
        "Official updates are posted on the website and Discord.",
        "Riders are responsible for joining the correct event and class.",
        "Unsportsmanlike conduct may result in suspensions or disqualification.",
    ]
    return render_template("rules.html", rules=rules)


@app.route("/register", methods=["GET", "POST"])
def register_page():
    user = current_user()
    if not user:
        return redirect(url_for("index"))

    message = None

    if request.method == "POST":
        event_id = request.form.get("event_id", type=int)
        rider_name = request.form.get("rider_name", "").strip()
        team_name = request.form.get("team_name", "").strip() or None

        event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
        if not event:
            message = "Event not found."
        elif event["status"] != "open":
            message = "Registration is closed for this event."
        elif not rider_name:
            message = "Rider name is required."
        else:
            existing = db_exec("""
                SELECT id FROM registrations
                WHERE event_id = %s AND discord_id = %s
                LIMIT 1;
            """, (event_id, user["discord_id"]), fetch="one")

            if existing:
                message = "You are already registered for this event."
            else:
                db_exec("""
                    INSERT INTO registrations (event_id, discord_id, rider_name, class_name, team_name, status)
                    VALUES (%s, %s, %s, %s, %s, 'registered');
                """, (event_id, user["discord_id"], rider_name, event["class_name"], team_name))
                message = "Registration successful."

    events = db_exec("""
        SELECT id, name, class_name, season, round_number, status
        FROM events
        WHERE status = 'open'
        ORDER BY id DESC;
    """, fetch="all")

    return render_template("register.html", events=events, message=message)


@app.route("/protests", methods=["GET", "POST"])
def protests_page():
    user = current_user()
    if not user:
        return redirect(url_for("index"))

    message = None

    if request.method == "POST":
        event_id = request.form.get("event_id", type=int)
        rider_name = request.form.get("rider_name", "").strip()
        against_rider = request.form.get("against_rider", "").strip()
        reason = request.form.get("reason", "").strip()

        if event_id and rider_name and against_rider and reason:
            db_exec("""
                INSERT INTO protests (event_id, discord_id, rider_name, against_rider, reason, status)
                VALUES (%s, %s, %s, %s, %s, 'open');
            """, (event_id, user["discord_id"], rider_name, against_rider, reason))
            message = "Protest submitted."

    protests = db_exec("""
        SELECT p.*, e.name AS event_name
        FROM protests p
        LEFT JOIN events e ON p.event_id = e.id
        ORDER BY p.id DESC
        LIMIT 50;
    """, fetch="all")

    events = db_exec("SELECT id, name FROM events ORDER BY id DESC LIMIT 30;", fetch="all")

    return render_template("protests.html", protests=protests, events=events, message=message)


@app.route("/upload", methods=["GET", "POST"])
def upload_page():
    message = None
    user = current_user()

    if request.method == "POST":
        if not user:
            return redirect(url_for("index"))

        event_id = request.form.get("event_id", type=int)
        rider_name = request.form.get("rider_name", "").strip()
        team_name = request.form.get("team_name", "").strip() or None
        position = request.form.get("position", type=int)
        points = request.form.get("points", type=int)
        notes = request.form.get("notes", "").strip() or None

        event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")

        if not event:
            message = "Event not found."
        elif not rider_name:
            message = "Rider name is required."
        else:
            db_exec("""
                INSERT INTO race_results
                (event_id, season, round_number, class_name, discord_id, rider_name, team_name, position, points, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
            """, (
                event["id"],
                event["season"],
                event["round_number"],
                event["class_name"],
                user["discord_id"],
                rider_name,
                team_name,
                position,
                points or 0,
                notes,
            ))
            message = "Result uploaded successfully."

    events = db_exec("""
        SELECT id, name, class_name, season, round_number, status
        FROM events
        ORDER BY id DESC
        LIMIT 20;
    """, fetch="all")

    return render_template("upload.html", events=events, message=message)


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

            if name and track and class_name:
                db_exec("""
                    INSERT INTO events (name, track, class_name, season, round_number, status, created_by_discord_id)
                    VALUES (%s, %s, %s, %s, %s, 'open', %s);
                """, (name, track, class_name, season, round_number, current_user()["discord_id"]))
                message = "Event created."

        elif action == "close_event":
            event_id = request.form.get("event_id", type=int)
            if event_id:
                db_exec("UPDATE events SET status = 'closed' WHERE id = %s;", (event_id,))
                message = "Event closed."

        elif action == "draw_gates":
            event_id = request.form.get("event_id", type=int)
            regs = db_exec("""
                SELECT id FROM registrations
                WHERE event_id = %s
                ORDER BY id ASC;
            """, (event_id,), fetch="all")
            picks = list(range(1, len(regs) + 1))
            random.shuffle(picks)
            for reg, gate in zip(regs, picks):
                db_exec("UPDATE registrations SET gate_pick = %s WHERE id = %s;", (gate, reg["id"]))
            message = "Gate picks randomized."

        elif action == "resolve_protest":
            protest_id = request.form.get("protest_id", type=int)
            status = request.form.get("status", "").strip()
            admin_note = request.form.get("admin_note", "").strip() or None
            if protest_id and status:
                db_exec("""
                    UPDATE protests
                    SET status = %s, admin_note = %s
                    WHERE id = %s;
                """, (status, admin_note, protest_id))
                message = "Protest updated."

    events = db_exec("SELECT * FROM events ORDER BY id DESC LIMIT 20;", fetch="all")
    protests = db_exec("""
        SELECT p.*, e.name AS event_name
        FROM protests p
        LEFT JOIN events e ON p.event_id = e.id
        ORDER BY p.id DESC
        LIMIT 30;
    """, fetch="all")

    return render_template("admin.html", message=message, events=events, protests=protests)


@app.route("/auth/discord/login")
def discord_login():
    if not oauth_ready():
        return redirect(url_for("index"))

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "prompt": "consent",
    }
    qs = "&".join([f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items()])
    return redirect(f"https://discord.com/api/oauth2/authorize?{qs}")


@app.route("/auth/discord/callback")
def discord_callback():
    if not oauth_ready():
        return redirect(url_for("index"))

    code = request.args.get("code")
    if not code:
        return redirect(url_for("index"))

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
        timeout=20,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    user_resp = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    user_resp.raise_for_status()
    discord_user = user_resp.json()

    discord_id = str(discord_user.get("id"))
    username = discord_user.get("username", "Unknown")
    global_name = discord_user.get("global_name")
    discord_name = global_name or username

    db_exec("""
        INSERT INTO users (discord_id, discord_name)
        VALUES (%s, %s)
        ON CONFLICT (discord_id)
        DO UPDATE SET discord_name = EXCLUDED.discord_name;
    """, (discord_id, discord_name))

    row = db_exec("""
        SELECT discord_id, discord_name, mxb_name, steam_id, team_name, rider_number
        FROM users
        WHERE discord_id = %s
        LIMIT 1;
    """, (discord_id,), fetch="one")

    session["user"] = dict(row)
    return redirect(url_for("profile"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/profile", methods=["GET", "POST"])
def profile():
    user = current_user()
    if not user:
        return redirect(url_for("index"))

    if request.method == "POST":
        mxb_name = request.form.get("mxb_name", "").strip() or None
        steam_id = request.form.get("steam_id", "").strip() or None
        team_name = request.form.get("team_name", "").strip() or None
        rider_number = request.form.get("rider_number", "").strip() or None

        db_exec("""
            UPDATE users
            SET mxb_name = %s,
                steam_id = %s,
                team_name = %s,
                rider_number = %s
            WHERE discord_id = %s;
        """, (mxb_name, steam_id, team_name, rider_number, user["discord_id"]))

        row = db_exec("""
            SELECT discord_id, discord_name, mxb_name, steam_id, team_name, rider_number
            FROM users
            WHERE discord_id = %s
            LIMIT 1;
        """, (user["discord_id"],), fetch="one")

        session["user"] = dict(row)
        return redirect(url_for("profile"))

    row = db_exec("""
        SELECT discord_id, discord_name, mxb_name, steam_id, team_name, rider_number
        FROM users
        WHERE discord_id = %s
        LIMIT 1;
    """, (user["discord_id"],), fetch="one")

    return render_template("profile.html", profile=row)


@app.route("/api/stats")
def api_stats():
    return jsonify({
        "users": db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")["c"],
        "events": db_exec("SELECT COUNT(*)::int AS c FROM events;", fetch="one")["c"],
        "results": db_exec("SELECT COUNT(*)::int AS c FROM race_results;", fetch="one")["c"],
        "registrations": db_exec("SELECT COUNT(*)::int AS c FROM registrations;", fetch="one")["c"],
        "season": DEFAULT_SEASON,
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
