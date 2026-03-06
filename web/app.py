import os
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
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
    

    db_exec(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            discord_name TEXT,
            mxb_name TEXT,
            steam_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    db_exec(
        """
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
        """
    )

    db_exec(
        """
        CREATE TABLE IF NOT EXISTS race_results (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            season TEXT DEFAULT 'S1',
            round_number INTEGER DEFAULT 1,
            class_name TEXT DEFAULT '450',
            discord_id TEXT,
            rider_name TEXT,
            position INTEGER,
            points INTEGER DEFAULT 0,
            penalty_points INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_event ON race_results(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_class ON race_results(class_name);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_season_round ON race_results(season, round_number);")


@app.before_request
def ensure_db():
    if DATABASE_URL:
        init_db()


def current_user():
    return session.get("user")


def oauth_ready() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)


@app.context_processor
def inject_globals():
    return {
        "invite_url": DISCORD_INVITE_URL,
        "user": current_user(),
        "oauth_ready": oauth_ready(),
        "default_season": DEFAULT_SEASON,
    }


@app.route("/")
def index():
    recent_events = db_exec(
        """
        SELECT id, name, track, class_name, season, round_number, start_time, status
        FROM events
        ORDER BY id DESC
        LIMIT 6;
        """,
        fetch="all",
    )

    standings_450 = db_exec(
        """
        SELECT
            rider_name,
            SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = '450'
          AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC
        LIMIT 5;
        """,
        (DEFAULT_SEASON,),
        fetch="all",
    )

    standings_250 = db_exec(
        """
        SELECT
            rider_name,
            SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = '250'
          AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC
        LIMIT 5;
        """,
        (DEFAULT_SEASON,),
        fetch="all",
    )

    count_row = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")
    rider_count = count_row["c"] if count_row else 0

    return render_template(
        "index.html",
        recent_events=recent_events,
        standings_450=standings_450,
        standings_250=standings_250,
        rider_count=rider_count,
    )


@app.route("/events")
def events_page():
    season = request.args.get("season", DEFAULT_SEASON)
    class_name = request.args.get("class", "").strip()
    status = request.args.get("status", "").strip()

    sql = """
    SELECT id, name, track, class_name, season, round_number, start_time, status
    FROM events
    WHERE season = %s
    """
    params = [season]

    if class_name:
        sql += " AND class_name = %s"
        params.append(class_name)

    if status:
        sql += " AND status = %s"
        params.append(status)

    sql += " ORDER BY round_number ASC, id ASC;"

    rows = db_exec(sql, tuple(params), fetch="all")
    return render_template(
        "events.html",
        events=rows,
        season=season,
        class_name=class_name,
        status=status,
    )


@app.route("/event/<int:event_id>")
def event_page(event_id: int):
    event = db_exec(
        """
        SELECT *
        FROM events
        WHERE id = %s
        LIMIT 1;
        """,
        (event_id,),
        fetch="one",
    )

    if not event:
        return "Event not found", 404

    results = db_exec(
        """
        SELECT rider_name, position, points, penalty_points, notes
        FROM race_results
        WHERE event_id = %s
        ORDER BY position ASC NULLS LAST, rider_name ASC;
        """,
        (event_id,),
        fetch="all",
    )

    return render_template("event.html", event=event, results=results)


@app.route("/standings")
def standings_page():
    season = request.args.get("season", DEFAULT_SEASON)
    class_name = request.args.get("class", "450")

    rows = db_exec(
        """
        SELECT
            rider_name,
            SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = %s
          AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC;
        """,
        (class_name, season),
        fetch="all",
    )

    return render_template(
        "standings.html",
        rows=rows,
        season=season,
        class_name=class_name,
    )


@app.route("/riders")
def riders_page():
    rows = db_exec(
        """
        SELECT discord_name, mxb_name, steam_id, created_at
        FROM users
        ORDER BY created_at DESC, discord_name ASC;
        """,
        fetch="all",
    )
    return render_template("riders.html", riders=rows)


@app.route("/schedule")
def schedule_page():
    season = request.args.get("season", DEFAULT_SEASON)
    rows = db_exec(
        """
        SELECT id, name, track, class_name, season, round_number, start_time, status
        FROM events
        WHERE season = %s
        ORDER BY round_number ASC, id ASC;
        """,
        (season,),
        fetch="all",
    )
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


@app.route("/upload", methods=["GET", "POST"])
def upload_page():
    message = None
    user = current_user()

    if request.method == "POST":
        if not user:
            return redirect(url_for("index"))

        event_id = request.form.get("event_id", type=int)
        rider_name = request.form.get("rider_name", "").strip()
        position = request.form.get("position", type=int)
        points = request.form.get("points", type=int)
        notes = request.form.get("notes", "").strip() or None

        event = db_exec(
            "SELECT * FROM events WHERE id = %s LIMIT 1;",
            (event_id,),
            fetch="one",
        )

        if not event:
            message = "Event not found."
        elif not rider_name:
            message = "Rider name is required."
        else:
            db_exec(
                """
                INSERT INTO race_results (event_id, season, round_number, class_name, discord_id, rider_name, position, points, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    event["id"],
                    event["season"],
                    event["round_number"],
                    event["class_name"],
                    user["discord_id"],
                    rider_name,
                    position,
                    points or 0,
                    notes,
                ),
            )
            message = "Result uploaded successfully."

    events = db_exec(
        """
        SELECT id, name, class_name, season, round_number, status
        FROM events
        ORDER BY id DESC
        LIMIT 20;
        """,
        fetch="all",
    )

    return render_template("upload.html", events=events, message=message)


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
    token_data = token_resp.json()
    access_token = token_data["access_token"]

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

    db_exec(
        """
        INSERT INTO users (discord_id, discord_name)
        VALUES (%s, %s)
        ON CONFLICT (discord_id)
        DO UPDATE SET discord_name = EXCLUDED.discord_name;
        """,
        (discord_id, discord_name),
    )

    row = db_exec(
        """
        SELECT discord_id, discord_name, mxb_name, steam_id
        FROM users
        WHERE discord_id = %s
        LIMIT 1;
        """,
        (discord_id,),
        fetch="one",
    )

    session["user"] = {
        "discord_id": row["discord_id"],
        "discord_name": row["discord_name"],
        "mxb_name": row.get("mxb_name"),
        "steam_id": row.get("steam_id"),
    }

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

        db_exec(
            """
            UPDATE users
            SET mxb_name = %s,
                steam_id = %s
            WHERE discord_id = %s;
            """,
            (mxb_name, steam_id, user["discord_id"]),
        )

        row = db_exec(
            """
            SELECT discord_id, discord_name, mxb_name, steam_id
            FROM users
            WHERE discord_id = %s
            LIMIT 1;
            """,
            (user["discord_id"],),
            fetch="one",
        )

        session["user"] = {
            "discord_id": row["discord_id"],
            "discord_name": row["discord_name"],
            "mxb_name": row.get("mxb_name"),
            "steam_id": row.get("steam_id"),
        }

        return redirect(url_for("profile"))

    row = db_exec(
        """
        SELECT discord_id, discord_name, mxb_name, steam_id
        FROM users
        WHERE discord_id = %s
        LIMIT 1;
        """,
        (user["discord_id"],),
        fetch="one",
    )

    return render_template("profile.html", profile=row)


@app.route("/api/stats")
def api_stats():
    user_count = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")["c"]
    event_count = db_exec("SELECT COUNT(*)::int AS c FROM events;", fetch="one")["c"]
    result_count = db_exec("SELECT COUNT(*)::int AS c FROM race_results;", fetch="one")["c"]

    return jsonify(
        {
            "users": user_count,
            "events": event_count,
            "results": result_count,
            "season": DEFAULT_SEASON,
        }
    )


@app.route("/api/discord_stats")
def api_discord_stats():
    user_count = db_exec("SELECT COUNT(*)::int AS c FROM users;", fetch="one")["c"]
    return jsonify({"registered_racers": user_count})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
