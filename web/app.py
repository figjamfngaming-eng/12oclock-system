import os
from flask import Flask, render_template, abort
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

@app.route("/")
def index():
    rider_count = 0
    event_count = 0

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM riders")
            rider_count = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM events")
            event_count = cur.fetchone()[0]

    return render_template(
        "index.html",
        rider_count=rider_count,
        event_count=event_count
    )

@app.route("/riders")
def riders():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    discord_id,
                    discord_name,
                    mxb_name,
                    steam_id,
                    guid_status,
                    class_name,
                    team_name,
                    rider_number,
                    approved
                FROM riders
                ORDER BY COALESCE(mxb_name, discord_name) ASC
            """)
            rows = cur.fetchall()

    return render_template("riders.html", riders=rows)

@app.route("/events")
def events():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    id,
                    name,
                    track,
                    class_name,
                    season,
                    round_number,
                    status,
                    event_type,
                    requires_guid,
                    created_at
                FROM events
                ORDER BY id DESC
            """)
            rows = cur.fetchall()

    return render_template("events.html", events=rows)

@app.route("/events/<int:event_id>")
def event_detail(event_id: int):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    id,
                    name,
                    track,
                    class_name,
                    season,
                    round_number,
                    status,
                    event_type,
                    requires_guid,
                    created_at
                FROM events
                WHERE id = %s
            """, (event_id,))
            event = cur.fetchone()

            if not event:
                abort(404)

            cur.execute("""
                SELECT
                    r.mxb_name,
                    r.discord_name,
                    r.class_name,
                    r.team_name,
                    r.rider_number,
                    r.guid_status,
                    reg.created_at
                FROM registrations reg
                JOIN riders r ON r.id = reg.rider_id
                WHERE reg.event_id = %s
                ORDER BY COALESCE(r.mxb_name, r.discord_name) ASC
            """, (event_id,))
            registrations = cur.fetchall()

    return render_template(
        "event_detail.html",
        event=event,
        registrations=registrations
    )

@app.route("/profile/<discord_id>")
def profile(discord_id: str):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    discord_id,
                    discord_name,
                    mxb_name,
                    steam_id,
                    guid,
                    guid_status,
                    class_name,
                    team_name,
                    rider_number,
                    approved,
                    created_at
                FROM riders
                WHERE discord_id = %s
            """, (discord_id,))
            rider = cur.fetchone()

            if not rider:
                abort(404)

    return render_template("profile.html", rider=rider)

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
