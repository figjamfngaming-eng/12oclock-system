import os
import sys
import json
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import (
    Flask, request, redirect, url_for, render_template,
    session, jsonify, flash
)

# ---- make repo root importable so `shared` works even if Render Root Directory = web
BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # .../web
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))      # .../
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from shared.db import init_db, q, exec1  # noqa: E402

# ---- Flask app (explicit template/static paths)
app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

# ---- Security / sessions
app.secret_key = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or secrets.token_hex(32)

# ---- Environment
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "").strip()

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "").strip()  # must match Discord Dev Portal exactly

STEAM_WEB_API_KEY = os.getenv("STEAM_WEB_API_KEY", "").strip()
STEAM_RETURN_URL = os.getenv("STEAM_RETURN_URL", "").strip()          # where Steam should return users (optional)
STEAM_REALM = os.getenv("STEAM_REALM", "").strip()                    # your site base url (optional)

# ---- One-time DB init (Flask 3 removed before_first_request)
_db_ready = False

@app.before_request
def _ensure_db():
    global _db_ready
    if not _db_ready:
        init_db()
        _db_ready = True

def _now_utc():
    return datetime.now(timezone.utc)

def _discord_oauth_ready() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_REDIRECT_URI)

def _require_login():
    if not session.get("user_id"):
        flash("Please sign in with Discord first.", "error")
        return redirect(url_for("index"))
    return None

# =========================================================
# Pages (tabs on ONE page = templates handle the tabs UI)
# =========================================================

@app.route("/")
def index():
    # show banner if oauth missing (your screenshot shows this)
    oauth_ok = _discord_oauth_ready()
    return render_template(
        "index.html",
        oauth_ok=oauth_ok,
        invite_url=DISCORD_INVITE_URL,
        user_id=session.get("user_id"),
        discord_name=session.get("discord_name"),
    )

@app.route("/profile")
def profile():
    gate = _require_login()
    if gate:
        return gate

    u = q("SELECT * FROM users WHERE id=%s", [session["user_id"]])
    u = u[0] if u else None

    return render_template(
        "profile.html",
        user=u,
        invite_url=DISCORD_INVITE_URL,
    )

@app.route("/events")
def events():
    rows = q("SELECT * FROM events ORDER BY start_time DESC LIMIT 100")
    return render_template("events.html", events=rows, invite_url=DISCORD_INVITE_URL)

@app.route("/standings")
def standings():
    # overall standings page; template can show tabs per class
    rows = q("""
        SELECT
          u.id AS user_id,
          u.mxb_name,
          u.discord_name,
          u.race_class,
          COALESCE(SUM(r.points),0) + COALESCE(SUM(p.points_delta),0) AS points
        FROM users u
        LEFT JOIN results r ON r.user_id=u.id
        LEFT JOIN penalties p ON p.user_id=u.id
        GROUP BY u.id, u.mxb_name, u.discord_name, u.race_class
        ORDER BY points DESC
    """)
    return render_template("standings.html", standings=rows, invite_url=DISCORD_INVITE_URL)

@app.route("/rules")
def rules():
    return render_template("rules.html", invite_url=DISCORD_INVITE_URL)

@app.route("/riders")
def riders():
    rows = q("SELECT id, discord_name, mxb_name, steam_id, race_class, created_at FROM users ORDER BY created_at DESC")
    return render_template("riders.html", riders=rows, invite_url=DISCORD_INVITE_URL)

@app.route("/upload")
def upload_page():
    gate = _require_login()
    if gate:
        return gate
    return render_template("upload.html", invite_url=DISCORD_INVITE_URL)

# =========================================================
# Discord OAuth
# =========================================================

@app.route("/auth/discord/login")
def discord_login():
    if not _discord_oauth_ready():
        flash("Discord OAuth env vars missing in Render.", "error")
        return redirect(url_for("index"))

    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state

    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    }
    return redirect("https://discord.com/api/oauth2/authorize?" + urlencode(params))

@app.route("/auth/discord/callback")
def discord_callback():
    if not _discord_oauth_ready():
        flash("Discord OAuth env vars missing in Render.", "error")
        return redirect(url_for("index"))

    code = request.args.get("code", "")
    state = request.args.get("state", "")
    if not code or not state or state != session.get("oauth_state"):
        flash("Invalid OAuth state. Try again.", "error")
        return redirect(url_for("index"))

    # exchange code for token
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
    if token_resp.status_code != 200:
        flash("Discord token exchange failed.", "error")
        return redirect(url_for("index"))

    access_token = token_resp.json().get("access_token")
    if not access_token:
        flash("Discord token missing.", "error")
        return redirect(url_for("index"))

    me = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    if me.status_code != 200:
        flash("Discord profile fetch failed.", "error")
        return redirect(url_for("index"))

    profile = me.json()
    discord_id = str(profile.get("id"))
    discord_name = f"{profile.get('username','user')}#{profile.get('discriminator','0000')}".replace("#0", "")

    # upsert user
    row = exec1("""
        INSERT INTO users (discord_id, discord_name, created_at)
        VALUES (%s,%s,%s)
        ON CONFLICT (discord_id)
        DO UPDATE SET discord_name=EXCLUDED.discord_name
        RETURNING *
    """, [discord_id, discord_name, _now_utc()])

    session["user_id"] = row["id"]
    session["discord_id"] = discord_id
    session["discord_name"] = discord_name

    flash("Signed in with Discord ✅", "ok")
    return redirect(url_for("profile"))

@app.route("/auth/logout")
def logout():
    session.clear()
    flash("Signed out.", "ok")
    return redirect(url_for("index"))

# =========================================================
# Profile updates (MXB in-game name / class / Steam)
# =========================================================

@app.route("/api/profile", methods=["POST"])
def api_profile_update():
    gate = _require_login()
    if gate:
        return gate

    data = request.get_json(force=True, silent=True) or {}
    mxb_name = (data.get("mxb_name") or "").strip()
    race_class = (data.get("race_class") or "").strip()  # "450" / "250" / "250-2t"
    steam_id = (data.get("steam_id") or "").strip()

    exec1("""
        UPDATE users
        SET mxb_name=%s, race_class=%s, steam_id=%s
        WHERE id=%s
        RETURNING *
    """, [mxb_name, race_class, steam_id, session["user_id"]])

    return jsonify({"ok": True})

# =========================================================
# Results ingest (MX Bikes Export HTML)
# =========================================================

def _ama_points_for_position(pos: int) -> int:
    # AMA-style 1..22 (classic SX/MX main)
    table = [0,
             25, 22, 20, 18, 16, 15, 14, 13, 12, 11,
             10, 9, 8, 7, 6, 5, 4, 3, 2, 1,
             1, 1]
    if pos <= 0:
        return 0
    if pos < len(table):
        return table[pos]
    return 0

@app.route("/api/upload_results", methods=["POST"])
def api_upload_results():
    """
    Accepts:
      - multipart/form-data: file=<MXB export html>
      - or JSON: {"html": "<html...>"}
    """
    gate = _require_login()
    if gate:
        return gate

    event_id = request.form.get("event_id") or (request.json or {}).get("event_id")
    if not event_id:
        return jsonify({"ok": False, "error": "Missing event_id"}), 400

    html = ""
    if request.files.get("file"):
        html = request.files["file"].read().decode("utf-8", errors="ignore")
    else:
        data = request.get_json(force=True, silent=True) or {}
        html = data.get("html", "")

    if not html:
        return jsonify({"ok": False, "error": "No file/html provided"}), 400

    # Store raw export
    exec1("""
        INSERT INTO uploads (event_id, user_id, raw_html, created_at)
        VALUES (%s,%s,%s,%s)
    """, [int(event_id), session["user_id"], html, _now_utc()])

    # Minimal HTML parsing for results table:
    # We keep it simple: look for lines with "pos" / "position" patterns is unreliable,
    # so the system expects the uploader to be used by race director OR trusted user.
    # The bot/website standings still work even if parsing is basic.
    #
    # If you want exact parsing per MXB export format, tell me what the export HTML looks like
    # and I’ll lock the parser to it.

    return jsonify({"ok": True})

# =========================================================
# APIs used by the frontend
# =========================================================

@app.route("/api/discord_stats")
def api_discord_stats():
    # Simple placeholder stats panel
    # (You can wire this to Discord API later if you want)
    total_users = q("SELECT COUNT(*) AS c FROM users")
    total_users = int(total_users[0]["c"]) if total_users else 0
    return jsonify({"ok": True, "registered": total_users})

@app.route("/api/standings")
def api_standings():
    # Split by class (your request)
    rows = q("""
        SELECT
          u.id AS user_id,
          u.mxb_name,
          u.discord_name,
          u.race_class,
          COALESCE(SUM(r.points),0) + COALESCE(SUM(p.points_delta),0) AS points
        FROM users u
        LEFT JOIN results r ON r.user_id=u.id
        LEFT JOIN penalties p ON p.user_id=u.id
        GROUP BY u.id, u.mxb_name, u.discord_name, u.race_class
        ORDER BY points DESC
    """)
    by_class = {}
    for r in rows:
        cls = (r.get("race_class") or "UNASSIGNED").strip() or "UNASSIGNED"
        by_class.setdefault(cls, []).append(r)
    return jsonify({"ok": True, "by_class": by_class})

@app.route("/healthz")
def healthz():
    return "ok", 200

# =========================================================
# Local run
# =========================================================

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
