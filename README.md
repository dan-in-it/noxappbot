# NoxAppBot - Discord Guild Application Bot

A Discord bot designed to streamline the guild application process for World of Warcraft guilds. Applicants fill out forms through Discord modals, and their responses are posted in private channels for review.

## ✨ Features

- **🔘 Button-Initiated Applications:** Simple "Apply" button to start the process
- **📝 Modal-Based Application Forms:** Two-part modal forms for comprehensive applications
- **🔒 Private Application Channels:** Automatically creates secure, private channels for each application
- **👥 Role-Based Access:** Configurable access for officers and administrators
- **⚡ Slash Commands:** Modern Discord slash command interface
- **🚫 No Privileged Intents:** Works with default Discord permissions only
- **🛡️ Duplicate Prevention:** Prevents users from submitting multiple applications
- **📊 Comprehensive Logging:** Built-in error handling and logging
- **⚙️ Easy Configuration:** Environment-based configuration with validation

## 📋 Prerequisites

- **Python 3.8+** installed on your system
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

### 4. Invite Bot to Server

1. In Discord Developer Portal: **OAuth2 → URL Generator**
2. **Scopes:** Select `bot` and `applications.commands`
3. **Bot Permissions:**
   - ✅ Manage Channels
   - ✅ Send Messages
   - ✅ Embed Links
   - ✅ Read Message History
4. Copy the generated URL and authorize the bot to your server

### 5. Test Configuration (Optional but Recommended)

```bash
python test_bot.py
```

This validates your setup before running the bot.

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
   /post_application
   ```
   Use this slash command in the channel where you want the application button.

2. **Application Flow:**
   - User clicks "Apply to Guild" button
   - First modal appears with 5 questions
   - After submitting, second modal appears with 2 remaining questions
   - Bot creates private channel: `application-{username}`
   - Application is posted as an embed in the new channel
   - User gets confirmation with channel link

### Managing Applications

- **Private Channels:** Each application gets its own private channel
- **Access Control:** Only the applicant and configured roles can see the channel
- **Review Process:** Officers can discuss applications privately in these channels
- **Cleanup:** Channels can be manually deleted after processing applications

## ⚙️ Customization

### Modifying Questions

Edit the `questions` list in [`src/bot.py`](src/bot.py:21-29):

```python
questions = [
    "Which raid team are you applying to? Weekday (Tues/Wed/Thurs), Weekend (Fri/Sat/Sun), Floater/Casual",
    "Have you reviewed the raid schedule for the team you're applying for?",
    "Is there a certain class/spec/role that you prefer to play?",
    "Please provide a link to your Warcraft Logs page for the character(s) you're applying with",
    "Do you currently have any friends or family in the guild? If so, who?",
    "Tell us about yourself and your raiding experience",
    "Any additional comments/questions?"
]
```

**Important:** Keep exactly 7 questions (5 in first modal, 2 in second modal) to maintain the modal structure. Discord limits modals to 5 components each.

### Adding Officer/Admin Role Access

To give specific roles access to application channels, modify the `overwrites` in [`src/bot.py`](src/bot.py:95-100):

```python
# Get your officer role ID (right-click role → Copy ID with Developer Mode enabled)
OFFICER_ROLE_ID = 123456789012345678  # Replace with actual ID

# In the overwrites dictionary:
overwrites = {
    guild.default_role: discord.PermissionOverwrite(read_messages=False),
    self.member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    guild.get_role(OFFICER_ROLE_ID): discord.PermissionOverwrite(read_messages=True, send_messages=True),
}
```

### Adjusting Text Limits

Modify character limits in the modal classes:
- **Short answers:** `max_length=1000`
- **Long answers:** `max_length=2000` with `style=discord.TextStyle.paragraph`

## 🔧 Troubleshooting

### Common Issues

**"Import discord could not be resolved"**
```bash
pip install discord.py python-dotenv
```

**"Bot not responding to commands"**
- Verify bot has proper permissions in your server
- Check that the bot is online (green status)
- Ensure you're using `/post_application` with administrator permissions
- Make sure slash commands are synced (happens automatically on bot startup)

**"Interview category not found"**
- Verify the category ID in your `.env` file
- Ensure the bot has access to the category
- Check that the category still exists

**"Permission denied when creating channels"**
- Bot needs "Manage Channels" permission
- Verify bot role is above the category in role hierarchy

**"Modal not appearing when clicking Apply"**
- Make sure you don't have an existing application channel
- Try refreshing Discord and clicking the button again
- Contact an administrator if the issue persists

### Validation Script

Run the test script to diagnose issues:
```bash
python test_bot.py
```

This checks:
- ✅ File structure
- ✅ Dependencies installed
- ✅ Environment variables configured
- ✅ Bot configuration validity

## 🛡️ Security Features

- **No Privileged Intents:** Uses only default Discord permissions
- **Environment Variables:** Sensitive data stored securely
- **Input Validation:** Prevents malformed data
- **Error Handling:** Comprehensive logging without exposing sensitive information
- **Duplicate Prevention:** Users can't submit multiple applications simultaneously
- **Private Channels:** Applications are handled in secure, private channels
- **Role-Based Access:** Configurable access control for staff roles

## 📝 Technical Details

- **Language:** Python 3.8+
- **Library:** discord.py 2.3.0+
- **Architecture:** Event-driven with modal-based forms
- **Storage:** In-memory with automatic cleanup (no database required)
- **Permissions:** Standard bot permissions (no privileged intents)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with `python test_bot.py`
5. Submit a pull request

## 📄 License

This project is open source. See the repository for license details.

---

**Need Help?** Check the troubleshooting section above or create an issue in the repository.
