import time
import asyncio
from pyrogram.enums import ChatAction
from aiogram import types
from aiogram.fsm.context import FSMContext
import database
from i18n import _

# ==========================================
# СИСТЕМА ПЛАГИНОВ (РЕЕСТР)
# ==========================================
class PluginManager:
    def __init__(self):
        self.userbot_handlers = []
        self.main_menu_buttons = []
        self.chat_menu_buttons = []
        self.settings_buttons = []  # <--- Кнопки для меню настроек
        self.startup_tasks = []
        self.bot = None
        self.api_id = None
        self.api_hash = None
        self.start_userbot_cb = None
        self.stop_userbot_cb = None
        self.generate_menu_cb = None
        self.generate_chat_menu_cb = None
        self.generate_settings_menu_cb = None  # <--- Генератор меню настроек

plugins = PluginManager()

# ==========================================
# ОБЩИЕ ФУНКЦИИ
# ==========================================
async def safe_edit(message: types.Message, state: FSMContext, text: str, reply_markup=None, parse_mode="Markdown"):
    """Безопасное редактирование меню с удалением старого при ошибках"""
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    bot = plugins.bot
    
    try:
        if menu_msg_id:
            await bot.edit_message_text(text, chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            raise Exception("No ID")
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
            
        if menu_msg_id:
            try: await bot.delete_message(chat_id=message.chat.id, message_id=menu_msg_id)
            except: pass
            
        msg = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        await state.update_data(menu_msg_id=msg.message_id)

async def get_current_global_settings():
    c = await database.get_config()
    if not c: return None
    return {
        "prompt": c[4] if c[4] else _("default_prompt_env"),
        "typing": c[5] if c[5] else 0.10,
        "db_min": c[6] if c[6] is not None else 6,
        "db_max": c[7] if c[7] is not None else 18,
        "da_min": c[8] if c[8] is not None else 1,
        "da_max": c[9] if c[9] is not None else 5,
        "split_chance": c[10] if (len(c) > 10 and c[10] is not None) else 30,
        "split_min": c[11] if (len(c) > 11 and c[11] is not None) else 1
    }

async def get_final_prompt(chat_id):
    gs = await get_current_global_settings()
    base_prompt = gs['prompt'] if gs else ""
    
    chat_cfg = await database.get_chat_settings(chat_id)
    chat_prompt = chat_cfg[1] if (chat_cfg and chat_cfg[1]) else ""
    
    if chat_prompt:
        return f"{base_prompt}{_('additional_rules_context', chat_prompt=chat_prompt)}"
    return base_prompt

async def simulate_typing(app, chat_id, duration):
    end_time = time.time() + duration
    while time.time() < end_time:
        try: await app.send_chat_action(chat_id, ChatAction.TYPING)
        except: pass
        await asyncio.sleep(min(4.0, end_time - time.time()))
    try: await app.send_chat_action(chat_id, ChatAction.CANCEL)
    except: pass