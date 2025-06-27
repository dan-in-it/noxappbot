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
intents.message_content = True  # Required for DM functionality

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

# Storage for application sessions
application_sessions = {}

def cleanup_old_sessions():
    """Clean up old application sessions to prevent memory leaks"""
    import time
    current_time = time.time()
    to_remove = []
    for user_id, session in application_sessions.items():
        if current_time - session.get('timestamp', 0) > 3600:  # 1 hour timeout
            to_remove.append(user_id)
    for user_id in to_remove:
        application_sessions.pop(user_id, None)

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
        
        # Check if user already has an active application session
        if user.id in application_sessions:
            await interaction.response.send_message(
                "You already have an active application in progress. Please check your DMs to continue.",
                ephemeral=True
            )
            return
        
        # Try to send DM to user
        try:
            dm_channel = await user.create_dm()
            
            # Initialize application session
            import time
            application_sessions[user.id] = {
                'guild_id': guild.id,
                'current_question': 0,
                'answers': [],
                'timestamp': time.time()
            }
            
            # Send first question
            embed = discord.Embed(
                title="Guild Application",
                description=f"Welcome to the guild application process! I'll ask you {len(questions)} questions.\n\n**Question 1/{len(questions)}:**\n{questions[0]}",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Please respond with your answer. Type 'cancel' to stop the application.")
            
            await dm_channel.send(embed=embed)
            
            await interaction.response.send_message(
                "Application started! Please check your DMs to continue.",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't send you a DM. Please enable DMs from server members and try again.",
                ephemeral=True
            )

@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return
    
    # Handle DM messages for applications
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        
        if user_id in application_sessions:
            session = application_sessions[user_id]
            
            # Check for cancel command
            if message.content.lower() == 'cancel':
                del application_sessions[user_id]
                await message.channel.send("Application cancelled. You can start a new application anytime.")
                return
            
            # Store the answer
            session['answers'].append(message.content)
            session['current_question'] += 1
            
            # Check if we have more questions
            if session['current_question'] < len(questions):
                # Send next question
                embed = discord.Embed(
                    title="Guild Application",
                    description=f"**Question {session['current_question'] + 1}/{len(questions)}:**\n{questions[session['current_question']]}",
                    color=discord.Color.blue()
                )
                embed.set_footer(text="Please respond with your answer. Type 'cancel' to stop the application.")
                await message.channel.send(embed=embed)
            else:
                # Application complete, create channel and post
                await complete_application(message.author, session)
    
    # Process commands
    await bot.process_commands(message)

async def complete_application(user, session):
    """Complete the application process by creating a channel and posting the application"""
    try:
        guild = bot.get_guild(session['guild_id'])
        if not guild:
            await user.send("Error: Could not find the guild. Please contact an administrator.")
            return
        
        # Get category
        if INTERVIEW_CATEGORY_ID is None:
            await user.send("Error: Interview category not configured. Please contact an officer.")
            return
        
        category_id = int(INTERVIEW_CATEGORY_ID)
        category = guild.get_channel(category_id)
        
        if not category or not isinstance(category, discord.CategoryChannel):
            await user.send("Error: Interview category not found. Please contact an officer.")
            return
        
        # Create channel
        channel_name = f"{APPLICATION_CHANNEL_PREFIX}-{user.name.lower()}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
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
        
        interview_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
        )
        
        # Create and send application embed
        embed = discord.Embed(
            title=f"New Application from {user.display_name}",
            color=discord.Color.blue(),
        )
        
        for i, question in enumerate(questions):
            embed.add_field(name=question, value=session['answers'][i], inline=False)
        
        embed.set_footer(text=f"Application submitted by {user} ({user.id})")
        
        await interview_channel.send(embed=embed)
        
        # Notify user
        await user.send(f"✅ Your application has been submitted successfully! Your application channel: {interview_channel.mention}")
        
        # Clean up session
        del application_sessions[user.id]
        
        logger.info(f"Application completed for {user.display_name} ({user.id})")
        
    except discord.Forbidden:
        await user.send("❌ I don't have permission to create channels. Please contact an administrator.")
        logger.error(f"Permission denied when creating application channel for {user.display_name}")
    except Exception as e:
        await user.send("❌ There was an error processing your application. Please contact an administrator.")
        logger.error(f"Error completing application for {user.display_name}: {e}")
    finally:
        # Clean up session even if there was an error
        application_sessions.pop(user.id, None)

@bot.tree.command(name="post_application", description="Post the guild application button (Admin only)")
@discord.app_commands.default_permissions(administrator=True)
async def post_application(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏰 Guild Application",
        description="Ready to join our guild? Click the button below to start your application!\n\n📝 The application will be sent to you via DM\n⏱️ Takes about 5 minutes to complete\n🔒 Your responses will be reviewed privately",
        color=discord.Color.blue()
    )
    embed.set_footer(text="Make sure you can receive DMs from server members!")
    
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
    
    # Start periodic cleanup task
    bot.loop.create_task(periodic_cleanup())
    logger.info('Application bot is ready and listening for applications')

async def periodic_cleanup():
    """Periodically clean up old application sessions"""
    import asyncio
    while True:
        await asyncio.sleep(3600)  # Run every hour
        cleanup_old_sessions()

bot.run(TOKEN)
