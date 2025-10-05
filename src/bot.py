import discord
from discord.ext import commands
import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone
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
    "Which raid team are you applying to? Weekend (Fri/Sat), Floater/Casual, 10M Weasals Weekday Team, 10M Casual Weekday Team",
    "Have you reviewed the raid schedule for the team you're applying for?",
    "How did you hear about us?",
    "Is there a certain class/spec/role that you prefer to play?",
    "Please provide a link to your Warcraft Logs page for the character(s) you're applying with",
    "Do you currently have any friends or family in the guild? If so, who?",
    "Tell us about yourself and your raiding experience",
    "Any additional comments/questions?",
]

# Store ongoing applications
ongoing_applications = {}

class ApplicationHandler:
    def __init__(self, user, guild):
        self.user = user
        self.guild = guild
        self.answers = []
        self.current_question = 0
        
    async def start_application(self):
        """Start the application process by sending the first question"""
        try:
            await self.send_current_question()
        except discord.Forbidden:
            logger.error(f"Cannot send DM to {self.user.display_name}")
            return False
        return True
    
    async def send_current_question(self):
        """Send the current question to the user"""
        if self.current_question < len(questions):
            question_num = self.current_question + 1
            question = questions[self.current_question]
            
            embed = discord.Embed(
                title=f"Guild Application - Question {question_num}/{len(questions)}",
                description=question,
                color=discord.Color.blue()
            )
            embed.set_footer(text="Please respond with your answer. Type 'cancel' to cancel the application.")
            
            await self.user.send(embed=embed)
        else:
            await self.complete_application()
    
    async def process_answer(self, message):
        """Process the user's answer and move to next question"""
        if message.content.lower() == 'cancel':
            await self.cancel_application()
            return
        
        # Handle "proceed" command for long answers
        if message.content.lower() == 'proceed' and hasattr(self, 'pending_long_answer'):
            answer = self.validate_and_truncate_answer(self.pending_long_answer)
            delattr(self, 'pending_long_answer')
        else:
            # Remove excessive whitespace
            clean_content = ' '.join(message.content.split())
            
            # Check if answer is too long
            if len(clean_content) > 800:
                embed = discord.Embed(
                    title="⚠️ Answer Too Long",
                    description=f"Your answer is **{len(clean_content)} characters** long, but the maximum is **800 characters**.",
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="Your Options:",
                    value="• **Shorten your answer** and send it again\n• **Type 'proceed'** to automatically truncate your answer\n• **Type 'cancel'** to cancel the application",
                    inline=False
                )
                embed.set_footer(text="If you choose to proceed, your answer will be cut off at 800 characters.")
                await self.user.send(embed=embed)
                
                # Store the long answer for potential truncation
                self.pending_long_answer = clean_content
                return
            
            answer = clean_content
        
        # Check if answer is just spam (repeated characters/stickers)
        if self.is_spam_answer(answer):
            embed = discord.Embed(
                title="⚠️ Invalid Answer",
                description="Your answer appears to contain excessive repeated characters or stickers. Please provide a meaningful response to the question.",
                color=discord.Color.orange()
            )
            embed.set_footer(text="Please try again with a proper answer, or type 'cancel' to cancel the application.")
            await self.user.send(embed=embed)
            return
        
        # Store the processed answer
        self.answers.append(answer)
        self.current_question += 1
        
        if self.current_question < len(questions):
            await self.send_current_question()
        else:
            await self.complete_application()
    
    def validate_and_truncate_answer(self, content):
        """Validate and truncate answer to prevent Discord limits"""
        # Maximum characters per answer (leaving room for embed formatting and truncation message)
        MAX_ANSWER_LENGTH = 800
        TRUNCATION_MESSAGE = "... [Answer truncated due to length - {} characters total]"
        
        # Remove excessive whitespace
        content = ' '.join(content.split())
        
        # Truncate if too long
        if len(content) > MAX_ANSWER_LENGTH:
            # Calculate how much space we need for the truncation message
            truncation_msg = TRUNCATION_MESSAGE.format(len(content))
            available_space = MAX_ANSWER_LENGTH - len(truncation_msg)
            
            # Ensure we have enough space for meaningful content
            if available_space < 50:
                available_space = 50
                truncation_msg = "... [Truncated]"
            
            truncated_content = content[:available_space].rstrip()
            
            # Try to truncate at a word boundary
            last_space = truncated_content.rfind(' ')
            if last_space > available_space * 0.8:  # Only if we don't lose too much
                truncated_content = truncated_content[:last_space]
            
            return f"{truncated_content}{truncation_msg}"
        
        return content
    
    def is_spam_answer(self, content):
        """Check if answer appears to be spam (repeated characters/stickers)"""
        if len(content) < 10:
            return False
        
        # Check for excessive repeated characters (more than 80% of the content)
        char_counts = {}
        for char in content:
            char_counts[char] = char_counts.get(char, 0) + 1
        
        # If any single character makes up more than 80% of the content, it's likely spam
        max_char_ratio = max(char_counts.values()) / len(content)
        if max_char_ratio > 0.8:
            return True
        
        # Check for repeated patterns (like sticker spam)
        # Look for sequences of the same 2-3 character pattern repeated
        for pattern_length in [2, 3, 4]:
            if len(content) >= pattern_length * 10:  # Only check if content is long enough
                pattern = content[:pattern_length]
                pattern_count = content.count(pattern)
                if pattern_count > len(content) / (pattern_length * 2):  # Pattern appears too frequently
                    return True
        
        return False
    
    async def cancel_application(self):
        """Cancel the application process"""
        embed = discord.Embed(
            title="Application Cancelled",
            description="Your guild application has been cancelled. You can start a new application anytime by clicking the Apply button again.",
            color=discord.Color.red()
        )
        await self.user.send(embed=embed)
        
        # Remove from ongoing applications
        if self.user.id in ongoing_applications:
            del ongoing_applications[self.user.id]
    
    async def complete_application(self):
        """Complete the application and create the channel"""
        try:
            # INTERVIEW_CATEGORY_ID is guaranteed to be non-None due to startup validation
            category_id = int(INTERVIEW_CATEGORY_ID)  # type: ignore
            category = self.guild.get_channel(category_id)
            
            if not category or not isinstance(category, discord.CategoryChannel):
                await self.user.send("❌ Interview category not found. Please contact an officer.")
                return
            
            # Create channel name
            channel_name = f"{APPLICATION_CHANNEL_PREFIX}-{self.user.name.lower()}"
            
            # Set up permissions
            overwrites = {
                self.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                self.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, create_public_threads=True, add_reactions=True, embed_links=True, attach_files=True, use_external_emojis=True, send_messages_in_threads=True, create_private_threads=True, manage_threads=True),
            }
            
            # Add officer role access if configured
            if OFFICER_ROLE_ID:
                try:
                    officer_role = self.guild.get_role(int(OFFICER_ROLE_ID))
                    if officer_role:
                        overwrites[officer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, create_public_threads=True, add_reactions=True, embed_links=True, attach_files=True, use_external_emojis=True, send_messages_in_threads=True, create_private_threads=True, manage_threads=True)
                        logger.info(f"Added officer role {officer_role.name} to application channel permissions")
                    else:
                        logger.warning(f"Officer role with ID {OFFICER_ROLE_ID} not found in guild")
                except ValueError:
                    logger.error(f"Invalid OFFICER_ROLE_ID: {OFFICER_ROLE_ID} (must be numeric)")
            
            # Add admin role access if configured
            if ADMIN_ROLE_ID:
                try:
                    admin_role = self.guild.get_role(int(ADMIN_ROLE_ID))
                    if admin_role:
                        overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, create_public_threads=True, add_reactions=True, embed_links=True, attach_files=True, use_external_emojis=True, send_messages_in_threads=True, create_private_threads=True, manage_threads=True)
                        logger.info(f"Added admin role {admin_role.name} to application channel permissions")
                    else:
                        logger.warning(f"Admin role with ID {ADMIN_ROLE_ID} not found in guild")
                except ValueError:
                    logger.error(f"Invalid ADMIN_ROLE_ID: {ADMIN_ROLE_ID} (must be numeric)")
            
            # Create the channel
            interview_channel = await self.guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
            )
            
            # Create and send application embed with safety checks
            embed = discord.Embed(
                title=f"New Application from {self.user.display_name}",
                color=discord.Color.blue(),
            )
            
            # Add questions and answers with additional safety checks
            total_embed_length = len(embed.title or "") + len(embed.description or "")
            
            for i, question in enumerate(questions):
                # Ensure field name doesn't exceed Discord's 256 character limit
                field_name = f"Q{i+1}: {question}"
                if len(field_name) > 256:
                    field_name = field_name[:253] + "..."
                
                # Ensure field value doesn't exceed Discord's 1024 character limit
                field_value = self.answers[i]
                if len(field_value) > 1024:
                    field_value = field_value[:1021] + "..."
                
                # Check if adding this field would exceed the total embed limit (6000 chars)
                field_length = len(field_name) + len(field_value)
                if total_embed_length + field_length > 5500:  # Leave some buffer
                    # Add a truncation notice instead
                    embed.add_field(
                        name="⚠️ Application Truncated",
                        value="Some answers were too long and have been truncated. Full responses are available in the application logs.",
                        inline=False
                    )
                    break
                
                embed.add_field(name=field_name, value=field_value, inline=False)
                total_embed_length += field_length
            
            # Add Discord ID as a field for easy extraction
            embed.add_field(name="Discord ID", value=str(self.user.id), inline=False)
            
            embed.set_footer(text=f"Application submitted by {self.user} ({self.user.id})")
            
            # Send the embed with mentions
            await interview_channel.send(
                content=f"{self.user.mention} <@&616354080704430130>",
                embed=embed
            )
            
            # Notify user of completion
            completion_embed = discord.Embed(
                title="✅ Application Submitted Successfully!",
                description=f"Your guild application has been submitted and reviewed by our officers.\n\nYour application channel: {interview_channel.mention}",
                color=discord.Color.green()
            )
            await self.user.send(embed=completion_embed)
            
            logger.info(f"Application completed for {self.user.display_name} ({self.user.id})")
            
        except discord.Forbidden:
            await self.user.send("❌ I don't have permission to create channels. Please contact an administrator.")
            logger.error(f"Permission denied when creating application channel for {self.user.display_name}")
        except Exception as e:
            await self.user.send("❌ There was an error processing your application. Please contact an administrator.")
            logger.error(f"Error completing application for {self.user.display_name}: {e}")
        finally:
            # Remove from ongoing applications
            if self.user.id in ongoing_applications:
                del ongoing_applications[self.user.id]

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
        
        # Check if user already has an ongoing application
        if user.id in ongoing_applications:
            await interaction.response.send_message(
                "You already have an application in progress. Please check your DMs to continue, or type 'cancel' to start over.",
                ephemeral=True
            )
            return
        
        # Start the application process
        application_handler = ApplicationHandler(user, guild)
        ongoing_applications[user.id] = application_handler
        
        success = await application_handler.start_application()
        
        if success:
            await interaction.response.send_message(
                "✅ Application started! Please check your DMs to continue with the questions.",
                ephemeral=True
            )
        else:
            # Remove from ongoing applications if failed to start
            if user.id in ongoing_applications:
                del ongoing_applications[user.id]
            await interaction.response.send_message(
                "❌ I couldn't send you a DM. Please make sure your DMs are open and try again.",
                ephemeral=True
            )

def parse_time_string(time_str):
    """Parse time string like '10m', '1h', '30m', '2h' into seconds"""
    if not time_str:
        return None
    
    time_str = time_str.lower().strip()
    
    if time_str.endswith('m'):
        try:
            minutes = int(time_str[:-1])
            if minutes < 1 or minutes > 10080:  # Max 1 week in minutes
                return None
            return minutes * 60
        except ValueError:
            return None
    elif time_str.endswith('h'):
        try:
            hours = int(time_str[:-1])
            if hours < 1 or hours > 168:  # Max 1 week in hours
                return None
            return hours * 3600
        except ValueError:
            return None
    else:
        # Try to parse as just a number (assume hours for backwards compatibility)
        try:
            hours = int(time_str)
            if hours < 1 or hours > 168:
                return None
            return hours * 3600
        except ValueError:
            return None

def format_time_duration(seconds):
    """Format seconds into a human-readable duration"""
    if seconds < 3600:  # Less than an hour
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"

async def schedule_channel_deletion(channel, delay_seconds):
    """Schedule a channel for deletion after a specified delay in seconds"""
    try:
        await asyncio.sleep(delay_seconds)
        if channel:
            await channel.delete(reason="Application processed - automatic cleanup")
            logger.info(f"Deleted application channel: {channel.name}")
    except discord.NotFound:
        logger.info(f"Channel was already deleted")
    except Exception as e:
        logger.error(f"Error deleting channel: {e}")

@bot.tree.command(name="noxpost", description="Post the guild application button (Admin only)")
@discord.app_commands.default_permissions(administrator=True)
async def post_application(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏰 Guild Application",
        description="Ready to join our guild? Click the button below to start your application!\n\n📝 Fill out the application form\n⏱️ Takes about 5 minutes to complete\n🔒 Your responses will be reviewed privately",
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message(embed=embed, view=ApplicationView())

@bot.tree.command(name="noxsync", description="Force sync slash commands (Admin only)")
@discord.app_commands.default_permissions(administrator=True)
async def sync_commands(interaction: discord.Interaction):
    try:
        synced = await bot.tree.sync()
        await interaction.response.send_message(
            f"✅ Successfully synced {len(synced)} slash commands!",
            ephemeral=True
        )
        logger.info(f"Manual sync completed by {interaction.user.display_name}: {len(synced)} commands synced")
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Failed to sync commands: {str(e)}",
            ephemeral=True
        )
        logger.error(f"Manual sync failed: {e}")

@bot.tree.command(name="noxreject", description="Reject an application")
@discord.app_commands.describe(
    reason="Reason for rejection (optional)",
    delete_time="Time until channel deletion (e.g., '10m', '1h', '30m') - if not specified, channel stays"
)
async def reject_application(
    interaction: discord.Interaction,
    reason: str = "No reason provided",
    delete_time: str | None = None
):
    # Ensure this is used in a guild
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return
    
    # Ensure user is a Member (not just User)
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ This command can only be used by server members.",
            ephemeral=True
        )
        return
    
    # Check if user has permission (officer or admin role)
    user_roles = [role.id for role in interaction.user.roles]
    has_permission = False
    
    if interaction.user.guild_permissions.administrator:
        has_permission = True
    elif OFFICER_ROLE_ID and int(OFFICER_ROLE_ID) in user_roles:
        has_permission = True
    elif ADMIN_ROLE_ID and int(ADMIN_ROLE_ID) in user_roles:
        has_permission = True
    
    if not has_permission:
        await interaction.response.send_message(
            "❌ You don't have permission to reject applications. Only officers and administrators can use this command.",
            ephemeral=True
        )
        return
    
    # Check if this is an application channel
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in text channels.",
            ephemeral=True
        )
        return
    
    if not channel.name.startswith(APPLICATION_CHANNEL_PREFIX):
        await interaction.response.send_message(
            f"❌ This command can only be used in application channels (channels starting with '{APPLICATION_CHANNEL_PREFIX}').",
            ephemeral=True
        )
        return
    
    # Parse and validate delete_time if provided
    delete_seconds = None
    if delete_time is not None:
        delete_seconds = parse_time_string(delete_time)
        if delete_seconds is None:
            await interaction.response.send_message(
                "❌ Invalid time format. Use formats like '10m' for minutes or '1h' for hours. Maximum is 1 week (168h or 10080m).",
                ephemeral=True
            )
            return
    
    # Extract applicant name from channel name
    applicant_name = channel.name.replace(f"{APPLICATION_CHANNEL_PREFIX}-", "").replace("-", " ").title()
    
    # Create rejection embed
    rejection_embed = discord.Embed(
        title="❌ Application Rejected",
        description=f"**Applicant:** {applicant_name}\n**Reason:** {reason}\n**Rejected by:** {interaction.user.mention}",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc)
    )
    
    if delete_seconds is not None:
        deletion_time = datetime.now(timezone.utc) + timedelta(seconds=delete_seconds)
        time_duration = format_time_duration(delete_seconds)
        rejection_embed.add_field(
            name="Channel Deletion",
            value=f"This channel will be automatically deleted in {time_duration} (<t:{int(deletion_time.timestamp())}:R>)",
            inline=False
        )
    else:
        rejection_embed.add_field(
            name="Channel Status",
            value="This channel will remain open for further discussion.",
            inline=False
        )
    
    await interaction.response.send_message(embed=rejection_embed)
    
    # Try to notify the applicant via DM
    dm_status = "❌ Failed to send DM"
    try:
        # Find the applicant by extracting Discord ID from the application embed
        applicant = None
        applicant_id = None
        
        logger.info(f"Looking for application embed in channel {channel.name}")
        
        # Look for the application embed in the channel
        async for message in channel.history(limit=50):
            if message.embeds and message.author == interaction.guild.me:
                embed = message.embeds[0]
                logger.info(f"Found embed with title: {embed.title}")
                if embed.title and "Application from" in embed.title:
                    logger.info(f"Found application embed, checking for Discord ID field")
                    # Look for Discord ID field in the embed
                    for field in embed.fields:
                        logger.info(f"Checking field: {field.name} = {field.value}")
                        if field.name == "Discord ID" and field.value:
                            try:
                                applicant_id = int(field.value)
                                logger.info(f"Extracted applicant ID: {applicant_id}")
                                break
                            except ValueError:
                                logger.error(f"Could not parse Discord ID: {field.value}")
                                continue
                    if applicant_id:
                        break
        
        if not applicant_id:
            logger.error(f"Could not find Discord ID in application embed for channel {channel.name}")
            dm_status = "❌ Could not find applicant Discord ID"
        else:
            # Try to get the applicant user object using the ID
            applicant = None
            try:
                # First try to get from guild cache
                applicant = interaction.guild.get_member(applicant_id)
                if applicant:
                    logger.info(f"Found applicant in guild cache: {applicant.display_name} ({applicant.id})")
                else:
                    # If not in cache, fetch directly from Discord API
                    logger.info(f"Member not in cache, fetching from Discord API...")
                    applicant = await bot.fetch_user(applicant_id)
                    logger.info(f"Fetched applicant from API: {applicant.display_name} ({applicant.id})")
            except discord.NotFound:
                logger.error(f"User with ID {applicant_id} not found on Discord")
                dm_status = "❌ User not found on Discord"
                applicant = None
            except Exception as e:
                logger.error(f"Error fetching user {applicant_id}: {e}")
                dm_status = f"❌ Error fetching user: {str(e)}"
                applicant = None
            
            if applicant:
                dm_embed = discord.Embed(
                    title="Application Update",
                    description=f"Your application to **{interaction.guild.name}** has been reviewed.",
                    color=discord.Color.red()
                )
                dm_embed.add_field(name="Status", value="❌ Rejected", inline=True)
                dm_embed.add_field(name="Reason", value=reason, inline=False)
                dm_embed.add_field(
                    name="What's Next?",
                    value="You're welcome to apply again in the future. Feel free to reach out to our officers if you have any questions.",
                    inline=False
                )
                
                await applicant.send(embed=dm_embed)
                logger.info(f"Successfully sent rejection notification to {applicant.display_name}")
                dm_status = "✅ DM sent successfully"
            
    except discord.Forbidden:
        logger.warning(f"Could not send DM to applicant - DMs may be disabled")
        dm_status = "❌ DMs disabled or blocked"
    except Exception as e:
        logger.error(f"Error sending rejection DM: {e}")
        dm_status = f"❌ Error: {str(e)}"
    
    # Send a follow-up message to the officer about DM status
    try:
        await interaction.followup.send(
            f"**DM Notification Status:** {dm_status}",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Could not send DM status follow-up: {e}")
    
    # Schedule channel deletion if requested
    if delete_seconds is not None:
        asyncio.create_task(schedule_channel_deletion(channel, delete_seconds))
        time_duration = format_time_duration(delete_seconds)
        logger.info(f"Application rejected by {interaction.user.display_name} in {channel.name}. Channel scheduled for deletion in {time_duration}.")
    else:
        logger.info(f"Application rejected by {interaction.user.display_name} in {channel.name}. Channel will remain open.")

@bot.tree.command(name="noxapprove", description="Approve an application")
@discord.app_commands.describe(
    welcome_message="Custom welcome message (optional)",
    delete_time="Time until channel deletion (e.g., '10m', '1h', '30m') - if not specified, channel stays"
)
async def approve_application(
    interaction: discord.Interaction,
    welcome_message: str = "Welcome to the guild! We're excited to have you join us.",
    delete_time: str | None = None
):
    # Ensure this is used in a guild
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return
    
    # Ensure user is a Member (not just User)
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "❌ This command can only be used by server members.",
            ephemeral=True
        )
        return
    
    # Check if user has permission (officer or admin role)
    user_roles = [role.id for role in interaction.user.roles]
    has_permission = False
    
    if interaction.user.guild_permissions.administrator:
        has_permission = True
    elif OFFICER_ROLE_ID and int(OFFICER_ROLE_ID) in user_roles:
        has_permission = True
    elif ADMIN_ROLE_ID and int(ADMIN_ROLE_ID) in user_roles:
        has_permission = True
    
    if not has_permission:
        await interaction.response.send_message(
            "❌ You don't have permission to accept applications. Only officers and administrators can use this command.",
            ephemeral=True
        )
        return
    
    # Check if this is an application channel
    channel = interaction.channel
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "❌ This command can only be used in text channels.",
            ephemeral=True
        )
        return
    
    if not channel.name.startswith(APPLICATION_CHANNEL_PREFIX):
        await interaction.response.send_message(
            f"❌ This command can only be used in application channels (channels starting with '{APPLICATION_CHANNEL_PREFIX}').",
            ephemeral=True
        )
        return
    
    # Parse and validate delete_time if provided
    delete_seconds = None
    if delete_time is not None:
        delete_seconds = parse_time_string(delete_time)
        if delete_seconds is None:
            await interaction.response.send_message(
                "❌ Invalid time format. Use formats like '10m' for minutes or '1h' for hours. Maximum is 1 week (168h or 10080m).",
                ephemeral=True
            )
            return
    
    # Extract applicant name from channel name
    applicant_name = channel.name.replace(f"{APPLICATION_CHANNEL_PREFIX}-", "").replace("-", " ").title()
    
    # Create acceptance embed
    acceptance_embed = discord.Embed(
        title="✅ Application Approved",
        description=f"**Applicant:** {applicant_name}\n**Approved by:** {interaction.user.mention}",
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc)
    )
    
    acceptance_embed.add_field(
        name="Welcome Message",
        value=welcome_message,
        inline=False
    )
    
    if delete_seconds is not None:
        deletion_time = datetime.now(timezone.utc) + timedelta(seconds=delete_seconds)
        time_duration = format_time_duration(delete_seconds)
        acceptance_embed.add_field(
            name="Channel Deletion",
            value=f"This channel will be automatically deleted in {time_duration} (<t:{int(deletion_time.timestamp())}:R>)",
            inline=False
        )
    else:
        acceptance_embed.add_field(
            name="Channel Status",
            value="This channel will remain open for further discussion.",
            inline=False
        )
    
    await interaction.response.send_message(embed=acceptance_embed)
    
    # Try to notify the applicant via DM
    dm_status = "❌ Failed to send DM"
    try:
        # Find the applicant by extracting Discord ID from the application embed
        applicant = None
        applicant_id = None
        
        logger.info(f"Looking for application embed in channel {channel.name}")
        
        # Look for the application embed in the channel
        async for message in channel.history(limit=50):
            if message.embeds and message.author == interaction.guild.me:
                embed = message.embeds[0]
                logger.info(f"Found embed with title: {embed.title}")
                if embed.title and "Application from" in embed.title:
                    logger.info(f"Found application embed, checking for Discord ID field")
                    # Look for Discord ID field in the embed
                    for field in embed.fields:
                        logger.info(f"Checking field: {field.name} = {field.value}")
                        if field.name == "Discord ID" and field.value:
                            try:
                                applicant_id = int(field.value)
                                logger.info(f"Extracted applicant ID: {applicant_id}")
                                break
                            except ValueError:
                                logger.error(f"Could not parse Discord ID: {field.value}")
                                continue
                    if applicant_id:
                        break
        
        if not applicant_id:
            logger.error(f"Could not find Discord ID in application embed for channel {channel.name}")
            dm_status = "❌ Could not find applicant Discord ID"
        else:
            # Try to get the applicant user object using the ID
            applicant = None
            try:
                # First try to get from guild cache
                applicant = interaction.guild.get_member(applicant_id)
                if applicant:
                    logger.info(f"Found applicant in guild cache: {applicant.display_name} ({applicant.id})")
                else:
                    # If not in cache, fetch directly from Discord API
                    logger.info(f"Member not in cache, fetching from Discord API...")
                    applicant = await bot.fetch_user(applicant_id)
                    logger.info(f"Fetched applicant from API: {applicant.display_name} ({applicant.id})")
            except discord.NotFound:
                logger.error(f"User with ID {applicant_id} not found on Discord")
                dm_status = "❌ User not found on Discord"
                applicant = None
            except Exception as e:
                logger.error(f"Error fetching user {applicant_id}: {e}")
                dm_status = f"❌ Error fetching user: {str(e)}"
                applicant = None
            
            if applicant:
                dm_embed = discord.Embed(
                    title="🎉 Application Approved!",
                    description=f"Congratulations! Your application to **{interaction.guild.name}** has been approved!",
                    color=discord.Color.green()
                )
                dm_embed.add_field(name="Status", value="✅ Approved", inline=True)
                dm_embed.add_field(name="Welcome Message", value=welcome_message, inline=False)
                dm_embed.add_field(
                    name="What's Next?",
                    value="You should now have access to the guild! Check out the guild channels and feel free to introduce yourself.",
                    inline=False
                )
                
                await applicant.send(embed=dm_embed)
                logger.info(f"Successfully sent approval notification to {applicant.display_name}")
                dm_status = "✅ DM sent successfully"
            
    except discord.Forbidden:
        logger.warning(f"Could not send DM to applicant - DMs may be disabled")
        dm_status = "❌ DMs disabled or blocked"
    except Exception as e:
        logger.error(f"Error sending approval DM: {e}")
        dm_status = f"❌ Error: {str(e)}"
    
    # Send a follow-up message to the officer about DM status
    try:
        await interaction.followup.send(
            f"**DM Notification Status:** {dm_status}",
            ephemeral=True
        )
    except Exception as e:
        logger.error(f"Could not send DM status follow-up: {e}")
    
    # Schedule channel deletion if requested
    if delete_seconds is not None:
        asyncio.create_task(schedule_channel_deletion(channel, delete_seconds))
        time_duration = format_time_duration(delete_seconds)
        logger.info(f"Application approved by {interaction.user.display_name} in {channel.name}. Channel scheduled for deletion in {time_duration}.")
    else:
        logger.info(f"Application approved by {interaction.user.display_name} in {channel.name}. Channel will remain open.")

@bot.event
async def on_message(message):
    # Ignore messages from bots
    if message.author.bot:
        return
    
    # Process DM messages for ongoing applications
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        if user_id in ongoing_applications:
            application_handler = ongoing_applications[user_id]
            await application_handler.process_answer(message)
            return
    
    # Process commands
    await bot.process_commands(message)

@bot.event
async def on_ready():
    if bot.user:
        logger.info(f'Bot logged in as {bot.user.name} (ID: {bot.user.id})')
    else:
        logger.info('Bot logged in')
    
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
