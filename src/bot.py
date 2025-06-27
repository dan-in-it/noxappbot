import discord
from discord.ext import commands
import os
import logging
from dotenv import load_dotenv

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Required environment variables
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
INTERVIEW_CATEGORY_ID = os.getenv("INTERVIEW_CATEGORY_ID")

# Optional environment variables with defaults
OFFICER_ROLE_ID = os.getenv("OFFICER_ROLE_ID")
ADMIN_ROLE_ID = os.getenv("ADMIN_ROLE_ID")
APPLICATION_CHANNEL_PREFIX = os.getenv("APPLICATION_CHANNEL_PREFIX", "application")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Validate required environment variables
if TOKEN is None or INTERVIEW_CATEGORY_ID is None:
    raise RuntimeError(
        "DISCORD_BOT_TOKEN and INTERVIEW_CATEGORY_ID must be set in the .env file"
    )

# Configure logging level
logging.getLogger().setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

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

class ApplicationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Guild Application")
        
        # Add all questions as text inputs (Discord allows up to 5 components per modal)
        self.q1 = discord.ui.TextInput(
            label=questions[0][:45] + "..." if len(questions[0]) > 45 else questions[0],
            placeholder="Weekday/Weekend/Floater",
            max_length=500
        )
        self.q2 = discord.ui.TextInput(
            label=questions[1][:45] + "..." if len(questions[1]) > 45 else questions[1],
            placeholder="Yes/No",
            max_length=500
        )
        self.q3 = discord.ui.TextInput(
            label=questions[2][:45] + "..." if len(questions[2]) > 45 else questions[2],
            placeholder="Class/spec/role preference",
            max_length=500
        )
        self.q4 = discord.ui.TextInput(
            label=questions[3][:45] + "..." if len(questions[3]) > 45 else questions[3],
            placeholder="Warcraft Logs URL",
            max_length=500
        )
        self.q5 = discord.ui.TextInput(
            label=questions[4][:45] + "..." if len(questions[4]) > 45 else questions[4],
            placeholder="Friends/family in guild",
            max_length=500
        )
        
        self.add_item(self.q1)
        self.add_item(self.q2)
        self.add_item(self.q3)
        self.add_item(self.q4)
        self.add_item(self.q5)

    async def on_submit(self, interaction: discord.Interaction):
        # Store answers for the second modal
        answers_part1 = [
            self.q1.value,
            self.q2.value,
            self.q3.value,
            self.q4.value,
            self.q5.value,
        ]
        
        # Send second modal for remaining questions
        second_modal = ApplicationModalPart2(answers_part1, interaction.user)
        await interaction.response.send_modal(second_modal)

class ApplicationModalPart2(discord.ui.Modal):
    def __init__(self, previous_answers, user):
        super().__init__(title="Guild Application (Part 2)")
        self.previous_answers = previous_answers
        self.user = user
        
        # Add remaining questions
        self.q6 = discord.ui.TextInput(
            label=questions[5][:45] + "..." if len(questions[5]) > 45 else questions[5],
            placeholder="Tell us about your raiding experience",
            style=discord.TextStyle.paragraph,
            max_length=2000
        )
        self.q7 = discord.ui.TextInput(
            label=questions[6][:45] + "..." if len(questions[6]) > 45 else questions[6],
            placeholder="Any additional comments",
            style=discord.TextStyle.paragraph,
            max_length=1000
        )
        
        self.add_item(self.q6)
        self.add_item(self.q7)

    async def on_submit(self, interaction: discord.Interaction):
        # Combine all answers
        all_answers = self.previous_answers + [self.q6.value, self.q7.value]
        
        # Create application channel
        await self.create_application_channel(interaction, all_answers)

    async def create_application_channel(self, interaction, answers):
        try:
            guild = interaction.guild
            
            # Validate category
            if INTERVIEW_CATEGORY_ID is None:
                await interaction.response.send_message(
                    "Interview category not configured. Please contact an officer.",
                    ephemeral=True,
                )
                return
            
            category_id = int(INTERVIEW_CATEGORY_ID)
            category = guild.get_channel(category_id)
            
            if not category or not isinstance(category, discord.CategoryChannel):
                await interaction.response.send_message(
                    "Interview category not found. Please contact an officer.",
                    ephemeral=True,
                )
                return
            
            # Create channel name
            channel_name = f"{APPLICATION_CHANNEL_PREFIX}-{self.user.name.lower()}"
            
            # Set up permissions
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                self.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            
            # Add officer role access if configured
            if OFFICER_ROLE_ID:
                try:
                    officer_role = guild.get_role(int(OFFICER_ROLE_ID))
                    if officer_role:
                        overwrites[officer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        logger.info(f"Added officer role {officer_role.name} to application channel permissions")
                    else:
                        logger.warning(f"Officer role with ID {OFFICER_ROLE_ID} not found in guild")
                except ValueError:
                    logger.error(f"Invalid OFFICER_ROLE_ID: {OFFICER_ROLE_ID} (must be numeric)")
            
            # Add admin role access if configured
            if ADMIN_ROLE_ID:
                try:
                    admin_role = guild.get_role(int(ADMIN_ROLE_ID))
                    if admin_role:
                        overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
                        logger.info(f"Added admin role {admin_role.name} to application channel permissions")
                    else:
                        logger.warning(f"Admin role with ID {ADMIN_ROLE_ID} not found in guild")
                except ValueError:
                    logger.error(f"Invalid ADMIN_ROLE_ID: {ADMIN_ROLE_ID} (must be numeric)")
            
            # Create the channel
            interview_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
            )
            
            # Create and send application embed
            embed = discord.Embed(
                title=f"New Application from {self.user.display_name}",
                color=discord.Color.blue(),
            )
            
            for i, question in enumerate(questions):
                embed.add_field(name=question, value=answers[i], inline=False)
            
            embed.set_footer(text=f"Application submitted by {self.user} ({self.user.id})")
            
            await interview_channel.send(embed=embed)
            
            # Notify user
            await interaction.response.send_message(
                f"✅ Your application has been submitted successfully! Your application channel: {interview_channel.mention}",
                ephemeral=True,
            )
            
            logger.info(f"Application completed for {self.user.display_name} ({self.user.id})")
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to create channels. Please contact an administrator.",
                ephemeral=True,
            )
            logger.error(f"Permission denied when creating application channel for {self.user.display_name}")
        except Exception as e:
            await interaction.response.send_message(
                "❌ There was an error processing your application. Please contact an administrator.",
                ephemeral=True,
            )
            logger.error(f"Error completing application for {self.user.display_name}: {e}")

class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply to Guild", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild
        
        # Check if user already has an application channel
        if guild:
            channel_name = f"{APPLICATION_CHANNEL_PREFIX}-{user.name.lower()}"
            existing_channel = discord.utils.get(guild.channels, name=channel_name)
            if existing_channel:
                await interaction.response.send_message(
                    f"You already have an application channel: {existing_channel.mention}",
                    ephemeral=True
                )
                return
        
        # Send the application modal
        modal = ApplicationModal()
        await interaction.response.send_modal(modal)

@bot.tree.command(name="post_application", description="Post the guild application button (Admin only)")
@discord.app_commands.default_permissions(administrator=True)
async def post_application(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏰 Guild Application",
        description="Ready to join our guild? Click the button below to start your application!\n\n📝 Fill out the application form\n⏱️ Takes about 5 minutes to complete\n🔒 Your responses will be reviewed privately",
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message(embed=embed, view=ApplicationView())

@bot.event
async def on_ready():
    logger.info(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    
    # Add persistent view
    bot.add_view(ApplicationView())
    
    logger.info('Application bot is ready and listening for applications')

bot.run(TOKEN)
