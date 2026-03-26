import os
import discord
from discord.ext import commands
from shared.db import execute, query

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")

    guild = discord.Object(id=GUILD_ID)
    await bot.tree.sync(guild=guild)

# TEST
@bot.tree.command(name="ping", description="Test bot")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong!")

# CREATE EVENT
@bot.tree.command(name="create_event", description="Create race event")
async def create_event(interaction: discord.Interaction, name: str, track: str):
    execute(
        "INSERT INTO events (name, track) VALUES (:n, :t)",
        {"n": name, "t": track}
    )
    await interaction.response.send_message(f"✅ Event created: {name}")

# LIST EVENTS
@bot.tree.command(name="events", description="List events")
async def events(interaction: discord.Interaction):
    data = query("SELECT * FROM events ORDER BY id DESC LIMIT 5")

    msg = "\n".join([f"{e.name} - {e.track}" for e in data]) or "No events"
    await interaction.response.send_message(msg)

bot.run(TOKEN)
