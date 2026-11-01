import discord
from discord.ext import commands
import os
import logging
import asyncio
import time
from datetime import datetime, timezone
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
APPROVAL_ROLE_IDS = tuple(int(value.strip()) for value in os.getenv(
    "APPROVAL_ROLE_IDS", "616354179522363400,1548423178794307745"
).split(",") if value.strip())
if not APPROVAL_ROLE_IDS:
    raise RuntimeError("APPROVAL_ROLE_IDS must contain at least one role ID")
ONBOARDING_LINKS = {
    "Guild rules": os.getenv("WELCOME_RULES_URL", ""),
    "Raid schedule": os.getenv("WELCOME_SCHEDULE_URL", ""),
    "Introductions": os.getenv("WELCOME_INTRODUCTIONS_URL", ""),
}

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
    "What is your full WoW: Forever character name (or planned name)?",
    "What class, preferred spec, and role do you plan to play? Include any alts you plan to play.",
    "Are you interested in raiding, dungeons, PvP, or social/casual play? What are you looking for in our guild?",
    "What is your timezone, and which days and times are you usually available? If you want to raid, what schedule can you commit to?",
    "Have you read our guild rules and any posted raid schedule? Are there any expectations or scheduling conflicts you would like to discuss?",
    "Tell us about yourself and your WoW experience. Warcraft Logs or other relevant links are optional; it is fine if you do not have logs.",
    "How did you hear about us, and do you have any friends or family in the guild? If so, who?",
    "Any additional comments or questions? You can answer 'none'.",
]

# Store ongoing applications
ongoing_applications = {}
_application_store = None
_registry_ready = set()
_registry_locks = {}
_decision_locks = {}
_deletion_tasks = {}


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

def answer_embeds(question_list, answers, title, footer=""):
    """Keep each message below Discord's limits without dropping later answers."""
    embeds = [discord.Embed(title=title, color=discord.Color.blue())]
    embeds[0].set_footer(text=footer)
    for index, answer in enumerate(answers):
        name = f"Q{index + 1}: {question_list[index]}"[:256]
        if len(embeds[-1]) + len(name) + len(answer) > 5500 or len(embeds[-1].fields) >= 20:
            embeds.append(discord.Embed(title="Application answers (continued)", color=discord.Color.blue()))
        embeds[-1].add_field(name=name, value=answer, inline=False)
    return embeds


class ApplicationHandler:
    def __init__(self, user, guild):
        self.user = user
        self.guild = guild
        self.answers = []
        self.current_question = 0
        self.lock = asyncio.Lock()
        self.state = "starting"
        self.questions = list(questions)
        self.editing = False
        self.pending_long_answer = None

    def snapshot(self):
        return {
            "questions": self.questions, "answers": list(self.answers),
            "current_question": self.current_question, "state": self.state,
            "editing": self.editing, "pending_long_answer": self.pending_long_answer,
        }

    def restore(self, draft):
        self.questions = list(draft["questions"])
        self.answers = list(draft["answers"])
        self.current_question = draft["current_question"]
        self.state = draft["state"]
        self.editing = draft["editing"]
        self.pending_long_answer = draft["pending_long_answer"]

    def save_progress(self):
        get_application_store().save_draft(self.guild.id, self.user.id, self.snapshot())

    def is_current(self):
        return ongoing_applications.get(self.user.id) is self

    def release(self):
        if self.is_current():
            del ongoing_applications[self.user.id]
        
    async def start_application(self):
        """Start the application process by sending the first question"""
        async with self.lock:
            if not self.is_current() or self.state not in ("starting", "active", "review"):
                return False
            if self.state == "starting":
                self.state = "active"
            try:
                self.save_progress()
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
        if self.state == "review":
            await self.send_review()
        elif self.current_question < len(self.questions):
            question_num = self.current_question + 1
            question = self.questions[self.current_question]
            
            embed = discord.Embed(
                title=f"WoW: Forever Application - Question {question_num}/{len(self.questions)}",
                description=question,
                color=discord.Color.blue()
            )
            if self.editing and self.current_question < len(self.answers):
                embed.add_field(name="Current answer", value=self.answers[self.current_question], inline=False)
            embed.set_footer(text="Answer in one message (up to 800 characters). Commands: back, edit N, review, resume, cancel.")
            
            await self.user.send(embed=embed)
        else:
            await self.send_review()

    async def send_review(self):
        for embed in answer_embeds(self.questions, self.answers, "Review your WoW: Forever application"):
            await self.user.send(embed=embed)
        if len(self.answers) == len(self.questions):
            await self.user.send("Check your answers, then type **submit** to send them to the officers. "
                                 "Use **edit N** (for example, `edit 3`), **back**, or **cancel**. Your progress is saved.")
        else:
            await self.user.send(f"{len(self.answers)}/{len(self.questions)} answers saved. "
                                 "Use **edit N**, or **resume** to see your next question.")
    
    async def process_answer(self, message):
        """Process the user's answer and move to next question"""
        async with self.lock:
            if not self.is_current() or self.state not in ("active", "review"):
                return
            previous = self.snapshot()
            try:
                await self._process_answer(message)
            except Exception:
                logger.exception("Could not advance application for %s", self.user.id)
                try:
                    saved = get_application_store().get_draft(self.user.id)
                except Exception:
                    saved = None
                self.restore(saved or previous)
                try:
                    await self.user.send("I couldn't continue the application. Your last saved progress is retained. "
                                         "Type **resume** to check it and try again.")
                except discord.HTTPException:
                    logger.warning("Could not deliver draft recovery message to %s", self.user.id)

    async def _process_answer(self, message):
        command = message.content.strip().lower()
        if command == 'cancel':
            await self._cancel_application()
            return

        if command == 'resume':
            await self.send_current_question()
            return
        if command == 'review':
            self.editing = False
            self.pending_long_answer = None
            self.current_question = len(self.answers)
            self.state = "review" if len(self.answers) == len(self.questions) else "active"
            self.save_progress()
            await self.send_review()
            return
        if command == 'back' or command == 'edit' or command.startswith('edit '):
            try:
                target = self.current_question - 1 if command == 'back' else int(command.split()[1]) - 1
                if not 0 <= target < len(self.answers):
                    raise ValueError
            except (ValueError, IndexError):
                await self.user.send("Use **edit N** for a question you have already answered. "
                                     "**back** goes to the previous answer.")
                return
            self.current_question, self.editing, self.state = target, True, "active"
            self.pending_long_answer = None
            self.save_progress()
            await self.send_current_question()
            return
        if command == 'submit':
            if self.state != "review" or len(self.answers) != len(self.questions):
                await self.user.send("Please finish your answers and type **review** before submitting.")
                return
            await self._complete_application()
            return
        if self.state == "review":
            await self.user.send("Your answers are saved. Type **submit**, **edit N**, **back**, or **cancel**.")
            return
        
        # Handle "proceed" command for long answers
        if command == 'proceed' and self.pending_long_answer is not None:
            answer = self.validate_and_truncate_answer(self.pending_long_answer)
            self.pending_long_answer = None
        else:
            # Remove excessive whitespace
            clean_content = ' '.join(message.content.split())
            if not clean_content:
                await self.user.send("Please send a text answer, or 'none' for an optional question.")
                return
            
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
                # Store the long answer for potential truncation
                self.pending_long_answer = clean_content
                self.save_progress()
                await self.user.send(embed=embed)
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
        self.pending_long_answer = None
        if self.current_question < len(self.answers):
            self.answers[self.current_question] = answer
        else:
            self.answers.append(answer)
        self.current_question = len(self.answers)
        self.editing = False
        self.state = "review" if len(self.answers) == len(self.questions) else "active"
        self.save_progress()
        await self.send_current_question()
    
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
            if self.is_current() and self.state in ("active", "review"):
                await self._cancel_application()

    async def _cancel_application(self):
        get_application_store().delete_draft(self.guild.id, self.user.id)
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
        if (not self.is_current() or self.state != "review"
                or len(self.answers) != len(self.questions)):
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
            store.record_created(record, reservation, self.questions, self.answers)
            
            embeds = answer_embeds(
                self.questions, self.answers, f"New Application from {self.user.display_name}",
                f"Application submitted by {self.user} ({self.user.id})"
            )
            embeds[0].add_field(name="Discord ID", value=str(self.user.id), inline=False)
            application_message = await interview_channel.send(
                content=f"{self.user.mention} <@&616354080704430130>",
                embed=embeds[0]
            )
            for embed in embeds[1:]:
                await interview_channel.send(embed=embed)
            get_application_store().mark_submitted(record, application_message.id)
            self.state = "completed"
            
            # Notify user of completion
            completion_embed = discord.Embed(
                title="✅ Application Submitted Successfully!",
                description=f"Your WoW: Forever guild application has been submitted and is awaiting officer review.\n\nYour application channel: {interview_channel.mention}",
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
                "You already have an application in progress. Type 'resume' in your DMs to continue, or 'cancel' to start over.",
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
            draft = get_application_store().get_draft(user.id)
            if draft is not None:
                if draft["guild_id"] != guild.id:
                    raise ApplicationRegistryError("A saved application belongs to a different server")
                application_handler.restore(draft)
            success = await application_handler.start_application()
            if success:
                await interaction.followup.send(
                    "✅ Your application is ready in your DMs. Saved progress has been restored if available.", ephemeral=True
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
            if application_handler.state not in ("active", "review", "finalizing"):
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
        deadline = get_application_store().workflow(record)["delete_at"]
        if deadline is None:
            return
        await asyncio.sleep(delay_seconds)
        channel, _ = await verified_application_channel(guild, record.channel_id, expected=record)
        workflow = get_application_store().workflow(record)
        if (workflow["delete_at"] != deadline or deadline > time.time()
                or workflow["status"] not in ("approved", "rejected")):
            return
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


def queue_channel_deletion(guild, record):
    workflow = get_application_store().workflow(record)
    if workflow["delete_at"] is None or workflow["status"] not in ("approved", "rejected"):
        return
    key = (guild.id, record.channel_id)
    existing = _deletion_tasks.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(schedule_channel_deletion(
        guild, record, max(0, workflow["delete_at"] - time.time())
    ))
    _deletion_tasks[key] = task

    def finished(completed):
        if _deletion_tasks.get(key) is completed:
            _deletion_tasks.pop(key, None)

    task.add_done_callback(finished)


def restore_cleanup_tasks(guild):
    for record in get_application_store().scheduled_deletions(guild.id):
        queue_channel_deletion(guild, record)

@bot.tree.command(name="noxpost", description="Post the guild application button (Admin only)")
@discord.app_commands.default_permissions(administrator=True)
async def post_application(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏰 WoW: Forever Guild Application",
        description=f"Ready to join our WoW: Forever guild? Click below to start or resume your application!\n\n📝 Answer {len(questions)} questions in DMs, then review and submit\n⏱️ Takes about 5 minutes; progress is saved\n🔒 Your responses are shared with the review team in your private application channel",
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

class RoleAssignmentError(Exception):
    pass


async def grant_approval_roles(guild, applicant_id, role_ids):
    # REST lookup works without the privileged member intent and avoids stale roles.
    applicant = await guild.fetch_member(applicant_id)
    roles = [guild.get_role(role_id) for role_id in role_ids]
    missing_ids = [role_id for role_id, role in zip(role_ids, roles) if role is None]
    if missing_ids:
        raise RoleAssignmentError(f"Configured role IDs were not found: {', '.join(map(str, missing_ids))}.")
    current_ids = {role.id for role in applicant.roles}
    needed = [role for role in roles if role.id not in current_ids]
    if needed:
        if guild.me is None or not guild.me.guild_permissions.manage_roles:
            raise RoleAssignmentError("The bot needs Manage Roles permission.")
        if any(not role.is_assignable() for role in needed):
            raise RoleAssignmentError("Both roles must be below the bot's highest role and must not be managed roles.")
        # Atomic role additions preserve unrelated roles and can safely be retried
        # after a partial failure or a crash between Discord and the SQLite commit.
        await applicant.add_roles(*needed, reason="WoW: Forever application approved", atomic=True)
    return applicant


def onboarding_text():
    lines = ["Your Discord membership roles have been granted."]
    lines.extend(f"[{label}]({url})" for label, url in ONBOARDING_LINKS.items() if url)
    lines.append("Read the guild rules and schedule, then introduce yourself. "
                 "Contact an officer for your in-game guild invitation.")
    return "\n".join(lines)


async def decide_application(interaction, approved, note, delete_time):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("❌ This command can only be used by server members.", ephemeral=True)
        return
    user_roles = {role.id for role in interaction.user.roles}
    staff_roles = {int(role_id) for role_id in (OFFICER_ROLE_ID, ADMIN_ROLE_ID) if role_id}
    if not interaction.user.guild_permissions.administrator and not user_roles.intersection(staff_roles):
        await interaction.response.send_message(
            "❌ Only officers and administrators can process applications.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ This command can only be used in text channels.", ephemeral=True)
        return
    delete_seconds = parse_time_string(delete_time) if delete_time is not None else None
    if delete_time is not None and delete_seconds is None:
        await interaction.response.send_message(
            "❌ Invalid time format. Use '10m' or '1h'; maximum is 1 week (168h or 10080m).", ephemeral=True)
        return
    if not note.strip() or len(note) > 1000:
        await interaction.response.send_message("❌ Please use a message between 1 and 1000 characters.", ephemeral=True)
        return
    verified = await prepare_application_decision(interaction)
    if verified is None:
        return
    channel, record = verified
    lock = _decision_locks.setdefault((record.guild_id, record.channel_id), asyncio.Lock())
    async with lock:
        try:
            channel, record = await verified_application_channel(interaction.guild, record.channel_id, expected=record)
            store = get_application_store()
            workflow = store.workflow(record)
            status = "approved" if approved else "rejected"
            in_progress = "approving" if approved else "rejecting"
            if workflow["status"] in ("approved", "rejected"):
                await interaction.followup.send(
                    f"This application was already {workflow['status']} by <@{workflow['reviewer_id']}>.",
                    ephemeral=True)
                return
            if workflow["status"] not in ("pending", in_progress):
                await interaction.followup.send(
                    "A different decision is already in progress. Finish that operation before continuing.", ephemeral=True)
                return
            if workflow["status"] == "pending":
                store.begin_decision(record, in_progress, interaction.user.id, note, delete_seconds,
                                     APPROVAL_ROLE_IDS if approved else ())
                workflow = store.workflow(record)
            applicant = None
            if approved:
                try:
                    applicant = await grant_approval_roles(
                        interaction.guild, record.applicant_id, workflow["approval_role_ids"])
                except (discord.HTTPException, RoleAssignmentError) as error:
                    logger.warning("Approval roles incomplete for %s: %s", record.applicant_id, error)
                    await interaction.followup.send(
                        f"❌ Approval is pending: {error}\n"
                        "Check that the applicant is still in the server, the bot has Manage Roles, and both roles "
                        "are below its highest role. Then retry **/noxapprove**. Any roles already granted are kept; "
                        "approval notifications and channel cleanup wait until both roles are granted.", ephemeral=True)
                    return
            workflow = store.finish_decision(record, status)
        except Exception:
            logger.exception("Could not persist application decision for channel %s", record.channel_id)
            await interaction.followup.send(
                "❌ The decision could not be completed. Saved progress is retained; retry the same command. "
                "If approval roles were already granted, retrying will keep them.", ephemeral=True)
            return

        # A retry completes the original officer's saved decision, including its note
        # and cleanup duration, rather than silently replacing the audit information.
        color = discord.Color.green() if approved else discord.Color.red()
        embed = discord.Embed(
            title="✅ Application Approved" if approved else "❌ Application Rejected",
            description=f"**Applicant:** <@{record.applicant_id}>\n**Reviewed by:** <@{workflow['reviewer_id']}>",
            color=color, timestamp=datetime.fromtimestamp(workflow["decided_at"], timezone.utc)
        )
        embed.add_field(name="Welcome Message" if approved else "Reason", value=workflow["decision_note"], inline=False)
        if workflow["delete_at"] is not None:
            embed.add_field(name="Channel Deletion",
                            value=f"Scheduled for <t:{int(workflow['delete_at'])}:F> (<t:{int(workflow['delete_at'])}:R>).",
                            inline=False)
        else:
            embed.add_field(name="Channel Status", value="This channel will remain open for further discussion.", inline=False)
        channel_status = "Review channel updated"
        try:
            await channel.send(embed=embed)
        except Exception:
            logger.exception("Could not post decision in channel %s", record.channel_id)
            channel_status = "Decision saved, but the review channel notification failed"

        dm_status = "❌ Failed to send DM"
        try:
            if applicant is None:
                applicant = interaction.guild.get_member(record.applicant_id) or await bot.fetch_user(record.applicant_id)
            dm_embed = discord.Embed(
                title="🎉 Application Approved!" if approved else "Application Update",
                description=f"Your WoW: Forever application to **{interaction.guild.name}** has been {status}.", color=color)
            dm_embed.add_field(name="Welcome Message" if approved else "Reason", value=workflow["decision_note"], inline=False)
            dm_embed.add_field(
                name="What's Next?",
                value=onboarding_text() if approved else "Contact an officer with questions or to discuss a future application.",
                inline=False)
            await applicant.send(embed=dm_embed)
            dm_status = "✅ DM sent successfully"
        except discord.Forbidden:
            dm_status = "❌ DMs disabled or blocked"
        except Exception:
            logger.exception("Could not deliver application decision to %s", record.applicant_id)
        finally:
            queue_channel_deletion(interaction.guild, record)
        await interaction.followup.send(
            f"**Application {status}.** {channel_status}.\n**DM Notification Status:** {dm_status}", ephemeral=True)


@bot.tree.command(name="noxreject", description="Reject an application")
@discord.app_commands.describe(reason="Reason for rejection (optional)",
                               delete_time="Time until channel deletion (e.g., '10m', '1h'); omit to keep it")
async def reject_application(interaction: discord.Interaction, reason: str = "No reason provided",
                             delete_time: str | None = None):
    await decide_application(interaction, False, reason, delete_time)


@bot.tree.command(name="noxapprove", description="Approve an application and grant membership roles")
@discord.app_commands.describe(welcome_message="Custom welcome message (optional)",
                               delete_time="Time until channel deletion (e.g., '10m', '1h'); omit to keep it")
async def approve_application(interaction: discord.Interaction,
                              welcome_message: str = "Welcome to our WoW: Forever guild! We're excited to have you join us.",
                              delete_time: str | None = None):
    await decide_application(interaction, True, welcome_message, delete_time)


@bot.event
async def on_message(message):
    # Ignore messages from bots
    if message.author.bot:
        return
    
    # Process DM messages for ongoing applications
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        if user_id not in ongoing_applications:
            try:
                draft = get_application_store().get_draft(user_id)
                if draft is not None:
                    guild = bot.get_guild(draft["guild_id"])
                    if guild is None:
                        await message.author.send("Your saved application's server is unavailable. Please contact an officer.")
                        return
                    handler = ApplicationHandler(message.author, guild)
                    handler.restore(draft)
                    ongoing_applications[user_id] = handler
            except Exception:
                logger.exception("Could not restore application for %s", user_id)
                await message.author.send("I couldn't load your saved application. Please try again later.")
                return
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
                restore_cleanup_tasks(guild)
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
