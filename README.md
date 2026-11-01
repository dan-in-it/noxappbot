# NoxAppBot - Discord Guild Application Bot

A Discord bot designed to streamline the guild application process for our WoW: Forever guild. Applicants answer questions through private messages, and their responses are posted in private channels for review by officers.

## ✨ Features

- **🔘 Button-Initiated Applications:** Simple "Apply" button to start the process
- **💬 DM-Based Application Process:** Questions sent one-by-one via private messages
- **🔒 Private Application Channels:** Automatically creates secure, private channels for each application
- **👥 Role-Based Access:** Configurable access for officers and administrators
- **⚡ Slash Commands:** Modern Discord slash command interface with approval/rejection workflow
- **🚫 No Privileged Intents:** Works with default Discord permissions only
- **🛡️ Duplicate Prevention:** Prevents users from submitting multiple applications
- **📊 Comprehensive Logging:** Built-in error handling and logging
- **⚙️ Easy Configuration:** Environment-based configuration with validation
- **⏰ Automatic Channel Cleanup:** Optional scheduled deletion of application channels
- **✅ Application Management:** Built-in approve/reject commands with notifications
- **💾 Saved Progress:** Draft answers, edits, decisions, and cleanup deadlines survive restarts
- **📝 Review Before Submission:** Back/Edit commands and an explicit final submit step
- **🎉 Approval Roles:** Grants both configured membership roles before confirming approval
- **📬 DM Notification Status:** Officers receive immediate feedback on whether DM notifications were delivered successfully
- **🔄 Robust User Lookup:** Fallback mechanisms to ensure users are found even when not in server cache

## 📋 Prerequisites

- **Python 3.10+** installed on your system
- **Discord account** with a server where you have administrative permissions
- **Basic command line knowledge** for setup and running

## 🚀 Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/dan-in-it/noxappbot.git
cd noxappbot
pip install -r requirements.txt
```

### 2. Create Discord Application

1. Visit the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **"New Application"** and name it (e.g., "Guild Application Bot")
3. Go to the **"Bot"** tab
4. Click **"Reset Token"** and **copy the token immediately**
5. **Important:** Keep this token secure - it's your bot's password!

### 3. Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your favorite text editor
# Add your bot token and category ID
```

Your `.env` file should look like:
```env
# Required Configuration
DISCORD_BOT_TOKEN="your_actual_bot_token_here"
INTERVIEW_CATEGORY_ID="your_category_id_here"

# Optional Role Configuration
OFFICER_ROLE_ID="123456789012345678"
ADMIN_ROLE_ID="987654321098765432"

# Optional Bot Configuration
APPLICATION_CHANNEL_PREFIX="application"
LOG_LEVEL="INFO"
```

### **Required Configuration**

**Getting the Category ID:**
1. In Discord: **User Settings → Advanced → Enable Developer Mode**
2. Right-click your desired category → **"Copy Category ID"**

### **Optional Configuration**

**Role IDs (for automatic access to application channels):**
1. **Officer Role ID:** Right-click your officer role → **"Copy Role ID"**
2. **Admin Role ID:** Right-click your admin role → **"Copy Role ID"**
3. Leave empty (`""`) if you don't want automatic role access

**Bot Settings:**
- **APPLICATION_CHANNEL_PREFIX:** Channel name prefix (default: `application`)
- **LOG_LEVEL:** Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`)
- **APPLICATION_DB_PATH:** SQLite application data (default: `data/applications.sqlite3`). Relative paths resolve from the repository root. Keep this file on persistent storage and back it up across restarts and deployments. SQLite is included with Python; no database service is needed. The database contains private applicant answers and decision history, so restrict file access and protect its backups. Responses and decisions remain in the database after channel cleanup; cancellation removes an unfinished draft.

The bot runs as one process and records each application by server, channel, and immutable Discord user ID. Renaming a user or channel does not release the duplicate check. A retained channel continues to block reapplication after approval or rejection; the record is released only after the channel is confirmed deleted.

On startup, existing channels in the interview category and channels elsewhere with the configured `application-` name prefix are checked for migration. They are imported only when their history contains one original application message from this bot, with a valid Discord ID and matching submission footer. History is checked beyond the latest 50 messages, so the bot needs Read Message History for these channels. Missing or forged application messages are never trusted by channel name. Ambiguous history, unavailable history, or a database error prevents application operations until an administrator resolves it. Keep the original messages during the first migration. Legacy channels that were both renamed and moved out of the category must be moved back for migration; channels whose original application message is missing require administrator review.

Staff commands require a recorded, submitted application in the configured category. Scheduled deletion checks the current category and the same application record again before deleting. Moving a channel outside the category prevents automated cleanup, while its retained record still blocks reapplication. If posting stops partway after the channel is created, the applicant can type `submit` again or click Apply to post the missing parts in the same channel; parts already posted are not repeated, and staff commands wait until every part is posted.

Before requesting a new channel, the bot commits a reservation in SQLite. A timeout, crash, or database failure after that point keeps the applicant blocked even if the channel identity could not be saved. For recovery, stop the bot, confirm and remove any incomplete channel for that applicant, and remove only the matching `(guild_id, applicant_id)` row from the `application_reservations` table using a SQLite client. Preserve the other records and restart the bot. A definite Discord refusal (such as missing permissions) automatically releases the reservation.

### 4. Invite Bot to Server

1. In Discord Developer Portal: **OAuth2 → URL Generator**
2. **Scopes:** Select `bot` and `applications.commands`
3. **Bot Permissions:**
   - ✅ Manage Roles
   - ✅ Manage Channels
   - ✅ Send Messages
   - ✅ Embed Links
   - ✅ Read Message History
4. Copy the generated URL and authorize the bot to your server

### 5. Test Configuration (Optional but Recommended)

```bash
python -m unittest -v test_application_security test_application_workflow
```

This runs offline regression tests with temporary databases and mocked Discord calls; it does not validate your live Discord permissions.

### 6. Start the Bot

```bash
python src/bot.py
```

You should see:
```
INFO:__main__:Bot logged in as YourBotName (ID: 123456789)
INFO:__main__:Application bot is ready and listening for applications
```

## 📖 Usage Guide

### Setting Up Applications

1. **Post the Application Button** (Administrator only):
   ```
   /noxpost
   ```
   Use this slash command in the channel where you want the application button.

2. **Application Flow:**
   - User clicks "Apply to Guild" button
   - Bot sends first question via DM
   - User responds in DM, bot automatically sends next question
   - Progress is saved after every answer; use `resume` in DMs or click Apply again after a restart
   - After all 8 questions, review the answers and type `submit`
   - Use `back` or `edit N` to change an answer, `review` to see saved answers, or `cancel` to discard the draft
   - Only after `submit`, the bot creates private channel: `application-{username}`
   - The complete application is posted in one or more embeds in the new channel
   - User gets confirmation with channel link

### Managing Applications

**Available Commands:**
- `/noxapprove` - Grant both membership roles and approve an application, with an optional welcome message and cleanup timing
- `/noxreject` - Reject an application with optional reason and flexible channel cleanup timing
- `/noxsync` - Force sync slash commands (Admin only)

**Application Management:**
- **Private Channels:** Each application gets its own private channel
- **Access Control:** Only the applicant and configured roles can see the channel
- **Review Process:** Officers can discuss applications privately in these channels
- **Approval/Rejection:** Use slash commands to approve or reject with automatic notifications. The status, reviewer, note, and decision time are saved; completed decisions cannot be repeated or reversed with these commands.
- **DM Status Feedback:** Officers receive immediate confirmation of whether DM notifications were delivered
- **Automatic Cleanup:** Optionally schedule channel deletion after approval/rejection. Deadlines survive restarts; overdue deletions run when the bot returns, after rechecking the channel and application record.

**Command Examples:**
```
# Approve with automatic deletion in 10 minutes
/noxapprove welcome_message:"Welcome to our raiding team!" delete_time:10m

# Reject with automatic deletion in 1 hour
/noxreject reason:"Needs more experience" delete_time:1h

# Approve with automatic deletion in 24 hours
/noxapprove welcome_message:"Great to have you!" delete_time:24h

# Reject without automatic deletion
/noxreject reason:"Application incomplete"

# Approve with deletion in 30 minutes
/noxapprove delete_time:30m
```

**Time Format Options:**
- **Minutes:** Use `m` suffix (e.g., `10m`, `30m`, `45m`)
- **Hours:** Use `h` suffix (e.g., `1h`, `2h`, `24h`)
- **Backwards Compatibility:** Plain numbers are treated as hours (e.g., `24` = `24h`)
- **Limits:** Minimum 1 minute, maximum 1 week (10080m or 168h)

## ⚙️ Customization

### Modifying Questions

Edit the `questions` list in [`src/bot.py`](src/bot.py). The Forever form covers character plans, interests, availability, guild expectations, experience with optional logs, referrals, and comments:

```python
questions = [
    'What is your full WoW: Forever character name (or planned name)?',
    'What class, preferred spec, and role do you plan to play? Include any alts you plan to play.',
    'Are you interested in raiding, dungeons, PvP, or social/casual play? What are you looking for in our guild?',
    'What is your timezone, and which days and times are you usually available? If you want to raid, what schedule can you commit to?',
    'Have you read our guild rules and any posted raid schedule? Are there any expectations or scheduling conflicts you would like to discuss?',
    'Tell us about yourself and your WoW experience. Warcraft Logs or other relevant links are optional; it is fine if you do not have logs.',
    'How did you hear about us, and do you have any friends or family in the guild? If so, who?',
    "Any additional comments or questions? You can answer 'none'.",
]
```

**Important:** Drafts keep a snapshot of their questions so a later form update does not change the meaning of saved answers. New applications use the updated questions. Applicants edit one answer at a time; afterward they return to the review or the next unanswered question.

### Adding Officer/Admin Role Access

Officer and admin roles are configured through environment variables in your `.env` file. The bot automatically grants these roles access to application channels and permission to use approval/rejection commands.

**Configuration in `.env`:**
```env
# Optional Role Configuration
OFFICER_ROLE_ID="123456789012345678"  # Replace with your officer role ID
ADMIN_ROLE_ID="987654321098765432"    # Replace with your admin role ID
```

**Getting Role IDs:**
1. Enable Developer Mode in Discord: **User Settings → Advanced → Developer Mode**
2. Right-click the desired role → **"Copy Role ID"**
3. Paste the ID into your `.env` file

**Permissions Granted:**
- **Application Channel Access:** Automatic read/write access to all application channels
- **Command Usage:** Permission to use `/noxapprove` and `/noxreject` commands
- **Administrator Override:** Users with Administrator permission can always use commands regardless of role configuration

### Approval Roles and Welcome Links

The default approval roles are `616354179522363400` and `1548423178794307745`. Configure them and optional onboarding links in `.env`:

```env
APPROVAL_ROLE_IDS="616354179522363400,1548423178794307745"
WELCOME_RULES_URL=""
WELCOME_SCHEDULE_URL=""
WELCOME_INTRODUCTIONS_URL=""
```

Paste Discord channel links into the welcome settings to include them in approval DMs. Blank links use general onboarding instructions, including contacting an officer for an in-game guild invitation.

The bot must have **Manage Roles**, and both roles must be below its highest role and not managed by an integration. Approval uses a fresh member lookup and adds only missing roles, preserving other roles. If the member has left, a role is missing, or Discord rejects a role grant, the saved decision stays `approving`: no successful approval notification or cleanup is scheduled. Correct the issue and repeat `/noxapprove`. A partial role grant is kept and safely completed on retry, including after a restart. The original reviewer, welcome message, and cleanup duration are preserved; the cleanup countdown starts once both roles are granted and approval is saved.

Final decisions are stored before notifications are attempted. A notification failure does not undo the decision; the officer receives delivery feedback. Notifications are not replayed automatically after a crash. Review the recorded status before following up manually.

### Adjusting Response Handling

Send one answer per message, up to 800 characters. Longer answers prompt the applicant to shorten them or explicitly choose `proceed` to truncate. Each accepted answer is saved before advancing. The review and submission are split across messages when necessary so later answers are retained. Empty messages and attachment-only answers do not advance the form.

## 🔧 Troubleshooting

### Common Issues

**"Import discord could not be resolved"**
```bash
pip install discord.py python-dotenv
```

**"Bot not responding to commands"**
- Verify bot has proper permissions in your server
- Check that the bot is online (green status)
- Ensure you're using `/noxpost` with administrator permissions
- Make sure slash commands are synced (use `/noxsync` if needed)

**"Interview category not found"**
- Verify the category ID in your `.env` file
- Ensure the bot has access to the category
- Check that the category still exists

**"Permission denied when creating channels"**
- Bot needs "Manage Channels" permission
- Verify bot role is above the category in role hierarchy

**"DM not received when clicking Apply"**
- Check if your DMs are enabled for server members
- Make sure you don't have an existing application in progress
- Verify the bot can send you direct messages
- Contact an administrator if the issue persists

**"DM notifications not working for approvals/rejections"**
- Officers will see a status message indicating if the DM was sent successfully
- Common issues include:
  - User has DMs disabled from server members
  - User left the server after applying
  - User blocked the bot
- The bot will attempt to fetch users from Discord's API if they're not in the server cache
- Check the bot logs for detailed error information

### Validation Script

Run the test script to diagnose issues:
```bash
python -m unittest -v test_application_security test_application_workflow
```

These offline tests verify bot behavior; check permissions and role ordering in your Discord server separately.

## 🛡️ Security Features

- **No Privileged Intents:** Uses only default Discord permissions
- **Environment Variables:** Sensitive data stored securely
- **Input Validation:** Prevents malformed data
- **Error Handling:** Comprehensive logging without exposing sensitive information
- **Duplicate Prevention:** Users can't submit multiple applications simultaneously
- **Private Channels:** Applications are handled in secure, private channels
- **Role-Based Access:** Configurable access control for staff roles

## 📝 Technical Details

- **Language:** Python 3.10+
- **Library:** discord.py 2.3.0+
- **Architecture:** Event-driven with DM-based conversation flow
- **Storage:** SQLite for application identities, question/answer snapshots, drafts, reviewer decisions, and absolute cleanup deadlines. Run one bot process per database. Drafts restore on the next DM or Apply click; cleanup tasks restore on startup/reconnect. Failed cleanup retains its deadline and is retried on the next startup/reconnect. Existing identity-only databases are upgraded automatically; previous decision messages are not inferred as saved decisions.
- **Permissions:** Standard bot permissions (no privileged intents)

## 🤝 Contributing

Run the offline regression suite with the dependencies installed:

```bash
python -m unittest -v test_application_security test_application_workflow
python test_bot_fixes.py
```

The regression tests call the real bot handlers using temporary SQLite files and mocked Discord I/O; they do not connect to Discord. They cover saved drafts, editing/review, restart recovery, role assignment failures, decision persistence, and cleanup alongside the existing security checks.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with `python -m unittest -v test_application_security test_application_workflow`
5. Submit a pull request

## 📄 License

This project is open source. See the repository for license details.

---

**Need Help?** Check the troubleshooting section above or create an issue in the repository.
