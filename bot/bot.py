import os
import sys
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, request, redirect, url_for, render_template, session, jsonify, flash
import requests

# ------------------------------------------------------------
# Make sure "shared" is importable no matter what Render root dir is
# (works even if Render Root Directory is set to "web")
# /opt/render/project/src/web/app.py -> parents[1] == /opt/render/project/src
# ------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.db import init_db, q, exec1  # noqa

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-change-me")

# ------------------------------------------------------------
# ENV
# ------------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL")

DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI")  # e.g. https://YOURWEB.onrender.com/oauth/discord/callback

STEAM_OPENID_RETURN = os.environ.get("STEAM_OPENID_RETURN")  # e.g. https://YOURWEB.onrender.com/oauth/steam/return

DISCORD_INVITE_URL = os.environ.get("DISCORD_INVITE_URL", "#")
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID")

# Secret used by your PC uploader script to post results
UPLOAD_TOKEN = os.environ.get("UPLOAD_TOKEN", "")

# Basic sanity: don't hard-crash if missing, but warn
if not DATABASE_URL:
    print("[WARN] DATABASE_URL not set")

# ------------------------------------------------------------
# INIT DB (Flask 3 compatible: do it at import time)
# ------------------------------------------------------------
try:
    init_db()
    print("[OK] DB initialized")
except Exception as e:
    print("[WARN] init_db() failed (will retry on first request):", e)

# ------------------------------------------------------------
# AMA-style points tables (CONFIG HERE)
# You can change these exactly how you want later.
# ------------------------------------------------------------
AMA_MX_POINTS = [
    50, 47, 45, 43, 41, 40, 39, 38, 37, 36,
    35, 34, 33, 32, 31, 30, 29, 28, 27, 26,
    25, 24, 23, 22, 21, 20, 19, 18, 17, 16,
    15, 14, 13, 12, 11, 10, 9, 8, 7, 6
]
AMA_SX_POINTS = [
    26, 23, 21, 19, 17, 15, 14, 13, 12, 11,
    10, 9, 8, 7, 6, 5, 4, 3, 2, 1
]

def points_for(mode: str, position: int) -> int:
    mode = (mode or "").upper().strip()
    if position <= 0:
        return 0
    if mode == "SX":
        return AMA_SX_POINTS[position - 1] if position <= len(AMA_SX_POINTS) else 0
    # default to MX
    return AMA_MX_POINTS[position - 1] if position <= len(AMA_MX_POINTS) else 0


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    rows = q("SELECT * FROM users WHERE id=%s", [uid])
    return rows[0] if rows else None

def require_login():
    u = current_user()
    if not u:
        return redirect(url_for("signup"))
    return None

def user_is_fully_linked(u) -> bool:
    # Require Discord + Steam + MXB ingame name for "fully registered"
    return bool(u.get("discord_id") and u.get("steam_id") and u.get("mxb_name"))

def safe_init_db_retry():
    try:
        init_db()
    except Exception as e:
        print("[WARN] init_db retry failed:", e)

@app.before_request
def _ensure_db():
    # light retry if init failed during boot
    safe_init_db_retry()


# ------------------------------------------------------------
# Pages
# ------------------------------------------------------------
@app.route("/")
def home():
    u = current_user()
    return render_template("home.html", user=u, invite_url=DISCORD_INVITE_URL)

@app.route("/rules")
def rules():
    u = current_user()
    return render_template("rules.html", user=u)

@app.route("/events")
def events():
    u = current_user()
    evs = q("SELECT * FROM events ORDER BY start_time DESC LIMIT 100")
    return render_template("events.html", user=u, events=evs)

@app.route("/standings")
def standings():
    u = current_user()
    # Standings split by CLASS (250/450 etc) and MODE (MX/SX)
    # Points = sum(results.points) + sum(penalties.points_delta)
    rows = q("""
        SELECT
          e.mode,
          e.class,
          u.id as user_id,
          COALESCE(u.mxb_name, u.username) AS rider,
          COALESCE(SUM(r.points), 0) +
          COALESCE((SELECT SUM(p.points_delta) FROM penalties p WHERE p.user_id=u.id), 0) AS total_points,
          COUNT(DISTINCT r.event_id) as events_count
        FROM users u
        JOIN results r ON r.user_id = u.id
        JOIN events e ON e.id = r.event_id
        GROUP BY e.mode, e.class, u.id, rider
        ORDER BY e.mode, e.class, total_points DESC
    """)
    # group for template
    grouped = {}
    for row in rows:
        key = f"{row['mode']}_{row['class']}"
        grouped.setdefault(key, []).append(row)
    return render_template("standings.html", user=u, grouped=grouped)

@app.route("/signup")
def signup():
    u = current_user()
    return render_template("signup.html", user=u, invite_url=DISCORD_INVITE_URL)

@app.route("/profile", methods=["GET", "POST"])
def profile():
    u = current_user()
    if not u:
        return redirect(url_for("signup"))

    if request.method == "POST":
        mxb_name = request.form.get("mxb_name", "").strip()
        if not mxb_name:
            flash("MX Bikes in-game name is required.", "error")
        else:
            exec1("UPDATE users SET mxb_name=%s WHERE id=%s", [mxb_name, u["id"]])
            flash("Saved MX Bikes in-game name.", "success")
        return redirect(url_for("profile"))

    # refresh
    u = current_user()
    return render_template("profile.html", user=u)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ------------------------------------------------------------
# Discord OAuth
# ------------------------------------------------------------
@app.route("/oauth/discord")
def oauth_discord():
    # Create local user if not exists
    if not session.get("user_id"):
        row = exec1("INSERT INTO users(username) VALUES(%s) RETURNING *", ["new-user"])
        session["user_id"] = row["id"]

    scope = "identify"
    return redirect(
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(DISCORD_REDIRECT_URI, safe='')}"
        f"&response_type=code"
        f"&scope={scope}"
    )

@app.route("/oauth/discord/callback")
def oauth_discord_callback():
    if not session.get("user_id"):
        return redirect(url_for("signup"))

    code = request.args.get("code")
    if not code:
        flash("Discord login failed: no code", "error")
        return redirect(url_for("signup"))

    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_resp = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    me = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
    me.raise_for_status()
    me_json = me.json()

    discord_id = int(me_json["id"])
    username = f"{me_json.get('username','user')}#{me_json.get('discriminator','0000')}"

    exec1(
        "UPDATE users SET discord_id=%s, username=%s WHERE id=%s",
        [discord_id, username, session["user_id"]],
    )

    flash("Discord linked ✅", "success")
    return redirect(url_for("profile"))


# ------------------------------------------------------------
# Steam OpenID (simple redirect helper)
# NOTE: for a full OpenID verify, keep your existing logic in templates/links.
# ------------------------------------------------------------
@app.route("/oauth/steam")
def oauth_steam():
    # You can keep your existing OpenID logic; this is placeholder redirect helper.
    if not session.get("user_id"):
        return redirect(url_for("signup"))
    flash("Steam linking is configured via OpenID return URL.", "info")
    return redirect(url_for("profile"))

@app.route("/oauth/steam/return")
def oauth_steam_return():
    # If you already built parsing earlier, keep it.
    # Minimal version: read steamid from query if your flow passes it.
    if not session.get("user_id"):
        return redirect(url_for("signup"))

    steam_id = request.args.get("steam_id", "").strip()
    if not steam_id:
        flash("Steam link failed: missing steam_id", "error")
        return redirect(url_for("profile"))

    exec1("UPDATE users SET steam_id=%s WHERE id=%s", [steam_id, session["user_id"]])
    flash("Steam linked ✅", "success")
    return redirect(url_for("profile"))


# ------------------------------------------------------------
# Results Upload UI Page (admin/manual)
# ------------------------------------------------------------
@app.route("/results/upload", methods=["GET"])
def results_upload_page():
    u = current_user()
    if not u:
        return redirect(url_for("signup"))
    evs = q("SELECT * FROM events ORDER BY start_time DESC LIMIT 50")
    return render_template("upload_results.html", user=u, events=evs)

# ------------------------------------------------------------
# API: upload results (THIS POWERS THE "PRO AUTOMATION UPLOADER")
# POST /api/results/upload
# Headers: X-Upload-Token: <UPLOAD_TOKEN>
# Body (multipart):
#  - event_id (int)
#  - export_html (file) OR export_text (string)
# ------------------------------------------------------------
@app.route("/api/results/upload", methods=["POST"])
def api_results_upload():
    if not UPLOAD_TOKEN:
        return jsonify({"ok": False, "error": "UPLOAD_TOKEN not set on server"}), 500

    token = request.headers.get("X-Upload-Token", "")
    if token != UPLOAD_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        event_id = int(request.form.get("event_id", "0"))
    except Exception:
        event_id = 0
    if not event_id:
        return jsonify({"ok": False, "error": "event_id required"}), 400

    export_text = request.form.get("export_text", "")
    if "export_html" in request.files:
        export_text = request.files["export_html"].read().decode("utf-8", errors="ignore")

    if not export_text or len(export_text) < 100:
        return jsonify({"ok": False, "error": "export html/text missing"}), 400

    # Get event info (mode/class)
    ev = q("SELECT * FROM events WHERE id=%s", [event_id])
    if not ev:
        return jsonify({"ok": False, "error": "event not found"}), 404
    ev = ev[0]
    mode = (ev["mode"] or "MX").upper()

    # Parse MXB export html:
    # Your exports look like HTML tables; we detect rows and extract position + name + time.
    parsed = parse_mxb_export_html(export_text)
    if not parsed:
        return jsonify({"ok": False, "error": "could not parse export"}), 400

    # Wipe existing results for this event and re-insert
    exec1("DELETE FROM results WHERE event_id=%s", [event_id])

    inserted = 0
    for row in parsed:
        pos = row["position"]
        raw_name = row["name"]
        time_text = row.get("time")

        # match to user by MXB name (case-insensitive)
        user = q("SELECT id FROM users WHERE LOWER(mxb_name)=LOWER(%s) LIMIT 1", [raw_name])
        user_id = user[0]["id"] if user else None

        pts = points_for(mode, pos)

        exec1(
            """
            INSERT INTO results(event_id, position, raw_name, user_id, points, time_text)
            VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(event_id, position) DO UPDATE SET
              raw_name=EXCLUDED.raw_name,
              user_id=EXCLUDED.user_id,
              points=EXCLUDED.points,
              time_text=EXCLUDED.time_text
            RETURNING id
            """,
            [event_id, pos, raw_name, user_id, pts, time_text],
        )
        inserted += 1

    return jsonify({"ok": True, "event_id": event_id, "inserted": inserted})


def parse_mxb_export_html(html: str):
    """
    Tries to parse PiBoSo MX Bikes export HTML.
    We look for table rows and pull:
      - position
      - rider name
      - time (optional)
    """
    # Normalize
    html = html.replace("\r", "")

    # Quick extract rows
    # This is intentionally tolerant to different export layouts
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL)
    out = []
    for r in rows:
        cols = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.IGNORECASE | re.DOTALL)
        cols = [re.sub(r"<.*?>", "", c).strip() for c in cols]
        cols = [c for c in cols if c]

        # Common layouts:
        # [Pos, Name, ... , Time]
        if len(cols) >= 2:
            # find first int in first column
            m = re.match(r"^(\d+)$", cols[0])
            if not m:
                continue
            pos = int(m.group(1))
            name = cols[1].strip()
            if not name:
                continue

            # optional time: last col that looks like mm:ss.xxx or hh:mm:ss.xxx
            time_text = None
            for c in reversed(cols):
                if re.search(r"\d+:\d+(\.\d+)?", c):
                    time_text = c
                    break

            out.append({"position": pos, "name": name, "time": time_text})

    # Sort by position
    out.sort(key=lambda x: x["position"])
    return out


# ------------------------------------------------------------
# API: Discord stats (widget)
# ------------------------------------------------------------
@app.route("/api/discord_stats")
def api_discord_stats():
    data = {
        "guild_id": DISCORD_GUILD_ID,
        "invite_url": DISCORD_INVITE_URL,
    }
    return jsonify(data)


# ------------------------------------------------------------
# Health
# ------------------------------------------------------------
@app.route("/healthz")
def healthz():
    return "ok", 200
