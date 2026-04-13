import time
import asyncio
import traceback
import logging
import re
import html
import random
from functools import wraps
from pyrogram.enums import ChatAction
from aiogram import types
from aiogram.fsm.context import FSMContext
from core.db import AsyncSessionLocal, CoreRepository
from core.config import _
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

class MemoryCache:
    def __init__(self):
        self._cache = {}

    def set(self, key, value, ttl=None):
        expires = time.time() + ttl if ttl else None
        self._cache[key] = (value, expires)

    def get(self, key):
        if key in self._cache:
            value, expires = self._cache[key]
            if expires is None or time.time() < expires:
                return value
            del self._cache[key]
        return None

    def delete(self, key):
        if key in self._cache:
            del self._cache[key]

class EventBus:
    def __init__(self):
        self._subscribers = {}

    def subscribe(self, event_type, handler):
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    async def publish(self, event_type, data=None):
        if event_type in self._subscribers:
            for handler in self._subscribers[event_type]:
                asyncio.create_task(handler(data))

class PyrogramFSM:
    def __init__(self):
        self._states = {}
        self._data = {}

    def set_state(self, chat_id, state):
        self._states[chat_id] = state

    def get_state(self, chat_id):
        return self._states.get(chat_id)

    def update_data(self, chat_id, **kwargs):
        if chat_id not in self._data:
            self._data[chat_id] = {}
        self._data[chat_id].update(kwargs)

    def get_data(self, chat_id):
        return self._data.get(chat_id, {})

    def clear(self, chat_id):
        self._states.pop(chat_id, None)
        self._data.pop(chat_id, None)

class CoreAPI:
    @staticmethod
    async def get_global_config():
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            return await repo.get_global_config()

    @staticmethod
    async def get_chat_config(chat_id):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            return await repo.get_chat_config(chat_id)

    @staticmethod
    async def update_global_config(**kwargs):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.update_global_config(**kwargs)

    @staticmethod
    async def update_chat_config(chat_id, **kwargs):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.update_chat_config(chat_id, **kwargs)

    @staticmethod
    async def save_session(phone: str, session_string: str):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.save_session(phone, session_string)

    @staticmethod
    async def delete_session():
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.delete_session()

    @staticmethod
    async def full_reset():
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.full_reset()

    @staticmethod
    async def get_module_cfg(module_name: str) -> dict:
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            return await repo.get_module_cfg(module_name)

    @staticmethod
    async def update_module_cfg(module_name: str, **kwargs):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.update_module_cfg(module_name, **kwargs)

    @staticmethod
    async def get_chat_module_cfg(chat_id: int, module_name: str) -> dict:
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            return await repo.get_chat_module_cfg(chat_id, module_name)

    @staticmethod
    async def update_chat_module_cfg(chat_id: int, module_name: str, **kwargs):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.update_chat_module_cfg(chat_id, module_name, **kwargs)

    @staticmethod
    async def add_ignored_msg(chat_id: int, message_id: int):
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            await repo.add_ignored_msg(chat_id, message_id)
            
class PluginManager:
    def __init__(self):
        self.userbot_handlers = []
        self.main_menu_buttons = []
        self.chat_menu_buttons = []
        self.settings_buttons = []
        self.startup_tasks = []
        self.bot = None
        self.api_id = None
        self.api_hash = None
        self.start_userbot_cb = None
        self.stop_userbot_cb = None
        self.generate_menu_cb = None
        self.generate_chat_menu_cb = None
        self.generate_settings_menu_cb = None
        
        self.cache = MemoryCache()
        self.events = EventBus()
        self.fsm = PyrogramFSM()
        self.db = CoreAPI()

    async def cleanup_media_cache(self):
        from datetime import datetime, timedelta
        from sqlalchemy import delete
        from core.db import MediaMemoryCache, AsyncSessionLocal
        
        while True:
            try:
                async with AsyncSessionLocal() as session:
                    threshold = datetime.utcnow() - timedelta(days=2)
                    await session.execute(delete(MediaMemoryCache).where(MediaMemoryCache.timestamp < threshold))
                    await session.commit()
            except Exception as e:
                logging.error(_("log_cache_cleanup_error", e=e))
            await asyncio.sleep(3600 * 24)

plugins = PluginManager()

def safe_userbot_handler(func):
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        try:
            return await func(client, message, *args, **kwargs)
        except Exception as e:
            error_msg = str(e)
            trace = traceback.format_exc()
            logging.error(_("log_unhandled_exception", error=error_msg, trace=trace))
            try:
                config = await plugins.db.get_global_config()
                if config and config.admin_id:
                    await plugins.bot.send_message(
                        chat_id=config.admin_id,
                        text=_("err_unhandled", error=error_msg, trace=trace[-1000:]),
                        parse_mode="HTML"
                    )
            except Exception:
                pass
    return wrapper

async def safe_edit(message: types.Message, state: FSMContext, text: str, reply_markup=None, parse_mode="HTML"):
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    bot = plugins.bot
    
    if not menu_msg_id:
        config = await plugins.db.get_global_config()
        menu_msg_id = config.admin_menu_id

    try:
        if menu_msg_id:
            await bot.edit_message_text(text, chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            raise ValueError("No ID")
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
            
        msg = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        await state.update_data(menu_msg_id=msg.message_id)
        await plugins.db.update_global_config(admin_menu_id=msg.message_id)

async def safe_delete(message):
    try: await message.delete()
    except: pass

async def get_current_global_settings():
    c = await plugins.db.get_global_config()
    if not c: return None
    return {
        "prompt": c.global_prompt if c.global_prompt else _("default_prompt_env"),
        "typing": c.typing_speed if c.typing_speed else 0.10
    }

async def get_final_prompt(chat_id):
    gs = await get_current_global_settings()
    base_prompt = gs['prompt'] if gs else ""
    
    chat_cfg = await plugins.db.get_chat_config(chat_id)
    chat_prompt = chat_cfg.custom_prompt if chat_cfg and chat_cfg.custom_prompt else ""
    
    if chat_prompt:
        return f"{base_prompt}\n{_('additional_rules_context', chat_prompt=chat_prompt)}"
    return base_prompt

def md_to_html(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r'```(\w+)?\n?(.*?)```', r'<pre><code>\2</code></pre>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'^#+\s+(.*?)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    return text

async def simulate_action(app, chat_id, action_type: ChatAction, duration: float):
    end_time = time.time() + duration
    while time.time() < end_time:
        try: 
            await app.send_chat_action(chat_id, action_type)
        except: 
            pass
        await asyncio.sleep(min(4.0, end_time - time.time()))
    try: 
        await app.send_chat_action(chat_id, ChatAction.CANCEL)
    except: 
        pass

async def simulate_typing(app, chat_id, duration: float):
    await simulate_action(app, chat_id, ChatAction.TYPING, duration)

async def simulate_human_typing(client, chat_id, total_time, is_human_mode, t_min=1.5, t_max=3.5, p_min=0.5, p_max=2.0):
    if not is_human_mode or total_time < 3.0:
        await simulate_typing(client, chat_id, total_time)
        try: await client.send_chat_action(chat_id, ChatAction.CANCEL)
        except: pass
        return
        
    elapsed = 0
    while elapsed < total_time:
        t_type = min(random.uniform(t_min, t_max), total_time - elapsed)
        try: await client.send_chat_action(chat_id, ChatAction.TYPING)
        except: pass
        
        await asyncio.sleep(t_type)
        elapsed += t_type
        if elapsed >= total_time: break
            
        t_pause = min(random.uniform(p_min, p_max), total_time - elapsed)
        try: await client.send_chat_action(chat_id, ChatAction.CANCEL)
        except: pass
        
        await asyncio.sleep(t_pause)
        elapsed += t_pause
        
    try: await client.send_chat_action(chat_id, ChatAction.CANCEL)
    except: pass

def introduce_typo(text: str) -> str:
    if len(text) < 5: return text
    words = text.split()
    candidates = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    
    if not candidates: return text
        
    idx = random.choice(candidates)
    word = list(words[idx])
    char_idx = random.randint(1, len(word) - 2)
    
    if word[char_idx].isupper() or word[char_idx+1].isupper(): 
        return text
        
    word[char_idx], word[char_idx+1] = word[char_idx+1], word[char_idx]
    words[idx] = "".join(word)
    return " ".join(words)

async def extract_text_from_message(message: types.Message) -> str | None:
    if message.document:
        buffer = io.BytesIO()
        await plugins.bot.download(message.document, destination=buffer)
        raw_data = buffer.getvalue()
        try: return raw_data.decode('utf-8')
        except UnicodeDecodeError: return raw_data.decode('cp1251', errors='ignore')
    return message.text

def get_cancel_kb(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=callback_data)]])

def get_back_kb(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data=callback_data)]])