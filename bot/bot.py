import os
import re
import asyncio
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras
import discord
from discord import app_commands

# ---------------------------
# ENV
# ---------------------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

RACE_ANNOUNCEMENTS_CHANNEL_ID = int(os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID", "0"))
ROLE_RACE_DIRECTOR_ID = int(os.getenv("ROLE_RACE_DIRECTOR_ID", "0"))

DEFAULT_TZ_OFFSET_HOURS = int(os.getenv("DEFAULT_TZ_OFFSET_HOURS", "11"))  # AU default +11

if not TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN env var")
if not GUILD_ID:
    raise RuntimeError("Missing DISCORD_GUILD_ID env var")
if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL env var")
if not RACE_ANNOUNCEMENTS_CHANNEL_ID:
    raise RuntimeError("Missing RACE_ANNOUNCEMENTS_CHANNEL_ID env var")
if not ROLE_RACE_DIRECTOR_ID:
    raise RuntimeError("Missing ROLE_RACE_DIRECTOR_ID env var")


# ---------------------------
# DB HELPERS
# ---------------------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


DDL = """
CREATE TABLE IF NOT EXISTS events (
  id SERIAL PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  mode TEXT NOT NULL CHECK (mode IN ('MX','SX')),
  class_name TEXT NOT NULL CHECK (class_name IN ('450','250','250-2T')),
  title TEXT NOT NULL,
  start_time TIMESTAMPTZ NOT NULL,
  season TEXT NOT NULL,
  track TEXT,
  notes TEXT,
  created_by_discord_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS racers (
  discord_id BIGINT PRIMARY KEY,
  display_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS results (
  id SERIAL PRIMARY KEY,
  event_id INT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  racer_discord_id BIGINT NOT NULL REFERENCES racers(discord_id) ON DELETE CASCADE,
  position INT NOT NULL CHECK (position >= 1 AND position <= 40),
  points INT NOT NULL CHECK (points >= 0 AND points <= 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(event_id, racer_discord_id),
  UNIQUE(event_id, position)
);

CREATE INDEX IF NOT EXISTS idx_results_event ON results(event_id);
CREATE INDEX IF NOT EXISTS idx_events_season ON events(season);
"""


def db_init():
    with db_conn() as con:
        with con.cursor() as cur:
            cur.execute(DDL)
        con.commit()


def parse_start_time(user_text: str) -> datetime:
    s = user_text.strip()
    s = re.sub(r"\b(\d{1,2})\s*(am|pm)\b", r"\1:00\2", s, flags=re.I)

    fmts = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M%p",
        "%b %d %I:%M%p",
        "%B %d %I:%M%p",
        "%d %b %I:%M%p",
        "%d %B %I:%M%p",
    ]

    dt = None
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            break
        except ValueError:
            continue

    if dt is None:
        raise ValueError("Could not parse time. Try: 2026-03-10 20:00 or Mar 10 8pm")

    if dt.year == 1900:
        dt = dt.replace(year=datetime.now().year)

    tz = timezone(timedelta(hours=DEFAULT_TZ_OFFSET_HOURS))
    return dt.replace(tzinfo=tz)


def has_role(member: discord.Member, role_id: int) -> bool:
    return any(r.id == role_id for r in member.roles)


# ---------------------------
# DISCORD BOT
# ---------------------------
intents = discord.Intents.none()
intents.guilds = True


class AMAClient(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await asyncio.to_thread(db_init)

        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print("✅ Slash commands synced to guild")


bot = AMAClient()


@bot.tree.command(name="ping", description="Check if AMA League bot is online.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! AMA League bot online.", ephemeral=True)


@bot.tree.command(name="create_event", description="(Race Director) Create a new MX/SX event and announce it.")
@app_commands.describe(
    mode="MX or SX",
    class_name="450 / 250 / 250-2T",
    title="Event title (e.g. Round 1)",
    start_time="Start time (e.g. 2026-03-10 20:00 OR Mar 10 8pm)",
    season="Season name (e.g. Season 1)",
    track="Track name (optional)",
    notes="Notes (optional)",
)
async def create_event(
    interaction: discord.Interaction,
    mode: str,
    class_name: str,
    title: str,
    start_time: str,
    season: str,
    track: str | None = None,
    notes: str | None = None,
):
    await interaction.response.defer(ephemeral=True)

    if not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("❌ This command must be used in the server.", ephemeral=True)
        return

    if not has_role(interaction.user, ROLE_RACE_DIRECTOR_ID):
        await interaction.followup.send("❌ Race Director only.", ephemeral=True)
        return

    m = mode.strip().upper()
    c = class_name.strip().upper()

    if m not in ("MX", "SX"):
        await interaction.followup.send("❌ mode must be MX or SX", ephemeral=True)
        return
    if c not in ("450", "250", "250-2T"):
        await interaction.followup.send("❌ class_name must be 450, 250, or 250-2T", ephemeral=True)
        return

    try:
        dt = parse_start_time(start_time)
    except Exception as e:
        await interaction.followup.send(f"❌ {e}", ephemeral=True)
        return

    creator_id = interaction.user.id
    row = None

    def _insert():
        nonlocal row
        with db_conn() as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO events (mode, class_name, title, start_time, season, track, notes, created_by_discord_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING *
                    """,
                    (m, c, title.strip(), dt, season.strip(), track, notes, creator_id),
                )
                row = cur.fetchone()
            con.commit()

    try:
        await asyncio.to_thread(_insert)
    except Exception as e:
        await interaction.followup.send(f"❌ Database error: {e}", ephemeral=True)
        return

    chan = interaction.guild.get_channel(RACE_ANNOUNCEMENTS_CHANNEL_ID)
    if chan:
        embed = discord.Embed(
            title=f"🏁 {m} {c} — {row['title']}",
            description=f"**Season:** {row['season']}\n**Start:** <t:{int(row['start_time'].timestamp())}:F>",
            color=0xB38BFF,
        )
        if row.get("track"):
            embed.add_field(name="Track", value=row["track"], inline=False)
        if row.get("notes"):
            embed.add_field(name="Notes", value=row["notes"], inline=False)
        embed.set_footer(text=f"Event ID: {row['id']}")
        try:
            await chan.send(embed=embed)
        except Exception:
            pass

    await interaction.followup.send(f"✅ Event created (ID **{row['id']}**) and announced.", ephemeral=True)


@bot.tree.command(name="add_result", description="(Race Director) Add a result for an event.")
@app_commands.describe(
    event_id="Event ID number",
    racer="Mention the racer",
    position="Finish position (1-40)",
    points="Points (0-100)",
)
async def add_result(interaction: discord.Interaction, event_id: int, racer: discord.Member, position: int, points: int):
    await interaction.response.defer(ephemeral=True)

    if not has_role(interaction.user, ROLE_RACE_DIRECTOR_ID):
        await interaction.followup.send("❌ Race Director only.", ephemeral=True)
        return

    if position < 1 or position > 40:
        await interaction.followup.send("❌ position must be 1-40", ephemeral=True)
        return
    if points < 0 or points > 100:
        await interaction.followup.send("❌ points must be 0-100", ephemeral=True)
        return

    def _upsert():
        with db_conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO racers (discord_id, display_name)
                    VALUES (%s,%s)
                    ON CONFLICT (discord_id) DO UPDATE SET display_name = EXCLUDED.display_name
                    """,
                    (racer.id, racer.display_name),
                )

                cur.execute(
                    """
                    INSERT INTO results (event_id, racer_discord_id, position, points)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT (event_id, racer_discord_id)
                    DO UPDATE SET position = EXCLUDED.position, points = EXCLUDED.points
                    """,
                    (event_id, racer.id, position, points),
                )
            con.commit()

    try:
        await asyncio.to_thread(_upsert)
    except Exception as e:
        await interaction.followup.send(f"❌ Database error: {e}", ephemeral=True)
        return

    await interaction.followup.send(
        f"✅ Saved result: Event **{event_id}** — {racer.mention} — P{position} ({points} pts)",
        ephemeral=True,
    )


@bot.tree.command(name="standings", description="Show standings for a season/mode/class.")
@app_commands.describe(season="Season name (e.g. Season 1)", mode="MX or SX", class_name="450 / 250 / 250-2T")
async def standings(interaction: discord.Interaction, season: str, mode: str, class_name: str):
    await interaction.response.defer(ephemeral=True)

    s = season.strip()
    m = mode.strip().upper()
    c = class_name.strip().upper()

    if m not in ("MX", "SX"):
        await interaction.followup.send("❌ mode must be MX or SX", ephemeral=True)
        return
    if c not in ("450", "250", "250-2T"):
        await interaction.followup.send("❌ class_name must be 450, 250, or 250-2T", ephemeral=True)
        return

    rows = []

    def _query():
        nonlocal rows
        with db_conn() as con:
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT r.display_name,
                           SUM(res.points) AS total_points,
                           COUNT(*) AS races
                    FROM results res
                    JOIN events e ON e.id = res.event_id
                    JOIN racers r ON r.discord_id = res.racer_discord_id
                    WHERE e.season = %s AND e.mode = %s AND e.class_name = %s
                    GROUP BY r.display_name
                    ORDER BY total_points DESC, races DESC, r.display_name ASC
                    LIMIT 15
                    """,
                    (s, m, c),
                )
                rows = cur.fetchall()

    try:
        await asyncio.to_thread(_query)
    except Exception as e:
        await interaction.followup.send(f"❌ Database error: {e}", ephemeral=True)
        return

    if not rows:
        await interaction.followup.send("No standings yet for that filter.", ephemeral=True)
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        lines.append(f"**{i}.** {row['display_name']} — **{row['total_points']}** pts ({row['races']} races)")

    embed = discord.Embed(title=f"📊 Standings — {s} — {m} {c}", description="\n".join(lines), color=0xFFD15A)
    await interaction.followup.send(embed=embed, ephemeral=True)


bot.run(TOKEN)
