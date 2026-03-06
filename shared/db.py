import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    # Don't crash import-time (so pages can still load), but DB ops will fail with clear error.
    DATABASE_URL = None


@contextmanager
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL env var")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        yield conn
    finally:
        conn.close()


def exec_sql(sql: str, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
        conn.commit()


def q(sql: str, params=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
        return rows


def init_db():
    """
    Creates tables we need if they don't exist.
    Safe to call repeatedly.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        discord_id TEXT UNIQUE,
        discord_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS race_results (
        id SERIAL PRIMARY KEY,
        season TEXT NOT NULL DEFAULT 'S1',
        round INTEGER NOT NULL DEFAULT 1,
        class_name TEXT NOT NULL DEFAULT '450',
        discord_id TEXT,
        rider_name TEXT,
        points INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_race_results_season_round
      ON race_results (season, round);

    CREATE INDEX IF NOT EXISTS idx_race_results_class
      ON race_results (class_name);
    """
    exec_sql(ddl)
