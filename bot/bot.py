import os
import discord
from discord.ext import commands
from discord import app_commands
import psycopg2

# =========================
# CONFIG (RENDER ENV VARS)
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
GUILD_ID = int(os.getenv("GUILD_ID"))  # your Discord server ID

# =========================
# DATABASE CONNECT
# =========================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

# =========================
# DISCORD SETUP
# =========================
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# =========================
# ON READY
# =========================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    guild = discord.Object(id=GUILD_ID)

    # 🔥 FORCE CLEAN SYNC (fixes all your command issues)
    try:
        tree.clear_commands(guild=guild)
        await tree.sync(guild=guild)

        synced = await tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} commands")

    except Exception as e:
        print(f"❌ Sync failed: {e}")

# =========================
# COMMANDS
# =========================

@tree.command(name="ping", description="Check if bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot is working")

# =========================
# REGISTER RIDER
# =========================
@tree.command(name="register_mxb", description="Register your MX Bikes account")
@app_commands.describe(name="Your MX Bikes name", steam_id="Your Steam ID")
async def register_mxb(interaction: discord.Interaction, name: str, steam_id: str):
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO riders (discord_id, mxb_name, steam_id)
            VALUES (%s, %s, %s)
            ON CONFLICT (discord_id) DO UPDATE
            SET mxb_name = EXCLUDED.mxb_name,
                steam_id = EXCLUDED.steam_id;
        """, (str(interaction.user.id), name, steam_id))

        conn.commit()
        cur.close()
        conn.close()

        await interaction.response.send_message(f"✅ Registered as **{name}**")

    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}")

# =========================
# START BOT
# =========================
bot.run(TOKEN)
