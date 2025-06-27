# noxappbot
Nox Guild Application Bot

NoxAppBot is a Discord bot designed to streamline the guild application process for World of Warcraft guilds. It allows potential applicants to start an application by clicking a button, answer a series of questions via direct messages, and then have their application posted in a private channel for review by the applicant and guild officers.

## Features

-   **Button-Initiated Applications:** A simple "Apply" button to start the application process.
-   **DM-Based Questionnaire:** The bot interacts with applicants through direct messages to ask a series of predefined questions.
-   **Private Application Channels:** Each application automatically generates a new, private text channel.
-   **Secure Review Process:** The application channel is only accessible to the applicant and designated roles (e.g., officers), ensuring privacy.
-   **Easy Configuration:** Bot settings, such as the token and channel category, are managed through a simple `config.json` file.
-   **Administrator Command:** A command for server administrators to post the initial application message.

## Prerequisites

-   Python 3.8 or higher
-   A Discord account and a Discord server where you have administrative permissions.
-   `pip` for installing Python packages.

## Setup Instructions

Follow these steps to get your application bot up and running.

### 1. Clone the Repository

First, clone this repository to your local machine:

```bash
git clone https://github.com/dan-in-it/noxappbot.git
cd noxappbot
```

### 2. Install Dependencies

Install the required Python libraries using the `requirements.txt` file:

```bash
pip install -r requirements.txt
```

### 3. Create a Discord Bot

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2.  Click on **"New Application"** and give your bot a name (e.g., "Nox Guild Applications").
3.  Navigate to the **"Bot"** tab on the left-hand menu.
4.  Under the "Privileged Gateway Intents" section, enable the following intents:
    -   **SERVER MEMBERS INTENT**
    -   **MESSAGE CONTENT INTENT**
5.  Click **"Reset Token"** to generate a new bot token. **Copy this token immediately and save it somewhere safe.** This is your bot's password.

### 4. Configure the Bot

Open the `src/config.json` file and fill in the required values:

```json
{
  "token": "YOUR_DISCORD_BOT_TOKEN",
  "interview_category_id": "YOUR_INTERVIEW_CATEGORY_ID"
}
```

-   `token`: Paste the bot token you copied from the Discord Developer Portal.
-   `interview_category_id`: This is the ID of the category in your Discord server where the private application channels will be created.

**How to get the Category ID:**

1.  In Discord, go to **User Settings > Advanced**.
2.  Enable **Developer Mode**.
3.  Right-click on the category in your server where you want the application channels to be created and select **"Copy Category ID"**.

### 5. Invite the Bot to Your Server

1.  In the Discord Developer Portal, go to the **"OAuth2"** tab and then **"URL Generator"**.
2.  Select the following scopes:
    -   `bot`
    -   `applications.commands`
3.  Select the following Bot Permissions:
    -   **Manage Channels** (to create the private application channels)
    -   **Send Messages**
    -   **Embed Links**
    -   **Read Message History**
4.  Copy the generated URL and paste it into your web browser.
5.  Select the server you want to add the bot to and click **"Authorize"**.

## Usage

### 1. Run the Bot

To start the bot, run the `bot.py` file from the root directory of the project:

```bash
python src/bot.py
```

If everything is configured correctly, you will see a message in your console:

```
Logged in as YourBotName
```

### 2. Post the Application Button

In your Discord server, go to the channel where you want the "Apply" button to be posted. Use the following command:

```
!post_application
```

Only users with administrator permissions can use this command. The bot will post an embedded message with an "Apply" button.

### 3. The Application Process

1.  A user clicks the "Apply" button.
2.  The bot sends the user a direct message to begin the application process.
3.  The user answers a series of questions one by one in the DM.
4.  Once all questions are answered, the bot creates a new private channel named `application-{username}` under the configured category.
5.  The bot posts the completed application in the new channel.
6.  The applicant receives a confirmation message with a link to their private application channel.

## Customization

### Changing the Questions

You can customize the application questions by editing the `questions` list in `src/bot.py`:

```python
# src/bot.py

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

### Adding Officer Roles

If you want to give specific roles (e.g., "Officers") access to the private application channels, you can add them to the `overwrites` dictionary in the `apply` function within `src/bot.py`.

1.  You will need the Role ID for your officer role (enable Developer Mode and right-click the role to copy the ID).
2.  Uncomment and edit the following line in `src/bot.py`:

```python
# src/bot.py

# ... inside the apply function
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            # Add your officer role ID here
            guild.get_role(123456789012345678): discord.PermissionOverwrite(read_messages=True)
        }
# ...
```

Replace `123456789012345678` with your actual officer role ID.
