import os
import asyncio
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
import psycopg2
import psycopg2.extras

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DEFAULT_SEASON = os.getenv("DEFAULT_SEASON", "S1")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
RACE_ANNOUNCEMENTS_CHANNEL_ID = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")
RULES_CHANNEL_ID = os.getenv("RULES_CHANNEL_ID")
SERVER_RULES_CHANNEL_ID = os.getenv("SERVER_RULES_CHANNEL_ID")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")
if not DISCORD_GUILD_ID:
    raise RuntimeError("Missing DISCORD_GUILD_ID env var")

GUILD_ID_INT = int(DISCORD_GUILD_ID)
RACE_DIRECTOR_ROLE_ID_INT = int(RACE_DIRECTOR_ROLE_ID) if RACE_DIRECTOR_ROLE_ID else None
RACE_ANNOUNCEMENTS_CHANNEL_ID_INT = int(RACE_ANNOUNCEMENTS_CHANNEL_ID) if RACE_ANNOUNCEMENTS_CHANNEL_ID else None
RULES_CHANNEL_ID_INT = int(RULES_CHANNEL_ID) if RULES_CHANNEL_ID else None
SERVER_RULES_CHANNEL_ID_INT = int(SERVER_RULES_CHANNEL_ID) if SERVER_RULES_CHANNEL_ID else None

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
    db_exec("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            discord_id TEXT UNIQUE,
            discord_name TEXT,
            mxb_name TEXT,
            steam_id TEXT,
            team_name TEXT,
            rider_number TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
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
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS race_results (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            season TEXT DEFAULT 'S1',
            round_number INTEGER DEFAULT 1,
            class_name TEXT DEFAULT '450',
            discord_id TEXT,
            rider_name TEXT,
            team_name TEXT,
            position INTEGER,
            points INTEGER DEFAULT 0,
            penalty_points INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS registrations (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            discord_id TEXT,
            rider_name TEXT,
            class_name TEXT,
            team_name TEXT,
            gate_pick INTEGER,
            status TEXT DEFAULT 'registered',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS protests (
            id SERIAL PRIMARY KEY,
            event_id INTEGER REFERENCES events(id) ON DELETE CASCADE,
            discord_id TEXT,
            rider_name TEXT,
            against_rider TEXT,
            reason TEXT,
            status TEXT DEFAULT 'open',
            admin_note TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("""
        CREATE TABLE IF NOT EXISTS bot_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS team_name TEXT;")
    db_exec("ALTER TABLE users ADD COLUMN IF NOT EXISTS rider_number TEXT;")
    db_exec("ALTER TABLE race_results ADD COLUMN IF NOT EXISTS team_name TEXT;")

    db_exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_event ON race_results(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_results_class ON race_results(class_name);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_reg_event ON registrations(event_id);")
    db_exec("CREATE INDEX IF NOT EXISTS idx_protest_event ON protests(event_id);")


def set_bot_state(key: str, value: Optional[str]):
    db_exec("""
        INSERT INTO bot_state (state_key, state_value, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (state_key)
        DO UPDATE SET
            state_value = EXCLUDED.state_value,
            updated_at = CURRENT_TIMESTAMP;
    """, (key, value))


def get_bot_state(key: str) -> Optional[str]:
    row = db_exec("""
        SELECT state_value
        FROM bot_state
        WHERE state_key = %s
        LIMIT 1;
    """, (key,), fetch="one")
    return row["state_value"] if row and row.get("state_value") is not None else None


# =========================
# HELPERS
# =========================
def ama_points(position: int) -> int:
    table = {
        1: 26, 2: 23, 3: 21, 4: 19, 5: 18, 6: 17, 7: 16, 8: 15, 9: 14, 10: 13,
        11: 12, 12: 11, 13: 10, 14: 9, 15: 8, 16: 7, 17: 6, 18: 5, 19: 4, 20: 3,
        21: 2, 22: 1,
    }
    return table.get(position, 0)


def is_race_director(interaction: discord.Interaction) -> bool:
    if not RACE_DIRECTOR_ROLE_ID_INT:
        return False
    if not isinstance(interaction.user, discord.Member):
        return False
    return any(role.id == RACE_DIRECTOR_ROLE_ID_INT for role in interaction.user.roles)


async def get_channel_safe(channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None
    return channel


async def announce(text: str):
    if not RACE_ANNOUNCEMENTS_CHANNEL_ID_INT:
        return
    channel = await get_channel_safe(RACE_ANNOUNCEMENTS_CHANNEL_ID_INT)
    if channel:
        await channel.send(text)


def build_race_rules_embed():
    embed = discord.Embed(
        title="🏁 12 O'Clock Boyz — Official Race Rules",
        description=(
            "These rules apply to all official MX Bikes events, race nights, "
            "championship rounds, and league sessions."
        ),
        color=discord.Color.gold()
    )
    embed.add_field(
        name="🏍 Clean Racing",
        value=(
            "• Race hard but fair\n"
            "• No intentional crashing\n"
            "• No dirty riding\n"
            "• Leave racing room when battling"
        ),
        inline=False
    )
    embed.add_field(
        name="🚫 Not Allowed",
        value=(
            "• Intentional takeouts\n"
            "• Brake checking\n"
            "• Blocking on purpose\n"
            "• Track cutting for advantage\n"
            "• Unsportsmanlike behaviour"
        ),
        inline=False
    )
    embed.add_field(
        name="🎮 Race Requirements",
        value=(
            "• Use your registered MX Bikes rider name\n"
            "• Join the correct event and class\n"
            "• Follow Race Director instructions"
        ),
        inline=False
    )
    embed.add_field(
        name="⚖️ Penalties",
        value=(
            "Breaking race rules may result in:\n"
            "• Warning\n"
            "• Position penalty\n"
            "• Points deduction\n"
            "• Disqualification"
        ),
        inline=False
    )
    embed.set_footer(text="12 O'Clock Boyz MX Bikes League")
    return embed


def build_server_rules_embed():
    embed = discord.Embed(
        title="💬 12 O'Clock Boyz — Official Server Rules",
        description=(
            "Welcome to the 12 O'Clock Boyz Discord.\n"
            "Respect the community, follow staff directions, and keep the server positive."
        ),
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="✅ Community Rules",
        value=(
            "• Respect everyone\n"
            "• No harassment or hate speech\n"
            "• No excessive toxicity\n"
            "• No spam\n"
            "• Use the correct channels"
        ),
        inline=False
    )
    embed.add_field(
        name="🚫 Not Allowed",
        value=(
            "• Racism or discrimination\n"
            "• Personal attacks\n"
            "• Unauthorized advertising\n"
            "• Starting drama in chat\n"
            "• Staff disrespect"
        ),
        inline=False
    )
    embed.add_field(
        name="⚖️ Staff & Enforcement",
        value=(
            "• Follow Admin and Race Director instructions\n"
            "• Staff decisions are final\n"
            "• Breaking rules can lead to warnings, mutes, kicks, or bans"
        ),
        inline=False
    )
    embed.set_footer(text="12 O'Clock Boyz Discord")
    return embed


async def replace_persistent_rules_message(channel_id: int, state_key: str, embed: discord.Embed):
    if not channel_id:
        return

    channel = await get_channel_safe(channel_id)
    if channel is None:
        return

    old_id = get_bot_state(state_key)
    if old_id:
        try:
            old_msg = await channel.fetch_message(int(old_id))
            await old_msg.delete()
        except Exception:
            pass

    new_msg = await channel.send(embed=embed)
    set_bot_state(state_key, str(new_msg.id))


async def repost_rules_messages():
    if RULES_CHANNEL_ID_INT:
        await replace_persistent_rules_message(
            RULES_CHANNEL_ID_INT,
            "race_rules_message_id",
            build_race_rules_embed()
        )

    if SERVER_RULES_CHANNEL_ID_INT:
        await replace_persistent_rules_message(
            SERVER_RULES_CHANNEL_ID_INT,
            "server_rules_message_id",
            build_server_rules_embed()
        )


@tasks.loop(hours=24)
async def auto_rules_repost_loop():
    await repost_rules_messages()


@auto_rules_repost_loop.before_loop
async def before_auto_rules_repost_loop():
    await bot.wait_until_ready()


# =========================
# BOT EVENTS
# =========================
@bot.event
async def on_ready():
    init_db()
    synced = await bot.tree.sync(guild=GUILD_OBJ)
    print(f"BOT ONLINE: {bot.user}")
    print(f"Synced {len(synced)} commands")

    if not auto_rules_repost_loop.is_running():
        auto_rules_repost_loop.start()

    await repost_rules_messages()


# =========================
# COMMANDS
# =========================
@bot.tree.command(name="ping", description="Check bot status", guild=GUILD_OBJ)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong. Bot online.", ephemeral=True)


@bot.tree.command(name="post_all_rules", description="Post fresh race rules and server rules now", guild=GUILD_OBJ)
async def post_all_rules(interaction: discord.Interaction):
    if not is_race_director(interaction):
        await interaction.response.send_message("Race Director only.", ephemeral=True)
        return

    await repost_rules_messages()
    await interaction.response.send_message("Posted fresh rules in both channels.", ephemeral=True)


@bot.tree.command(name="register_mxb", description="Register your MX Bikes info", guild=GUILD_OBJ)
@app_commands.describe(
    mxb_name="Your in-game name",
    steam_id="Steam ID or profile text",
    team_name="Optional team name",
    rider_number="Optional rider number"
)
async def register_mxb(
    interaction: discord.Interaction,
    mxb_name: str,
    steam_id: Optional[str] = None,
    team_name: Optional[str] = None,
    rider_number: Optional[str] = None
):
    db_exec("""
        INSERT INTO users (discord_id, discord_name, mxb_name, steam_id, team_name, rider_number)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (discord_id)
        DO UPDATE SET
            discord_name = EXCLUDED.discord_name,
            mxb_name = EXCLUDED.mxb_name,
            steam_id = EXCLUDED.steam_id,
            team_name = EXCLUDED.team_name,
            rider_number = EXCLUDED.rider_number;
    """, (
        str(interaction.user.id),
        interaction.user.display_name,
        mxb_name.strip(),
        (steam_id or "").strip() or None,
        (team_name or "").strip() or None,
        (rider_number or "").strip() or None,
    ))
    await interaction.response.send_message("Rider profile saved.", ephemeral=True)


@bot.tree.command(name="create_event", description="Create event", guild=GUILD_OBJ)
@app_commands.describe(name="Event name", track="Track", class_name="Class", round_number="Round")
async def create_event(
    interaction: discord.Interaction,
    name: str,
    track: str,
    class_name: str,
    round_number: int
):
    if not is_race_director(interaction):
        await interaction.response.send_message("Race Director only.", ephemeral=True)
        return

    row = db_exec("""
        INSERT INTO events (name, track, class_name, season, round_number, status, created_by_discord_id)
        VALUES (%s, %s, %s, %s, %s, 'open', %s)
        RETURNING *;
    """, (name, track, class_name, DEFAULT_SEASON, round_number, str(interaction.user.id)), fetch="one")

    await interaction.response.send_message(f"Created event ID {row['id']}: {row['name']}", ephemeral=True)
    await announce(f"🏁 Event Created — **{row['name']}** | {row['track']} | {row['class_name']} | Round {row['round_number']}")


@bot.tree.command(name="join_race", description="Register into an event", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID")
async def join_race(interaction: discord.Interaction, event_id: int):
    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    if not event:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return
    if event["status"] != "open":
        await interaction.response.send_message("Event registration is closed.", ephemeral=True)
        return

    rider = db_exec("SELECT * FROM users WHERE discord_id = %s LIMIT 1;", (str(interaction.user.id),), fetch="one")
    if not rider or not rider.get("mxb_name"):
        await interaction.response.send_message("Register first with /register_mxb", ephemeral=True)
        return

    existing = db_exec("""
        SELECT id FROM registrations
        WHERE event_id = %s AND discord_id = %s
        LIMIT 1;
    """, (event_id, str(interaction.user.id)), fetch="one")

    if existing:
        await interaction.response.send_message("You are already registered.", ephemeral=True)
        return

    db_exec("""
        INSERT INTO registrations (event_id, discord_id, rider_name, class_name, team_name, status)
        VALUES (%s, %s, %s, %s, %s, 'registered');
    """, (event_id, str(interaction.user.id), rider["mxb_name"], event["class_name"], rider.get("team_name")))

    await interaction.response.send_message(f"Registered for **{event['name']}**.", ephemeral=True)


@bot.tree.command(name="event_entries", description="Show event entries", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID")
async def event_entries(interaction: discord.Interaction, event_id: int):
    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    if not event:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    rows = db_exec("""
        SELECT rider_name, team_name, gate_pick, status
        FROM registrations
        WHERE event_id = %s
        ORDER BY gate_pick ASC NULLS LAST, rider_name ASC;
    """, (event_id,), fetch="all")

    if not rows:
        await interaction.response.send_message("No registrations yet.", ephemeral=True)
        return

    desc = []
    for i, r in enumerate(rows, start=1):
        gate = r["gate_pick"] if r["gate_pick"] else "-"
        team = r["team_name"] or "Independent"
        desc.append(f"**{i}.** {r['rider_name']} | {team} | Gate: {gate}")

    embed = discord.Embed(
        title=f"Entries — {event['name']}",
        description="\n".join(desc[:20]),
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="draw_gates", description="Randomize gate picks", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID")
async def draw_gates(interaction: discord.Interaction, event_id: int):
    if not is_race_director(interaction):
        await interaction.response.send_message("Race Director only.", ephemeral=True)
        return

    regs = db_exec("SELECT id, rider_name FROM registrations WHERE event_id = %s ORDER BY id ASC;", (event_id,), fetch="all")
    if not regs:
        await interaction.response.send_message("No registrations found.", ephemeral=True)
        return

    picks = list(range(1, len(regs) + 1))
    random.shuffle(picks)

    lines = []
    for reg, gate in zip(regs, picks):
        db_exec("UPDATE registrations SET gate_pick = %s WHERE id = %s;", (gate, reg["id"]))
        lines.append(f"**Gate {gate}** — {reg['rider_name']}")

    embed = discord.Embed(
        title=f"Gate Picks — Event {event_id}",
        description="\n".join(lines[:20]),
        color=discord.Color.gold()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await announce(f"🎲 Gate picks drawn for event **{event_id}**.")


@bot.tree.command(name="add_result", description="Add race result", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID", rider_name="Rider name", position="Position")
async def add_result(interaction: discord.Interaction, event_id: int, rider_name: str, position: int):
    if not is_race_director(interaction):
        await interaction.response.send_message("Race Director only.", ephemeral=True)
        return

    event = db_exec("SELECT * FROM events WHERE id = %s LIMIT 1;", (event_id,), fetch="one")
    if not event:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    rider = db_exec("SELECT * FROM users WHERE LOWER(mxb_name) = LOWER(%s) LIMIT 1;", (rider_name,), fetch="one")
    points = ama_points(position)

    db_exec("""
        INSERT INTO race_results
        (event_id, season, round_number, class_name, discord_id, rider_name, team_name, position, points)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """, (
        event["id"],
        event["season"],
        event["round_number"],
        event["class_name"],
        rider["discord_id"] if rider else None,
        rider_name,
        rider.get("team_name") if rider else None,
        position,
        points,
    ))

    await interaction.response.send_message(f"Added result for {rider_name} — P{position} — {points} pts", ephemeral=True)


@bot.tree.command(name="standings", description="Show rider standings", guild=GUILD_OBJ)
@app_commands.describe(class_name="Class")
async def standings(interaction: discord.Interaction, class_name: str = "450"):
    rows = db_exec("""
        SELECT rider_name,
               SUM(COALESCE(points,0) - COALESCE(penalty_points,0))::int AS total_points
        FROM race_results
        WHERE class_name = %s AND season = %s
        GROUP BY rider_name
        ORDER BY total_points DESC, rider_name ASC;
    """, (class_name, DEFAULT_SEASON), fetch="all")

    if not rows:
        await interaction.response.send_message("No standings yet.", ephemeral=True)
        return

    desc = [f"**{i}.** {r['rider_name']} — **{r['total_points']} pts**" for i, r in enumerate(rows[:15], start=1)]
    embed = discord.Embed(title=f"{class_name} Standings", description="\n".join(desc), color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="team_standings", description="Show team standings", guild=GUILD_OBJ)
async def team_standings(interaction: discord.Interaction):
    rows = db_exec("""
        SELECT COALESCE(team_name, 'Independent') AS team_name,
               SUM(COALESCE(points,0) - COALESCE(penalty_points,0))::int AS total_points
        FROM race_results
        WHERE season = %s
        GROUP BY COALESCE(team_name, 'Independent')
        ORDER BY total_points DESC, team_name ASC;
    """, (DEFAULT_SEASON,), fetch="all")

    if not rows:
        await interaction.response.send_message("No team standings yet.", ephemeral=True)
        return

    desc = [f"**{i}.** {r['team_name']} — **{r['total_points']} pts**" for i, r in enumerate(rows[:15], start=1)]
    embed = discord.Embed(title="Team Standings", description="\n".join(desc), color=discord.Color.purple())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="file_protest", description="File a protest", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID", against_rider="Rider being protested", reason="Reason")
async def file_protest(interaction: discord.Interaction, event_id: int, against_rider: str, reason: str):
    rider = db_exec("SELECT * FROM users WHERE discord_id = %s LIMIT 1;", (str(interaction.user.id),), fetch="one")
    if not rider or not rider.get("mxb_name"):
        await interaction.response.send_message("Register first with /register_mxb", ephemeral=True)
        return

    db_exec("""
        INSERT INTO protests (event_id, discord_id, rider_name, against_rider, reason, status)
        VALUES (%s, %s, %s, %s, %s, 'open');
    """, (event_id, str(interaction.user.id), rider["mxb_name"], against_rider, reason))

    await interaction.response.send_message("Protest filed.", ephemeral=True)


@bot.tree.command(name="close_event", description="Close an event", guild=GUILD_OBJ)
@app_commands.describe(event_id="Event ID")
async def close_event(interaction: discord.Interaction, event_id: int):
    if not is_race_director(interaction):
        await interaction.response.send_message("Race Director only.", ephemeral=True)
        return

    row = db_exec("UPDATE events SET status = 'closed' WHERE id = %s RETURNING *;", (event_id,), fetch="one")
    if not row:
        await interaction.response.send_message("Event not found.", ephemeral=True)
        return

    await interaction.response.send_message(f"Closed event {row['name']}.", ephemeral=True)
    await announce(f"🔒 Event Closed — **{row['name']}**")


async def main():
    async with bot:
        await bot.start(DISCORD_BOT_TOKEN)
if __name__ == "__main__":
    asyncio.run(main())
