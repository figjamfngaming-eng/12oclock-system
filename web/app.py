import os
import hashlib
import urllib.parse
import requests

from flask import (
    Flask, render_template, request, redirect, url_for,
    jsonify, session, flash
)
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-now")

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/yourinvite")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:10000")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

oauth = OAuth(app)

if DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET:
    oauth.register(
        name="discord",
        client_id=DISCORD_CLIENT_ID,
        client_secret=DISCORD_CLIENT_SECRET,
        access_token_url="https://discord.com/api/oauth2/token",
        authorize_url="https://discord.com/api/oauth2/authorize",
        api_base_url="https://discord.com/api/",
        client_kwargs={
            "scope": "identify email"
        }
    )


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
        "logged_in_user": current_user(),
        "discord_connected": session.get("discord_connected", False),
        "steam_connected": session.get("steam_connected", False),
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

    return render_template("dashboard.html", user=user, link_data=link_data, recent_events=recent_events)


@app.route("/link-accounts")
def link_accounts():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    link_data = None
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM account_links
                WHERE site_user_id = %s
            """, (user["id"],))
            link_data = cur.fetchone()

    return render_template("link_accounts.html", link_data=link_data)


# =========================
# DISCORD OAUTH
# =========================
@app.route("/auth/discord")
def auth_discord():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    if not (DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET):
        flash("Discord OAuth is not configured yet.")
        return redirect(url_for("link_accounts"))

    redirect_uri = f"{BASE_URL}/auth/discord/callback"
    return oauth.discord.authorize_redirect(redirect_uri)


@app.route("/auth/discord/callback")
def auth_discord_callback():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    try:
        token = oauth.discord.authorize_access_token()
        resp = oauth.discord.get("users/@me", token=token)
        discord_user = resp.json()

        discord_id = discord_user.get("id")
        username = discord_user.get("username")
        global_name = discord_user.get("global_name")
        avatar = discord_user.get("avatar")
        email = discord_user.get("email")

        display_name = global_name or username or "Unknown"
        avatar_url = None
        if discord_id and avatar:
            avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar}.png"

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO account_links (
                        site_user_id, discord_id, discord_username, discord_avatar, discord_email,
                        verified_discord, link_status
                    )
                    VALUES (%s, %s, %s, %s, %s, TRUE, 'partial')
                    ON CONFLICT (site_user_id) DO UPDATE SET
                        discord_id = EXCLUDED.discord_id,
                        discord_username = EXCLUDED.discord_username,
                        discord_avatar = EXCLUDED.discord_avatar,
                        discord_email = EXCLUDED.discord_email,
                        verified_discord = TRUE
                """, (user["id"], discord_id, display_name, avatar_url, email))
                conn.commit()

        session["discord_connected"] = True
        flash("Discord account linked successfully.")
        return redirect(url_for("link_accounts"))

    except Exception as e:
        flash(f"Discord auth failed: {e}")
        return redirect(url_for("link_accounts"))


# =========================
# STEAM OPENID
# =========================
@app.route("/auth/steam")
def auth_steam():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    realm = BASE_URL
    return_to = f"{BASE_URL}/auth/steam/callback"

    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }

    url = "https://steamcommunity.com/openid/login?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/auth/steam/callback")
def auth_steam_callback():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    query = dict(request.args)

    params = dict(query)
    params["openid.mode"] = "check_authentication"

    response = requests.post("https://steamcommunity.com/openid/login", data=params, timeout=20)
    if "is_valid:true" not in response.text:
        flash("Steam verification failed.")
        return redirect(url_for("link_accounts"))

    claimed_id = query.get("openid.claimed_id", "")
    steam_id = claimed_id.split("/")[-1] if claimed_id else None

    if not steam_id:
        flash("Steam ID could not be read.")
        return redirect(url_for("link_accounts"))

    steam_profile_url = f"https://steamcommunity.com/profiles/{steam_id}"

    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO account_links (
                    site_user_id, steam_id, steam_name, steam_profile_url,
                    verified_steam, link_status
                )
                VALUES (%s, %s, %s, %s, TRUE, 'partial')
                ON CONFLICT (site_user_id) DO UPDATE SET
                    steam_id = EXCLUDED.steam_id,
                    steam_name = EXCLUDED.steam_name,
                    steam_profile_url = EXCLUDED.steam_profile_url,
                    verified_steam = TRUE
            """, (user["id"], steam_id, steam_id, steam_profile_url))
            conn.commit()

    session["steam_connected"] = True
    flash("Steam account linked successfully.")
    return redirect(url_for("link_accounts"))


@app.route("/finalize-link")
def finalize_link():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return redirect(url_for("login"))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT *
                FROM account_links
                WHERE site_user_id = %s
            """, (user["id"],))
            link_data = cur.fetchone()

            if not link_data:
                flash("No link data found yet.")
                return redirect(url_for("link_accounts"))

            if link_data.get("verified_discord") and link_data.get("verified_steam"):
                cur.execute("""
                    UPDATE account_links
                    SET link_status = 'approved'
                    WHERE site_user_id = %s
                """, (user["id"],))
                conn.commit()
                flash("Discord and Steam are fully linked.")
            else:
                flash("You still need both Discord and Steam connected.")

    return redirect(url_for("link_accounts"))


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
    events = []
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
