import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        discord_id TEXT UNIQUE,
        username TEXT,
        mx_name TEXT,
        steam_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id SERIAL PRIMARY KEY,
        name TEXT,
        track TEXT,
        class TEXT,
        date TIMESTAMP
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS results (
        id SERIAL PRIMARY KEY,
        event_id INTEGER,
        user_id INTEGER,
        position INTEGER,
        points INTEGER
    );
    """)

    conn.commit()
    cur.close()
    conn.close()


def exec1(query, params=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    conn.commit()
    cur.close()
    conn.close()


def q(query, params=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result
