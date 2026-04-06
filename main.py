import asyncio
import os
import importlib
import logging
from datetime import datetime
from dotenv import load_dotenv

# ВАЖНО: Настраиваем логи ДО импорта остальных библиотек и модулей!
logging.basicConfig(level=logging.INFO, force=True)
load_dotenv(override=True)

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext # type: ignore
from aiogram.fsm.storage.memory import MemoryStorage

from pyrogram import Client
from pyrogram.enums import ChatType

import database
from utils import plugins, safe_edit
from i18n import _

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")

bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
userbot_app = None

# ==========================================
# ЗАГРУЗЧИК ПЛАГИНОВ
# ==========================================
def load_modules(dispatcher: Dispatcher):
    if not os.path.exists("modules"):
        os.makedirs("modules")
        
    for filename in os.listdir("modules"):
        if filename.endswith(".py") and not filename.startswith("__"):
            module_name = filename[:-3]
            try:
                mod = importlib.import_module(f"modules.{module_name}")
                
                if hasattr(mod, "router"):
                    dispatcher.include_router(mod.router)
                if hasattr(mod, "register_userbot"):
                    plugins.userbot_handlers.append(mod.register_userbot)
                if hasattr(mod, "get_main_menu_buttons"):
                    plugins.main_menu_buttons.append(mod.get_main_menu_buttons)
                if hasattr(mod, "get_chat_menu_buttons"):
                    plugins.chat_menu_buttons.append(mod.get_chat_menu_buttons)
                if hasattr(mod, "get_settings_buttons"):
                    plugins.settings_buttons.append(mod.get_settings_buttons)
                if hasattr(mod, "on_startup"):
                    plugins.startup_tasks.append(mod.on_startup)
                    
                logging.info(_("log_module_loaded", module_name=module_name))
            except Exception as e:
                logging.error(_("log_module_error", module_name=module_name, e=e))

# ==========================================
# ИНТЕРФЕЙС ЯДРА
# ==========================================
async def generate_main_menu_content():
    if not userbot_app or not userbot_app.is_connected:
        return _("menu_connecting"), None

    bot_time = datetime.now().strftime("%H:%M")
    text = _("menu_main_title", time=bot_time)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for btn_func in plugins.main_menu_buttons:
        extra_buttons = await btn_func()
        if extra_buttons:
            kb.inline_keyboard.extend(extra_buttons)
            
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_settings"), callback_data="global_settings")])
    
    try:
        async for dialog in userbot_app.get_dialogs(limit=30):
            chat = dialog.chat
            if chat.type != ChatType.PRIVATE: continue
            if chat.id == 777000: continue
            
            name_parts = []
            if chat.first_name: name_parts.append(chat.first_name)
            if chat.last_name: name_parts.append(chat.last_name)
            name = " ".join(name_parts) if name_parts else _("no_name")
            
            kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_chat_name", name=name), callback_data=f"chat_{chat.id}")])
    except: pass
    
    return text, kb

async def generate_settings_menu_content():
    text = _("menu_settings_title")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for btn_func in plugins.settings_buttons:
        extra_buttons = await btn_func()
        if extra_buttons:
            kb.inline_keyboard.extend(extra_buttons)
            
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_logout"), callback_data="logout_confirm")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")])
    return text, kb

async def get_generic_chat_menu_content(chat_id):
    chat_name = f"`{chat_id}`"
    if userbot_app and userbot_app.is_connected:
        try:
            chat = await userbot_app.get_chat(chat_id)
            name_parts = []
            if chat.first_name: name_parts.append(chat.first_name)
            if chat.last_name: name_parts.append(chat.last_name)
            if name_parts: chat_name = f"*{' '.join(name_parts)}*"
        except: pass

    text = _("menu_chat_title", chat_name=chat_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    for btn_func in plugins.chat_menu_buttons:
        extra_buttons = await btn_func(chat_id)
        if extra_buttons:
            kb.inline_keyboard.extend(extra_buttons)
            
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")])
    return text, kb

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try: await message.delete()
    except: pass
    
    config = await database.get_config()
    if not config or not config[1]:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_auth_start"), callback_data="auth_start")]])
        await safe_edit(message, state, _("bot_ready_auth"), kb)
    else:
        text, kb = await generate_main_menu_content()
        await safe_edit(message, state, text, kb)

@dp.callback_query(F.data == "main_menu")
async def back_to_main(call: types.CallbackQuery, state: FSMContext):
    text, kb = await generate_main_menu_content()
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, text, kb)

@dp.callback_query(F.data == "global_settings")
async def global_settings_menu(call: types.CallbackQuery, state: FSMContext):
    text, kb = await generate_settings_menu_content()
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, text, kb)

@dp.callback_query(F.data.startswith("chat_"))
async def generic_chat_menu(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[1])
    await state.update_data(menu_msg_id=call.message.message_id)
    text, kb = await get_generic_chat_menu_content(chat_id)
    await safe_edit(call.message, state, text, kb)

# ==========================================
# PYROGRAM ЗАПУСК
# ==========================================
async def start_userbot(session_string):
    global userbot_app
    if userbot_app:
        await userbot_app.stop()
    
    userbot_app = Client("userbot", session_string=session_string, api_id=API_ID, api_hash=API_HASH, in_memory=True)

    for handler_func in plugins.userbot_handlers:
        handler_func(userbot_app)

    await userbot_app.start()
    logging.info(_("log_pyrogram_started"))

async def stop_userbot():
    global userbot_app
    if userbot_app:
        await userbot_app.stop()
        userbot_app = None

async def main():
    await database.init_db()
    
    plugins.bot = bot
    plugins.api_id = API_ID
    plugins.api_hash = API_HASH
    plugins.start_userbot_cb = start_userbot
    plugins.stop_userbot_cb = stop_userbot
    plugins.generate_menu_cb = generate_main_menu_content
    plugins.generate_chat_menu_cb = get_generic_chat_menu_content
    plugins.generate_settings_menu_cb = generate_settings_menu_content
    
    load_modules(dp)
    
    if plugins.startup_tasks:
        logging.info(_("log_startup_tasks"))
        for task in plugins.startup_tasks:
            try:
                await task()
            except Exception as e:
                logging.error(_("log_module_start_error", e=e))
    
    session = await database.get_config()
    if session and session[1]:
        logging.info(_("log_session_found"))
        await start_userbot(session[1])
        
    logging.info(_("log_polling_start"))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())