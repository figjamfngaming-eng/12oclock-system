from flask import Flask, render_template
import os
import psycopg2

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/events")
def event_page():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT name, track FROM events ORDER BY id DESC")
    events = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("events.html", events=events)

@app.route("/schedule")
def schedule_page():
    return render_template("schedule.html")

if __name__ == "__main__":
    app.run()
