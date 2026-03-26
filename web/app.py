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
def home():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS count FROM riders")
            rider_count = cur.fetchone()["count"]

            cur.execute("SELECT COUNT(*) AS count FROM events")
            event_count = cur.fetchone()["count"]

            cur.execute("""
                SELECT id, name, track, event_type, status
                FROM events
                ORDER BY id DESC
                LIMIT 5
            """)
            recent_events = cur.fetchall()

    return render_template(
        "index.html",
        rider_count=rider_count,
        event_count=event_count,
        recent_events=recent_events
    )


@app.route("/events")
def events_page():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, track, class_name, season, round_number,
                       status, event_type, requires_guid, race_password,
                       started_at, ended_at, created_at
                FROM events
                ORDER BY id DESC
            """)
            events = cur.fetchall()

    return render_template("events.html", events=events)


@app.route("/riders")
def riders_page():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT mxb_name, steam_id, guid, guid_status, team_name,
                       rider_number, class_name, approved, suspended, suspension_reason
                FROM riders
                ORDER BY mxb_name ASC NULLS LAST
            """)
            riders = cur.fetchall()

    return render_template("riders.html", riders=riders)


@app.route("/leaderboard")
def leaderboard_page():
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    r.mxb_name,
                    r.class_name,
                    COALESCE(SUM(res.points), 0) AS total_points
                FROM riders r
                LEFT JOIN results res ON r.id = res.rider_id
                GROUP BY r.id, r.mxb_name, r.class_name
                ORDER BY total_points DESC, r.mxb_name ASC
                LIMIT 100
            """)
            rows = cur.fetchall()

    return render_template("leaderboard.html", rows=rows)


@app.route("/event/<int:event_id>")
def event_results_page(event_id: int):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM events
                WHERE id = %s
            """, (event_id,))
            event = cur.fetchone()

            if not event:
                abort(404)

            cur.execute("""
                SELECT
                    r.mxb_name,
                    res.position,
                    res.points
                FROM results res
                JOIN riders r ON r.id = res.rider_id
                WHERE res.event_id = %s
                ORDER BY res.position ASC
            """, (event_id,))
            results = cur.fetchall()

            cur.execute("""
                SELECT
                    r.mxb_name,
                    r.class_name
                FROM registrations reg
                JOIN riders r ON r.id = reg.rider_id
                WHERE reg.event_id = %s
                ORDER BY r.mxb_name ASC
            """, (event_id,))
            registrations = cur.fetchall()

    return render_template(
        "event_results.html",
        event=event,
        results=results,
        registrations=registrations
    )


@app.route("/race/<int:event_id>")
def race_info_page(event_id: int):
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, track, status, event_type, requires_guid,
                       race_password, started_at, ended_at, class_name, season, round_number
                FROM events
                WHERE id = %s
            """, (event_id,))
            event = cur.fetchone()

            if not event:
                abort(404)

    return render_template("race_info.html", event=event)


@app.route("/status")
def status():
    return {"status": "ok", "system": "12 O'Clock Boyz website live"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
