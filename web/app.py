import os
import re
import json
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

# ------------------------------------------------------------
# Flask setup (your templates are in web/templates)
# ------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# API key (optional) if you later want the uploader/bot to call the site
API_KEY = os.getenv("API_KEY", "").strip()

DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "").strip()

# ------------------------------------------------------------
# AMA-style (common outdoor motocross) points table
# NOTE: If you want a different series, edit this table.
# This is per-moto placement points.
# ------------------------------------------------------------
POINTS_TABLE = {
    1: 50, 2: 47, 3: 45, 4: 43, 5: 41,
    6: 40, 7: 39, 8: 38, 9: 37, 10: 36,
    11: 35, 12: 34, 13: 33, 14: 32, 15: 31,
    16: 30, 17: 29, 18: 28, 19: 27, 20: 26,
    21: 25, 22: 24, 23: 23, 24: 22, 25: 21,
    26: 20, 27: 19, 28: 18, 29: 17, 30: 16,
    31: 15, 32: 14, 33: 13, 34: 12, 35: 11,
    36: 10, 37: 9, 38: 8, 39: 7, 40: 6,
    41: 5, 42: 4, 43: 3, 44: 2, 45: 1,
}

# ------------------------------------------------------------
# DB helpers
# ------------------------------------------------------------
def db_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL env var is missing on Render.")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    """Create tables if they don't exist (safe to call on every boot)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        discord_user_id BIGINT UNIQUE,
        discord_tag TEXT,
        steam_id TEXT,
        mxb_name TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        mode TEXT NOT NULL,              -- MX / SX / ENDURO etc
        class_name TEXT NOT NULL,        -- 450 / 250 / etc
        title TEXT NOT NULL,
        season INT NOT NULL DEFAULT 1,
        track TEXT,
        notes TEXT,
        start_time TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS race_results (
        id SERIAL PRIMARY KEY,
        event_id INT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        source TEXT DEFAULT 'upload',     -- upload/bot/manual
        uploaded_at TIMESTAMPTZ DEFAULT NOW(),
        raw_html TEXT,
        parsed_json JSONB
    );

    CREATE TABLE IF NOT EXISTS standings_points (
        id SERIAL PRIMARY KEY,
        event_id INT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        class_name TEXT NOT NULL,
        rider_name TEXT NOT NULL,
        points INT NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

# Call init_db at import time (Flask 3 removed before_first_request)
try:
    init_db()
except Exception as e:
    # Don't hard crash on import; Render will show logs and you can fix env vars.
    print(f"[INIT_DB] failed: {e}")

# ------------------------------------------------------------
# Parsing MX Bikes export HTML
# Your MXB export is often a single .html file (like 1.html)
# We'll extract rider rows in a tolerant way and compute points.
# ------------------------------------------------------------
def _clean_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s

def parse_mxb_export_html(html: str):
    """
    Try to parse rider standings/finish from MX Bikes exported HTML.
    Returns:
      {
        "rows": [{"pos": 1, "name": "Devo", "time": "..."}],
        "detected": {...}
      }
    Works even if the HTML structure changes a bit.
    """
    # Very tolerant parsing: look for table rows with position numbers.
    # This isn't perfect but works for the typical MXB export.
    rows = []
    # Find rows like: <tr> ... <td>1</td> <td>RiderName</td> ...
    tr_blocks = re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S)
    for tr in tr_blocks:
        tds = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.I | re.S)
        tds = [_clean_text(re.sub(r"<[^>]+>", "", x)) for x in tds]
        if not tds:
            continue

        # find first integer cell = position
        pos = None
        for cell in tds[:3]:
            if re.fullmatch(r"\d{1,3}", cell):
                pos = int(cell)
                break
        if pos is None:
            continue

        # next likely cell is name
        name = None
        for cell in tds:
            if cell and not re.fullmatch(r"\d{1,3}", cell) and len(cell) <= 40:
                # skip obvious headers
                if cell.lower() in {"pos", "position", "name", "rider"}:
                    continue
                name = cell
                break

        if not name:
            continue

        # optional time/laps
        extra = tds[2:] if len(tds) > 2 else []
        rows.append({"pos": pos, "name": name, "extra": extra})

    # Remove duplicates, keep best (lowest pos)
    best = {}
    for r in rows:
        n = r["name"]
        if n not in best or r["pos"] < best[n]["pos"]:
            best[n] = r
    rows = sorted(best.values(), key=lambda x: x["pos"])

    return {
        "rows": rows,
        "count": len(rows),
    }

def compute_points_from_rows(rows):
    scored = []
    for r in rows:
        pos = r["pos"]
        pts = POINTS_TABLE.get(pos, 0)
        scored.append({"pos": pos, "name": r["name"], "points": pts})
    return scored

# ------------------------------------------------------------
# Routes (tabs/pages)
# You said "tabs on ONE page" earlier — your templates already do top nav tabs.
# This backend supports those pages.
# ------------------------------------------------------------
@app.get("/")
def home():
    # You have index.html, NOT home.html
    return render_template("index.html", invite_url=DISCORD_INVITE_URL)

@app.get("/events")
def events():
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM events ORDER BY start_time DESC NULLS LAST, id DESC LIMIT 100;")
            ev = cur.fetchall()
    return render_template("events.html", events=ev)

@app.get("/event/<int:event_id>")
def event_page(event_id: int):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM events WHERE id=%s;", (event_id,))
            ev = cur.fetchone()
            cur.execute(
                "SELECT * FROM race_results WHERE event_id=%s ORDER BY uploaded_at DESC LIMIT 1;",
                (event_id,)
            )
            rr = cur.fetchone()

            cur.execute(
                "SELECT rider_name, points FROM standings_points WHERE event_id=%s ORDER BY points DESC, rider_name ASC;",
                (event_id,)
            )
            pts = cur.fetchall()

    return render_template("event.html", event=ev, latest_result=rr, points=pts)

@app.get("/standings")
def standings():
    class_filter = request.args.get("class", "").strip()  # 450 / 250 etc
    mode_filter = request.args.get("mode", "").strip()

    query = """
        SELECT e.mode, sp.class_name, sp.rider_name, SUM(sp.points) AS total_points
        FROM standings_points sp
        JOIN events e ON e.id = sp.event_id
        WHERE 1=1
    """
    params = []
    if class_filter:
        query += " AND sp.class_name = %s"
        params.append(class_filter)
    if mode_filter:
        query += " AND e.mode = %s"
        params.append(mode_filter)

    query += """
        GROUP BY e.mode, sp.class_name, sp.rider_name
        ORDER BY e.mode, sp.class_name, total_points DESC, sp.rider_name ASC;
    """

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    # Split standings by class for the template
    grouped = {}
    for r in rows:
        key = f"{r['mode']} {r['class_name']}"
        grouped.setdefault(key, []).append(r)

    return render_template("standings.html", grouped=grouped, class_filter=class_filter, mode_filter=mode_filter)

@app.get("/rules")
def rules():
    return render_template("rules.html")

@app.get("/profile")
def profile():
    # Basic profile page (manual linking for now)
    # (Later you can add Discord OAuth + Steam OpenID)
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 200;")
            users = cur.fetchall()
    return render_template("profile.html", users=users)

@app.post("/profile/update")
def profile_update():
    discord_user_id = request.form.get("discord_user_id", "").strip()
    discord_tag = request.form.get("discord_tag", "").strip()
    steam_id = request.form.get("steam_id", "").strip()
    mxb_name = request.form.get("mxb_name", "").strip()

    if not discord_user_id.isdigit():
        flash("Discord User ID must be a number.", "error")
        return redirect(url_for("profile"))

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (discord_user_id, discord_tag, steam_id, mxb_name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (discord_user_id)
                DO UPDATE SET discord_tag=EXCLUDED.discord_tag,
                              steam_id=EXCLUDED.steam_id,
                              mxb_name=EXCLUDED.mxb_name;
            """, (int(discord_user_id), discord_tag, steam_id, mxb_name))
        conn.commit()

    flash("Profile updated.", "success")
    return redirect(url_for("profile"))

@app.get("/upload")
def upload_page():
    # Your upload.html exists
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, mode, class_name, title, season, start_time FROM events ORDER BY id DESC LIMIT 100;")
            events = cur.fetchall()
    return render_template("upload.html", events=events)

@app.post("/upload")
def upload_results():
    """
    Upload MX Bikes export HTML (1.html). Parses it and writes points for event.
    """
    event_id = request.form.get("event_id", "").strip()
    if not event_id.isdigit():
        flash("Pick a valid event.", "error")
        return redirect(url_for("upload_page"))

    f = request.files.get("file")
    if not f:
        flash("Please choose your MX Bikes export HTML file.", "error")
        return redirect(url_for("upload_page"))

    html = f.read().decode("utf-8", errors="ignore")
    parsed = parse_mxb_export_html(html)
    scored = compute_points_from_rows(parsed["rows"])

    # Get event class/mode from DB so points go into correct class
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM events WHERE id=%s;", (int(event_id),))
            ev = cur.fetchone()
            if not ev:
                flash("Event not found.", "error")
                return redirect(url_for("upload_page"))

            # save raw + parsed
            cur.execute(
                "INSERT INTO race_results (event_id, raw_html, parsed_json) VALUES (%s, %s, %s);",
                (int(event_id), html, json.dumps({"parsed": parsed, "scored": scored}))
            )

            # clear previous points for this event (re-upload allowed)
            cur.execute("DELETE FROM standings_points WHERE event_id=%s;", (int(event_id),))

            # insert points
            for r in scored:
                cur.execute("""
                    INSERT INTO standings_points (event_id, class_name, rider_name, points)
                    VALUES (%s, %s, %s, %s);
                """, (int(event_id), ev["class_name"], r["name"], r["points"]))

        conn.commit()

    flash(f"Uploaded results. Parsed {parsed['count']} riders.", "success")
    return redirect(url_for("event_page", event_id=int(event_id)))

# ------------------------------------------------------------
# Bot / external helper API endpoints (optional)
# ------------------------------------------------------------
def require_api_key():
    if not API_KEY:
        return True
    sent = request.headers.get("X-API-KEY", "")
    return sent == API_KEY

@app.get("/api/discord_stats")
def api_discord_stats():
    # simple endpoint your site footer can call
    return jsonify({"ok": True, "ts": datetime.now(timezone.utc).isoformat()})

@app.post("/api/events/create")
def api_create_event():
    if not require_api_key():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.get_json(force=True, silent=True) or {}
    mode = (data.get("mode") or "MX").strip()
    class_name = (data.get("class") or "450").strip()
    title = (data.get("title") or "Race").strip()
    season = int(data.get("season") or 1)
    track = (data.get("track") or "").strip()
    notes = (data.get("notes") or "").strip()
    start_time = data.get("start_time")  # ISO string optional

    dt = None
    if start_time:
        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except Exception:
            dt = None

    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO events (mode, class_name, title, season, track, notes, start_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *;
            """, (mode, class_name, title, season, track, notes, dt))
            ev = cur.fetchone()
        conn.commit()

    return jsonify({"ok": True, "event": ev})

# ------------------------------------------------------------
# Local dev
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
