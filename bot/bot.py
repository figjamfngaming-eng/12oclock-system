import os
import asyncio
import discord
from discord import app_commands

from shared.db import init_db, q, exec_sql

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # strongly recommended for fast slash command updates
DATABASE_URL = os.getenv("DATABASE_URL")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_BOT_TOKEN env var")

intents = discord.Intents.default()
# If you also want prefix text commands later, you can enable message_content
# intents.message_content = True

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # DB init (safe)
        if DATABASE_URL:
            try:
                init_db()
            except Exception as e:
                print("DB init warning:", e)

        # Slash command sync
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Synced commands to guild {GUILD_ID}")
        else:
            # Global sync is slow to appear (can take up to ~1 hour). Use GUILD_ID.
            await self.tree.sync()
            print("Synced commands globally (may take time to appear)")

bot = MyBot()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")


@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    # Respond immediately so Discord doesn't timeout
    await interaction.response.send_message("✅ Pong!", ephemeral=True)


@bot.tree.command(name="standings", description="Show standings from the website database")
@app_commands.describe(season="Season name e.g. S1", round="Round number", class_name="Bike class e.g. 450/250")
async def standings(interaction: discord.Interaction, season: str = "S1", round: int = 1, class_name: str = "450"):
    await interaction.response.defer(ephemeral=True)  # prevents timeout

    if not DATABASE_URL:
        await interaction.followup.send("❌ DATABASE_URL is missing on Render for the bot service.", ephemeral=True)
        return

    try:
        rows = q(
            """
            SELECT rider_name, SUM(points)::int AS points
            FROM race_results
            WHERE season = %s AND round = %s AND class_name = %s
            GROUP BY rider_name
            ORDER BY points DESC, rider_name ASC
            LIMIT 20
            """,
            (season, round, class_name),
        )
    except Exception as e:
        await interaction.followup.send(f"❌ DB error: `{e}`", ephemeral=True)
        return

    if not rows:
        await interaction.followup.send(f"No results yet for **{season} R{round} {class_name}**.", ephemeral=True)
        return

    lines = []
    for i, r in enumerate(rows, start=1):
        name = r.get("rider_name") or "Unknown"
        pts = r.get("points") or 0
        lines.append(f"**#{i}** {name} — **{pts} pts**")

    msg = f"🏁 **Standings** — {season} Round {round} ({class_name})\n" + "\n".join(lines)
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="add_result", description="(Admin) Add race points quickly")
@app_commands.describe(season="Season e.g. S1", round="Round number", class_name="450/250", rider_name="Rider name", points="Points")
async def add_result(interaction: discord.Interaction, season: str, round: int, class_name: str, rider_name: str, points: int):
    await interaction.response.defer(ephemeral=True)

    # Simple permission check: must have Manage Guild
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.followup.send("❌ You need **Manage Server** permission to use this.", ephemeral=True)
        return

    if not DATABASE_URL:
        await interaction.followup.send("❌ DATABASE_URL missing on bot service.", ephemeral=True)
        return

    try:
        exec_sql(
            """
            INSERT INTO race_results (season, round, class_name, discord_id, rider_name, points)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (season, round, class_name, str(interaction.user.id), rider_name, int(points)),
        )
        await interaction.followup.send(f"✅ Added **{points}** pts for **{rider_name}** ({season} R{round} {class_name}).", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ DB error: `{e}`", ephemeral=True)


async def main():
    # Basic reconnect backoff to avoid hammering Discord if Render restarts a lot
    while True:
        try:
            await bot.start(TOKEN)
        except discord.HTTPException as e:
            print("Discord HTTPException:", e)
            await asyncio.sleep(15)
        except Exception as e:
            print("Bot crashed:", e)
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main())
