import asyncio
import os
import importlib
import logging
import inspect
import zoneinfo
from datetime import datetime
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, force=True)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
load_dotenv(override=True)

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from pyrogram import Client
from pyrogram.enums import ChatType

from core.db import init_db
from core.utils import plugins, safe_edit
from core.config import _
from core.auth import router as auth_router

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH")

bot = Bot(token=TG_BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(auth_router)
userbot_app = None

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

async def generate_main_menu_content():
    if not userbot_app or not userbot_app.is_connected:
        return _("menu_connecting"), None

    config = await plugins.db.get_global_config()
    tz_str = config.tz if config and config.tz else "Europe/London"
    try:
        tz = zoneinfo.ZoneInfo(tz_str)
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")
        tz_str = "UTC"

    bot_time = datetime.now(tz).strftime("%H:%M")
    text = _("menu_main_title", time=bot_time, tz=tz_str)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_my_chats"), callback_data="chats_list")])
    
    for btn_func in plugins.main_menu_buttons:
        extra_buttons = await btn_func()
        if extra_buttons:
            kb.inline_keyboard.extend(extra_buttons)
            
    for btn_func in plugins.settings_buttons:
        extra_buttons = await btn_func()
        if extra_buttons:
            kb.inline_keyboard.extend(extra_buttons)
            
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_settings"), callback_data="global_settings")])
    
    return text, kb

async def generate_settings_menu_content():
    text = _("menu_settings_title")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_edit_keys"), callback_data="set_edit_keys")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_edit_models"), callback_data="set_edit_models")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_edit_tz"), callback_data="set_edit_tz")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_test_ai"), callback_data="test_ai_config")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_yt_cookies_menu"), callback_data="yt_cookies_menu")])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_full_reset"), callback_data="full_reset")])
            
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")])
    return text, kb

@dp.callback_query(F.data == "chats_list")
async def cb_chats_list(call: types.CallbackQuery, state: FSMContext):
    text = _("menu_chats_list")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    if userbot_app and userbot_app.is_connected:
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
        
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")])
    
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, text, kb)

async def get_generic_chat_menu_content(chat_id):
    chat_name = f"<code>{chat_id}</code>"
    if userbot_app and userbot_app.is_connected:
        try:
            chat = await userbot_app.get_chat(chat_id)
            name_parts = []
            if chat.first_name: name_parts.append(chat.first_name)
            if chat.last_name: name_parts.append(chat.last_name)
            if name_parts: chat_name = f"<b>{' '.join(name_parts)}</b>"
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
    try: await message.delete()
    except: pass
    await state.clear()
    
    config = await plugins.db.get_global_config()
    
    if not config.admin_id:
        await plugins.db.update_global_config(admin_id=message.from_user.id)
        config.admin_id = message.from_user.id
        logging.info(_("log_admin_claimed", admin_id=message.from_user.id))
        
    if message.from_user.id != config.admin_id:
        return await message.answer(_("auth_not_authorized"))
    
    if not config.is_setup_completed:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_ready"), callback_data="setup_ready")]])
        await safe_edit(message, state, _("setup_guide"), kb)
    elif not config.session_string:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_login_userbot"), callback_data="auth_start")]])
        await safe_edit(message, state, _("setup_configured_not_logged"), kb)
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

async def start_userbot(session_string):
    global userbot_app
    if userbot_app:
        await userbot_app.stop()
    
    userbot_app = Client("userbot", session_string=session_string, api_id=API_ID, api_hash=API_HASH, in_memory=True)

    for handler_func in plugins.userbot_handlers:
        try:
            sig = inspect.signature(handler_func)
            if len(sig.parameters) >= 2:
                handler_func(userbot_app, bot)
            else:
                handler_func(userbot_app)
        except Exception as e:
            logging.error(_("log_module_start_error", e=e))

    await userbot_app.start()
    logging.info(_("log_pyrogram_started"))

async def stop_userbot():
    global userbot_app
    if userbot_app:
        await userbot_app.stop()
        userbot_app = None

async def main():
    await init_db()
    
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
    
    config = await plugins.db.get_global_config()
    if config and config.session_string:
        logging.info(_("log_session_found"))
        await start_userbot(config.session_string)
        
    logging.info(_("log_polling_start"))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())