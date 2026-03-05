import os
import asyncio
from datetime import datetime
import discord
from discord import app_commands
from discord.ext import commands

from shared.db import init_db, exec1, q

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID") or os.getenv("DISCORD_GUILD_ID")
ANNOUNCE_CH = os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID")
RACE_DIRECTOR_ROLE_ID = os.getenv("RACE_DIRECTOR_ROLE_ID")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL","") or "").rstrip("/")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN")
if not ANNOUNCE_CH:
    raise RuntimeError("Missing RACE_ANNOUNCEMENTS_CHANNEL_ID")
if not RACE_DIRECTOR_ROLE_ID:
    raise RuntimeError("Missing RACE_DIRECTOR_ROLE_ID")

ANNOUNCE_CH = int(ANNOUNCE_CH)
RACE_DIRECTOR_ROLE_ID = int(RACE_DIRECTOR_ROLE_ID)

intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

def is_race_director(interaction: discord.Interaction) -> bool:
    if not interaction.user or not isinstance(interaction.user, discord.Member):
        return False
    return any(r.id == RACE_DIRECTOR_ROLE_ID for r in interaction.user.roles)

def require_director():
    async def predicate(interaction: discord.Interaction):
        if is_race_director(interaction):
            return True
        raise app_commands.CheckFailure("Race Director only.")
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    init_db()
    try:
        # Sync commands globally (can take time to appear). For instant, use guild sync by setting DISCORD_GUILD_ID.
        if os.getenv("DISCORD_GUILD_ID"):
            guild = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID")))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("Synced commands to guild.")
        else:
            await bot.tree.sync()
            print("Synced commands globally.")
    except Exception as e:
        print("Sync error:", e)

    print(f"Logged in as {bot.user}.")

@bot.tree.command(name="ping", description="Check if AMA League bot is online.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! AMA League bot online.", ephemeral=True)

@bot.tree.command(name="create_event", description="Create an event and announce it.")
@require_director()
@app_commands.describe(
    mode="MX / SX / ENDURO",
    race_class="450 / 250 / 250-2t etc",
    title="Event title",
    track="Track name",
    season="Season number",
    start="Start time (YYYY-MM-DD HH:MM) in your local time",
    notes="Extra notes"
)
async def create_event(
    interaction: discord.Interaction,
    mode: str,
    race_class: str,
    title: str,
    track: str,
    season: int = 1,
    start: str = "",
    notes: str = "",
):
    # Parse time
    if not start:
        start_dt = datetime.utcnow()
    else:
        try:
            start_dt = datetime.strptime(start.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return await interaction.response.send_message(
                "❌ Bad start format. Use: `YYYY-MM-DD HH:MM` (example `2026-03-10 20:00`)",
                ephemeral=True,
            )

    # store to DB
    row = exec1(
        """
        INSERT INTO events (season, mode, class, title, track, start_time, notes, created_by_discord_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        [season, mode.upper(), race_class, title, track, start_dt, notes, interaction.user.id],
    )
    event_id = row["id"]

    # announce embed
    ch = bot.get_channel(ANNOUNCE_CH)
    if not ch:
        ch = await bot.fetch_channel(ANNOUNCE_CH)

    e = discord.Embed(
        title=f"🏁 {row['mode']} {row['class']} — {row['title']}",
        description=(row.get("notes") or " "),
        color=0x7C5CFF,
    )
    e.add_field(name="Season", value=str(row["season"]), inline=True)
    e.add_field(name="Start", value=str(row["start_time"]), inline=True)
    e.add_field(name="Track", value=row["track"], inline=True)
    e.set_footer(text=f"Event ID: {event_id}")

    view = discord.ui.View()
    if PUBLIC_BASE_URL:
        view.add_item(discord.ui.Button(label="View on Website", url=f"{PUBLIC_BASE_URL}/events"))

    await ch.send(embed=e, view=view)
    await interaction.response.send_message(f"✅ Event created & announced. Event ID: {event_id}", ephemeral=True)

@bot.tree.command(name="penalty", description="Apply a points penalty/bonus to a rider.")
@require_director()
@app_commands.describe(discord_user="Discord user", points_delta="Negative to deduct points", reason="Reason")
async def penalty(interaction: discord.Interaction, discord_user: discord.Member, points_delta: int, reason: str):
    # Find user in DB by discord id
    u = q("SELECT * FROM users WHERE discord_id=%s", [discord_user.id])
    if not u:
        return await interaction.response.send_message("❌ That rider has not signed up on the website yet.", ephemeral=True)
    u = u[0]

    # optional event_id
    event_id = None
    try:
        last = q("SELECT id FROM events ORDER BY start_time DESC LIMIT 1")
        event_id = int(last[0]["id"]) if last else None
    except Exception:
        event_id = None

    exec1(
        """
        INSERT INTO penalties (event_id, user_id, points_delta, reason, issued_by_discord_id)
        VALUES (%s,%s,%s,%s,%s)
        """,
        [event_id, u["id"], points_delta, reason, interaction.user.id],
    )
    await interaction.response.send_message(
        f"✅ Penalty applied to **{discord_user.display_name}**: {points_delta} pts — {reason}",
        ephemeral=True,
    )

@bot.tree.command(name="sync", description="Force sync slash commands (Race Director).")
@require_director()
async def sync(interaction: discord.Interaction):
    try:
        if os.getenv("DISCORD_GUILD_ID"):
            guild = discord.Object(id=int(os.getenv("DISCORD_GUILD_ID")))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
        await interaction.response.send_message("✅ Commands synced.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Sync failed: {e}", ephemeral=True)

bot.run(TOKEN)

