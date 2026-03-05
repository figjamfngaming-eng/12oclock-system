import os
import sys
from pathlib import Path
from datetime import datetime

# ✅ Make sibling folders importable (shared/) even when Render Root Directory = web
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flask import Flask, request, redirect, url_for, render_template, session, jsonify, flash
import requests

from shared.db import init_db, q, exec1
from shared.points import AMA_POINTS
from shared.parse_mxb_export import parse_export_html


# ----------------------------
# Config
# ----------------------------
APP_SECRET = os.getenv("SECRET_KEY", "dev_secret_change_me")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")  # optional; can be blank on Render
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/")

STEAM_API_KEY = os.getenv("STEAM_API_KEY", "")
STEAM_RETURN_URL = os.getenv("STEAM_RETURN_URL", "")
STEAM_REALM = os.getenv("STEAM_REALM", "")

# For web -> bot links / UI
LEAGUE_NAME = os.getenv("LEAGUE_NAME", "12 O'Clock Boyz AMA League")


# ----------------------------
# App
# ----------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = APP_SECRET


# ✅ Flask 3 fix: init db at import time (no before_first_request)
try:
    init_db()
except Exception as e:
    # Don’t crash boot if DB is temporarily not ready; Render can retry.
    print("[WARN] init_db failed:", e)


# ----------------------------
# Helpers
# ----------------------------
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    rows = q("SELECT * FROM users WHERE id=%s", (uid,))
    return rows[0] if rows else None


def login_required():
    u = current_user()
    if not u:
        flash("Please sign in first.", "warning")
        return redirect(url_for("home"))
    return None


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ----------------------------
# Pages (Tabs on one page)
# ----------------------------
@app.get("/")
def home():
    """
    Your templates folder shows: index.html, base.html, events.html, standings.html, profile.html etc.
    We render index.html as the main hub page.
    """
    u = current_user()
    return render_template(
        "index.html",
        league_name=LEAGUE_NAME,
        user=u,
        invite_url=DISCORD_INVITE_URL,
        now=_now_iso(),
    )


@app.get("/profile")
def profile():
    redir = login_required()
    if redir:
        return redir
    u = current_user()

    # Pull linked accounts + saved MXB name
    return render_template("profile.html", user=u, league_name=LEAGUE_NAME)


@app.get("/events")
def events():
    # latest 100 events
    rows = q(
        "SELECT id, mode, class, title, season, start_ts FROM events ORDER BY id DESC LIMIT 100"
    )
    return render_template("events.html", events=rows, league_name=LEAGUE_NAME)


@app.get("/events/<int:event_id>")
def event_view(event_id: int):
    ev = q("SELECT * FROM events WHERE id=%s", (event_id,))
    if not ev:
        flash("Event not found.", "danger")
        return redirect(url_for("events"))
    ev = ev[0]

    results = q(
        "SELECT r.position, u.mxb_name, u.discord_name, r.points, r.raw_json "
        "FROM results r "
        "JOIN users u ON u.id=r.user_id "
        "WHERE r.event_id=%s "
        "ORDER BY r.position ASC",
        (event_id,),
    )

    return render_template("event.html", event=ev, results=results, league_name=LEAGUE_NAME)


@app.get("/standings")
def standings():
    """
    Split by class using query params:
      /standings?class=450
      /standings?class=250
      /standings?class=2t
    """
    cls = (request.args.get("class") or "").strip()

    # If no class chosen, show all classes separated
    if not cls:
        classes = q("SELECT DISTINCT class FROM events ORDER BY class ASC")
        classes = [c["class"] for c in classes] if classes else ["450", "250", "2T"]
        packed = []
        for c in classes:
            packed.append({"class": c, "rows": _standings_rows(c)})
        return render_template("standings.html", packed=packed, class_filter="", league_name=LEAGUE_NAME)

    return render_template(
        "standings.html",
        packed=[{"class": cls, "rows": _standings_rows(cls)}],
        class_filter=cls,
        league_name=LEAGUE_NAME,
    )


def _standings_rows(cls: str):
    # Sum points from results joined to events filtered by class
    rows = q(
        "SELECT u.id, u.mxb_name, u.discord_name, COALESCE(SUM(r.points),0) AS points "
        "FROM users u "
        "JOIN results r ON r.user_id=u.id "
        "JOIN events e ON e.id=r.event_id "
        "WHERE e.class=%s "
        "GROUP BY u.id, u.mxb_name, u.discord_name "
        "ORDER BY points DESC, u.mxb_name ASC "
        "LIMIT 200",
        (cls,),
    )
    return rows


@app.get("/rules")
def rules():
    return render_template("rules.html", league_name=LEAGUE_NAME)


@app.get("/schedule")
def schedule():
    # simple schedule list from events
    rows = q("SELECT id, mode, class, title, season, start_ts FROM events ORDER BY start_ts ASC LIMIT 200")
    return render_template("schedule.html", events=rows, league_name=LEAGUE_NAME)


@app.get("/riders")
def riders():
    rows = q("SELECT id, mxb_name, discord_name, steam_id FROM users ORDER BY mxb_name ASC LIMIT 500")
    return render_template("riders.html", riders=rows, league_name=LEAGUE_NAME)


@app.get("/logout")
def logout():
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("home"))


# ----------------------------
# Discord OAuth
# ----------------------------
@app.get("/auth/discord/login")
def discord_login():
    if not DISCORD_CLIENT_ID or not DISCORD_REDIRECT_URI:
        flash("Discord OAuth env vars missing.", "danger")
        return redirect(url_for("home"))

    scope = "identify"
    url = (
        "https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(DISCORD_REDIRECT_URI, safe='')}"
        f"&response_type=code"
        f"&scope={scope}"
    )
    return redirect(url)


@app.get("/auth/discord/callback")
def discord_callback():
    code = request.args.get("code")
    if not code:
        flash("Discord login failed (no code).", "danger")
        return redirect(url_for("home"))

    # token
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
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    # user
    me = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    me.raise_for_status()
    me = me.json()

    discord_id = me.get("id")
    discord_name = f"{me.get('username')}#{me.get('discriminator')}" if me.get("discriminator") else me.get("username")

    # upsert user
    existing = q("SELECT id FROM users WHERE discord_id=%s", (discord_id,))
    if existing:
        user_id = existing[0]["id"]
        exec1("UPDATE users SET discord_name=%s WHERE id=%s", (discord_name, user_id))
    else:
        user_id = exec1(
            "INSERT INTO users (discord_id, discord_name, created_ts) VALUES (%s,%s, NOW()) RETURNING id",
            (discord_id, discord_name),
        )["id"]

    session["user_id"] = user_id
    flash("Discord linked ✅", "success")
    return redirect(url_for("profile"))


# ----------------------------
# Steam OpenID (simple)
# ----------------------------
@app.get("/auth/steam/login")
def steam_login():
    redir = login_required()
    if redir:
        return redir

    if not STEAM_RETURN_URL or not STEAM_REALM:
        flash("Steam OpenID env vars missing.", "danger")
        return redirect(url_for("profile"))

    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": STEAM_RETURN_URL,
        "openid.realm": STEAM_REALM,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    url = "https://steamcommunity.com/openid/login"
    return redirect(url + "?" + requests.compat.urlencode(params))


@app.get("/auth/steam/callback")
def steam_callback():
    redir = login_required()
    if redir:
        return redir

    claimed = request.args.get("openid.claimed_id", "")
    # SteamID64 is the last path segment
    steam_id = claimed.rstrip("/").split("/")[-1] if claimed else ""
    if not steam_id.isdigit():
        flash("Steam link failed.", "danger")
        return redirect(url_for("profile"))

    u = current_user()
    exec1("UPDATE users SET steam_id=%s WHERE id=%s", (steam_id, u["id"]))
    flash("Steam linked ✅", "success")
    return redirect(url_for("profile"))


# ----------------------------
# Profile save (MXB in-game name)
# ----------------------------
@app.post("/profile/save")
def profile_save():
    redir = login_required()
    if redir:
        return redir

    u = current_user()
    mxb_name = (request.form.get("mxb_name") or "").strip()
    if not mxb_name:
        flash("MXB name can’t be empty.", "warning")
        return redirect(url_for("profile"))

    exec1("UPDATE users SET mxb_name=%s WHERE id=%s", (mxb_name, u["id"]))
    flash("Saved MXB name ✅", "success")
    return redirect(url_for("profile"))


# ----------------------------
# Upload Results (MXB export HTML)
# ----------------------------
@app.get("/upload")
def upload_page():
    redir = login_required()
    if redir:
        return redir
    return render_template("upload.html", league_name=LEAGUE_NAME)


@app.post("/upload")
def upload_post():
    redir = login_required()
    if redir:
        return redir

    u = current_user()

    event_id = request.form.get("event_id")
    if not event_id or not event_id.isdigit():
        flash("Missing event_id.", "danger")
        return redirect(url_for("upload_page"))

    f = request.files.get("file")
    if not f:
        flash("Upload the MXB export .html file.", "danger")
        return redirect(url_for("upload_page"))

    html = f.read().decode("utf-8", errors="ignore")
    parsed = parse_export_html(html)

    # Save raw upload
    exec1(
        "INSERT INTO uploads (user_id, event_id, filename, uploaded_ts, raw_html) VALUES (%s,%s,%s, NOW(), %s)",
        (u["id"], int(event_id), f.filename or "export.html", html),
    )

    # Store placements as results (match riders by MXB name)
    # parsed["rows"] = [{"pos":1,"name":"...","best":...,"total":...}, ...]
    stored = 0
    for row in parsed["rows"]:
        pos = row["pos"]
        rider_name = row["name"].strip()

        # Find user by MXB name
        match = q("SELECT id FROM users WHERE LOWER(mxb_name)=LOWER(%s) LIMIT 1", (rider_name,))
        if not match:
            continue

        uid = match[0]["id"]
        points = AMA_POINTS.get(pos, 0)

        # upsert result
        existing = q("SELECT id FROM results WHERE event_id=%s AND user_id=%s", (int(event_id), uid))
        if existing:
            exec1(
                "UPDATE results SET position=%s, points=%s, raw_json=%s WHERE id=%s",
                (pos, points, str(row), existing[0]["id"]),
            )
        else:
            exec1(
                "INSERT INTO results (event_id, user_id, position, points, raw_json) VALUES (%s,%s,%s,%s,%s)",
                (int(event_id), uid, pos, points, str(row)),
            )
        stored += 1

    flash(f"Uploaded ✅ Stored {stored} matched results.", "success")
    return redirect(url_for("event_view", event_id=int(event_id)))


# ----------------------------
# API (for bot + widgets)
# ----------------------------
@app.get("/api/discord_stats")
def api_discord_stats():
    # lightweight placeholder used by your footer widget
    return jsonify({"ok": True, "ts": _now_iso()})


@app.get("/api/standings")
def api_standings():
    cls = (request.args.get("class") or "").strip()
    if not cls:
        return jsonify({"ok": False, "error": "missing class"}), 400
    rows = _standings_rows(cls)
    return jsonify({"ok": True, "class": cls, "rows": rows})


@app.get("/api/events")
def api_events():
    rows = q("SELECT id, mode, class, title, season, start_ts FROM events ORDER BY id DESC LIMIT 100")
    return jsonify({"ok": True, "events": rows})


if __name__ == "__main__":
    # local dev
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
