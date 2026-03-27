import os
import discord
from discord.ext import commands
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

def db():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def points(p):
    return {1:26,2:23,3:21,4:19,5:18}.get(p,0)

@bot.event
async def on_ready():
    print("Bot ready")

@bot.command()
async def register(ctx, name, guid, cls):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO riders (mxb_name,guid,class_name)
            VALUES (%s,%s,%s)
            """,(name,guid,cls))
            conn.commit()
    await ctx.send("registered")

@bot.command()
async def create_event(ctx, name, cls):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO events (name,class_name,race_stage)
            VALUES (%s,%s,'qualifying')
            """,(name,cls))
            conn.commit()
    await ctx.send("event created")

@bot.command()
async def join(ctx, event_id:int, rider_id:int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO registrations (event_id,rider_id)
            VALUES (%s,%s)
            """,(event_id,rider_id))
            conn.commit()
    await ctx.send("joined")

@bot.command()
async def result(ctx, event_id:int, rider_id:int, pos:int):
    pts = points(pos)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            INSERT INTO results (event_id,rider_id,position,points)
            VALUES (%s,%s,%s,%s)
            """,(event_id,rider_id,pos,pts))
            conn.commit()
    await ctx.send("result saved")

bot.run(TOKEN)
