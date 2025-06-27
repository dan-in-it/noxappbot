import discord
from discord.ext import commands
import json

with open('src/config.json', 'r') as f:
    config = json.load(f)

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True


bot = commands.Bot(command_prefix="!", intents=intents)

questions = [
    "Which raid team are you applying to? Weekday (Tues/Wed/Thurs), Weekend (Fri/Sat/Sun), Floater/Casual",
    "Have you reviewed the raid schedule for the team you're applying for?",
    "Is there a certain class/spec/role that you prefer to play?",
    "Please provide a link to your Warcraft Logs page for the character(s) you're applying with",
    "Do you currently have any friends or family in the guild? If so, who?",
    "Tell us about yourself and your raiding experience",
    "Any additional comments/questions?"
]

class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("I have sent you a DM with the application questions.", ephemeral=True)
        
        member = interaction.user
        dm_channel = await member.create_dm()
        
        answers = []
        for question in questions:
            await dm_channel.send(question)
            
            def check(m):
                return m.author == member and m.channel == dm_channel

            try:
                answer = await bot.wait_for('message', timeout=300.0, check=check)
                answers.append(answer.content)
            except TimeoutError:
                await dm_channel.send("You took too long to answer. Please start the application process again.")
                return

        guild = interaction.guild
        category_id = int(config["interview_category_id"])
        category = guild.get_channel(category_id)

        if not category or not isinstance(category, discord.CategoryChannel):
            await dm_channel.send("Interview category not found. Please contact an officer.")
            return

        channel_name = f"application-{member.name.lower()}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            # You can add officer roles here, e.g.:
            # guild.get_role(YOUR_OFFICER_ROLE_ID): discord.PermissionOverwrite(read_messages=True)
        }

        try:
            interview_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites
            )
            embed = discord.Embed(title=f"New Application from {member.name}", color=discord.Color.blue())
            for i, question in enumerate(questions):
                embed.add_field(name=question, value=answers[i], inline=False)
            
            await interview_channel.send(embed=embed)
            await dm_channel.send(f"Your application channel has been created: {interview_channel.mention}. We will review it shortly.")
        except discord.Forbidden:
            await dm_channel.send("I do not have permissions to create a channel. Please contact an officer.")
        except Exception as e:
            print(e)
            await dm_channel.send("There was an error submitting your application. Please contact an officer.")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    bot.add_view(ApplicationView())

@bot.command()
@commands.has_permissions(administrator=True)
async def post_application(ctx):
    embed = discord.Embed(
        title="Guild Application",
        description="Click the button below to apply to our guild.",
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed, view=ApplicationView())

bot.run(config['token'])
