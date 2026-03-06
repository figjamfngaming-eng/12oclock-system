import os
import asyncio
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import psycopg2
import psycopg2.extras


# =========================
# ENV
# =========================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")
if not DISCORD_GUILD_ID:
    raise RuntimeError("Missing DISCORD_GUILD_ID env var")

GUILD_ID_INT = int(DISCORD_GUILD_ID)
RACE_DIRECTOR_ROLE_ID_INT = int(RACE_DIRECTOR_ROLE_ID) if RACE_DIRECTOR_ROLE_ID else None
RACE_ANNOUNCEMENTS_CHANNEL_ID_INT = int(RACE_ANNOUNCEMENTS_CHANNEL_ID) if RACE_ANNOUNCEMENTS_CHANNEL_ID else None


# =========================
# DISCORD SETUP
# =========================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
GUILD_OBJ = discord.Object(id=GUILD_ID_INT)


# =========================
# DATABASE
# =========================
def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def db_exec(sql: str, params=None, fetch: str = "none"):
    with db_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params or ())
            if fetch == "one":
                row = cur.fetchone()
                conn.commit()
                return row
            if fetch == "all":
                rows = cur.fetchall()
                conn.commit()
                return rows
            conn.commit()
            return None


def init_db():
    db_exec(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            discord_name TEXT,
            mxb_name TEXT,
            steam_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    db_exec(
        """
        CREATE TABLE IF NOT EXISTS events (
            id SERIAL PRIMARY KEY,
            name TEXT,
            track TEXT,
            class_name TEXT,
            season TEXT DEFAULT 'S1',
            round_number INTEGER DEFAULT 1,
            start_time TIMESTAMP NULL,
            status TEXT DEFAULT 'open',
            created_by_discord_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    db_exec(
        """
        CREATE TABLE IF NOT EXISTS race_results (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            season TEXT DEFAULT 'S1',
            round_number INTEGER DEFAULT 1,
            class_name TEXT DEFAULT '450',
            discord_id TEXT,
            rider_name TEXT,
            position INTEGER,
            points INTEGER DEFAULT 0,
            penalty_points INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Safe upgrades for old schemas
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_id TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_name TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS mxb_name TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS steam_id TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS name TEXT;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS track TEXT;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS class_name TEXT;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS season TEXT DEFAULT 'S1';")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS round_number INTEGER DEFAULT 1;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS start_time TIMESTAMP NULL;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS created_by_discord_id TEXT;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS event_id INTEGER;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS season TEXT DEFAULT 'S1';")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS round_number INTEGER DEFAULT 1;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS class_name TEXT DEFAULT '450';")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS discord_id TEXT;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS rider_name TEXT;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS position INTEGER;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS points INTEGER DEFAULT 0;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS penalty_points INTEGER DEFAULT 0;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS notes TEXT;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;")

    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_event ON race_results(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_class ON race_results(class_name);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_season_round ON race_results(season, round_number);")


# =========================
# HELPERS
# =========================
def is_race_director(interaction: discord.Interaction) -> bool:
    if not RACE_DIRECTOR_ROLE_ID_INT:
        return False
    if not interaction.user or not isinstance(interaction.user, discord.Member):
        return False
    return any(role.id == RACE_DIRECTOR_ROLE_ID_INT for role in interaction.user.roles)


def ama_points(position: int) -> int:
    table = {
        1: 26, 2: 23, 3: 21, 4: 19, 5: 18, 6: 17, 7: 16, 8: 15, 9: 14, 10: 13,
        11: 12, 12: 11, 13: 10, 14: 9, 15: 8, 16: 7, 17: 6, 18: 5, 19: 4, 20: 3,
        21: 2, 22: 1,
    }
    return table.get(position, 0)


def fmt_event_line(event) -> str:
    start_str = "TBA"
    if event.get("start_time"):
        try:
            start_str = event["start_time"].strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            start_str = str(event["start_time"])
    return (
        f"**ID {event['id']}** — {event.get('name') or 'Unnamed Event'} | "
        f"{event.get('track') or 'Unknown Track'} | "
        f"{event.get('class_name') or 'Unknown Class'} | "
        f"{event.get('season') or DEFAULT_SEASON} Round {event.get('round_number') or 1} | "
        f"{event.get('status') or 'open'} | {start_str}"
    )


async def announce_event_created(event_row):
    if not RACE_ANNOUNCEMENTS_CHANNEL_ID_INT:
        return
    channel = bot.get_channel(RACE_ANNOUNCEMENTS_CHANNEL_ID_INT)
    if channel is None:
        try:
            channel = await bot.fetch_channel(RACE_ANNOUNCEMENTS_CHANNEL_ID_INT)
        except Exception:
            return
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return

    embed = discord.Embed(
        title=f"{event_row.get('class_name', 'MX')} — {event_row.get('name', 'Event Created')}",
        color=discord.Color.blurple(),
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Track", value=event_row.get("track") or "TBA", inline=True)
    embed.add_field(name="Season", value=event_row.get("season") or DEFAULT_SEASON, inline=True)
    embed.add_field(name="Round", value=str(event_row.get("round_number") or 1), inline=True)
    embed.add_field(name="Status", value=event_row.get("status") or "open", inline=True)
    if event_row.get("start_time"):
        embed.add_field(name="Start", value=str(event_row["start_time"]), inline=False)
    embed.set_footer(text=f"Event ID: {event_row['id']}")
    await channel.send(embed=embed)


async def announce_results(event_row, result_rows):
    if not RACE_ANNOUNCEMENTS_CHANNEL_ID_INT:
        return
    channel = bot.get_channel(RACE_ANNOUNCEMENTS_CHANNEL_ID_INT)
    if channel is None:
        try:
            channel = await bot.fetch_channel(RACE_ANNOUNCEMENTS_CHANNEL_ID_INT)
        except Exception:
            return
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return

    lines = []
    for r in result_rows[:10]:
        total = int((r.get("points") or 0) - (r.get("penalty_points") or 0))
        lines.append(
            f"**P{r.get('position') or '-'}** {r.get('rider_name') or 'Unknown'} — "
            f"{total} pts"
        )
    if not lines:
        lines = ["No results entered yet."]

    embed = discord.Embed(
        title=f"Results — {event_row.get('name') or 'Event'}",
        description="\n".join(lines),
        color=discord.Color.green(),
        timestamp=datetime.utcnow(),
    )
    embed.add_field(name="Track", value=event_row.get("track") or "TBA", inline=True)
    embed.add_field(name="Class", value=event_row.get("class_name") or "TBA", inline=True)
    embed.add_field(name="Round", value=str(event_row.get("round_number") or 1), inline=True)
    embed.set_footer(text=f"Event ID: {event_row['id']}")
    await channel.send(embed=embed)


async def sync_commands():
    try:
        synced = await bot.tree.sync(guild=GUILD_OBJ)
        print(f"Synced {len(synced)} guild commands")
    except Exception as e:
        print(f"Command sync failed: {e}")


# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    print(f"BOT ONLINE: {bot.user} ({bot.user.id})")
    init_db()
    await sync_commands()


# =========================
# COMMANDS
# =========================
@bot.tree.command(name="ping", description="Check if the bot is online", guild=GUILD_OBJ)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong. Bot is online.", ephemeral=True)


@bot.tree.command(name="league_help", description="Show league commands", guild=GUILD_OBJ)
async def league_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="12 O'Clock Boyz — League Commands",
        color=discord.Color.blurple()
    )
    embed.description = (
        "`/ping` — bot status\n"
        "`/register_mxb` — set your MX Bikes name + Steam ID\n"
        "`/rider` — view a rider profile\n"
        "`/events` — list events\n"
        "`/standings` — show standings by class\n"
        "`/create_event` — race director only\n"
        "`/close_event` — race director only\n"
        "`/add_result` — race director only\n"
        "`/penalty` — race director only\n"
        "`/post_results` — race director only\n"
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="register_mxb", description="Register your MX Bikes rider name", guild=GUILD_OBJ)
@app_commands.describe(
    mxb_name="Your in-game MX Bikes rider name",
    steam_id="Your Steam ID or Steam profile text"
)
async def register_mxb(interaction: discord.Interaction, mxb_name: str, steam_id: Optional[str] = None):
    discord_id = str(interaction.user.id)
    discord_name = interaction.user.display_name

    db_exec(
        """
        INSERT INTO users (discord_id, discord_name, mxb_name, steam_id)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (discord_id)
        DO UPDATE SET
            discord_name = EXCLUDED.discord_name,
            mxb_name = EXCLUDED.mxb_name,
            steam_id = EXCLUDED.steam_id;
        """,
        (discord_id, discord_name, mxb_name.strip(), (steam_id or "").strip() or None),
    )

    await interaction.response.send_message(
        f"Registered.\nMX Bikes name: **{mxb_name.strip()}**"
        + (f"\nSteam ID: **{steam_id.strip()}**" if steam_id and steam_id.strip() else ""),
        ephemeral=True
    )


@bot.tree.command(name="rider", description="View a rider profile", guild=GUILD_OBJ)
@app_commands.describe(member="Discord member to look up")
async def rider(interaction: discord.Interaction, member: discord.Member):
    row = db_exec(
        """
        SELECT discord_name, mxb_name, steam_id, created_at
        FROM users
        WHERE discord_id = %s
        LIMIT 1;
        """,
        (str(member.id),),
        fetch="one",
    )

    if not row:
        await interaction.response.send_message("That rider is not registered yet.", ephemeral=True)
        return

    embed = discord.Embed(title=f"Rider Profile — {member.display_name}", color=discord.Color.blurple())
    embed.add_field(name="Discord", value=row.get("discord_name") or member.display_name, inline=False)
    embed.add_field(name="MX Bikes", value=row.get("mxb_name") or "Not set", inline=False)
    embed.add_field(name="Steam", value=row.get("steam_id") or "Not set", inline=False)
    embed.set_footer(text="12 O'Clock Boyz League")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="create_event", description="Create a new race event", guild=GUILD_OBJ)
@app_commands.describe(
    name="Event name",
    track="Track name",
    class_name="450 / 250 / Open",
    round_number="Round number",
    season="Season code like S1"
)
async def create_event(
    interaction: discord.Interaction,
    name: str,
    track: str,
    class_name: str,
    round_number: int,
    season: Optional[str] = None
):
    if not is_race_director(interaction):
        await interaction.response.send_message("You need the Race Director role.", ephemeral=True)
        return

    season = (season or DEFAULT_SEASON).strip()
    row = db_exec(
        """
        INSERT INTO events (name, track, class_name, season, round_number, status, created_by_discord_id)
        VALUES (%s, %s, %s, %s, %s, 'open', %s)
        RETURNING *;
        """,
        (name.strip(), track.strip(), class_name.strip(), season, round_number, str(interaction.user.id)),
        fetch="one",
    )

    await interaction.response.send_message(
        f"Event created.\n{fmt_event_line(row)}",
        ephemeral=True
    )
    await announce_event_created(row)


@bot.tree.command(name="close_event", description="Close an event", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID")
async def close_event(interaction: discord.Interaction, event_id: int):
    if not is_race_director(interaction):
        await interaction.response.send_message("You need the Race Director role.", ephemeral=True)
        return

    row = db_exec(
        """
        UPDATE events
        SET status = 'closed'
        WHERE id = %s
        RETURNING *;
        """,
        (event_id,),
        fetch="one",
    )

    if not row:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    await interaction.response.send_message(f"Closed event ID {event_id}.", ephemeral=True)


@bot.tree.command(name="events", description="List recent events", guild=GUILD_OBJ)
@app_commands.describe(
    season="Season code like S1",
    class_name="450 / 250 / Open",
    status="open / closed"
)
async def events(
    interaction: discord.Interaction,
    season: Optional[str] = None,
    class_name: Optional[str] = None,
    status: Optional[str] = None
):
    sql = """
    SELECT id, name, track, class_name, season, round_number, start_time, status
    FROM events
    WHERE 1=1
    """
    params = []

    if season and season.strip():
        sql += " AND season = %s"
        params.append(season.strip())
    if class_name and class_name.strip():
        sql += " AND class_name = %s"
        params.append(class_name.strip())
    if status and status.strip():
        sql += " AND status = %s"
        params.append(status.strip())

    sql += " ORDER BY id DESC LIMIT 15;"

    rows = db_exec(sql, tuple(params), fetch="all")

    if not rows:
        await interaction.response.send_message("No events found.", ephemeral=True)
        return

    lines = [fmt_event_line(r) for r in rows]
    embed = discord.Embed(title="Recent Events", description="\n".join(lines[:10]), color=discord.Color.blurple())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="add_result", description="Add a race result", guild=GUILD_OBJ)
@app_commands.describe(
    event_id="Event ID",
    rider_name="MX Bikes rider name",
    position="Finish position",
    penalty_points="Penalty points if any",
    notes="Reason or notes"
)
async def add_result(
    interaction: discord.Interaction,
    event_id: int,
    rider_name: str,
    position: int,
    penalty_points: Optional[int] = 0,
    notes: Optional[str] = None
):
    if not is_race_director(interaction):
        await interaction.response.send_message("You need the Race Director role.", ephemeral=True)
        return

    event_row = db_exec(
        "SELECT * FROM events WHERE id = %s LIMIT 1;",
        (event_id,),
        fetch="one",
    )
    if not event_row:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    rider_user = db_exec(
        "SELECT * FROM users WHERE LOWER(mxb_name) = LOWER(%s) LIMIT 1;",
        (rider_name.strip(),),
        fetch="one",
    )

    points = ama_points(position)
    result_row = db_exec(
        """
        INSERT INTO race_results
        (event_id, season, round_number, class_name, discord_id, rider_name, position, points, penalty_points, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *;
        """,
        (
            event_row["id"],
            event_row["season"],
            event_row["round_number"],
            event_row["class_name"],
            rider_user["discord_id"] if rider_user else None,
            rider_name.strip(),
            position,
            points,
            penalty_points or 0,
            (notes or "").strip() or None,
        ),
        fetch="one",
    )

    total = int((result_row["points"] or 0) - (result_row["penalty_points"] or 0))
    await interaction.response.send_message(
        f"Result added.\n"
        f"Event: **{event_row['name']}**\n"
        f"Rider: **{rider_name.strip()}**\n"
        f"Position: **{position}**\n"
        f"AMA Points: **{points}**\n"
        f"Penalty: **{penalty_points or 0}**\n"
        f"Total Awarded: **{total}**",
        ephemeral=True
    )


@bot.tree.command(name="penalty", description="Apply or update a rider penalty", guild=GUILD_OBJ)
@app_commands.describe(
    result_id="Result ID",
    penalty_points="Penalty points to subtract",
    notes="Penalty reason"
)
async def penalty(
    interaction: discord.Interaction,
    result_id: int,
    penalty_points: int,
    notes: Optional[str] = None
):
    if not is_race_director(interaction):
        await interaction.response.send_message("You need the Race Director role.", ephemeral=True)
        return

    row = db_exec(
        """
        UPDATE race_results
        SET penalty_points = %s,
            notes = %s
        WHERE id = %s
        RETURNING *;
        """,
        (penalty_points, (notes or "").strip() or None, result_id),
        fetch="one",
    )

    if not row:
        await interaction.response.send_message("Result not found.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"Penalty updated for **{row['rider_name']}**.\n"
        f"Penalty points: **{penalty_points}**",
        ephemeral=True
    )


@bot.tree.command(name="standings", description="Show championship standings", guild=GUILD_OBJ)
@app_commands.describe(
    class_name="450 / 250 / Open",
    season="Season code like S1"
)
async def standings(
    interaction: discord.Interaction,
    class_name: str = "450",
    season: Optional[str] = None
):
    season = (season or DEFAULT_SEASON).strip()
    rows = db_exec(
        """
        SELECT
            rider_name,
            SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = %s
          AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC;
        """,
        (class_name.strip(), season),
        fetch="all",
    )

    if not rows:
        await interaction.response.send_message("No standings yet for that class/season.", ephemeral=True)
        return

    lines = []
    for i, row in enumerate(rows[:15], start=1):
        lines.append(f"**{i}.** {row['rider_name']} — **{row['total_points']} pts**")

    embed = discord.Embed(
        title=f"{class_name.strip()} Standings — {season}",
        description="\n".join(lines),
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="post_results", description="Post event results into race announcements", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID")
async def post_results(interaction: discord.Interaction, event_id: int):
    if not is_race_director(interaction):
        await interaction.response.send_message("You need the Race Director role.", ephemeral=True)
        return

    event_row = db_exec(
        "SELECT * FROM events WHERE id = %s LIMIT 1;",
        (event_id,),
        fetch="one",
    )
    if not event_row:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    results = db_exec(
        """
        SELECT rider_name, position, points, penalty_points
        FROM race_results
        WHERE event_id = %s
        ORDER BY position ASC NULLS LAST, rider_name ASC;
        """,
        (event_id,),
        fetch="all",
    )

    await announce_results(event_row, results)
    await interaction.response.send_message("Results posted to announcements.", ephemeral=True)


# =========================
# MAIN
# =========================
async def main():
    async with bot:
        await bot.start(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
