<div align="center">

  <h1>🤖 NexusTG | Your Digital AI Twin</h1>
  <p><b>A powerful Telegram Userbot managed through a classic Telegram Bot interface.</b></p>

  <p>
    <a href="https://github.com/newfpv/NexusTG"><img src="https://img.shields.io/badge/GitHub-Repository-blue?style=for-the-badge&logo=github" alt="GitHub Repo"></a>
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
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/newfpv/NexusTG/main/install.ps1 | iex"
```
3. Follow the on-screen instructions (the script will ask for your preferred language and your Bot Token).
4. A shortcut named **Start NexusTG** will appear on your desktop. Double-click it to run your bot!

#### 🐧 For Linux / Ubuntu (VPS Server)
Connect to your server via SSH and paste this command:
```bash
bash <(curl -sL "https://raw.githubusercontent.com/newfpv/NexusTG/main/install.sh?t=$(date +%s)")
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
git clone https://github.com/newfpv/NexusTG.git
cd NexusTG

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

Here is a comprehensive, step-by-step documentation for creating custom modules in your system. You can copy and append this directly to your `README.md` or save it as a separate `docs/MODULES.md` file.

***

# 🧩 Developer Guide: Creating Custom Modules

NexusTG (AI Twin) uses a highly modular architecture. The core system acts as a bridge between **Aiogram** (which handles the settings UI via the official bot token) and **Pyrogram** (which handles the actual message interception and actions via your Telegram account). 

Modules are hot-plugged automatically: you simply drop a `.py` file into the `modules/` directory, and `main.py` will discover and initialize it.

## 📐 The Anatomy of a Module

Every module in the `modules/` folder is scanned during startup for specific "magic" variables and functions. **All of them are optional** — you only need to define the ones your module actually uses.

### Available Hooks

1. `router` *(aiogram.Router)*: Handles UI interactions (commands, callbacks, FSM states) in the settings bot.
2. `register_userbot(app: Client, [bot: Bot])`: Registers event listeners for the Pyrogram userbot.
3. `get_main_menu_buttons() -> list`: Adds inline buttons to the Bot's main dashboard.
4. `get_settings_buttons() -> list`: Adds inline buttons to the Global Settings menu.
5. `get_chat_menu_buttons(chat_id: int) -> list`: Adds inline buttons to a specific user's chat settings menu.
6. `on_startup()`: An async function that runs once when the bot boots up (useful for initializing DB tables or background tasks).

---

## 🛠️ Step-by-Step Guide

Let's build a sample module called `auto_responder.py` that auto-replies to specific keywords.

### Step 1: Base Setup & Router
Create `modules/auto_responder.py`. Import the necessary core utilities and initialize your Aiogram router.

```python
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram import Client, filters

from core.utils import safe_edit, CoreAPI
from core.config import _

# 1. Initialize the router for the UI
router = Router()
```

### Step 2: Database & Configuration
Your module shouldn't create its own database tables for simple settings. Instead, use the built-in `CoreAPI` which saves JSON data directly into the global or chat-specific config.

```python
# Helper to get global settings for this module
async def _get_cfg():
    cfg = await CoreAPI.get_module_cfg("auto_responder")
    return {
        "is_active": cfg.get("is_active", False),
        "keyword": cfg.get("keyword", "ping"),
        "reply_text": cfg.get("reply_text", "pong")
    }

# Helper to save settings
async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg("auto_responder", **kwargs)
```

### Step 3: UI Integration (Aiogram)
Expose your module in the bot's menus. If you want a button in the main menu, define `get_main_menu_buttons`.

```python
async def get_main_menu_buttons():
    # Returns a list of rows for the Inline Keyboard
    return [[InlineKeyboardButton(text=_("btn_auto_resp"), callback_data="auto_resp_main")]]

# Handle the button click
@router.callback_query(F.data == "auto_resp_main")
async def auto_resp_menu(call: types.CallbackQuery, state):
    cfg = await _get_cfg()
    status = _("status_on") if cfg["is_active"] else _("status_off")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Status: {status}", callback_data="auto_resp_toggle")],
        [InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")]
    ])
    
    # safe_edit handles message editing without throwing "Message is not modified" errors
    await safe_edit(call.message, state, _("menu_auto_resp_title"), kb, parse_mode="HTML")

@router.callback_query(F.data == "auto_resp_toggle")
async def toggle_plugin(call: types.CallbackQuery, state):
    cfg = await _get_cfg()
    await _upd_cfg(is_active=not cfg["is_active"])
    await auto_resp_menu(call, state) # Refresh menu
```

### Step 4: The Core Logic (Pyrogram)
Define the `register_userbot` function. This is where you intercept messages sent to your personal account.

```python
def register_userbot(app: Client):
    # Filter to intercept private messages
    @app.on_message(filters.private & ~filters.me)
    async def handle_incoming(client, message):
        cfg = await _get_cfg()
        
        # Check if module is active
        if not cfg["is_active"]:
            return
            
        text = message.text or message.caption or ""
        
        if text.lower() == cfg["keyword"].lower():
            # Send the reply using Pyrogram
            await client.send_message(
                chat_id=message.chat.id,
                text=cfg["reply_text"],
                reply_to_message_id=message.id
            )
```

### Step 5: Localization (i18n)
Never hardcode text strings in the Python file. Always use the `_("key_name")` function. Open your `language_RU.json` (and other language files) and add your keys:

```json
{
  "auto_responder": {
    "btn_auto_resp": "🤖 Авто-ответчик",
    "menu_auto_resp_title": "🤖 <b>Настройки авто-ответчика</b>\n\nЗдесь можно включить реакцию на ключевые слова."
  }
}
```
*Note: The `config.py` script automatically flattens the JSON, so nesting them under `"auto_responder"` is perfectly fine and keeps things organized.*

---

## 🚀 Complete Module Boilerplate

Use this template as a starting point for any new module:

```python
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from pyrogram import Client, filters

from core.utils import safe_edit, CoreAPI
from core.config import _

# --- 1. ROUTER SETUP ---
router = Router()
MODULE_NAME = "my_custom_module"

# --- 2. DATABASE HELPERS ---
async def _get_cfg():
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    return {"is_active": cfg.get("is_active", False)}

async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg(MODULE_NAME, **kwargs)

# --- 3. MENU INJECTIONS ---
async def get_settings_buttons():
    # Injects button into Global Settings menu
    return [[InlineKeyboardButton(text=_("btn_my_module"), callback_data="my_mod_main")]]

# --- 4. AIOGRAM UI HANDLERS ---
@router.callback_query(F.data == "my_mod_main")
async def render_main_menu(call: types.CallbackQuery, state: FSMContext):
    # Always save the menu message ID so safe_edit knows what to edit
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_cfg()
    
    status = _("status_on") if cfg["is_active"] else _("status_off")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Active: {status}", callback_data="my_mod_toggle")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])
    
    await safe_edit(call.message, state, _("menu_my_mod_title"), kb)

@router.callback_query(F.data == "my_mod_toggle")
async def toggle_module(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await _upd_cfg(is_active=not cfg["is_active"])
    await render_main_menu(call, state)

# --- 5. PYROGRAM USERBOT LOGIC ---
def register_userbot(app: Client):
    # Example: Listen to your own commands
    @app.on_message(filters.me & filters.command("testmod", prefixes="."))
    async def handle_my_command(client, message):
        cfg = await _get_cfg()
        if cfg["is_active"]:
            await message.edit_text("Module is active and working!")
        else:
            await message.edit_text("Module is currently disabled.")

# --- 6. STARTUP TASKS (Optional) ---
async def on_startup():
    logging.info(f"Module {MODULE_NAME} initialized successfully.")
```

## ⚠️ Best Practices

1. **Do not block Pyrogram handlers:** If your Pyrogram filter or handler calls external APIs (like LLMs or YouTube downloads), always wrap it in `asyncio.create_task(...)` so it doesn't freeze the rest of the userbot.
2. **Handle Exceptions:** Use `try...except` blocks, especially around `message.delete()` or `message.edit()`, as Telegram often throws exceptions if a message is deleted before the bot can act on it.
3. **Use `safe_edit`:** Aiogram will throw a `MessageNotModified` exception if you try to edit a message with the exact same text/keyboard. The `safe_edit` utility in `core.utils` safely catches and ignores this for you.
4. **Accessing the Official Bot inside Userbot:** If you need the userbot to send a notification to your admin chat via the official bot, update the signature to `def register_userbot(app: Client, bot: aiogram.Bot):` — `main.py` will automatically pass the bot instance to it!

> ⚠️ **Disclaimer:**
> The use of userbots is not officially endorsed by Telegram's Terms of Service. NexusTG includes built-in "humanity" modules (realistic delays, typos) to minimize risks. However, **you use this software at your own risk**. Do not use this bot for spam or mass messaging, as this will lead to account suspension.