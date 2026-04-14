<div align="center">

  <h1>🧬 NexusTG</h1>
  <h3>Your AI Alter Ego for Telegram</h3>
  <p><b>Not a bot. Not a script. It's you — but powered by neural networks.</b></p>

  <p>
    <img src="https://img.shields.io/badge/Python-3.11%2B-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
    <img src="https://img.shields.io/badge/Gemini-AI%20Powered-orange?style=for-the-badge&logo=google&logoColor=white" alt="AI">
    <img src="https://img.shields.io/badge/Telegram-Userbot-purple?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram">
    <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker">
    <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License">
  </p>

  <p>
    <a href="#features">Features</a> •
    <a href="#installation">Install</a> •
    <a href="#modules">Modules</a> •
    <a href="#developers">API</a> •
    <a href="https://t.me/yourchannel">Community</a>
  </p>
</div>

---

## 🎯 The Mission

**NexusTG** is your digital ghost in the machine. It lives inside your Telegram account, reads your chats, learns your vibe, and speaks with your voice — literally.

While you sleep, work, or exist offline, your AI Twin keeps the conversation flowing. It doesn't just reply; it **thinks**, **hesitates**, **makes typos**, and **reacts** like a real human would.

> *"The future isn't about chatbots answering for you. It's about AI becoming indistinguishable from you."*

---

## ✨ Features That Hit Different

| Module | What It Does | The Vibe |
|--------|-------------|----------|
| 🧠 **AI Twin** | Auto-replies using Gemini with your personality, custom prompts per chat, realistic typing delays, and "sleep mode" | Your digital doppelgänger |
| 🕵️ **Ghost Saver** | Secretly saves deleted/edited messages and self-destructing media to a private dump chat with user profiles | Digital black box |
| 🎙 **Voice Hacker** | Transcribes voice messages to text automatically, summarizes long audio (>1 min) | Your personal stenographer |
| 🎭 **Fake Activity** | Shows "typing...", "recording video", or "playing game" status for hours | Social engineering toolkit |
| 🧠 **Manual AI (.ai)** | Type `.ai analyze this` in any chat for instant context-aware AI responses | Jarvis in your pocket |
| 🛒 **Smart Shopping** | Convert "buy milk, bread, eggs" into beautiful checklists automatically | Life organizer |
| 🎬 **Media Downloader** | Auto-downloads TikTok/Instagram Reels without watermarks | Content vault |
| 🔎 **Fact Checker** | Instantly verifies claims using Google Search + academic sources | Bullshit detector |

---

## 🏗️ Architecture

**Dual-core engine:**

```
┌─────────────────┐      ┌──────────────────┐
│   Your Telegram │      │   Settings Bot   │
│     Account     │◄────►│   (Aiogram)      │
└────────┬────────┘      └────────┬─────────┘
         │                        │
         ▼                        ▼
┌──────────────────────────────────────┐
│         NexusTG Core Engine          │
│  ┌─────────────┐    ┌─────────────┐  │
│  │   Pyrogram  │    │   Gemini    │  │
│  │  Userbot    │◄──►│    AI       │  │
│  └─────────────┘    └─────────────┘  │
│  ┌─────────────┐    ┌─────────────┐  │
│  │   SQLite    │    │  yt-dlp     │  │
│  │   Database  │    │  Media Proc │  │
│  └─────────────┘    └─────────────┘  │
└──────────────────────────────────────┘
```

- **🤖 Aiogram** — Controls the settings UI through a dedicated bot
- **⚡ Pyrogram** — The userbot that actually lives in your account and intercepts messages
- **🧠 Gemini API** — Neural network brains with fallback chains
- **💾 SQLite** — Local encrypted storage for configs and message cache

---

## 🚀 Quick Start (Zero to Hero in 5 Minutes)

### Prerequisites
- Python 3.11+ or Docker
- Telegram account (obviously)
- 3 brain cells

### 🪟 Windows (One-Line PowerShell)

**Run as Administrator:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/newfpv/NexusTG/main/install.ps1 | iex"
```

The installer will:
- Install Git, Python, and Docker if missing
- Clone the repo to `%USERPROFILE%\NexusTG`
- Create a desktop shortcut "Start NexusTG"
- Launch the bot

### 🐧 Linux/VPS (One-Line Bash)

```bash
bash <(curl -sL "https://raw.githubusercontent.com/newfpv/NexusTG/main/install.sh")
```

Installs to `~/NexusTG` with systemd service support.

### 🐳 Docker (Cross-Platform)

```bash
git clone https://github.com/newfpv/NexusTG.git
cd NexusTG
cp .env.example .env
# Edit .env with your TG_BOT_TOKEN and LANG_FILE
docker compose up -d --build
```

### 🛠️ Manual Install (Hacker Mode)

```bash
git clone https://github.com/newfpv/NexusTG.git
cd NexusTG
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m core.main
```

---

## ⚙️ Configuration (Inside Telegram)

Once running, open your bot in Telegram:

1. **Send `/start`** → Claim admin rights
2. **Enter API credentials**:
   - `api_id` & `api_hash` from [my.telegram.org](https://my.telegram.org)
   - Gemini API keys from [Google AI Studio](https://aistudio.google.com/app/apikey)
3. **Authorize** your account → Enter phone → Confirm code via inline numpad
4. **Done.** Welcome to the Matrix.

### Pro Tips:
- Set different personalities for different chats (Boss = professional, Friends = chill)
- Configure "Sleep Mode" so AI ignores messages at night (you need to appear offline sometimes)
- Enable "Human Mode" for realistic typing patterns with random pauses and typos

---

## 🔌 Module Deep Dive

### 🧠 AI Twin Engine

The crown jewel. It doesn't just reply — it **performs**.

**Human Simulation Features:**
- ⏱️ **Smart Delays**: Reads message length × 0.05s per character (simulates reading)
- ⌨️ **Jagged Typing**: Types in bursts with pauses (1.5-3.5s typing, 0.5-2s breaks)
- 🎲 **Imperfection**: 5% chance of typo → correction flow
- 🎭 **Reaction System**: Sends [LIKE] → converts to emoji reaction
- 🚫 **Ignore Logic**: 10% chance to ignore non-questions (like a real busy human)

**Context Awareness:**
- Parses YouTube links (title, description, subtitles)
- Understands replies and forwarded messages
- Remembers conversation history (last 50 messages)
- Handles images, voice, and video context

### 🕵️ Spy Module (Ghost Saver)

Your personal NSA. Creates a **secret forum topic** for every contact:

- 📁 **Deleted Messages**: Captured before vanishing
- ✏️ **Edited Messages**: Shows before/after diff
- ⏳ **Self-Destructing Media**: Screenshots TTL photos/videos
- 👤 **User Dossiers**: Auto-generates profiles with photos and metadata

*All evidence is sent with random delays (1-5 min) to avoid flood limits.*

---

# 🧩 Developer Guide: Building God-Tier Modules

Welcome to the engine room. This isn't your grandma's "Hello World" tutorial — we're building **cybernetic implants** for your digital twin.

## 📐 Architecture Deep Dive

### The Plugin Ecosystem

NexusTG uses a **hot-pluggable architecture**. Drop a Python file into `modules/` and the core automatically:

1. **Scans** for magic variables (`router`, `register_userbot`, etc.)
2. **Injects** UI components into the settings bot
3. **Mounts** Pyrogram handlers to the userbot
4. **Registers** startup tasks and database schemas

**Core Components:**

| Component | Purpose | Access Pattern |
|-----------|---------|----------------|
| `CoreAPI` | Database abstraction | Static methods |
| `plugins` | Global event bus & shared state | Singleton instance |
| `FSMContext` | User state management | Aiogram native |
| `PyrogramFSM` | Userbot-side state tracking | `plugins.fsm` |

### Data Flow

```
User Message → Pyrogram Handler → Your Module Logic → CoreAPI → SQLite
                     ↓
              Optional: AI Service (Gemini)
                     ↓
              Response → Typing Simulation → Send Message
```

---

## 🛠️ Module Anatomy: From Zero to Hero

Let's build a **real-world module**: `crypto_tracker.py` that monitors crypto prices and alerts when BTC drops.

### Step 1: Scaffold the Module

Create `modules/crypto_tracker.py`:

```python
"""
Crypto Tracker Module for NexusTG
Monitors cryptocurrency prices and sends alerts
"""

import asyncio
import logging
import aiohttp
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup as PyroKeyboard, InlineKeyboardButton as PyroButton

from core.utils import safe_edit, safe_delete, get_cancel_kb, CoreAPI, plugins
from core.config import _

# Initialize router for Aiogram (Settings UI)
router = Router()

# Module identifier - used for DB namespace
MODULE_NAME = "crypto_tracker"

# FSM States for configuration
class CryptoFSM(StatesGroup):
    set_threshold = State()
    set_currency = State()


# ============================================================================
# SECTION 1: DATABASE LAYER
# ============================================================================

async def _get_cfg() -> dict:
    """
    Fetch module configuration from global settings.
    Returns empty dict with defaults if not initialized.
    """
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    return {
        "enabled": cfg.get("enabled", False),
        "threshold": cfg.get("threshold", 5.0),  # 5% drop alert
        "currency": cfg.get("currency", "BTC"),
        "alerts_enabled": cfg.get("alerts_enabled", True),
        "chat_id": cfg.get("chat_id", None)  # Where to send alerts
    }

async def _upd_cfg(**kwargs):
    """Atomic update of module configuration."""
    await CoreAPI.update_module_cfg(MODULE_NAME, **kwargs)


# ============================================================================
# SECTION 2: UI INTEGRATION (Aiogram)
# ============================================================================

async def get_settings_buttons() -> list:
    """
    Inject button into Global Settings menu.
    Returns list of button rows.
    """
    return [
        [InlineKeyboardButton(
            text="💰 Crypto Tracker", 
            callback_data="crypto_main"
        )]
    ]

async def get_main_menu_buttons() -> list:
    """
    Optional: Add quick-access button to main dashboard
    """
    cfg = await _get_cfg()
    if cfg["enabled"]:
        return [
            [InlineKeyboardButton(
                text=f"📊 {cfg['currency']} Tracker", 
                callback_data="crypto_quick_view"
            )]
        ]
    return []

@router.callback_query(F.data == "crypto_main")
async def crypto_menu(call: types.CallbackQuery, state: FSMContext):
    """Main configuration menu."""
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_cfg()
    
    status = _("status_on") if cfg["enabled"] else _("status_off")
    alert_status = _("status_on") if cfg["alerts_enabled"] else _("status_off")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Status: {status}", 
            callback_data="crypto_toggle"
        )],
        [InlineKeyboardButton(
            text=f"Currency: {cfg['currency']}", 
            callback_data="crypto_set_currency"
        )],
        [InlineKeyboardButton(
            text=f"Threshold: {cfg['threshold']}%", 
            callback_data="crypto_set_threshold"
        )],
        [InlineKeyboardButton(
            text=f"Alerts: {alert_status}", 
            callback_data="crypto_toggle_alerts"
        )],
        [InlineKeyboardButton(
            text=_("btn_back"), 
            callback_data="global_settings"
        )]
    ])
    
    text = (
        f"💰 <b>Crypto Tracker</b>\n\n"
        f"Monitor: <code>{cfg['currency']}</code>\n"
        f"Alert on drop: <code>{cfg['threshold']}%</code>\n\n"
        f"<i>Tracks price changes in real-time.</i>"
    )
    
    await safe_edit(call.message, state, text, kb, parse_mode="HTML")

@router.callback_query(F.data == "crypto_toggle")
async def toggle_crypto(call: types.CallbackQuery, state: FSMContext):
    """Toggle module on/off."""
    cfg = await _get_cfg()
    new_state = not cfg["enabled"]
    await _upd_cfg(enabled=new_state)
    
    # If enabling, set current chat as alert target
    if new_state:
        await _upd_cfg(chat_id=call.message.chat.id)
    
    await crypto_menu(call, state)

@router.callback_query(F.data == "crypto_set_threshold")
async def set_threshold_prompt(call: types.CallbackQuery, state: FSMContext):
    """Ask user for percentage threshold."""
    kb = get_cancel_kb("crypto_main")
    await safe_edit(
        call.message, 
        state, 
        "📉 Enter percentage drop threshold (e.g., 5.5):", 
        kb
    )
    await state.set_state(CryptoFSM.set_threshold)

@router.message(CryptoFSM.set_threshold)
async def save_threshold(message: types.Message, state: FSMContext):
    """Save threshold to DB."""
    await safe_delete(message)
    
    try:
        value = float(message.text.replace(",", "."))
        if 0.1 <= value <= 100:
            await _upd_cfg(threshold=value)
            
            # Return to menu
            await state.set_state(None)
            data = await state.get_data()
            if data.get("menu_msg_id"):
                mock_call = types.CallbackQuery(
                    id="mock",
                    from_user=message.from_user,
                    chat_instance="",
                    message=types.Message(
                        message_id=data["menu_msg_id"],
                        chat=message.chat,
                        date=message.date
                    )
                )
                await crypto_menu(mock_call, state)
        else:
            raise ValueError("Out of range")
    except ValueError:
        msg = await message.answer("❌ Enter a valid number between 0.1 and 100")
        await asyncio.sleep(3)
        await safe_delete(msg)


# ============================================================================
# SECTION 3: USERBOT LOGIC (Pyrogram)
# ============================================================================

def register_userbot(app: Client):
    """
    Register Pyrogram handlers. This runs once at startup.
    app: Pyrogram Client instance
    """
    
    # Background task for price monitoring
    @app.on_startup
    async def start_monitoring():
        """Start background price checker when userbot connects."""
        asyncio.create_task(_price_monitor_loop(app))
    
    # Command handler for quick checks
    @app.on_message(filters.me & filters.command("price", prefixes="."))
    async def check_price_cmd(client, message):
        """.price BTC - Get current price."""
        args = message.text.split()
        symbol = args[1].upper() if len(args) > 1 else "BTC"
        
        price = await _fetch_price(symbol)
        if price:
            await message.edit(f"💰 {symbol}: <code>${price:,.2f}</code>")
        else:
            await message.edit(f"❌ Failed to fetch {symbol}")

    # Inline button handler for quick actions
    @app.on_callback_query(filters.regex("^crypto_"))
    async def handle_inline_buttons(client, callback_query):
        """Handle callbacks from sent messages."""
        if callback_query.data == "crypto_refresh":
            cfg = await _get_cfg()
            price = await _fetch_price(cfg["currency"])
            if price:
                await callback_query.edit_message_text(
                    f"💰 {cfg['currency']}: <code>${price:,.2f}</code>",
                    reply_markup=PyroKeyboard([[
                        PyroButton("🔄 Refresh", callback_data="crypto_refresh")
                    ]])
                )


# ============================================================================
# SECTION 4: BUSINESS LOGIC
# ============================================================================

async def _fetch_price(symbol: str) -> float | None:
    """Fetch current price from CoinGecko API."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.coingecko.com/api/v3/simple/price"
            params = {
                "ids": symbol.lower(),
                "vs_currencies": "usd"
            }
            async with session.get(url, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get(symbol.lower(), {}).get("usd")
    except Exception as e:
        logging.error(f"[Crypto] API Error: {e}")
    return None

async def _price_monitor_loop(app: Client):
    """
    Background task that checks prices every 60 seconds.
    Sends alert if drop exceeds threshold.
    """
    await asyncio.sleep(10)  # Wait for connection
    
    last_price = None
    
    while True:
        try:
            cfg = await _get_cfg()
            
            if not cfg["enabled"] or not cfg["alerts_enabled"]:
                await asyncio.sleep(60)
                continue
            
            current_price = await _fetch_price(cfg["currency"])
            if not current_price:
                await asyncio.sleep(60)
                continue
            
            if last_price and cfg["chat_id"]:
                change_pct = ((current_price - last_price) / last_price) * 100
                
                if change_pct <= -cfg["threshold"]:
                    # ALERT! Price dropped significantly
                    alert_text = (
                        f"🚨 <b>Crypto Alert</b>\n\n"
                        f"💰 {cfg['currency']} dropped <code>{abs(change_pct):.2f}%</code>\n"
                        f"📉 From: <code>${last_price:,.2f}</code>\n"
                        f"📉 To: <code>${current_price:,.2f}</code>"
                    )
                    
                    try:
                        await app.send_message(
                            cfg["chat_id"], 
                            alert_text,
                            reply_markup=PyroKeyboard([[
                                PyroButton("📊 View Chart", url=f"https://coingecko.com/en/coins/{cfg['currency'].lower()}")
                            ]])
                        )
                    except Exception as e:
                        logging.error(f"[Crypto] Failed to send alert: {e}")
            
            last_price = current_price
            
        except Exception as e:
            logging.error(f"[Crypto] Monitor error: {e}")
        
        await asyncio.sleep(60)


# ============================================================================
# SECTION 5: LIFECYCLE HOOKS
# ============================================================================

async def on_startup():
    """
    Runs once when bot starts. Use for initialization.
    """
    logging.info(f"[{MODULE_NAME}] Module initialized")
    # Validate API endpoints, warm up cache, etc.

async def on_shutdown():
    """Cleanup on exit."""
    logging.info(f"[{MODULE_NAME}] Shutting down...")
```

---

## 🗄️ Database API Reference

### CoreRepository Methods

All database operations are async and transaction-safe:

```python
from core.db import AsyncSessionLocal, CoreRepository

# Global Configuration (Module Settings)
await CoreAPI.get_module_cfg("module_name")        # Returns dict
await CoreAPI.update_module_cfg("module_name", key="value", count=42)

# Chat-Specific Configuration
await CoreAPI.get_chat_module_cfg(chat_id, "module_name")
await CoreAPI.update_chat_module_cfg(chat_id, "module_name", enabled=True)

# Raw Global Config (System Level)
config = await CoreAPI.get_global_config()
# Access: config.api_keys, config.global_prompt, etc.

# Session Management
await CoreAPI.save_session(phone, session_string)
await CoreAPI.delete_session()
```

### Custom Database Tables

If you need complex relational data, extend the SQLAlchemy models in `core/db.py`:

```python
# In core/db.py
class CryptoAlert(Base):
    __tablename__ = "crypto_alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    target_price: Mapped[float] = mapped_column(Float)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

---

## 🎛️ Advanced Patterns

### 1. Event Bus Communication

Modules can communicate without direct imports:

```python
# Publisher (in module A)
plugins.events.publish("price_alert", {
    "symbol": "BTC",
    "price": 42000,
    "change": -5.2
})

# Subscriber (in module B)
def setup_events():
    plugins.events.subscribe("price_alert", handle_price_alert)

async def handle_price_alert(data):
    print(f"Received alert: {data['symbol']} dropped {data['change']}%")
```

### 2. Cross-Module State Sharing

```python
# Cache (TTL = 300 seconds)
plugins.cache.set("btc_price", 42000, ttl=300)
price = plugins.cache.get("btc_price")  # Returns None if expired

# Persistent State (survives restart)
await CoreAPI.update_module_cfg("my_module", persistent_state={"last_alert": "2024-01-01"})
```

### 3. FSM (Finite State Machine)

For multi-step user interactions:

```python
class SetupFSM(StatesGroup):
    step1 = State()
    step2 = State()

@router.callback_query(F.data == "start_setup")
async def step1_handler(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetupFSM.step1)
    await call.message.edit_text("Enter value 1:")

@router.message(SetupFSM.step1)
async def process_step1(message: types.Message, state: FSMContext):
    await state.update_data(val1=message.text)
    await state.set_state(SetupFSM.step2)
    await message.reply("Enter value 2:")

@router.message(SetupFSM.step2)
async def process_step2(message: types.Message, state: FSMContext):
    data = await state.get_data()
    val1 = data["val1"]
    val2 = message.text
    await state.clear()  # Always clean up!
```

### 4. Safe Message Editing

Always use `safe_edit` to avoid "Message is not modified" errors:

```python
from core.utils import safe_edit

# Instead of:
await message.edit_text("Same text")  # Crashes if unchanged

# Use:
await safe_edit(message, state, "Text", keyboard, parse_mode="HTML")
```

---

## 🎨 Localization (i18n)

Add strings to `language_EN.json`:

```json
{
  "crypto_tracker": {
    "alert_template": "🚨 {symbol} dropped by {percent}%!",
    "status_active": "Tracking Active",
    "error_api": "Failed to connect to exchange API"
  }
}
```

Usage in code:

```python
from core.config import _

text = _("alert_template", symbol="BTC", percent="5.2")
```

---

## ⚡ Performance Best Practices

### 1. Non-Blocking I/O
Always wrap blocking calls in `asyncio.to_thread`:

```python
# Bad (blocks event loop):
result = heavy_computation()

# Good:
result = await asyncio.to_thread(heavy_computation)
```

### 2. Background Tasks
Don't block the handler:

```python
# Fire and forget for non-critical operations:
asyncio.create_task(log_to_external_service(data))

# Or use proper background worker:
asyncio.create_task(background_processor())
```

### 3. Rate Limiting
Respect Telegram's limits:

```python
from pyrogram.errors import FloodWait

try:
    await message.edit_text("Updated")
except FloodWait as e:
    await asyncio.sleep(e.value)
    await message.edit_text("Updated")
```

### 4. Memory Management
Clean up downloaded files:

```python
import tempfile
import os

try:
    path = await message.download()
    # Process file...
finally:
    if os.path.exists(path):
        os.remove(path)
```

---

## 🐛 Debugging & Testing

### Debug Logging

```python
import logging

logger = logging.getLogger(MODULE_NAME)
logger.info("Processing message %s", message.id)
logger.debug("API Response: %s", response)
```

### Unit Testing

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_crypto_fetch():
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_get.return_value.__aenter__.return_value.json = AsyncMock(
            return_value={"bitcoin": {"usd": 42000}}
        )
        mock_get.return_value.__aenter__.return_value.status = 200
        
        price = await _fetch_price("bitcoin")
        assert price == 42000
```

---

## 🚫 Anti-Patterns (Don't Do This)

❌ **Global mutable state without cache:**
```python
# Bad
prices = {}  # Lost on restart, memory leak

# Good
plugins.cache.set("prices", {})
```

❌ **Synchronous database queries:**
```python
# Bad
conn = sqlite3.connect("db.sqlite")
cursor = conn.execute("SELECT * FROM table")

# Good
async with AsyncSessionLocal() as session:
    result = await session.execute(select(Model))
```

❌ **Ignoring Pyrogram disconnects:**
```python
# Bad
await app.send_message(chat_id, "Hi")  # Crashes if disconnected

# Good
if app.is_connected:
    await app.send_message(chat_id, "Hi")
```

---

## 📦 Publishing Your Module

1. **Create a repo** with your module
2. **Add `module.json`** metadata:
```json
{
  "name": "crypto_tracker",
  "version": "1.0.0",
  "author": "@yourhandle",
  "description": "Real-time crypto price alerts",
  "requires": ["aiohttp>=3.8.0"],
  "min_nexus_version": "2.0.0"
}
```
3. **Submit PR** to the community modules repo or distribute via GitHub

---

**Now go build something that would make Satoshi Nakamoto jealous.** 🚀

<div align="center">

  **[⭐ Star this repo](https://github.com/newfpv/NexusTG)** • 
  **[🐛 Report Bug](https://github.com/newfpv/NexusTG/issues)** • 
  **[💡 Request Feature](https://github.com/newfpv/NexusTG/discussions)**

  <sub>Built with 🖤 and too much caffeine by the Nexus Team</sub>

</div>