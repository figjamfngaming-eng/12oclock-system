import os
import sys
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

# ---- make repo root importable so `shared` works even if Render Root Directory = bot
BOT_DIR = os.path.dirname(os.path.abspath(__file__))           # .../bot
REPO_ROOT = os.path.abspath(os.path.join(BOT_DIR, ".."))       # .../
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from shared.db import init_db, exec1, q  # noqa: E402

# Accept BOTH env var names so you don’t get stuck
TOKEN = (os.getenv("DISCORD_BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = (os.getenv("DISCORD_GUILD_ID") or "").strip()
ANNOUNCE_CH = (os.getenv("RACE_ANNOUNCEMENTS_CHANNEL_ID") or "").strip()
RACE_DIRECTOR_ROLE_ID = (os.getenv("RACE_DIRECTOR_ROLE_ID") or "").strip()
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN (or DISCORD_TOKEN)")
if not ANNOUNCE_CH:
    raise RuntimeError("Missing RACE_ANNOUNCEMENTS_CHANNEL_ID")
if not RACE_DIRECTOR_ROLE_ID:
    raise RuntimeError("Missing RACE_DIRECTOR_ROLE_ID")

ANNOUNCE_CH = int(ANNOUNCE_CH)
RACE_DIRECTOR_ROLE_ID = int(RACE_DIRECTOR_ROLE_ID)

intents = discord.Intents.default()
intents.guilds = True  # slash commands
bot = commands.Bot(command_prefix="!", intents=intents)

_synced_once = False

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
    global _synced_once

    init_db()
    print(f"Logged in as {bot.user} (guild-sync={'YES' if GUILD_ID else 'NO'})")

    # Don’t spam sync every reconnect (helps avoid rate limits)
    if _synced_once:
        return

    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            print("✅ Synced commands to guild (instant).")
        else:
            await bot.tree.sync()
            print("✅ Synced commands globally (can take time to appear).")

        _synced_once = True
    except Exception as e:
        print("❌ Sync error:", e)

# ---------------- Slash Commands ----------------

@bot.tree.command(name="ping", description="Check if the AMA League bot is online.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("✅ Pong! AMA League bot online.", ephemeral=True)

@bot.tree.command(name="sync", description="Force sync slash commands (Race Director).")
@require_director()
async def sync(interaction: discord.Interaction):
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
        await interaction.response.send_message("✅ Commands synced.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Sync failed: {e}", ephemeral=True)

@bot.tree.command(name="create_event", description="Create an event and announce it.")
@require_director()
@app_commands.describe(
    mode="MX / SX / ENDURO",
    race_class="450 / 250 / 250-2t etc",
    title="Event title",
    track="Track name",
    season="Season number",
    start="Start time (YYYY-MM-DD HH:MM) local time",
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
    if start:
        try:
            start_dt = datetime.strptime(start.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return await interaction.response.send_message(
                "❌ Bad start format. Use `YYYY-MM-DD HH:MM` (example `2026-03-10 20:00`)",
                ephemeral=True,
            )
    else:
        start_dt = datetime.utcnow()

    row = exec1(
        """
        INSERT INTO events (season, mode, class, title, track, start_time, notes, created_by_discord_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        [season, mode.upper().strip(), race_class.strip(), title.strip(), track.strip(), start_dt, notes.strip(), str(interaction.user.id)],
    )

    event_id = row["id"]

    ch = bot.get_channel(ANNOUNCE_CH) or await bot.fetch_channel(ANNOUNCE_CH)

    embed = discord.Embed(
        title=f"🏁 {row['mode']} {row['class']} — {row['title']}",
        description=(row.get("notes") or " "),
    )
    embed.add_field(name="Season", value=str(row["season"]), inline=True)
    embed.add_field(name="Start", value=str(row["start_time"]), inline=True)
    embed.add_field(name="Track", value=row["track"], inline=True)
    embed.set_footer(text=f"Event ID: {event_id}")

    view = discord.ui.View()
    if PUBLIC_BASE_URL:
        view.add_item(discord.ui.Button(label="View Events", url=f"{PUBLIC_BASE_URL}/events"))

    await ch.send(embed=embed, view=view)
    await interaction.response.send_message(f"✅ Event created. Event ID: {event_id}", ephemeral=True)

@bot.tree.command(name="standings", description="Show standings (split by class).")
async def standings(interaction: discord.Interaction):
    rows = q("""
        SELECT
          u.discord_name,
          u.mxb_name,
          COALESCE(u.race_class,'UNASSIGNED') AS race_class,
          COALESCE(SUM(r.points),0) + COALESCE(SUM(p.points_delta),0) AS points
        FROM users u
        LEFT JOIN results r ON r.user_id=u.id
        LEFT JOIN penalties p ON p.user_id=u.id
        GROUP BY u.discord_name, u.mxb_name, u.race_class
        ORDER BY race_class ASC, points DESC
        LIMIT 100
    """)

    if not rows:
        return await interaction.response.send_message("No standings yet.", ephemeral=True)

    # Build message grouped by class
    grouped = {}
    for r in rows:
        grouped.setdefault(r["race_class"], []).append(r)

    lines = []
    for cls, items in grouped.items():
        lines.append(f"**{cls}**")
        for i, it in enumerate(items[:10], start=1):
            name = it.get("mxb_name") or it.get("discord_name") or "Rider"
            lines.append(f"{i}. {name} — **{int(it['points'])}** pts")
        lines.append("")

    msg = "\n".join(lines).strip()
    await interaction.response.send_message(msg[:1900], ephemeral=True)

@bot.tree.command(name="penalty", description="Apply a points penalty/bonus to a rider.")
@require_director()
@app_commands.describe(discord_user="Discord user", points_delta="Negative to deduct points", reason="Reason")
async def penalty(interaction: discord.Interaction, discord_user: discord.Member, points_delta: int, reason: str):
    u = q("SELECT * FROM users WHERE discord_id=%s", [str(discord_user.id)])
    if not u:
        return await interaction.response.send_message("❌ Rider not signed up on website yet.", ephemeral=True)
    u = u[0]

    last = q("SELECT id FROM events ORDER BY start_time DESC LIMIT 1")
    event_id = int(last[0]["id"]) if last else None

    exec1(
        """
        INSERT INTO penalties (event_id, user_id, points_delta, reason, issued_by_discord_id)
        VALUES (%s,%s,%s,%s,%s)
        """,
        [event_id, u["id"], int(points_delta), str(reason), str(interaction.user.id)],
    )
    await interaction.response.send_message(
        f"✅ Applied {points_delta} pts to {discord_user.display_name} — {reason}",
        ephemeral=True,
    )

bot.run(TOKEN)
