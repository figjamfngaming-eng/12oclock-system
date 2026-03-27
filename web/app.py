import os
from flask import Flask, render_template, request, redirect, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
DB = os.getenv("DATABASE_URL")

def db():
    return psycopg2.connect(DB, sslmode="require")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/events")
def events():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM events ORDER BY id DESC")
            return render_template("events.html", events=cur.fetchall())

@app.route("/leaderboard")
def lb():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
            SELECT mxb_name, SUM(points) pts
            FROM riders r JOIN results res ON r.id=res.rider_id
            GROUP BY mxb_name
            ORDER BY pts DESC
            """)
            return render_template("leaderboard.html", rows=cur.fetchall())

@app.route("/event/<int:id>")
def event(id):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM events WHERE id=%s",(id,))
            e = cur.fetchone()
            cur.execute("""
            SELECT mxb_name, position, points
            FROM results JOIN riders ON riders.id=results.rider_id
            WHERE event_id=%s
            """,(id,))
            r = cur.fetchall()
            return render_template("event.html", event=e, results=r)

@app.route("/director")
def director():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM events")
            return render_template("director.html", events=cur.fetchall())

@app.route("/director/action", methods=["POST"])
def act():
    id = request.form["event_id"]
    action = request.form["action"]
    with db() as conn:
        with conn.cursor() as cur:
            if action=="advance":
                cur.execute("""
                UPDATE events SET race_stage=
                CASE WHEN race_stage='qualifying' THEN 'heat1'
                     WHEN race_stage='heat1' THEN 'heat2'
                     ELSE 'final' END
                WHERE id=%s
                """,(id,))
            conn.commit()
    return redirect("/director")

@app.route("/api/live")
def live():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
            SELECT mxb_name, gate_order
            FROM gate_orders JOIN riders ON riders.id=gate_orders.rider_id
            ORDER BY gate_order
            """)
            return jsonify(cur.fetchall())

if __name__ == "__main__":
    app.run()
