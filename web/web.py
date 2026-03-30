import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, jsonify, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "change-this-now")
app.permanent_session_lifetime = timedelta(days=30)

DATABASE_URL = os.getenv("DATABASE_URL")
BASE_URL = os.getenv("BASE_URL", "https://one2oclock-system.onrender.com")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/YOUR_LINK_HERE")

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", f"{BASE_URL}/auth/discord/callback")
STEAM_REALM = os.getenv("STEAM_REALM", f"{BASE_URL}/")
STEAM_RETURN_URL = os.getenv("STEAM_RETURN_URL", f"{BASE_URL}/auth/steam/callback")
STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"

DISCORD_AUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_BASE = "https://discord.com/api/v10"

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

@app.before_request
def make_session_persistent():
    session.permanent = True

def current_user():
    if "site_user_id" not in session:
        return None
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM site_users WHERE id = %s LIMIT 1", (session["site_user_id"],))
            return cur.fetchone()

@app.context_processor
def inject_globals():
    return {"discord_invite_url": DISCORD_INVITE_URL, "logged_in_user": current_user()}

def require_login():
    user = current_user()
    if not user:
        flash("Please log in first.")
        return None
    return user

def build_discord_oauth_url():
    state = secrets.token_urlsafe(32)
    session["discord_oauth_state"] = state
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify email",
        "state": state,
        "prompt": "consent",
    }
    return f"{DISCORD_AUTH_URL}?{urlencode(params)}"

def exchange_discord_code(code: str):
    response = requests.post(
        DISCORD_TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": DISCORD_REDIRECT_URI},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        auth=(DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()

def fetch_discord_user(access_token: str):
    response = requests.get(
        f"{DISCORD_API_BASE}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()

def extract_steam_id_from_claimed_id(claimed_id: str):
    if not claimed_id:
        return None
    match = re.match(r"^https?://steamcommunity\.com/openid/id/(\d+)$", claimed_id)
    return match.group(1) if match else None

def build_steam_openid_url():
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": STEAM_RETURN_URL,
        "openid.realm": STEAM_REALM,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return f"{STEAM_OPENID_ENDPOINT}?{urlencode(params)}"

@app.route("/")
def home():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM riders")
            riders_total = cur.fetchone()["total"]
            cur.execute("SELECT COUNT(*) AS total FROM events")
            events_total = cur.fetchone()["total"]
            cur.execute("SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage FROM events ORDER BY id DESC LIMIT 6")
            latest_events = cur.fetchall()
            cur.execute("SELECT * FROM live_state ORDER BY id DESC LIMIT 1")
            live_state = cur.fetchone()
            cur.execute("SELECT title, body, kind, created_at FROM announcements ORDER BY id DESC LIMIT 8")
            announcements = cur.fetchall()
            cur.execute("SELECT rider_name, series, class_name, points, feed_type, created_at FROM winner_feed ORDER BY id DESC LIMIT 8")
            winner_feed = cur.fetchall()
            cur.execute("""
                SELECT r.mxb_name, r.class_name, COALESCE(SUM(res.points), 0) AS pts
                FROM riders r LEFT JOIN results res ON r.id = res.rider_id
                GROUP BY r.id, r.mxb_name, r.class_name
                ORDER BY pts DESC, r.mxb_name ASC LIMIT 10
            """)
            top_leaderboard = cur.fetchall()
    return render_template("index.html", riders_total=riders_total, events_total=events_total, latest_events=latest_events, live_state=live_state, announcements=announcements, winner_feed=winner_feed, top_leaderboard=top_leaderboard)

@app.route("/rules")
def rules():
    return render_template("rules.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        if not username or not email or not password:
            flash("All fields are required.")
            return redirect(url_for("signup"))
        try:
            with db() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("INSERT INTO site_users (username, email, password_hash, verified_discord) VALUES (%s, %s, %s, FALSE) RETURNING id", (username, email, generate_password_hash(password)))
                    row = cur.fetchone()
                    conn.commit()
            session["site_user_id"] = row["id"]
            flash("Account created and logged in.")
            return redirect(url_for("dashboard"))
        except Exception:
            flash("Username or email already exists.")
            return redirect(url_for("signup"))
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM site_users WHERE email = %s LIMIT 1", (email,))
                user = cur.fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.")
            return redirect(url_for("login"))
        session["site_user_id"] = user["id"]
        flash("Logged in successfully.")
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.route("/login/discord")
def login_discord():
    return redirect(build_discord_oauth_url())

@app.route("/auth/discord/callback")
def auth_discord_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.get("discord_oauth_state")
    session.pop("discord_oauth_state", None)
    if not code or not state or state != expected_state:
        flash("Invalid Discord OAuth state.")
        return redirect(url_for("login"))
    data = exchange_discord_code(code)
    duser = fetch_discord_user(data["access_token"])
    discord_id = duser.get("id")
    email = duser.get("email") or f"{discord_id}@discord.local"
    display_name = duser.get("global_name") or duser.get("username") or f"discord_{discord_id}"
    avatar_url = None
    if duser.get("avatar") and discord_id:
        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{duser['avatar']}.png"
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM site_users WHERE discord_user_id = %s LIMIT 1", (discord_id,))
            existing = cur.fetchone()
            if existing:
                user_id = existing["id"]
                cur.execute("UPDATE site_users SET discord_username=%s, discord_avatar=%s, discord_email=%s, verified_discord=TRUE WHERE id=%s", (display_name, avatar_url, email, user_id))
            else:
                cur.execute("SELECT id FROM site_users WHERE email = %s LIMIT 1", (email,))
                email_match = cur.fetchone()
                if email_match:
                    user_id = email_match["id"]
                    cur.execute("UPDATE site_users SET discord_user_id=%s, discord_username=%s, discord_avatar=%s, discord_email=%s, verified_discord=TRUE WHERE id=%s", (discord_id, display_name, avatar_url, email, user_id))
                else:
                    cur.execute("""
                        INSERT INTO site_users (username, email, password_hash, discord_user_id, discord_username, discord_avatar, discord_email, verified_discord)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                        RETURNING id
                    """, (display_name, email, generate_password_hash(secrets.token_urlsafe(32)), discord_id, display_name, avatar_url, email))
                    user_id = cur.fetchone()["id"]
            conn.commit()
    session["site_user_id"] = user_id
    flash("Logged in with Discord successfully.")
    return redirect(url_for("dashboard"))

@app.route("/auth/steam")
def auth_steam():
    if not require_login():
        return redirect(url_for("login"))
    return redirect(build_steam_openid_url())

@app.route("/auth/steam/callback")
def auth_steam_callback():
    user = require_login()
    if not user:
        return redirect(url_for("login"))
    if request.args.get("openid.mode") != "id_res":
        flash("Steam login was cancelled or failed.")
        return redirect(url_for("link_accounts"))
    steam_id = extract_steam_id_from_claimed_id(request.args.get("openid.claimed_id", ""))
    if not steam_id:
        flash("Steam link failed.")
        return redirect(url_for("link_accounts"))
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO account_links (site_user_id, discord_id, discord_username, steam_id, link_status, approved, auto_approved)
                VALUES (%s, %s, %s, %s, 'pending', FALSE, FALSE)
                ON CONFLICT (site_user_id) DO UPDATE SET
                    discord_id = COALESCE(account_links.discord_id, EXCLUDED.discord_id),
                    discord_username = COALESCE(account_links.discord_username, EXCLUDED.discord_username),
                    steam_id = EXCLUDED.steam_id,
                    link_status = 'pending',
                    approved = FALSE,
                    auto_approved = FALSE
            """, (user["id"], user.get("discord_user_id"), user.get("discord_username"), steam_id))
            conn.commit()
    flash(f"Steam linked successfully: {steam_id}")
    return redirect(url_for("link_accounts"))

@app.route("/dashboard")
def dashboard():
    user = require_login()
    if not user:
        return redirect(url_for("login"))
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM account_links WHERE site_user_id = %s LIMIT 1", (user["id"],))
            link_data = cur.fetchone()
            discord_lookup_id = link_data["discord_id"] if link_data and link_data.get("discord_id") else user.get("discord_user_id")
            rider_data = None
            if discord_lookup_id:
                cur.execute("SELECT * FROM riders WHERE discord_id = %s OR discord_user_id = %s LIMIT 1", (discord_lookup_id, discord_lookup_id))
                rider_data = cur.fetchone()
            cur.execute("SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage, status, queue_open FROM events ORDER BY id DESC LIMIT 5")
            recent_events = cur.fetchall()
            cur.execute("SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage, status, queue_open FROM events WHERE status IN ('pending','queue_open','live','running') ORDER BY id DESC LIMIT 1")
            next_event = cur.fetchone()
            one_w_roles = []
            if rider_data:
                cur.execute("""
                    WITH class_leaders AS (
                        SELECT COALESCE(e.series, 'MXGP') AS series, e.class_name, r.id AS rider_id, r.mxb_name,
                               COALESCE(SUM(res.points), 0) AS pts,
                               ROW_NUMBER() OVER (PARTITION BY COALESCE(e.series, 'MXGP'), e.class_name
                               ORDER BY COALESCE(SUM(res.points), 0) DESC, r.mxb_name ASC) AS rn
                        FROM results res
                        JOIN riders r ON r.id = res.rider_id
                        JOIN events e ON e.id = res.event_id
                        GROUP BY COALESCE(e.series, 'MXGP'), e.class_name, r.id, r.mxb_name
                    )
                    SELECT series, class_name, pts
                    FROM class_leaders
                    WHERE rider_id = %s AND rn = 1
                """, (rider_data["id"],))
                one_w_roles = cur.fetchall()
    return render_template("dashboard.html", user=user, link_data=link_data, rider_data=rider_data, recent_events=recent_events, next_event=next_event, one_w_roles=one_w_roles)

@app.route("/link-accounts", methods=["GET", "POST"])
def link_accounts():
    user = require_login()
    if not user:
        return redirect(url_for("login"))

    default_discord_id = user.get("discord_user_id") or ""
    default_discord_username = user.get("discord_username") or ""

    if request.method == "POST":
        discord_id = request.form.get("discord_id", "").strip() or default_discord_id
        discord_username = request.form.get("discord_username", "").strip() or default_discord_username
        steam_id = request.form.get("steam_id", "").strip()
        steam_name = request.form.get("steam_name", "").strip()
        rider_name = request.form.get("rider_name", "").strip()
        rider_class = request.form.get("rider_class", "").strip().upper()
        rider_guid = request.form.get("rider_guid", "").strip()

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO account_links (site_user_id, discord_id, discord_username, steam_id, steam_name, link_status, approved, auto_approved)
                    VALUES (%s, %s, %s, %s, %s, 'pending', FALSE, FALSE)
                    ON CONFLICT (site_user_id) DO UPDATE SET
                        discord_id = EXCLUDED.discord_id,
                        discord_username = EXCLUDED.discord_username,
                        steam_id = COALESCE(NULLIF(EXCLUDED.steam_id, ''), account_links.steam_id),
                        steam_name = COALESCE(NULLIF(EXCLUDED.steam_name, ''), account_links.steam_name),
                        link_status = 'pending',
                        approved = FALSE,
                        auto_approved = FALSE
                """, (user["id"], discord_id, discord_username, steam_id, steam_name))

                if rider_name and rider_class:
                    cur.execute("""
                        INSERT INTO riders (discord_id, discord_user_id, discord_username, mxb_name, guid, steam_id, class_name, is_linked, approved, auto_approved)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, FALSE, FALSE)
                        ON CONFLICT (discord_id) DO UPDATE SET
                            discord_user_id = EXCLUDED.discord_user_id,
                            discord_username = EXCLUDED.discord_username,
                            mxb_name = EXCLUDED.mxb_name,
                            guid = EXCLUDED.guid,
                            steam_id = COALESCE(NULLIF(EXCLUDED.steam_id, ''), riders.steam_id),
                            class_name = EXCLUDED.class_name,
                            is_linked = FALSE,
                            approved = FALSE,
                            auto_approved = FALSE
                    """, (discord_id, discord_id, discord_username, rider_name, rider_guid, steam_id, rider_class))
                conn.commit()
        flash("Link details saved.")
        return redirect(url_for("link_accounts"))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM account_links WHERE site_user_id = %s LIMIT 1", (user["id"],))
            link_data = cur.fetchone()
            discord_lookup_id = link_data["discord_id"] if link_data and link_data.get("discord_id") else user.get("discord_user_id")
            rider_data = None
            if discord_lookup_id:
                cur.execute("SELECT * FROM riders WHERE discord_id = %s OR discord_user_id = %s LIMIT 1", (discord_lookup_id, discord_lookup_id))
                rider_data = cur.fetchone()

    return render_template("link_accounts.html", link_data=link_data, rider_data=rider_data, oauth_discord_id=default_discord_id, oauth_discord_username=default_discord_username)

@app.route("/events")
def events():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage, status, COALESCE(track_name, 'Unknown') AS track_name FROM events ORDER BY id DESC")
            events_list = cur.fetchall()
    return render_template("events.html", events=events_list)

@app.route("/leaderboard")
def leaderboard():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT r.mxb_name, r.class_name, COALESCE(SUM(res.points), 0) AS pts
                FROM riders r
                LEFT JOIN results res ON r.id = res.rider_id
                GROUP BY r.id, r.mxb_name, r.class_name
                ORDER BY pts DESC, r.mxb_name ASC
            """)
            rows = cur.fetchall()
    return render_template("leaderboard.html", rows=rows)

@app.route("/event/<int:event_id>")
def event_detail(event_id: int):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage, status, queue_open, COALESCE(track_name, 'Unknown') AS track_name, winner_name FROM events WHERE id = %s LIMIT 1", (event_id,))
            event = cur.fetchone()
            if not event:
                flash("Event not found.")
                return redirect(url_for("events"))
            cur.execute("SELECT r.mxb_name, res.position, res.points FROM results res JOIN riders r ON r.id = res.rider_id WHERE res.event_id = %s ORDER BY res.position ASC", (event_id,))
            results = cur.fetchall()
            cur.execute("SELECT rider_name, position, laps, best_lap, gap, status FROM live_results WHERE event_id = %s ORDER BY position ASC", (event_id,))
            live_results = cur.fetchall()
    return render_template("event.html", event=event, results=results, live_results=live_results)

@app.route("/live")
def live():
    return render_template("live.html")

@app.route("/api/live_results")
def api_live_results():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, name, COALESCE(series, 'MXGP') AS series, class_name, race_stage, status, COALESCE(track_name, 'Unknown') AS track_name FROM events WHERE status IN ('queue_open','live','running','pending') ORDER BY id DESC LIMIT 1")
            event = cur.fetchone()
            if not event:
                return jsonify({"event": None, "results": []})
            cur.execute("SELECT rider_name, position, laps, best_lap, gap, status, updated_at FROM live_results WHERE event_id = %s ORDER BY position ASC, rider_name ASC", (event["id"],))
            results = cur.fetchall()
    return jsonify({"event": event, "results": results})

@app.route("/google1417f573d8738bb4.html")
def google_verify():
    return send_from_directory(app.static_folder, "google1417f573d8738bb4.html")

@app.route("/robots.txt")
def robots():
    content = f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n"
    return content, 200, {"Content-Type": "text/plain"}

@app.route("/sitemap.xml")
def sitemap():
    routes = ["", "rules", "signup", "login", "dashboard", "link-accounts", "events", "leaderboard", "live"]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for route in routes:
        loc = f"{BASE_URL}/{route}" if route else f"{BASE_URL}/"
        xml.append("<url>")
        xml.append(f"<loc>{loc}</loc>")
        xml.append("</url>")
    xml.append("</urlset>")
    return app.response_class("\n".join(xml), mimetype="application/xml")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
