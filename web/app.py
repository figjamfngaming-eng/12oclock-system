import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests
from flask import Flask, render_template, request, redirect, session, url_for, flash, jsonify

from shared.db import init_db, q, exec1
from shared.points import points_for_position
from shared.parse_mxb_export import parse_export_html

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")  # e.g. https://12oclock-system.onrender.com
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "")
RESULTS_UPLOAD_KEY = os.getenv("RESULTS_UPLOAD_KEY", "")

STEAM_WEB_API_KEY = os.getenv("STEAM_WEB_API_KEY")  # optional

def _require_env(name: str):
    if not os.getenv(name):
        raise RuntimeError(f"Missing env var: {name}")

@app.before_first_request
def _boot():
    init_db()

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    rows = q("SELECT * FROM users WHERE id=%s", [uid])
    return rows[0] if rows else None

def login_required():
    if not current_user():
        flash("Please sign in with Discord first.", "warn")
        return redirect(url_for("home"))
    return None

@app.get("/")
def home():
    user = current_user()
    return render_template(
        "index.html",
        user=user,
        invite_url=DISCORD_INVITE_URL,
    )

@app.get("/rules")
def rules():
    return render_template("rules.html", user=current_user())

@app.get("/events")
def events():
    rows = q("SELECT * FROM events ORDER BY start_time DESC LIMIT 100")
    return render_template("events.html", user=current_user(), events=rows)

@app.get("/standings/<cls>")
def standings(cls):
    # total points = sum(results.points) + sum(penalties.points_delta)
    sql = """
    SELECT
      u.id as user_id,
      COALESCE(u.mxb_name, u.discord_username, 'Unknown') AS rider,
      COALESCE(u.discord_username,'') AS discord,
      COALESCE(SUM(r.points),0) + COALESCE(SUM(p.points_delta),0) AS points
    FROM users u
    LEFT JOIN results r
      ON r.user_id = u.id
    LEFT JOIN events e
      ON e.id = r.event_id AND e.class = %s
    LEFT JOIN penalties p
      ON p.user_id = u.id
    GROUP BY u.id, rider, discord
    HAVING (COALESCE(SUM(r.points),0) + COALESCE(SUM(p.points_delta),0)) > 0
    ORDER BY points DESC, rider ASC
    LIMIT 200
    """
    rows = q(sql, [cls])
    return render_template("standings.html", user=current_user(), rows=rows, cls=cls)

@app.get("/profile")
def profile():
    r = login_required()
    if r: return r
    user = current_user()
    return render_template("profile.html", user=user)

@app.post("/profile")
def profile_save():
    r = login_required()
    if r: return r
    user = current_user()
    mxb_name = (request.form.get("mxb_name") or "").strip()
    exec1("UPDATE users SET mxb_name=%s WHERE id=%s", [mxb_name or None, user["id"]])
    flash("Profile saved.", "ok")
    return redirect(url_for("profile"))

# ---------------------------
# Discord OAuth (login)
# ---------------------------
@app.get("/auth/discord/login")
def discord_login():
    _require_env("DISCORD_CLIENT_ID")
    _require_env("DISCORD_CLIENT_SECRET")
    if not PUBLIC_BASE_URL:
        # fall back to request host
        base = request.url_root.rstrip("/")
    else:
        base = PUBLIC_BASE_URL
    redirect_uri = f"{base}/auth/discord/callback"
    scope = "identify"
    params = {
        "client_id": DISCORD_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
    }
    return redirect("https://discord.com/api/oauth2/authorize?" + urlencode(params))

@app.get("/auth/discord/callback")
def discord_callback():
    code = request.args.get("code")
    if not code:
        flash("Discord login cancelled.", "warn")
        return redirect(url_for("home"))

    if not PUBLIC_BASE_URL:
        base = request.url_root.rstrip("/")
    else:
        base = PUBLIC_BASE_URL
    redirect_uri = f"{base}/auth/discord/callback"

    token_resp = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "scope": "identify",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    me = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    me.raise_for_status()
    me = me.json()

    discord_id = int(me["id"])
    username = f'{me.get("username","user")}#{me.get("discriminator","0000")}'

    # upsert user
    row = exec1(
        """
        INSERT INTO users (discord_id, discord_username)
        VALUES (%s, %s)
        ON CONFLICT (discord_id)
        DO UPDATE SET discord_username=EXCLUDED.discord_username
        RETURNING *
        """,
        [discord_id, username],
    )
    session["user_id"] = row["id"]
    flash("Signed in with Discord.", "ok")
    return redirect(url_for("profile"))

@app.get("/logout")
def logout():
    session.clear()
    flash("Signed out.", "ok")
    return redirect(url_for("home"))

# ---------------------------
# Steam OpenID linking
# ---------------------------
@app.get("/auth/steam/login")
def steam_login():
    r = login_required()
    if r: return r

    if not PUBLIC_BASE_URL:
        base = request.url_root.rstrip("/")
    else:
        base = PUBLIC_BASE_URL
    return_to = f"{base}/auth/steam/callback"
    realm = base

    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return redirect("https://steamcommunity.com/openid/login?" + urlencode(params))

@app.get("/auth/steam/callback")
def steam_callback():
    r = login_required()
    if r: return r

    # Verify response with Steam
    args = dict(request.args)
    args["openid.mode"] = "check_authentication"
    resp = requests.post("https://steamcommunity.com/openid/login", data=args, timeout=20)
    resp.raise_for_status()
    if "is_valid:true" not in resp.text:
        flash("Steam linking failed.", "warn")
        return redirect(url_for("profile"))

    claimed = request.args.get("openid.claimed_id","")
    m = __import__("re").search(r"steamcommunity\.com/openid/id/(\d+)", claimed)
    if not m:
        flash("Steam linking failed (no SteamID).", "warn")
        return redirect(url_for("profile"))

    steam_id = m.group(1)
    user = current_user()
    exec1("UPDATE users SET steam_id=%s WHERE id=%s", [steam_id, user["id"]])

    # Optional persona name fetch
    if STEAM_WEB_API_KEY:
        try:
            data = requests.get(
                "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
                params={"key": STEAM_WEB_API_KEY, "steamids": steam_id},
                timeout=20,
            ).json()
            players = data.get("response", {}).get("players", [])
            if players:
                persona = players[0].get("personaname")
                exec1("UPDATE users SET steam_persona=%s WHERE id=%s", [persona, user["id"]])
        except Exception:
            pass

    flash("Steam linked.", "ok")
    return redirect(url_for("profile"))

# ---------------------------
# Results upload
# ---------------------------
@app.get("/upload")
def upload_page():
    r = login_required()
    if r: return r
    ev = q("SELECT id, title, class, mode, start_time FROM events ORDER BY start_time DESC LIMIT 50")
    return render_template("upload.html", user=current_user(), events=ev)

def _match_rows_to_users(rows):
    # Build lookup of MXB names
    users = q("SELECT id, mxb_name FROM users WHERE mxb_name IS NOT NULL")
    name_to_id = { (u["mxb_name"] or "").strip().lower(): u["id"] for u in users }
    out = []
    for r in rows:
        raw = (r["name"] or "").strip()
        uid = name_to_id.get(raw.lower())
        out.append({**r, "user_id": uid})
    return out

def _store_results(event_id: int, parsed_rows):
    # Replace existing results for event
    exec1("DELETE FROM results WHERE event_id=%s", [event_id])
    for r in parsed_rows:
        pos = int(r["position"])
        pts = points_for_position(pos)
        exec1(
            """
            INSERT INTO results (event_id, position, raw_name, user_id, points, time_text)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (event_id, position) DO UPDATE
            SET raw_name=EXCLUDED.raw_name, user_id=EXCLUDED.user_id, points=EXCLUDED.points, time_text=EXCLUDED.time_text
            """,
            [event_id, pos, r["name"], r.get("user_id"), pts, r.get("time")],
        )

@app.post("/upload")
def upload_submit():
    r = login_required()
    if r: return r

    f = request.files.get("file")
    event_id = request.form.get("event_id")
    if not f or not event_id:
        flash("Select an event and choose your export HTML file.", "warn")
        return redirect(url_for("upload_page"))

    html = f.read().decode("utf-8", errors="ignore")
    parsed = parse_export_html(html)
    rows = _match_rows_to_users(parsed["rows"])

    _store_results(int(event_id), rows)
    flash(f"Imported {len(rows)} rows into Event #{event_id}.", "ok")
    return redirect(url_for("events"))

@app.post("/api/upload_results")
def api_upload_results():
    # Auth with header or form field
    key = request.headers.get("X-RESULTS-KEY") or request.form.get("key") or ""
    if not RESULTS_UPLOAD_KEY or key != RESULTS_UPLOAD_KEY:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    event_id = request.form.get("event_id")
    if event_id:
        event_id = int(event_id)
    else:
        # default: latest event
        ev = q("SELECT id FROM events ORDER BY start_time DESC LIMIT 1")
        if not ev:
            return jsonify({"ok": False, "error": "no events"}), 400
        event_id = int(ev[0]["id"])

    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "missing file"}), 400

    html = f.read().decode("utf-8", errors="ignore")
    parsed = parse_export_html(html)
    rows = _match_rows_to_users(parsed["rows"])
    _store_results(event_id, rows)
    return jsonify({"ok": True, "event_id": event_id, "rows": len(rows)})

# ---------------------------
# Live stats widgets
# ---------------------------
@app.get("/api/stats")
def api_stats():
    # racers = users with mxb_name set
    racers = q("SELECT COUNT(*)::int AS c FROM users WHERE mxb_name IS NOT NULL")[0]["c"]
    return jsonify({"racers": racers})

@app.get("/api/discord_stats")
def api_discord_stats():
    if not DISCORD_GUILD_ID:
        return jsonify({"ok": False, "error": "missing DISCORD_GUILD_ID"}), 400
    try:
        data = requests.get(f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/widget.json", timeout=15).json()
        members = len(data.get("members", []))
        name = data.get("name")
        return jsonify({"ok": True, "name": name, "members": members})
    except Exception as e:
        return jsonify({"ok": False, "error": "Discord widget disabled or not accessible."}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","5000")), debug=True)
