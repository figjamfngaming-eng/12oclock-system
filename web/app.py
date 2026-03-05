from flask import Flask, render_template, request, redirect, url_for, jsonify
import os
from shared.db import init_db, q, exec1

app = Flask(__name__)
app.config["SECRET_KEY"] = "mxbleague"

# Initialize database on startup
init_db()


# -----------------------------
# HOME
# -----------------------------
@app.route("/")
def index():
    events = q("SELECT * FROM events ORDER BY id DESC")
    return render_template("index.html", events=events)


# -----------------------------
# SIGNUP
# -----------------------------
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        discord_id = request.form.get("discord_id")
        steam_id = request.form.get("steam_id")
        rider_name = request.form.get("rider_name")

        exec1(
            "INSERT INTO riders (discord_id, steam_id, rider_name) VALUES (?, ?, ?)",
            (discord_id, steam_id, rider_name),
        )

        return redirect(url_for("index"))

    return render_template("signup.html")


# -----------------------------
# EVENTS
# -----------------------------
@app.route("/events")
def events():
    events = q("SELECT * FROM events ORDER BY id DESC")
    return render_template("events.html", events=events)


# -----------------------------
# STANDINGS
# -----------------------------
@app.route("/standings")
def standings():
    standings = q(
        """
        SELECT rider_name, SUM(points) as total_points
        FROM results
        GROUP BY rider_name
        ORDER BY total_points DESC
        """
    )
    return render_template("standings.html", standings=standings)


# -----------------------------
# RESULTS UPLOAD
# -----------------------------
@app.route("/upload_results", methods=["POST"])
def upload_results():
    rider = request.form.get("rider")
    position = int(request.form.get("position"))

    ama_points = {
        1: 25,
        2: 22,
        3: 20,
        4: 18,
        5: 16,
        6: 15,
        7: 14,
        8: 13,
        9: 12,
        10: 11,
        11: 10,
        12: 9,
        13: 8,
        14: 7,
        15: 6,
        16: 5,
        17: 4,
        18: 3,
        19: 2,
        20: 1,
    }

    points = ama_points.get(position, 0)

    exec1(
        "INSERT INTO results (rider_name, position, points) VALUES (?, ?, ?)",
        (rider, position, points),
    )

    return jsonify({"status": "success"})


# -----------------------------
# DISCORD STATS API
# -----------------------------
@app.route("/api/discord_stats")
def discord_stats():
    riders = q("SELECT COUNT(*) as count FROM riders")[0]["count"]
    events = q("SELECT COUNT(*) as count FROM events")[0]["count"]

    return jsonify({
        "riders": riders,
        "events": events
    })


# -----------------------------
# START SERVER
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
