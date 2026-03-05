import os
import time

import psycopg2
import psycopg2.extras
import requests
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "").strip()

if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")

def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

_stats_cache = {"ts": 0, "data": {"discord_members": None}}
CACHE_SECONDS = 60

@app.get("/")
def index():
    return render_template("index.html", invite_url=DISCORD_INVITE_URL)

@app.get("/events")
def events():
    season = request.args.get("season", "").strip()
    mode = request.args.get("mode", "").strip().upper()
    class_name = request.args.get("class", "").strip().upper()

    where = []
    params = []

    if season:
        where.append("e.season = %s")
        params.append(season)
    if mode in ("MX", "SX"):
        where.append("e.mode = %s")
        params.append(mode)
    if class_name in ("450", "250", "250-2T"):
        where.append("e.class_name = %s")
        params.append(class_name)

    sql = "SELECT e.* FROM events e"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY e.start_time DESC LIMIT 100"

    with db_conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return render_template("events.html", rows=rows, invite_url=DISCORD_INVITE_URL)

@app.get("/standings")
def standings():
    season = request.args.get("season", "").strip() or "Season 1"
    mode = request.args.get("mode", "").strip().upper() or "MX"
    class_name = request.args.get("class", "").strip().upper() or "450"

    if mode not in ("MX", "SX"):
        mode = "MX"
    if class_name not in ("450", "250", "250-2T"):
        class_name = "450"

    with db_conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT r.display_name,
                       SUM(res.points) AS total_points,
                       COUNT(*) AS races
                FROM results res
                JOIN events e ON e.id = res.event_id
                JOIN racers r ON r.discord_id = res.racer_discord_id
                WHERE e.season = %s AND e.mode = %s AND e.class_name = %s
                GROUP BY r.display_name
                ORDER BY total_points DESC, races DESC, r.display_name ASC
                """,
                (season, mode, class_name),
            )
            rows = cur.fetchall()

    return render_template(
        "standings.html",
        rows=rows,
        season=season,
        mode=mode,
        class_name=class_name,
        invite_url=DISCORD_INVITE_URL,
    )

@app.get("/api/discord_stats")
def discord_stats():
    now = time.time()
    if now - _stats_cache["ts"] < CACHE_SECONDS:
        return jsonify(_stats_cache["data"])

    data = {"discord_members": None}

    if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID:
        try:
            resp = requests.get(
                f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}?with_counts=true",
                headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
                timeout=8,
            )
            if resp.ok:
                j = resp.json()
                data["discord_members"] = j.get("approximate_member_count")
        except Exception:
            pass

    _stats_cache["ts"] = now
    _stats_cache["data"] = data
    return jsonify(data)

@app.get("/health")
def health():
    return "ok", 200
