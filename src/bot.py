import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
INTERVIEW_CATEGORY_ID = os.getenv("INTERVIEW_CATEGORY_ID")

if TOKEN is None or INTERVIEW_CATEGORY_ID is None:
    raise RuntimeError(
        "DISCORD_BOT_TOKEN and INTERVIEW_CATEGORY_ID must be set in the .env file"
    )

intents = discord.Intents.default()


bot = commands.Bot(command_prefix="!", intents=intents)

questions = [
    "Which raid team are you applying to? Weekday (Tues/Wed/Thurs), Weekend (Fri/Sat/Sun), Floater/Casual",
    "Have you reviewed the raid schedule for the team you're applying for?",
    "Is there a certain class/spec/role that you prefer to play?",
    "Please provide a link to your Warcraft Logs page for the character(s) you're applying with",
    "Do you currently have any friends or family in the guild? If so, who?",
    "Tell us about yourself and your raiding experience",
    "Any additional comments/questions?",
]

# Temporary storage for answers between modal submissions
partial_answers = {}


class ApplicationModalPart1(discord.ui.Modal):
    def __init__(self, member: discord.Member):
        super().__init__(title="Guild Application (1/2)")
        self.member = member
        self.q1 = discord.ui.TextInput(label=questions[0])
        self.q2 = discord.ui.TextInput(label=questions[1])
        self.q3 = discord.ui.TextInput(label=questions[2])
        self.q4 = discord.ui.TextInput(label=questions[3])
        self.q5 = discord.ui.TextInput(label=questions[4])
        for item in (self.q1, self.q2, self.q3, self.q4, self.q5):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        partial_answers[self.member.id] = [
            self.q1.value,
            self.q2.value,
            self.q3.value,
            self.q4.value,
            self.q5.value,
        ]
        await interaction.response.send_modal(ApplicationModalPart2(self.member))


class ApplicationModalPart2(discord.ui.Modal):
    def __init__(self, member: discord.Member):
        super().__init__(title="Guild Application (2/2)")
        self.member = member
        self.q6 = discord.ui.TextInput(label=questions[5])
        self.q7 = discord.ui.TextInput(label=questions[6])
        self.add_item(self.q6)
        self.add_item(self.q7)

    async def on_submit(self, interaction: discord.Interaction):
        answers = partial_answers.pop(self.member.id, [])
        answers.extend([self.q6.value, self.q7.value])

        guild = interaction.guild
        category_id = int(INTERVIEW_CATEGORY_ID)
        category = guild.get_channel(category_id)

        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(
                "Interview category not found. Please contact an officer.",
                ephemeral=True,
            )
            return

        channel_name = f"application-{self.member.name.lower()}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            # You can add officer roles here, e.g.:
            # guild.get_role(YOUR_OFFICER_ROLE_ID): discord.PermissionOverwrite(read_messages=True),
        }

        try:
            interview_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
            )
            embed = discord.Embed(
                title=f"New Application from {self.member.name}",
                color=discord.Color.blue(),
            )
            for i, question in enumerate(questions):
                embed.add_field(name=question, value=answers[i], inline=False)

            await interview_channel.send(embed=embed)
            await interaction.response.send_message(
                f"Your application channel has been created: {interview_channel.mention}. We will review it shortly.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I do not have permissions to create a channel. Please contact an officer.",
                ephemeral=True,
            )
        except Exception as e:
            print(e)
            await interaction.response.send_message(
                "There was an error submitting your application. Please contact an officer.",
                ephemeral=True,
            )


class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApplicationModalPart1(interaction.user))


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

bot.run(TOKEN)
