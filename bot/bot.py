import os
import discord
from discord.ext import commands
import psycopg2

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


DATABASE
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


@bot.event
async def on_ready():
    print("====================================")
    print(f"BOT ONLINE: {bot.user}")
    print("====================================")


@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")


@bot.command()
async def create_event(ctx, name: str, track: str, class_name: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO events (name, track, class_name)
        VALUES (%s, %s, %s)
        """,
        (name, track, class_name)
    )

    conn.commit()
    cur.close()
    conn.close()

    await ctx.send(f"✅ Event created: {name}")


@bot.command()
async def standings(ctx, class_name: str = "450"):

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT rider_name,
        SUM(points - penalty_points) as total_points
        FROM race_results
        WHERE class_name=%s
        GROUP BY rider_name
        ORDER BY total_points DESC
        LIMIT 10
        """,
        (class_name,)
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:
        await ctx.send("No standings yet.")
        return

    msg = f"🏁 {class_name} Standings\n"

    for i, r in enumerate(rows, start=1):
        msg += f"{i}. {r[0]} — {r[1]} pts\n"

    await ctx.send(msg)


bot.run(TOKEN)
