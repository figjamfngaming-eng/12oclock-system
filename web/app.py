import os
from flask import Flask, render_template
from shared.db import query

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev")

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/events")
def event_page():
    events = query("SELECT * FROM events ORDER BY id DESC")
    return render_template("event.html", events=events)

@app.route("/schedule")
def schedule_page():
    return render_template("event.html")

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
