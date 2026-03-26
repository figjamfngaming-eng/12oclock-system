import os
import json
import argparse
from datetime import datetime, timedelta, timezone
import psycopg2
import psycopg2.extras

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")


def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_exec(sql: str, params=None):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            conn.commit()


def set_bot_state(key: str, value: str):
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db_exec(
        """
        INSERT INTO bot_state (state_key, state_value, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (state_key)
        DO UPDATE SET state_value = EXCLUDED.state_value, updated_at = CURRENT_TIMESTAMP;
        """,
        (key, value),
    )


parser = argparse.ArgumentParser(description="Update 12 O'Clock Boyz live server state")
parser.add_argument("--series", required=True, choices=["mxgp", "smx"])
parser.add_argument("--mode", required=True, choices=["practice", "qualifiers", "finals"])
parser.add_argument("--current-track", required=True)
parser.add_argument("--next-track", default="")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--status", default="online", choices=["online", "offline"])
parser.add_argument("--rotation-hours", type=int, default=3)
args = parser.parse_args()

next_rotation_at = (datetime.now(timezone.utc) + timedelta(hours=args.rotation_hours)).isoformat()
payload = {
    "series": args.series,
    "mode": args.mode,
    "current_track": args.current_track,
    "next_track": args.next_track or None,
    "port": args.port,
    "status": args.status,
    "rotation_hours": args.rotation_hours,
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "next_rotation_at": next_rotation_at,
}
set_bot_state(f"server:{args.series}", json.dumps(payload))
print(json.dumps(payload, indent=2))
