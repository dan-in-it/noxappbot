import discord
from discord.ext import commands
import os
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from application_store import ApplicationRecord, ApplicationStore

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
    "Which raid team are you applying to? TBC Team, Floater/Casual (TBC), 10M Weasals Weekday Team (MoP), 10M Cumby Team (MoP)",
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
_application_store = None
_registry_ready = set()
_registry_locks = {}


class ApplicationRegistryError(Exception):
    """The bot cannot establish a trusted application identity."""


def get_application_store():
    global _application_store
    if _application_store is None:
        path = Path(os.getenv("APPLICATION_DB_PATH", "data/applications.sqlite3")).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        _application_store = ApplicationStore(path)
    return _application_store


async def legacy_application_record(channel):
    """Adopt only an unambiguous original application posted by this bot."""
    record = None
    async for message in channel.history(limit=None, oldest_first=True):
        if message.author.id != channel.guild.me.id or message.webhook_id is not None:
            continue
        for embed in message.embeds:
            if not (embed.title or "").startswith("New Application from "):
                continue
            ids = [field.value for field in embed.fields if field.name == "Discord ID"]
            if len(ids) != 1 or not ids[0].isascii() or not ids[0].isdecimal():
                raise ApplicationRegistryError(f"Invalid application identity in channel {channel.id}")
            applicant_id = int(ids[0])
            footer = embed.footer.text or ""
            if (not 0 < applicant_id < 2**63
                    or not footer.startswith("Application submitted by ")
                    or not footer.endswith(f" ({applicant_id})") or record is not None):
                raise ApplicationRegistryError(f"Ambiguous application history in channel {channel.id}")
            record = ApplicationRecord(channel.guild.id, channel.id, applicant_id, message.id)
    return record


async def ensure_application_registry(guild):
    """Migrate existing channels once per process, before accepting applications."""
    if guild.id in _registry_ready:
        return
    lock = _registry_locks.setdefault(guild.id, asyncio.Lock())
    async with lock:
        if guild.id in _registry_ready:
            return
        store = get_application_store()
        channels = await guild.fetch_channels()
        category_id = int(INTERVIEW_CATEGORY_ID)
        if not any(isinstance(channel, discord.CategoryChannel) and channel.id == category_id
                   for channel in channels):
            raise ApplicationRegistryError("Interview category not found in this server")
        for channel in channels:
            # Old applications may have been moved before this registry existed.
            # Their generated names locate migration candidates; only the original
            # bot-authored message establishes identity, and decisions still require
            # the current interview category.
            if (isinstance(channel, discord.TextChannel)
                    and (channel.category_id == category_id
                         or channel.name.startswith(f"{APPLICATION_CHANNEL_PREFIX.lower()}-"))
                    and store.get(guild.id, channel.id) is None):
                record = await legacy_application_record(channel)
                if record is not None:
                    store.add(record)
        # A failed history/API/DB read must leave migration incomplete and retryable.
        _registry_ready.add(guild.id)


async def existing_application_channel(guild, applicant_id):
    await ensure_application_registry(guild)
    store = get_application_store()
    for record in store.for_applicant(guild.id, applicant_id):
        try:
            return await guild.fetch_channel(record.channel_id)
        except discord.NotFound as error:
            if error.code != 10003:  # Only a confirmed Unknown Channel releases the record.
                raise
            store.remove(record)
    if store.has_reservation(guild.id, applicant_id):
        raise ApplicationRegistryError("An earlier submission needs administrator review")
    return None


async def verified_application_channel(guild, channel_id, expected=None):
    await ensure_application_registry(guild)
    record = get_application_store().get(guild.id, channel_id)
    if record is None or record.message_id is None or (expected is not None and record != expected):
        raise ApplicationRegistryError("Channel has no matching submitted application record")
    channel = await guild.fetch_channel(channel_id)
    if (not isinstance(channel, discord.TextChannel) or channel.guild.id != record.guild_id
            or channel.category_id != int(INTERVIEW_CATEGORY_ID)
            or get_application_store().get(guild.id, channel_id) != record):
        raise ApplicationRegistryError("Application channel is outside the configured interview category")
    return channel, record


async def prepare_application_decision(interaction):
    # Verification can read legacy history or call Discord; acknowledge promptly.
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        return await verified_application_channel(interaction.guild, interaction.channel.id)
    except Exception:
        logger.exception("Could not verify application channel %s", interaction.channel.id)
        await interaction.followup.send(
            "❌ This channel could not be verified as a submitted application in the interview category. "
            "Please contact an administrator.", ephemeral=True
        )
        return None

class ApplicationHandler:
    def __init__(self, user, guild):
        self.user = user
        self.guild = guild
        self.answers = []
        self.current_question = 0
        self.lock = asyncio.Lock()
        self.state = "starting"

    def is_current(self):
        return ongoing_applications.get(self.user.id) is self

    def release(self):
        if self.is_current():
            del ongoing_applications[self.user.id]
        
    async def start_application(self):
        """Start the application process by sending the first question"""
        async with self.lock:
            if not self.is_current() or self.state != "starting":
                return False
            self.state = "active"
            try:
                await self.send_current_question()
            except discord.Forbidden:
                self.state = "failed"
                self.release()
                logger.error(f"Cannot send DM to {self.user.display_name}")
                return False
            except Exception:
                self.state = "failed"
                self.release()
                raise
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
            await self._complete_application()
    
    async def process_answer(self, message):
        """Process the user's answer and move to next question"""
        async with self.lock:
            if not self.is_current() or self.state != "active":
                return
            await self._process_answer(message)

    async def _process_answer(self, message):
        if message.content.lower() == 'cancel':
            await self._cancel_application()
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
            await self._complete_application()
    
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
        async with self.lock:
            if self.is_current() and self.state == "active":
                await self._cancel_application()

    async def _cancel_application(self):
        self.state = "cancelled"
        self.release()
        embed = discord.Embed(
            title="Application Cancelled",
            description="Your guild application has been cancelled. You can start a new application anytime by clicking the Apply button again.",
            color=discord.Color.red()
        )
        await self.user.send(embed=embed)
        
    async def complete_application(self):
        """Complete the application and create the channel"""
        async with self.lock:
            await self._complete_application()

    async def _complete_application(self):
        if not self.is_current() or self.state != "active":
            return
        self.state = "finalizing"
        try:
            existing_channel = await existing_application_channel(self.guild, self.user.id)
            if existing_channel is not None:
                await self.user.send(f"You already have an application channel: {existing_channel.mention}")
                return
            # INTERVIEW_CATEGORY_ID is guaranteed to be non-None due to startup validation
            category_id = int(INTERVIEW_CATEGORY_ID)  # type: ignore
            category = await self.guild.fetch_channel(category_id)
            
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
            if not self.is_current() or self.state != "finalizing":
                return
            store = get_application_store()
            reservation = store.reserve(self.guild.id, self.user.id)
            try:
                interview_channel = await self.guild.create_text_channel(
                    name=channel_name,
                    category=category,
                    overwrites=overwrites,
                )
            except discord.HTTPException as error:
                # Discord definitively refused these requests. Timeouts, server
                # errors and process crashes can leave a channel, so keep their
                # durable reservation until an administrator reconciles it.
                if error.status in (400, 401, 403, 404):
                    store.release_reservation(self.guild.id, self.user.id, reservation)
                raise
            record = ApplicationRecord(self.guild.id, interview_channel.id, self.user.id)
            # Retain the identity even if publishing the embed or sending the DM fails.
            store.record_created(record, reservation)
            
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
            application_message = await interview_channel.send(
                content=f"{self.user.mention} <@&616354080704430130>",
                embed=embed
            )
            get_application_store().mark_submitted(record, application_message.id)
            self.state = "completed"
            
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
            if self.state == "finalizing":
                self.state = "failed"
            self.release()

class ApplicationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply to Guild", style=discord.ButtonStyle.primary, custom_id="apply_button")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        guild = interaction.guild
        
        if guild is None:
            await interaction.response.send_message("❌ Applications must be started in a server.", ephemeral=True)
            return
        
        # Check if user already has an ongoing application
        if user.id in ongoing_applications:
            await interaction.response.send_message(
                "You already have an application in progress. Please check your DMs to continue, or type 'cancel' to start over.",
                ephemeral=True
            )
            return
        
        # Reserve before the first await: duplicate Apply events must share one owner.
        application_handler = ApplicationHandler(user, guild)
        ongoing_applications[user.id] = application_handler
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            existing_channel = await existing_application_channel(guild, user.id)
            if existing_channel is not None:
                await interaction.followup.send(
                    f"You already have an application channel: {existing_channel.mention}", ephemeral=True
                )
                return
            success = await application_handler.start_application()
            if success:
                await interaction.followup.send(
                    "✅ Application started! Please check your DMs to continue with the questions.", ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ I couldn't send you a DM. Please make sure your DMs are open and try again.", ephemeral=True
                )
        except Exception:
            logger.exception("Could not start application for %s", user.id)
            await interaction.followup.send(
                "❌ I couldn't verify your application status. Please contact an administrator.", ephemeral=True
            )
        finally:
            if application_handler.state not in ("active", "finalizing"):
                application_handler.release()

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

async def schedule_channel_deletion(guild, record, delay_seconds):
    """Schedule a channel for deletion after a specified delay in seconds"""
    try:
        await asyncio.sleep(delay_seconds)
        channel, _ = await verified_application_channel(guild, record.channel_id, expected=record)
        await channel.delete(reason="Application processed - automatic cleanup")
        get_application_store().remove(record)
        logger.info(f"Deleted application channel: {channel.name}")
    except discord.NotFound as error:
        if error.code == 10003:
            get_application_store().remove(record)
            logger.info("Application channel %s was already deleted", record.channel_id)
        else:
            logger.exception("Could not confirm application channel deletion")
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
    
    verified = await prepare_application_decision(interaction)
    if verified is None:
        return
    channel, application_record = verified
    applicant_name = f"<@{application_record.applicant_id}>"
    
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
    
    await channel.send(embed=rejection_embed)
    
    # Try to notify the applicant via DM
    dm_status = "❌ Failed to send DM"
    try:
        applicant_id = application_record.applicant_id
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
        asyncio.create_task(schedule_channel_deletion(interaction.guild, application_record, delete_seconds))
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
    
    verified = await prepare_application_decision(interaction)
    if verified is None:
        return
    channel, application_record = verified
    applicant_name = f"<@{application_record.applicant_id}>"
    
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
    
    await channel.send(embed=acceptance_embed)
    
    # Try to notify the applicant via DM
    dm_status = "❌ Failed to send DM"
    try:
        applicant_id = application_record.applicant_id
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
        asyncio.create_task(schedule_channel_deletion(interaction.guild, application_record, delete_seconds))
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

    for guild in bot.guilds:
        if guild.get_channel(int(INTERVIEW_CATEGORY_ID)) is not None:
            try:
                await ensure_application_registry(guild)
            except Exception:
                logger.exception("Application registry initialization failed for guild %s", guild.id)
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    
    # Add persistent view
    bot.add_view(ApplicationView())
    
    logger.info('Application bot is ready and listening for applications')

if __name__ == "__main__":
    bot.run(TOKEN)
