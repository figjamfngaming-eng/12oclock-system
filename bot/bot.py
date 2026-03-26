import discord
from discord import app_commands
import psycopg2
import os

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# DATABASE CONNECTION
def get_db():
    return psycopg2.connect(DATABASE_URL)

# SYNC COMMANDS ONLY ONCE
@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Sync error: {e}")

# ------------------------
# COMMANDS
# ------------------------

@tree.command(name="ping", description="Check if bot works")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong! Bot is working")

# REGISTER RIDER
@tree.command(name="register_mxb", description="Register your MX Bikes name")
@app_commands.describe(name="Your MX Bikes name")
async def register_mxb(interaction: discord.Interaction, name: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO riders (discord_id, mxb_name)
        VALUES (%s, %s)
        ON CONFLICT (discord_id)
        DO UPDATE SET mxb_name = EXCLUDED.mxb_name
    """, (str(interaction.user.id), name))

    conn.commit()
    cur.close()
    conn.close()

    await interaction.response.send_message(f"✅ Registered as {name}")

# GUID STATUS
@tree.command(name="guid_status", description="Check your GUID status")
async def guid_status(interaction: discord.Interaction):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT guid_status FROM riders WHERE discord_id = %s
    """, (str(interaction.user.id),))

    result = cur.fetchone()

    cur.close()
    conn.close()

    if result:
        await interaction.response.send_message(f"📊 GUID Status: {result[0]}")
    else:
        await interaction.response.send_message("❌ You are not registered")

# CREATE EVENT
@tree.command(name="create_event", description="Create a race event")
@app_commands.describe(name="Event name")
async def create_event(interaction: discord.Interaction, name: str):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO events (name)
        VALUES (%s)
    """, (name,))

    conn.commit()
    cur.close()
    conn.close()

    await interaction.response.send_message(f"🏁 Event '{name}' created")

# RUN BOT
client.run(TOKEN)
