import discord
from discord.ext import commands
import json

with open('config.json', 'r') as f:
    config = json.load(f)

bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

bot.run(config['token'])
