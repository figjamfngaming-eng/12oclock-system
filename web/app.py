import os
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-now")

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/yourinvite")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")


def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def current_user():
    if "site_user_id" not in session:
        return None

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, username, email, created_at
                FROM site_users
                WHERE id = %s
            """, (session["site_user_id"],))
            return cur.fetchone()


@app.context_processor
def inject_globals():
    return {
        "discord_invite_url": DISCORD_INVITE_URL,
        "logged_in_user": current_user()
    }


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
                LIMIT 6
            """)
            latest_events = cur.fetchall()

    return render_template(
        "index.html",
        riders_total=riders_total,
        events_total=events_total,
        latest_events=latest_events
    )


@app.route("/rules")
def rules():
    return render_template("rules.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not username or not email or not password:
            flash("All fields are required.")
            return redirect(url_for("signup"))

        try:
            password_hash = generate_password_hash(password)

            with db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO site_users (username, email, password_hash)
                        VALUES (%s, %s, %s)
                    """, (username, email, password_hash))
                    conn.commit()

            flash("Account created successfully. Please log in.")
            return redirect(url_for("login"))
        except Exception:
            flash("Username or email already exists.")
            return redirect(url_for("signup"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, username, email, password_hash
                    FROM site_users
                    WHERE email = %s
                """, (email,))
                user = cur.fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.")
            return redirect(url_for("login"))

        session["site_user_id"] = user["id"]
        flash("Logged in successfully.")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("home"))


@app.route("/dashboard")
def dashboard():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    link_data = None
    recent_events = []

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM account_links
                WHERE site_user_id = %s
            """, (user["id"],))
            link_data = cur.fetchone()

            cur.execute("""
                SELECT id, name, class_name, race_stage
                FROM events
                ORDER BY id DESC
                LIMIT 5
            """)
            recent_events = cur.fetchall()

    return render_template(
        "dashboard.html",
        user=user,
        link_data=link_data,
        recent_events=recent_events
    )


@app.route("/link-accounts", methods=["GET", "POST"])
def link_accounts():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    if request.method == "POST":
        discord_id = request.form.get("discord_id", "").strip()
        discord_username = request.form.get("discord_username", "").strip()
        steam_id = request.form.get("steam_id", "").strip()
        steam_name = request.form.get("steam_name", "").strip()

        if not discord_id or not discord_username or not steam_id or not steam_name:
            flash("All fields are required.")
            return redirect(url_for("link_accounts"))

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO account_links (
                        site_user_id, discord_id, discord_username, steam_id, steam_name, link_status, approved
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending', FALSE)
                    ON CONFLICT (site_user_id) DO UPDATE SET
                        discord_id = EXCLUDED.discord_id,
                        discord_username = EXCLUDED.discord_username,
                        steam_id = EXCLUDED.steam_id,
                        steam_name = EXCLUDED.steam_name,
                        link_status = 'pending',
                        approved = FALSE
                """, (user["id"], discord_id, discord_username, steam_id, steam_name))

                cur.execute("""
                    UPDATE riders
                    SET discord_user_id = %s,
                        discord_username = %s,
                        steam_id = %s,
                        is_linked = FALSE
                    WHERE discord_id = %s
                """, (discord_id, discord_username, steam_id, discord_id))

                conn.commit()

        flash("Link request saved. Waiting for admin approval.")
        return redirect(url_for("link_accounts"))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM account_links
                WHERE site_user_id = %s
            """, (user["id"],))
            link_data = cur.fetchone()

    return render_template("link_accounts.html", link_data=link_data)


@app.route("/events")
def events():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, class_name, race_stage, created_at
                FROM events
                ORDER BY id DESC
            """)
            events = cur.fetchall()

    return render_template("events.html", events=events)


@app.route("/event/<int:event_id>")
def event(event_id: int):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, name, class_name, race_stage, created_at
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
        return redirect(url_for("director"))

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

    return redirect(url_for("director"))


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
