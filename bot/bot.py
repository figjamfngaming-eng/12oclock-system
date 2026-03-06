import os
import asyncio
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands
import psycopg2
import psycopg2.extras

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")

GUILD_ID_INT = int(DISCORD_GUILD_ID) if DISCORD_GUILD_ID else None
RACE_DIRECTOR_ROLE_ID_INT = int(RACE_DIRECTOR_ROLE_ID) if RACE_DIRECTOR_ROLE_ID else None
ANNOUNCE_CHANNEL_ID_INT = int(RACE_ANNOUNCEMENTS_CHANNEL_ID) if RACE_ANNOUNCEMENTS_CHANNEL_ID else None


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

    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_id TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS discord_name TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS mxb_name TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS steam_id TEXT;")

    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS track TEXT;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS class_name TEXT;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS season TEXT DEFAULT 'S1';")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS round_number INTEGER DEFAULT 1;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS start_time TIMESTAMP NULL;")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';")
    db_exec("ALTER TABLE events ADD COLUMN IF NOT EXISTS created_by_discord_id TEXT;")

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

    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_event ON race_results(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_class ON race_results(class_name);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_season_round ON race_results(season, round_number);")


def is_race_director(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    if RACE_DIRECTOR_ROLE_ID_INT is None:
        return False
    return any(role.id == RACE_DIRECTOR_ROLE_ID_INT for role in member.roles)


async def announce(guild: Optional[discord.Guild], message: str):
    if not guild or not ANNOUNCE_CHANNEL_ID_INT:
        return
    channel = guild.get_channel(ANNOUNCE_CHANNEL_ID_INT)
    if channel:
        await channel.send(message)


class LeagueBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()
        if GUILD_ID_INT:
            guild_obj = discord.Object(id=GUILD_ID_INT)
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} guild commands to {GUILD_ID_INT}")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands")


bot = LeagueBot()


@bot.event
async def on_ready():
    print("====================================")
    print(f"BOT ONLINE: {bot.user} ({bot.user.id})")
    print("====================================")


@bot.tree.command(name="ping", description="Check if the bot is online")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! 12 O'Clock Boyz bot is online.", ephemeral=True)


@bot.tree.command(name="league_help", description="Show available league commands")
async def league_help(interaction: discord.Interaction):
    msg = (
        "🏁 **12 O'Clock Boyz League Commands**\n\n"
        "**Public**\n"
        "`/ping` — bot status\n"
        "`/register_mxb` — save MX Bikes name + Steam\n"
        "`/events` — list recent events\n"
        "`/standings` — show class standings\n"
        "`/rider` — rider profile lookup\n\n"
        "**Race Director**\n"
        "`/create_event`\n"
        "`/close_event`\n"
        "`/add_result`\n"
        "`/penalty`\n"
    )
    await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(name="register_mxb", description="Register or update your MX Bikes rider name")
@app_commands.describe(mxb_name="Your in-game MX Bikes name", steam_id="Optional Steam profile or Steam ID")
async def register_mxb(interaction: discord.Interaction, mxb_name: str, steam_id: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)

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
        (
            str(interaction.user.id),
            str(interaction.user),
            mxb_name.strip(),
            steam_id.strip() if steam_id else None,
        ),
    )

    await interaction.followup.send(
        f"✅ Registered MX Bikes name as **{mxb_name.strip()}**",
        ephemeral=True,
    )


@bot.tree.command(name="rider", description="Look up a rider profile")
@app_commands.describe(name="Discord name or MX Bikes rider name")
async def rider(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True)

    row = db_exec(
        """
        SELECT discord_id, discord_name, mxb_name, steam_id
        FROM users
        WHERE LOWER(COALESCE(discord_name, '')) = LOWER(%s)
           OR LOWER(COALESCE(mxb_name, '')) = LOWER(%s)
        LIMIT 1;
        """,
        (name.strip(), name.strip()),
        fetch="one",
    )

    if not row:
        await interaction.followup.send("Rider not found.", ephemeral=True)
        return

    msg = (
        f"👤 **Rider Profile**\n"
        f"**Discord:** {row.get('discord_name') or 'N/A'}\n"
        f"**MX Bikes Name:** {row.get('mxb_name') or 'N/A'}\n"
        f"**Steam ID:** {row.get('steam_id') or 'N/A'}\n"
        f"**Discord ID:** {row.get('discord_id') or 'N/A'}"
    )
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="create_event", description="Create a race event")
@app_commands.describe(
    name="Event name",
    track="Track name",
    class_name="450 / 250 / Open",
    season="Season name",
    round_number="Round number"
)
async def create_event(
    interaction: discord.Interaction,
    name: str,
    track: str,
    class_name: str,
    season: Optional[str] = DEFAULT_SEASON,
    round_number: Optional[int] = 1,
):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.followup.send("❌ Race Director role required.", ephemeral=True)
        return

    event = db_exec(
        """
        INSERT INTO events (name, track, class_name, season, round_number, status, created_by_discord_id)
        VALUES (%s, %s, %s, %s, %s, 'open', %s)
        RETURNING *;
        """,
        (
            name.strip(),
            track.strip(),
            class_name.strip(),
            (season or DEFAULT_SEASON).strip(),
            int(round_number or 1),
            str(interaction.user.id),
        ),
        fetch="one",
    )

    await interaction.followup.send(
        f"✅ Event created\n"
        f"**ID:** {event['id']}\n"
        f"**Name:** {event['name']}\n"
        f"**Track:** {event['track']}\n"
        f"**Class:** {event['class_name']}\n"
        f"**Season:** {event['season']}\n"
        f"**Round:** {event['round_number']}",
        ephemeral=True,
    )

    await announce(
        interaction.guild,
        f"🏁 **New Event Created**\n"
        f"**{event['name']}**\n"
        f"Track: **{event['track']}**\n"
        f"Class: **{event['class_name']}**\n"
        f"Season: **{event['season']}** | Round **{event['round_number']}**"
    )


@bot.tree.command(name="close_event", description="Close a race event")
@app_commands.describe(event_id="Event ID")
async def close_event(interaction: discord.Interaction, event_id: int):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.followup.send("❌ Race Director role required.", ephemeral=True)
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
        await interaction.followup.send("❌ Event not found.", ephemeral=True)
        return

    await interaction.followup.send(f"✅ Closed event **{row['name']}**", ephemeral=True)
    await announce(interaction.guild, f"🔒 **Event Closed**\n**{row['name']}** (ID {row['id']})")


@bot.tree.command(name="events", description="List recent events")
async def events(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    rows = db_exec(
        """
        SELECT id, name, track, class_name, season, round_number, status
        FROM events
        ORDER BY id DESC
        LIMIT 10;
        """,
        fetch="all",
    )

    if not rows:
        await interaction.followup.send("No events created yet.", ephemeral=True)
        return

    msg = "🏁 **Recent Events**\n\n"
    for e in rows:
        msg += (
            f"**ID {e['id']}** — {e['name']} | {e['track']} | "
            f"{e['class_name']} | {e['season']} R{e['round_number']} | {e['status']}\n"
        )

    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="add_result", description="Add a rider result to an event")
@app_commands.describe(
    event_id="Event ID",
    rider_name="MX Bikes rider name",
    position="Finish position",
    points="Points awarded",
    discord_id="Optional Discord ID",
    notes="Optional notes"
)
async def add_result(
    interaction: discord.Interaction,
    event_id: int,
    rider_name: str,
    position: int,
    points: int,
    discord_id: Optional[str] = None,
    notes: Optional[str] = None,
):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.followup.send("❌ Race Director role required.", ephemeral=True)
        return

    event = db_exec(
        "SELECT * FROM events WHERE id = %s LIMIT 1;",
        (event_id,),
        fetch="one",
    )

    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True)
        return

    db_exec(
        """
        INSERT INTO race_results (event_id, season, round_number, class_name, discord_id, rider_name, position, points, notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
        """,
        (
            event["id"],
            event["season"],
            event["round_number"],
            event["class_name"],
            discord_id.strip() if discord_id else None,
            rider_name.strip(),
            int(position),
            int(points),
            notes.strip() if notes else None,
        ),
    )

    await interaction.followup.send(
        f"✅ Added result\n"
        f"**Rider:** {rider_name}\n"
        f"**Position:** {position}\n"
        f"**Points:** {points}\n"
        f"**Event:** {event['name']}",
        ephemeral=True,
    )


@bot.tree.command(name="penalty", description="Apply a points penalty")
@app_commands.describe(
    rider_name="Rider name",
    class_name="450 / 250 / Open",
    points="Points to remove",
    season="Season name",
    round_number="Round number",
    notes="Penalty reason"
)
async def penalty(
    interaction: discord.Interaction,
    rider_name: str,
    class_name: str,
    points: int,
    season: Optional[str] = DEFAULT_SEASON,
    round_number: Optional[int] = 1,
    notes: Optional[str] = None,
):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member) or not is_race_director(interaction.user):
        await interaction.followup.send("❌ Race Director role required.", ephemeral=True)
        return

    row = db_exec(
        """
        UPDATE race_results
        SET penalty_points = COALESCE(penalty_points, 0) + %s,
            notes = COALESCE(notes, '') || %s
        WHERE id = (
            SELECT id
            FROM race_results
            WHERE rider_name = %s
              AND class_name = %s
              AND season = %s
              AND round_number = %s
            ORDER BY created_at DESC
            LIMIT 1
        )
        RETURNING *;
        """,
        (
            int(points),
            f" | PENALTY: {notes}" if notes else " | PENALTY APPLIED",
            rider_name.strip(),
            class_name.strip(),
            (season or DEFAULT_SEASON).strip(),
            int(round_number or 1),
        ),
        fetch="one",
    )

    if not row:
        await interaction.followup.send("❌ Rider result not found.", ephemeral=True)
        return

    await interaction.followup.send(
        f"⚠️ Penalty applied to **{rider_name}**\nRemoved: **{points} pts**",
        ephemeral=True,
    )


@bot.tree.command(name="standings", description="Show standings by class")
@app_commands.describe(class_name="450 / 250 / Open", season="Season name")
async def standings(interaction: discord.Interaction, class_name: str, season: Optional[str] = DEFAULT_SEASON):
    await interaction.response.defer(ephemeral=True)

    rows = db_exec(
        """
        SELECT
            rider_name,
            SUM(COALESCE(points, 0) - COALESCE(penalty_points, 0))::int AS total_points
        FROM race_results
        WHERE class_name = %s
          AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC
        LIMIT 20;
        """,
        (class_name.strip(), (season or DEFAULT_SEASON).strip()),
        fetch="all",
    )

    if not rows:
        await interaction.followup.send(
            f"No standings yet for **{class_name}** in **{season or DEFAULT_SEASON}**.",
            ephemeral=True,
        )
        return

    msg = f"🏆 **{class_name} Standings** — {season or DEFAULT_SEASON}\n\n"
    for i, row in enumerate(rows, start=1):
        msg += f"**{i}.** {row['rider_name']} — **{row['total_points']} pts**\n"

    await interaction.followup.send(msg, ephemeral=True)


async def main():
    backoff = 5
    while True:
        try:
            await bot.start(DISCORD_BOT_TOKEN)
        except discord.HTTPException as e:
            print(f"Discord HTTPException: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            print(f"Bot crashed: {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
