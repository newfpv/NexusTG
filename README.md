<div align="center">
  <img src="https://via.placeholder.com/800x200/000000/FFFFFF?text=NexusTG+Banner" alt="NexusTG Banner">

  <h1>🤖 NexusTG | Your Digital AI Twin</h1>
  <p><b>A powerful Telegram Userbot managed through a classic Telegram Bot interface.</b></p>

  <p>
    <a href="https://github.com/newfpv/NexusTGTest"><img src="https://img.shields.io/badge/GitHub-Repository-blue?style=for-the-badge&logo=github" alt="GitHub Repo"></a>
    <img src="https://img.shields.io/badge/Python-3.11+-yellow?style=for-the-badge&logo=python" alt="Python">
    <img src="https://img.shields.io/badge/Gemini_AI-Powered-orange?style=for-the-badge&logo=google" alt="Gemini">
  </p>
</div>

---

**NexusTG** is not just a script; it's your **digital twin**. You set it up once, and it runs in the background on your personal Telegram account. It can reply for you using neural networks, read deleted messages from your contacts, transcribe voice messages, and automate your daily routine.

Best of all—**everything is managed directly inside Telegram** using convenient buttons. No need to mess with config files or code after installation!

## ✨ Features & Modules

* 🧠 **AI Twin** — A smart auto-responder powered by Google Gemini. It imitates your communication style, "types" text with realistic human delays, and features a customizable "sleep mode".
* 🕵️ **Spy-Module** — Secretly saves **deleted** and **edited** messages from your chat partners, and downloads "disappearing" (view-once) photos/videos to your private dump chat.
* 🎙 **Voice-to-Text** — Automatically (or manually) transcribes voice and video messages. For long audio, the AI will provide a short summary.
* 🎭 **Fake Activity** — Shows a fake status to your chat partner (e.g., "typing...", "recording video", or "playing a game") for a specified duration.
* 🧠 **Manual AI (`.ai` command)** — Type `.ai help me solve this` in any chat, and the bot will analyze the context of the conversation and send an AI-generated response directly from your account.
* 🛒 **Shopping List** — A smart parser. Send a message like *"buy bread, 2L of milk, and cheese"*, and the bot will convert it into a neat, clickable checklist.
* 👤 **Info Module** — View detailed, hidden technical information about any Telegram user.

---

## 🚀 QUICK START (1-Click Installation)

Installation is super easy. The script will automatically download the necessary tools, create a blazing-fast virtual environment using `uv`, and place a shortcut right on your desktop.

### Step 1: Get your Bot Token
Go to Telegram, search for [@BotFather](https://t.me/BotFather), and send `/newbot`. Choose a name and a username. BotFather will give you a **Token** (e.g., `1234567890:AAH...`). Copy it; you'll need it in a moment.

### Step 2: Installation

#### 🪟 For Windows (Fast & Native)
1. Open the Start menu, type **PowerShell**, right-click it, and select **"Run as Administrator"**.
2. Paste the following command and press Enter:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm [https://raw.githubusercontent.com/newfpv/NexusTGTest/main/install.ps1](https://raw.githubusercontent.com/newfpv/NexusTGTest/main/install.ps1) | iex"
```
3. Follow the on-screen instructions (the script will ask for your preferred language and your Bot Token).
4. A shortcut named **Start NexusTG** will appear on your desktop. Double-click it to run your bot!

#### 🐧 For Linux / Ubuntu (VPS Server)
Connect to your server via SSH and paste this command:
```bash
bash <(curl -sL "[https://raw.githubusercontent.com/newfpv/NexusTGTest/main/install.sh?t=$(date](https://raw.githubusercontent.com/newfpv/NexusTGTest/main/install.sh?t=$(date) +%s)")
```
The script will install the bot in the `~/NexusTG` folder and create a convenient `./start.sh` execution file.

---

## ⚙️ Step 3: In-App Setup (Inside Telegram)

Once you've launched the bot on your PC or server (and the black console window is open), head over to Telegram!

1. Open the bot you created with BotFather and press **START** (`/start`).
2. The bot will ask you to input your system keys. You will need:
   * **API_ID** and **API_HASH** (Get these at [my.telegram.org](https://my.telegram.org) under *API development tools*).
   * **Gemini API Key** (Get this for free at [Google AI Studio](https://aistudio.google.com/app/apikey)).
3. After entering the keys, click **"Log into Userbot"**.
4. Enter your Telegram phone number and the confirmation code (you must enter the code using the inline buttons inside the bot).
5. *If you have 2FA (Cloud Password) enabled, the bot will ask for it. This is processed locally and safely.*

🎉 **ALL DONE! Welcome to the Main Menu.**

---

## 🐳 For Advanced Users (Docker / Manual Setup)

If you prefer to deploy the project using Docker Compose:

```bash
# 1. Clone the repository
git clone [https://github.com/newfpv/NexusTGTest.git](https://github.com/newfpv/NexusTGTest.git)
cd NexusTGTest

# 2. Configure the environment
cp .env.example .env
nano .env # Enter your TG_BOT_TOKEN and select your LANG_FILE

# 3. Start the container
docker compose up -d --build

# View logs:
docker compose logs -f
```

---

## 🛠 How to Use the Bot?

* Go to **"My Chats"** — select any dialog and configure your AI Twin individually (e.g., set the bot to answer your boss strictly professionally, but reply to friends informally).
* In the **"Core Settings"** section, you can update your API keys, change your timezone, and configure global system behavior.
* To trigger manual modules (like AI or Shopping List), simply open the desired chat from your main Telegram account and type the trigger command (default: `.ai your prompt` or `.shop bread, milk`).

> ⚠️ **Disclaimer:**
> The use of userbots is not officially endorsed by Telegram's Terms of Service. NexusTG includes built-in "humanity" modules (realistic delays, typos) to minimize risks. However, **you use this software at your own risk**. Do not use this bot for spam or mass messaging, as this will lead to account suspension.
