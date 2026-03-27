import os
from flask import Flask, render_template, request, redirect, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")


def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


@app.route("/")
def home():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM riders")
            riders_total = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) AS total FROM events")
            events_total = cur.fetchone()["total"]

            cur.execute("""
                SELECT id, name, class_name, race_stage
                FROM events
                ORDER BY id DESC
                LIMIT 5
            """)
            latest_events = cur.fetchall()

    return render_template(
        "index.html",
        riders_total=riders_total,
        events_total=events_total,
        latest_events=latest_events
    )


@app.route("/events")
def events():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, class_name, race_stage
                FROM events
                ORDER BY id DESC
            """)
            events = cur.fetchall()

    return render_template("events.html", events=events)


@app.route("/leaderboard")
def leaderboard():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    r.id,
                    r.mxb_name,
                    r.class_name,
                    COALESCE(SUM(res.points), 0) AS pts
                FROM riders r
                LEFT JOIN results res ON r.id = res.rider_id
                GROUP BY r.id, r.mxb_name, r.class_name
                ORDER BY pts DESC, r.mxb_name ASC
            """)
            rows = cur.fetchall()

    return render_template("leaderboard.html", rows=rows)


@app.route("/event/<int:event_id>")
def event(event_id: int):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, class_name, race_stage
                FROM events
                WHERE id = %s
            """, (event_id,))
            event = cur.fetchone()

            if not event:
                return "Event not found", 404

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
                    g.gate_order
                FROM gate_orders g
                JOIN riders r ON r.id = g.rider_id
                WHERE g.event_id = %s
                ORDER BY g.gate_order ASC
            """, (event_id,))
            gates = cur.fetchall()

    return render_template("event.html", event=event, results=results, gates=gates)


@app.route("/director")
def director():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, class_name, race_stage
                FROM events
                ORDER BY id DESC
                LIMIT 20
            """)
            events = cur.fetchall()

    return render_template("director.html", events=events)


@app.route("/director/action", methods=["POST"])
def director_action():
    event_id = request.form.get("event_id")
    action = request.form.get("action")

    if not event_id or not action:
        return redirect("/director")

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if action == "advance":
                cur.execute("SELECT race_stage FROM events WHERE id = %s", (event_id,))
                row = cur.fetchone()

                if row:
                    current_stage = row["race_stage"]

                    if current_stage == "qualifying":
                        next_stage = "heat1"
                    elif current_stage == "heat1":
                        next_stage = "heat2"
                    elif current_stage == "heat2":
                        next_stage = "final"
                    else:
                        next_stage = "final"

                    cur.execute("""
                        UPDATE events
                        SET race_stage = %s
                        WHERE id = %s
                    """, (next_stage, event_id))

            elif action == "delete_results":
                cur.execute("DELETE FROM results WHERE event_id = %s", (event_id,))

            elif action == "delete_gates":
                cur.execute("DELETE FROM gate_orders WHERE event_id = %s", (event_id,))

            conn.commit()

    return redirect("/director")


@app.route("/live")
def live():
    return render_template("live.html")


@app.route("/api/live")
def api_live():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    g.event_id,
                    r.mxb_name,
                    g.gate_order
                FROM gate_orders g
                JOIN riders r ON r.id = g.rider_id
                ORDER BY g.event_id DESC, g.gate_order ASC
            """)
            rows = cur.fetchall()

    return jsonify(rows)


@app.route("/status")
def status():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
