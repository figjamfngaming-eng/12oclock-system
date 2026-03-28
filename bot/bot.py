import os
import discord
from discord.ext import commands
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def points_for_position(position: int) -> int:
    table = {
        1: 26, 2: 23, 3: 21, 4: 19, 5: 18,
        6: 17, 7: 16, 8: 15, 9: 14, 10: 13,
        11: 12, 12: 11, 13: 10, 14: 9, 15: 8,
        16: 7, 17: 6, 18: 5, 19: 4, 20: 3,
        21: 2, 22: 1
    }
    return table.get(position, 0)


@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong! Bot is working")


@bot.command()
async def register(ctx, name: str, guid: str, cls: str):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO riders (discord_id, mxb_name, guid, class_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (discord_id) DO UPDATE SET
                        mxb_name = EXCLUDED.mxb_name,
                        guid = EXCLUDED.guid,
                        class_name = EXCLUDED.class_name
                """, (str(ctx.author.id), name, guid, cls))
                conn.commit()
        await ctx.send(f"✅ Registered {name} in class {cls}")
    except Exception as e:
        await ctx.send(f"❌ register failed: {e}")


@bot.command()
async def riders(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, mxb_name, class_name
                    FROM riders
                    ORDER BY id DESC
                    LIMIT 20
                """)
                rows = cur.fetchall()

        if not rows:
            await ctx.send("No riders found.")
            return

        msg = "\n".join([f"#{r['id']} - {r['mxb_name']} | {r['class_name']}" for r in rows])
        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ riders failed: {e}")


@bot.command()
async def create_event(ctx, *, args: str):
    try:
        parts = args.rsplit(" ", 1)
        if len(parts) != 2:
            await ctx.send("Usage: !create_event Event Name 450")
            return

        name, cls = parts[0], parts[1]

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO events (name, class_name, race_stage)
                    VALUES (%s, %s, 'qualifying')
                """, (name, cls))
                conn.commit()

        await ctx.send(f"✅ Event created: {name} ({cls})")
    except Exception as e:
        await ctx.send(f"❌ create_event failed: {e}")


@bot.command()
async def events(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, name, class_name, race_stage
                    FROM events
                    ORDER BY id DESC
                    LIMIT 20
                """)
                rows = cur.fetchall()

        if not rows:
            await ctx.send("No events found.")
            return

        msg = "\n".join([
            f"#{r['id']} - {r['name']} | {r['class_name']} | {r['race_stage']}"
            for r in rows
        ])
        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ events failed: {e}")


@bot.command()
async def join(ctx, event_id: int):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id FROM riders WHERE discord_id = %s", (str(ctx.author.id),))
                rider = cur.fetchone()

                if not rider:
                    await ctx.send("❌ Register first with !register")
                    return

                cur.execute("""
                    INSERT INTO registrations (event_id, rider_id)
                    VALUES (%s, %s)
                    ON CONFLICT (event_id, rider_id) DO NOTHING
                """, (event_id, rider["id"]))
                conn.commit()

        await ctx.send(f"✅ Joined event #{event_id}")
    except Exception as e:
        await ctx.send(f"❌ join failed: {e}")


@bot.command()
async def gate(ctx, event_id: int, rider_id: int, gate_order: int):
    try:
        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO gate_orders (event_id, rider_id, gate_order)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (event_id, rider_id) DO UPDATE SET
                        gate_order = EXCLUDED.gate_order
                """, (event_id, rider_id, gate_order))
                conn.commit()

        await ctx.send(f"✅ Gate saved: rider #{rider_id} -> gate {gate_order}")
    except Exception as e:
        await ctx.send(f"❌ gate failed: {e}")


@bot.command()
async def result(ctx, event_id: int, rider_id: int, position: int):
    try:
        pts = points_for_position(position)

        with db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO results (event_id, rider_id, position, points)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (event_id, rider_id) DO UPDATE SET
                        position = EXCLUDED.position,
                        points = EXCLUDED.points
                """, (event_id, rider_id, position, pts))
                conn.commit()

        await ctx.send(f"✅ Result saved | Rider #{rider_id} | Pos {position} | Points {pts}")
    except Exception as e:
        await ctx.send(f"❌ result failed: {e}")


@bot.command()
async def leaderboard(ctx):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT r.mxb_name, r.class_name, COALESCE(SUM(res.points), 0) AS pts
                    FROM riders r
                    LEFT JOIN results res ON r.id = res.rider_id
                    GROUP BY r.id, r.mxb_name, r.class_name
                    ORDER BY pts DESC, r.mxb_name ASC
                """)
                rows = cur.fetchall()

        if not rows:
            await ctx.send("No leaderboard data yet.")
            return

        msg = "\n".join([
            f"{i+1}. {r['mxb_name']} | {r['class_name']} | {r['pts']} pts"
            for i, r in enumerate(rows[:20])
        ])
        await ctx.send("🏆 Leaderboard\n" + msg)
    except Exception as e:
        await ctx.send(f"❌ leaderboard failed: {e}")


@bot.command()
async def advance(ctx, event_id: int):
    try:
        with db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT race_stage FROM events WHERE id = %s", (event_id,))
                event = cur.fetchone()

                if not event:
                    await ctx.send("❌ Event not found")
                    return

                current = event["race_stage"]
                if current == "qualifying":
                    new_stage = "heat1"
                elif current == "heat1":
                    new_stage = "heat2"
                elif current == "heat2":
                    new_stage = "final"
                else:
                    new_stage = "final"

                cur.execute("""
                    UPDATE events
                    SET race_stage = %s
                    WHERE id = %s
                """, (new_stage, event_id))
                conn.commit()

        await ctx.send(f"✅ Event #{event_id} moved to {new_stage}")
    except Exception as e:
        await ctx.send(f"❌ advance failed: {e}")


bot.run(TOKEN)
