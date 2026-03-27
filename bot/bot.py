import os
import discord
from discord.ext import commands
import psycopg2

TOKEN = os.getenv("DISCORD_TOKEN")
DB = os.getenv("DATABASE_URL")

bot = commands.Bot(command_prefix="!")

def db():
    return psycopg2.connect(DB, sslmode="require")

def points(p):
    return {1:26,2:23,3:21,4:19,5:18}.get(p,0)

@bot.event
async def on_ready():
    print("Bot online")

@bot.command()
async def register(ctx, name, guid, cls):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO riders (mxb_name,guid,class_name) VALUES (%s,%s,%s)",
                        (name,guid,cls))
            conn.commit()
    await ctx.send("registered")

@bot.command()
async def create_event(ctx, name, cls):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO events (name,class_name) VALUES (%s,%s)",(name,cls))
            conn.commit()
    await ctx.send("event created")

@bot.command()
async def join(ctx, event_id:int, rider_id:int):
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO registrations (event_id,rider_id) VALUES (%s,%s)",
                        (event_id,rider_id))
            conn.commit()
    await ctx.send("joined")

@bot.command()
async def result(ctx, event_id:int, rider_id:int, pos:int):
    pts = points(pos)
    with db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO results VALUES (DEFAULT,%s,%s,%s,%s)",
                        (event_id,rider_id,pos,pts))
            conn.commit()
    await ctx.send("result saved")

bot.run(TOKEN)
