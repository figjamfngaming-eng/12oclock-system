import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    jsonify,
    session,
    flash,
    send_from_directory,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "change-this-now")
app.permanent_session_lifetime = timedelta(days=30)

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/YOUR_LINK_HERE")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "https://one2oclock-system.onrender.com/auth/discord/callback",
)

STEAM_REALM = os.getenv("STEAM_REALM", "https://one2oclock-system.onrender.com/")
STEAM_RETURN_URL = os.getenv(
    "STEAM_RETURN_URL",
    "https://one2oclock-system.onrender.com/auth/steam/callback",
)
STEAM_OPENID_ENDPOINT = "https://steamcommunity.com/openid/login"

DISCORD_AUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_API_BASE = "https://discord.com/api/v10"

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")


# =========================
# DB
# =========================
def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# =========================
# SCHEMA BOOTSTRAP
# =========================
def ensure_schema():
    schema_path = os.path.join(os.path.dirname(__file__), "..", "database", "setup.sql")
    schema_path = os.path.abspath(schema_path)
    if not os.path.exists(schema_path):
        return
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
            conn.commit()


ensure_schema()


# =========================
# HELPERS
# =========================
def utcnow():
    return datetime.now(timezone.utc)


def current_user():
    if "site_user_id" not in session:
        return None

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    username,
                    email,
                    discord_user_id,
                    discord_username,
                    discord_avatar,
                    discord_email,
                    verified_discord,
                    created_at
                FROM site_users
                WHERE id = %s
                LIMIT 1
                """,
                (session["site_user_id"],),
            )
            return cur.fetchone()


@app.before_request
def make_session_persistent():
    session.permanent = True


@app.context_processor
def inject_globals():
    return {
        "discord_invite_url": DISCORD_INVITE_URL,
        "logged_in_user": current_user(),
    }


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
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
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


def discord_avatar_url(user: dict):
    avatar = user.get("avatar")
    user_id = user.get("id")
    if not avatar or not user_id:
        return None
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png"


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


def auto_approve_if_ready(site_user_id: int):
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    su.discord_user_id,
                    su.discord_username,
                    su.verified_discord,
                    al.discord_id,
                    al.discord_username AS link_discord_username,
                    al.steam_id,
                    al.steam_name,
                    al.relink_available_at,
                    r.id AS rider_id,
                    r.mxb_name,
                    r.class_name,
                    r.guid
                FROM site_users su
                LEFT JOIN account_links al
                    ON al.site_user_id = su.id
                LEFT JOIN riders r
                    ON r.discord_id = su.discord_user_id
                    OR r.discord_user_id = su.discord_user_id
                WHERE su.id = %s
                LIMIT 1
                """,
                (site_user_id,),
            )
            row = cur.fetchone()
            if not row:
                return False

            has_discord = bool(row["verified_discord"] and row["discord_user_id"])
            has_steam = bool(row["steam_id"])
            has_rider = bool(row["mxb_name"] and row["class_name"] and row["guid"])

            if has_discord and has_steam and has_rider:
                cur.execute(
                    """
                    UPDATE account_links
                    SET approved = TRUE,
                        auto_approved = TRUE,
                        link_status = 'approved'
                    WHERE site_user_id = %s
                    """,
                    (site_user_id,),
                )
                cur.execute(
                    """
                    UPDATE riders
                    SET approved = TRUE,
                        auto_approved = TRUE,
                        is_linked = TRUE,
                        discord_user_id = %s,
                        discord_username = %s,
                        steam_id = %s
                    WHERE discord_id = %s OR discord_user_id = %s
                    """,
                    (
                        row["discord_user_id"],
                        row["discord_username"] or row["link_discord_username"],
                        row["steam_id"],
                        row["discord_user_id"],
                        row["discord_user_id"],
                    ),
                )
                conn.commit()
                return True

    return False


def fetch_logged_in_link_bundle(user):
    link_data = None
    rider_data = None
    suspension = None

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM account_links WHERE site_user_id = %s LIMIT 1",
                (user["id"],),
            )
            link_data = cur.fetchone()

            discord_lookup_id = (link_data or {}).get("discord_id") or user.get("discord_user_id")
            if discord_lookup_id:
                cur.execute(
                    """
                    SELECT
                        id,
                        discord_id,
                        discord_user_id,
                        discord_username,
                        mxb_name,
                        guid,
                        steam_id,
                        class_name,
                        is_linked,
                        approved,
                        auto_approved,
                        created_at
                    FROM riders
                    WHERE discord_id = %s OR discord_user_id = %s
                    LIMIT 1
                    """,
                    (discord_lookup_id, discord_lookup_id),
                )
                rider_data = cur.fetchone()

            if rider_data:
                cur.execute(
                    """
                    SELECT *
                    FROM suspensions
                    WHERE is_active = TRUE
                      AND (
                            discord_user_id = %s
                         OR (steam_id IS NOT NULL AND steam_id = %s)
                         OR (rider_guid IS NOT NULL AND rider_guid = %s)
                      )
                      AND (ends_at IS NULL OR ends_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (
                        rider_data.get("discord_id") or rider_data.get("discord_user_id"),
                        rider_data.get("steam_id"),
                        rider_data.get("guid"),
                    ),
                )
                suspension = cur.fetchone()

    return link_data, rider_data, suspension


# =========================
# ROUTES
# =========================
@app.route("/")
def home():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT COUNT(*) AS total FROM riders WHERE approved = TRUE AND is_linked = TRUE")
            riders_total = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) AS total FROM events")
            events_total = cur.fetchone()["total"]

            cur.execute(
                """
                SELECT
                    id,
                    name,
                    track_name,
                    COALESCE(series, 'MXGP') AS series,
                    class_name,
                    race_stage,
                    status,
                    start_time
                FROM events
                ORDER BY start_time DESC NULLS LAST, id DESC
                LIMIT 6
                """
            )
            latest_events = cur.fetchall()

            cur.execute(
                """
                SELECT
                    id,
                    name,
                    track_name,
                    COALESCE(series, 'MXGP') AS series,
                    class_name,
                    race_stage,
                    status,
                    start_time
                FROM events
                WHERE status = 'live'
                ORDER BY start_time DESC NULLS LAST
                LIMIT 1
                """
            )
            live_event = cur.fetchone()

            cur.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.status,
                    e.start_time
                FROM events e
                WHERE e.status IN ('scheduled', 'queue_open')
                ORDER BY e.start_time ASC
                LIMIT 1
                """
            )
            next_event = cur.fetchone()

    return render_template(
        "index.html",
        riders_total=riders_total,
        events_total=events_total,
        latest_events=latest_events,
        live_event=live_event,
        next_event=next_event,
    )


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
                    cur.execute(
                        """
                        INSERT INTO site_users (username, email, password_hash, verified_discord)
                        VALUES (%s, %s, %s, FALSE)
                        RETURNING id
                        """,
                        (username, email, generate_password_hash(password)),
                    )
                    new_user = cur.fetchone()
                    conn.commit()

            session["site_user_id"] = new_user["id"]
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
                cur.execute(
                    "SELECT id, email, password_hash FROM site_users WHERE email = %s LIMIT 1",
                    (email,),
                )
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


@app.route("/login/discord")
def login_discord():
    if current_user():
        return redirect(url_for("dashboard"))

    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not DISCORD_REDIRECT_URI:
        flash("Discord OAuth is not configured yet.")
        return redirect(url_for("login"))

    return redirect(build_discord_oauth_url())


@app.route("/auth/discord/callback")
def auth_discord_callback():
    error = request.args.get("error")
    if error:
        flash(f"Discord login failed: {error}")
        return redirect(url_for("login"))

    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.get("discord_oauth_state")
    session.pop("discord_oauth_state", None)

    if not code or not state or state != expected_state:
        flash("Invalid Discord OAuth state.")
        return redirect(url_for("login"))

    try:
        token_data = exchange_discord_code(code)
        discord_user = fetch_discord_user(token_data["access_token"])
    except Exception as e:
        flash(f"Discord OAuth exchange failed: {e}")
        return redirect(url_for("login"))

    discord_id = discord_user.get("id")
    email = discord_user.get("email")
    display_name = discord_user.get("global_name") or discord_user.get("username") or f"discord_{discord_id}"
    avatar_url = discord_avatar_url(discord_user)

    if not discord_id:
        flash("Discord login failed.")
        return redirect(url_for("login"))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM site_users WHERE discord_user_id = %s LIMIT 1", (discord_id,))
            existing_discord = cur.fetchone()

            if existing_discord:
                user_id = existing_discord["id"]
                cur.execute(
                    """
                    UPDATE site_users
                    SET discord_username = %s,
                        discord_avatar = %s,
                        discord_email = %s,
                        verified_discord = TRUE
                    WHERE id = %s
                    """,
                    (display_name, avatar_url, email, user_id),
                )
            else:
                email_match = None
                if email:
                    cur.execute("SELECT id FROM site_users WHERE email = %s LIMIT 1", (email,))
                    email_match = cur.fetchone()

                if email_match:
                    user_id = email_match["id"]
                    cur.execute(
                        """
                        UPDATE site_users
                        SET discord_user_id = %s,
                            discord_username = %s,
                            discord_avatar = %s,
                            discord_email = %s,
                            verified_discord = TRUE
                        WHERE id = %s
                        """,
                        (discord_id, display_name, avatar_url, email, user_id),
                    )
                else:
                    fallback_email = email or f"{discord_id}@discord.local"
                    cur.execute(
                        """
                        INSERT INTO site_users (
                            username,
                            email,
                            password_hash,
                            discord_user_id,
                            discord_username,
                            discord_avatar,
                            discord_email,
                            verified_discord
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                        RETURNING id
                        """,
                        (
                            display_name,
                            fallback_email,
                            generate_password_hash(secrets.token_urlsafe(32)),
                            discord_id,
                            display_name,
                            avatar_url,
                            email,
                        ),
                    )
                    user_id = cur.fetchone()["id"]

            conn.commit()

    session["site_user_id"] = user_id
    auto_approve_if_ready(user_id)
    flash("Logged in with Discord successfully.")
    return redirect(url_for("dashboard"))


@app.route("/auth/steam")
def auth_steam():
    user = require_login()
    if not user:
        return redirect(url_for("login"))
    return redirect(build_steam_openid_url())


@app.route("/auth/steam/callback")
def auth_steam_callback():
    user = require_login()
    if not user:
        return redirect(url_for("login"))

    openid_params = dict(request.args)
    if openid_params.get("openid.mode") != "id_res":
        flash("Steam login was cancelled or failed.")
        return redirect(url_for("link_accounts"))

    steam_id = extract_steam_id_from_claimed_id(openid_params.get("openid.claimed_id", ""))
    if not steam_id:
        flash("Steam link failed.")
        return redirect(url_for("link_accounts"))

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM account_links WHERE site_user_id = %s LIMIT 1", (user["id"],))
            existing = cur.fetchone()

            if existing and existing.get("relink_available_at") and utcnow() < existing["relink_available_at"]:
                flash("You cannot relink this account yet. Wait until your relink date.")
                return redirect(url_for("link_accounts"))

            relink_available_at = utcnow() + timedelta(days=30)

            cur.execute(
                """
                INSERT INTO account_links (
                    site_user_id,
                    discord_id,
                    discord_username,
                    steam_id,
                    link_status,
                    approved,
                    auto_approved,
                    last_linked_at,
                    relink_available_at
                )
                VALUES (%s, %s, %s, %s, 'pending', FALSE, FALSE, NOW(), %s)
                ON CONFLICT (site_user_id) DO UPDATE SET
                    discord_id = COALESCE(account_links.discord_id, EXCLUDED.discord_id),
                    discord_username = COALESCE(account_links.discord_username, EXCLUDED.discord_username),
                    steam_id = EXCLUDED.steam_id,
                    link_status = 'pending',
                    approved = FALSE,
                    auto_approved = FALSE,
                    last_linked_at = NOW(),
                    relink_available_at = EXCLUDED.relink_available_at
                """,
                (
                    user["id"],
                    user.get("discord_user_id"),
                    user.get("discord_username"),
                    steam_id,
                    relink_available_at,
                ),
            )
            conn.commit()

    auto_approve_if_ready(user["id"])
    flash(f"Steam linked successfully: {steam_id}")
    return redirect(url_for("link_accounts"))


@app.route("/dashboard")
def dashboard():
    user = require_login()
    if not user:
        return redirect(url_for("login"))

    link_data, rider_data, suspension = fetch_logged_in_link_bundle(user)

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    name,
                    track_name,
                    COALESCE(series, 'MXGP') AS series,
                    class_name,
                    race_stage,
                    status,
                    queue_open,
                    start_time
                FROM events
                ORDER BY start_time DESC NULLS LAST, id DESC
                LIMIT 5
                """
            )
            recent_events = cur.fetchall()

            cur.execute(
                """
                SELECT
                    id,
                    name,
                    track_name,
                    COALESCE(series, 'MXGP') AS series,
                    class_name,
                    race_stage,
                    status,
                    queue_open,
                    start_time
                FROM events
                WHERE status IN ('scheduled', 'queue_open', 'live')
                ORDER BY start_time ASC
                LIMIT 1
                """
            )
            next_event = cur.fetchone()

            live_event = None
            cur.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.race_stage,
                    e.status,
                    e.queue_open,
                    e.start_time,
                    e.created_by_name,
                    COALESCE(eq.queue_total, 0) AS queue_total
                FROM events e
                LEFT JOIN (
                    SELECT event_id, COUNT(*) AS queue_total
                    FROM event_queue
                    GROUP BY event_id
                ) eq ON eq.event_id = e.id
                WHERE e.status IN ('queue_open', 'live', 'scheduled')
                ORDER BY
                    CASE WHEN e.status = 'live' THEN 0
                         WHEN e.status = 'queue_open' THEN 1
                         ELSE 2
                    END,
                    e.start_time ASC
                LIMIT 1
                """
            )
            live_event = cur.fetchone()

            recent_results = []
            cur.execute(
                """
                SELECT
                    res.event_id,
                    res.position,
                    res.points,
                    r.mxb_name,
                    e.name AS event_name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.created_at
                FROM results res
                JOIN riders r ON r.id = res.rider_id
                JOIN events e ON e.id = res.event_id
                ORDER BY e.created_at DESC, res.position ASC NULLS LAST
                LIMIT 15
                """
            )
            recent_results = cur.fetchall()

            rider_mod_uploads = []
            if rider_data:
                cur.execute(
                    """
                    SELECT
                        id,
                        original_filename,
                        status,
                        detected_roots,
                        notes,
                        approved_at,
                        rejected_at,
                        created_at
                    FROM mod_uploads
                    WHERE discord_user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    (rider_data.get("discord_id") or rider_data.get("discord_user_id"),),
                )
                rider_mod_uploads = cur.fetchall()

            one_w_roles = []
            if rider_data:
                cur.execute(
                    """
                    WITH class_leaders AS (
                        SELECT
                            COALESCE(e.series, 'MXGP') AS series,
                            e.class_name,
                            r.id AS rider_id,
                            r.mxb_name,
                            COALESCE(SUM(res.points), 0) AS pts,
                            ROW_NUMBER() OVER (
                                PARTITION BY COALESCE(e.series, 'MXGP'), e.class_name
                                ORDER BY COALESCE(SUM(res.points), 0) DESC, r.mxb_name ASC
                            ) AS rn
                        FROM results res
                        JOIN riders r ON r.id = res.rider_id
                        JOIN events e ON e.id = res.event_id
                        GROUP BY COALESCE(e.series, 'MXGP'), e.class_name, r.id, r.mxb_name
                    )
                    SELECT series, class_name, pts
                    FROM class_leaders
                    WHERE rider_id = %s
                      AND rn = 1
                    """,
                    (rider_data["id"],),
                )
                one_w_roles = cur.fetchall()

    return render_template(
        "dashboard.html",
        user=user,
        link_data=link_data,
        rider_data=rider_data,
        suspension=suspension,
        recent_events=recent_events,
        next_event=next_event,
        live_event=live_event,
        recent_results=recent_results,
        rider_mod_uploads=rider_mod_uploads,
        one_w_roles=one_w_roles,
    )


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

        if not discord_id or not discord_username:
            flash("Discord login is required.")
            return redirect(url_for("link_accounts"))

        if not steam_id or not rider_name or not rider_class or not rider_guid:
            flash("Steam ID, rider name, class, and GUID are required.")
            return redirect(url_for("link_accounts"))

        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM account_links WHERE site_user_id = %s LIMIT 1", (user["id"],))
                existing = cur.fetchone()

                if existing and existing.get("relink_available_at") and utcnow() < existing["relink_available_at"]:
                    flash("You cannot relink this account yet. Wait until your relink date.")
                    return redirect(url_for("link_accounts"))

                relink_available_at = utcnow() + timedelta(days=30)

                cur.execute(
                    """
                    INSERT INTO account_links (
                        site_user_id, discord_id, discord_username, steam_id, steam_name,
                        link_status, approved, auto_approved, last_linked_at, relink_available_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending', FALSE, FALSE, NOW(), %s)
                    ON CONFLICT (site_user_id) DO UPDATE SET
                        discord_id = EXCLUDED.discord_id,
                        discord_username = EXCLUDED.discord_username,
                        steam_id = EXCLUDED.steam_id,
                        steam_name = EXCLUDED.steam_name,
                        link_status = 'pending',
                        approved = FALSE,
                        auto_approved = FALSE,
                        last_linked_at = NOW(),
                        relink_available_at = EXCLUDED.relink_available_at
                    """,
                    (
                        user["id"],
                        discord_id,
                        discord_username,
                        steam_id,
                        steam_name,
                        relink_available_at,
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO riders (
                        discord_id,
                        discord_user_id,
                        discord_username,
                        mxb_name,
                        guid,
                        steam_id,
                        class_name,
                        is_linked,
                        approved,
                        auto_approved
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, FALSE, FALSE, FALSE)
                    ON CONFLICT (discord_id) DO UPDATE SET
                        discord_user_id = EXCLUDED.discord_user_id,
                        discord_username = EXCLUDED.discord_username,
                        mxb_name = EXCLUDED.mxb_name,
                        guid = EXCLUDED.guid,
                        steam_id = EXCLUDED.steam_id,
                        class_name = EXCLUDED.class_name,
                        is_linked = FALSE,
                        approved = FALSE,
                        auto_approved = FALSE
                    """,
                    (
                        discord_id,
                        discord_id,
                        discord_username,
                        rider_name,
                        rider_guid,
                        steam_id,
                        rider_class,
                    ),
                )

                conn.commit()

        auto_approve_if_ready(user["id"])
        flash("Link details saved.")
        return redirect(url_for("link_accounts"))

    link_data, rider_data, suspension = fetch_logged_in_link_bundle(user)

    return render_template(
        "link_accounts.html",
        link_data=link_data,
        rider_data=rider_data,
        suspension=suspension,
        oauth_discord_id=default_discord_id,
        oauth_discord_username=default_discord_username,
    )


@app.route("/events")
def events():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.race_stage,
                    e.created_at,
                    e.status,
                    e.queue_open,
                    e.start_time,
                    COALESCE(eq.queue_total, 0) AS queue_total
                FROM events e
                LEFT JOIN (
                    SELECT event_id, COUNT(*) AS queue_total
                    FROM event_queue
                    GROUP BY event_id
                ) eq ON eq.event_id = e.id
                ORDER BY e.start_time DESC NULLS LAST, e.id DESC
                """
            )
            events_list = cur.fetchall()

    return render_template("events.html", events=events_list)


@app.route("/leaderboard")
def leaderboard():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    r.id,
                    r.mxb_name,
                    r.class_name,
                    COALESCE(SUM(res.points), 0) AS total_points,
                    COALESCE(COUNT(DISTINCT res.event_id), 0) AS races_entered
                FROM riders r
                LEFT JOIN results res ON res.rider_id = r.id
                WHERE r.approved = TRUE
                GROUP BY r.id, r.mxb_name, r.class_name
                ORDER BY total_points DESC, r.mxb_name ASC
                """
            )
            leaderboard_rows = cur.fetchall()

    return render_template("leaderboard.html", leaderboard=leaderboard_rows)


@app.route("/director")
def director():
    return render_template("director.html")


@app.route("/live")
def live():
    return render_template("live.html")


# =========================
# APIs
# =========================
@app.route("/api/live")
def api_live():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    e.id AS event_id,
                    e.name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.status,
                    e.queue_open,
                    e.start_time,
                    r.mxb_name,
                    g.gate_order
                FROM events e
                LEFT JOIN gate_orders g ON g.event_id = e.id
                LEFT JOIN riders r ON r.id = g.rider_id
                WHERE e.status IN ('scheduled', 'queue_open', 'live')
                ORDER BY e.start_time ASC, g.gate_order ASC NULLS LAST
                """
            )
            rows = cur.fetchall()

    return jsonify(rows)


@app.route("/api/dashboard/live")
def api_dashboard_live():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.race_stage,
                    e.status,
                    e.queue_open,
                    e.start_time,
                    e.created_by_name,
                    COALESCE(eq.queue_total, 0) AS queue_total
                FROM events e
                LEFT JOIN (
                    SELECT event_id, COUNT(*) AS queue_total
                    FROM event_queue
                    GROUP BY event_id
                ) eq ON eq.event_id = e.id
                WHERE e.status IN ('scheduled', 'queue_open', 'live')
                ORDER BY
                    CASE WHEN e.status = 'live' THEN 0
                         WHEN e.status = 'queue_open' THEN 1
                         ELSE 2
                    END,
                    e.start_time ASC
                LIMIT 1
                """
            )
            event = cur.fetchone()

            cur.execute("SELECT COUNT(*) AS total FROM riders WHERE approved = TRUE AND is_linked = TRUE")
            riders_total = cur.fetchone()["total"]

            cur.execute("SELECT COUNT(*) AS total FROM events")
            events_total = cur.fetchone()["total"]

            cur.execute(
                """
                SELECT
                    res.event_id,
                    res.position,
                    res.points,
                    r.mxb_name,
                    e.name AS event_name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.created_at
                FROM results res
                JOIN riders r ON r.id = res.rider_id
                JOIN events e ON e.id = res.event_id
                ORDER BY e.created_at DESC, res.position ASC NULLS LAST
                LIMIT 10
                """
            )
            recent_results = cur.fetchall()

    return jsonify({
        "event": event,
        "riders_total": riders_total,
        "events_total": events_total,
        "recent_results": recent_results,
    })


@app.route("/api/events/upcoming")
def api_events_upcoming():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    e.id,
                    e.name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.status,
                    e.queue_open,
                    e.start_time,
                    COALESCE(eq.queue_total, 0) AS queue_total
                FROM events e
                LEFT JOIN (
                    SELECT event_id, COUNT(*) AS queue_total
                    FROM event_queue
                    GROUP BY event_id
                ) eq ON eq.event_id = e.id
                WHERE e.status IN ('scheduled', 'queue_open', 'live')
                ORDER BY e.start_time ASC
                LIMIT 20
                """
            )
            rows = cur.fetchall()

    return jsonify(rows)


@app.route("/api/results/recent")
def api_results_recent():
    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    res.event_id,
                    res.position,
                    res.points,
                    r.id AS rider_id,
                    r.mxb_name,
                    e.name AS event_name,
                    e.track_name,
                    COALESCE(e.series, 'MXGP') AS series,
                    e.class_name,
                    e.created_at
                FROM results res
                JOIN riders r ON r.id = res.rider_id
                JOIN events e ON e.id = res.event_id
                ORDER BY e.created_at DESC, res.position ASC NULLS LAST
                LIMIT 50
                """
            )
            rows = cur.fetchall()

    return jsonify(rows)


@app.route("/api/me/mods")
def api_me_mods():
    user = require_login()
    if not user:
        return jsonify({"error": "not_logged_in"}), 401

    link_data, rider_data, _ = fetch_logged_in_link_bundle(user)
    if not rider_data:
        return jsonify([])

    discord_id = rider_data.get("discord_id") or rider_data.get("discord_user_id")

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    id,
                    original_filename,
                    status,
                    detected_roots,
                    notes,
                    approved_at,
                    rejected_at,
                    created_at
                FROM mod_uploads
                WHERE discord_user_id = %s
                ORDER BY created_at DESC
                LIMIT 25
                """,
                (discord_id,),
            )
            rows = cur.fetchall()

    return jsonify(rows)


@app.route("/api/me/link-status")
def api_me_link_status():
    user = require_login()
    if not user:
        return jsonify({"error": "not_logged_in"}), 401

    link_data, rider_data, suspension = fetch_logged_in_link_bundle(user)

    return jsonify({
        "user": user,
        "link_data": link_data,
        "rider_data": rider_data,
        "suspension": suspension,
    })


@app.route("/status")
def status():
    return jsonify({"status": "ok"})


@app.route("/google1417f573d8738bb4.html")
def google_verify():
    return send_from_directory(app.static_folder, "google1417f573d8738bb4.html")


@app.route("/robots.txt")
def robots():
    content = "User-agent: *\nAllow: /\nSitemap: https://one2oclock-system.onrender.com/sitemap.xml\n"
    return content, 200, {"Content-Type": "text/plain"}


@app.route("/sitemap.xml")
def sitemap():
    base_url = "https://one2oclock-system.onrender.com"
    routes = [
        "",
        "rules",
        "signup",
        "login",
        "dashboard",
        "link-accounts",
        "events",
        "leaderboard",
        "director",
        "live",
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for route in routes:
        xml.append(f"<url><loc>{base_url}/{route}</loc></url>")
    xml.append("</urlset>")
    return "\n".join(xml), 200, {"Content-Type": "application/xml"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
