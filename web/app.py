import os
from flask import Flask, render_template, send_from_directory

app = Flask(
    __name__,
    static_folder="static",
    template_folder="templates"
)

app.secret_key = os.getenv("SECRET_KEY", "change-this")

DISCORD_INVITE_URL = os.getenv("DISCORD_INVITE_URL", "https://discord.gg/YOUR_LINK_HERE")


@app.context_processor
def inject_globals():
    return {
        "discord_invite_url": DISCORD_INVITE_URL,
        "logged_in_user": None
    }


@app.route("/")
def home():
    riders_total = 120
    events_total = 15

    latest_events = [
        {
            "id": 1,
            "name": "Round 1",
            "series": "MXGP",
            "class_name": "450",
            "race_stage": "Finished"
        },
        {
            "id": 2,
            "name": "Round 2",
            "series": "SMX",
            "class_name": "250F",
            "race_stage": "Upcoming"
        }
    ]

    return render_template(
        "index.html",
        riders_total=riders_total,
        events_total=events_total,
        latest_events=latest_events
    )


@app.route("/rules")
def rules():
    return render_template("rules.html")


@app.route("/signup")
def signup():
    return render_template("signup.html")


@app.route("/login")
def login():
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    user = {
        "username": "Devo",
        "discord_username": "12 O'Clock Devo",
        "email": "devo@example.com",
        "verified_discord": True
    }

    link_data = {
        "approved": True,
        "link_status": "approved",
        "steam_id": "76561198000000000"
    }

    rider_data = {
        "mxb_name": "Devo27",
        "class_name": "450",
        "guid": "GUID-SAVED",
        "is_linked": True,
        "approved": True
    }

    one_w_roles = [
        {
            "series": "MXGP",
            "class_name": "450",
            "pts": 26
        }
    ]

    next_event = {
        "name": "Round 3",
        "series": "MXGP",
        "class_name": "450",
        "race_stage": "qualifying",
        "queue_open": False,
        "status": "pending"
    }

    recent_events = [
        {
            "id": 1,
            "name": "Round 1",
            "series": "MXGP",
            "class_name": "450",
            "race_stage": "Finished",
            "status": "finished"
        },
        {
            "id": 2,
            "name": "Round 2",
            "series": "SMX",
            "class_name": "250F",
            "race_stage": "Upcoming",
            "status": "pending"
        }
    ]

    return render_template(
        "dashboard.html",
        user=user,
        link_data=link_data,
        rider_data=rider_data,
        one_w_roles=one_w_roles,
        next_event=next_event,
        recent_events=recent_events
    )


@app.route("/link-accounts")
def link_accounts():
    link_data = {
        "approved": True,
        "link_status": "approved",
        "discord_id": "123456789012345678",
        "discord_username": "12 O'Clock Devo",
        "steam_id": "76561198000000000",
        "steam_name": "DevoSteam"
    }

    rider_data = {
        "mxb_name": "Devo27",
        "class_name": "450",
        "guid": "GUID-SAVED"
    }

    return render_template(
        "link_accounts.html",
        link_data=link_data,
        rider_data=rider_data,
        oauth_discord_id="123456789012345678",
        oauth_discord_username="12 O'Clock Devo"
    )


@app.route("/events")
def events():
    events_list = [
        {
            "id": 1,
            "name": "Round 1",
            "series": "MXGP",
            "class_name": "450",
            "race_stage": "Finished",
            "status": "finished"
        },
        {
            "id": 2,
            "name": "Round 2",
            "series": "SMX",
            "class_name": "250F",
            "race_stage": "Upcoming",
            "status": "pending"
        }
    ]

    return render_template("events.html", events=events_list)


@app.route("/leaderboard")
def leaderboard():
    rows = [
        {"mxb_name": "Devo27", "class_name": "450", "pts": 26},
        {"mxb_name": "Sync", "class_name": "450", "pts": 23},
        {"mxb_name": "Rider3", "class_name": "250F", "pts": 21},
    ]

    return render_template("leaderboard.html", rows=rows)


@app.route("/event/<int:event_id>")
def event_detail(event_id: int):
    event = {
        "id": event_id,
        "name": f"Event {event_id}",
        "series": "MXGP",
        "class_name": "450",
        "race_stage": "Live",
        "status": "queue_open",
        "queue_open": True
    }

    results = [
        {"position": 1, "mxb_name": "Devo27", "points": 26},
        {"position": 2, "mxb_name": "Sync", "points": 23},
    ]

    gates = [
        {"gate_order": 1, "mxb_name": "Devo27"},
        {"gate_order": 2, "mxb_name": "Sync"},
    ]

    return render_template(
        "event.html",
        event=event,
        results=results,
        gates=gates
    )


@app.route("/director")
def director():
    events_list = [
        {
            "id": 1,
            "name": "Round 1",
            "series": "MXGP",
            "class_name": "450",
            "race_stage": "Finished",
            "status": "finished",
            "queue_open": False
        },
        {
            "id": 2,
            "name": "Round 2",
            "series": "SMX",
            "class_name": "250F",
            "race_stage": "qualifying",
            "status": "pending",
            "queue_open": False
        }
    ]

    return render_template("director.html", events=events_list)


@app.route("/live")
def live():
    return render_template("live.html")


@app.route("/api/live")
def api_live():
    return [
        {"event_id": 1, "mxb_name": "Devo27", "gate_order": 1},
        {"event_id": 1, "mxb_name": "Sync", "gate_order": 2},
    ]


@app.route("/status")
def status():
    return {"status": "ok"}


# Google Search Console verification file
@app.route("/google1417f573d8738bb4.html")
def google_verify():
    return send_from_directory(app.static_folder, "google1417f573d8738bb4.html")


# Optional direct static fallback
@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
