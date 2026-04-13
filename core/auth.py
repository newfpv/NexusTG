import os
import logging
import re
import zoneinfo
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

from core.utils import plugins, safe_edit
from core.config import _

router = Router()
auth_clients = {}

class SetupFSM(StatesGroup):
    api_id = State()
    api_hash = State()
    api_keys = State()
    tz = State()
    yt_cookies = State()

class AuthFSM(StatesGroup):
    phone = State()
    code = State()
    password = State()

class BotSettingsFSM(StatesGroup):
    keys = State()
    models = State()
    tz = State()
    yt_cookies = State()

def get_numpad_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data="num_1"), InlineKeyboardButton(text="2", callback_data="num_2"), InlineKeyboardButton(text="3", callback_data="num_3")],
        [InlineKeyboardButton(text="4", callback_data="num_4"), InlineKeyboardButton(text="5", callback_data="num_5"), InlineKeyboardButton(text="6", callback_data="num_6")],
        [InlineKeyboardButton(text="7", callback_data="num_7"), InlineKeyboardButton(text="8", callback_data="num_8"), InlineKeyboardButton(text="9", callback_data="num_9")],
        [InlineKeyboardButton(text=_("btn_numpad_del"), callback_data="num_del"), InlineKeyboardButton(text="0", callback_data="num_0"), InlineKeyboardButton(text=_("btn_numpad_submit"), callback_data="num_submit")],
        [InlineKeyboardButton(text=_("btn_change_phone"), callback_data="auth_change_phone")]
    ])

def get_auth_error_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_retry_auth"), callback_data="auth_start")],
        [InlineKeyboardButton(text=_("btn_reenter_all"), callback_data="full_reset")]
    ])

@router.callback_query(F.data == "setup_ready")
async def setup_ready(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetupFSM.api_id)
    await safe_edit(call.message, state, _("setup_ask_api_id"), parse_mode="HTML")

@router.message(SetupFSM.api_id)
async def setup_api_id(message: types.Message, state: FSMContext):
    try: await message.delete() 
    except: pass
    if not message.text.isdigit(): 
        return await safe_edit(message, state, _("setup_api_id_error"))
    await plugins.db.update_global_config(api_id=int(message.text))
    await state.set_state(SetupFSM.api_hash)
    await safe_edit(message, state, _("setup_ask_api_hash"), parse_mode="HTML")

@router.message(SetupFSM.api_hash)
async def setup_api_hash(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    text = message.text.strip()
    if len(text) != 32 or not re.match(r"^[a-fA-F0-9]+$", text):
        return await safe_edit(message, state, _("setup_api_hash_error"))
    await plugins.db.update_global_config(api_hash=text)
    await state.set_state(SetupFSM.api_keys)
    await safe_edit(message, state, _("setup_ask_api_keys"), parse_mode="HTML")

@router.message(SetupFSM.api_keys)
async def setup_api_keys(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    await plugins.db.update_global_config(api_keys=message.text.strip())
    await state.set_state(SetupFSM.tz)
    await safe_edit(message, state, _("setup_ask_tz"), parse_mode="HTML")

@router.message(SetupFSM.tz)
async def setup_timezone(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    new_tz = message.text.strip()
    try:
        zoneinfo.ZoneInfo(new_tz)
        await plugins.db.update_global_config(tz=new_tz)
        await state.set_state(SetupFSM.yt_cookies)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_skip"), callback_data="skip_yt_setup")]])
        await safe_edit(message, state, _("setup_ask_yt_cookies"), kb, parse_mode="HTML")
    except Exception:
        await safe_edit(message, state, _("tz_error_setup"), parse_mode="HTML")

@router.message(SetupFSM.yt_cookies, F.document)
async def setup_yt_cookies(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    doc = message.document
    if not doc.file_name.endswith('.txt'):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_skip"), callback_data="skip_yt_setup")]])
        return await safe_edit(message, state, _("yt_invalid_format"), kb, parse_mode="HTML")
    
    file = await message.bot.get_file(doc.file_id)
    await message.bot.download_file(file.file_path, "data/cookies.txt")
    await finish_setup(message, state)

@router.callback_query(F.data == "skip_yt_setup")
async def skip_yt_setup(call: types.CallbackQuery, state: FSMContext):
    await finish_setup(call.message, state)

async def finish_setup(message, state):
    await plugins.db.update_global_config(is_setup_completed=True)
    await state.set_state(None)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_login_userbot"), callback_data="auth_start")]])
    await safe_edit(message, state, _("setup_completed"), kb, parse_mode="HTML")

@router.callback_query(F.data == "auth_start")
async def auth_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AuthFSM.phone)
    await safe_edit(call.message, state, _("auth_enter_phone"), parse_mode="HTML")

@router.callback_query(F.data == "auth_change_phone")
async def auth_change_phone(call: types.CallbackQuery, state: FSMContext):
    client = auth_clients.get(call.from_user.id)
    if client:
        try: await client.disconnect()
        except: pass
        del auth_clients[call.from_user.id]
    await state.set_state(AuthFSM.phone)
    await safe_edit(call.message, state, _("auth_enter_phone"), parse_mode="HTML")

@router.message(AuthFSM.phone)
async def auth_phone(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    config = await plugins.db.get_global_config()
    phone = message.text.strip()
    client = Client(f"temp_{message.from_user.id}", api_id=config.api_id, api_hash=config.api_hash, in_memory=True, no_updates=True)
    await client.connect()
    try:
        sent_code = await client.send_code(phone)
        auth_clients[message.from_user.id] = client
        await safe_edit(message, state, _("auth_code_sent", phone=phone), get_numpad_kb(), parse_mode="HTML")
        await state.update_data(phone=phone, hash=sent_code.phone_code_hash, entered_code="")
        await state.set_state(AuthFSM.code)
    except Exception as e:
        await safe_edit(message, state, _("auth_error_options", e=e), get_auth_error_kb(), parse_mode="HTML")
        await client.disconnect()
        await state.set_state(None)

@router.callback_query(F.data.startswith("num_"), AuthFSM.code)
async def process_numpad(call: types.CallbackQuery, state: FSMContext):
    action = call.data.split("_")[1]
    data = await state.get_data()
    current_code = data.get("entered_code", "")
    if action == "del": current_code = current_code[:-1]
    elif action == "submit":
        if len(current_code) < 5: return await call.answer(_("auth_code_short"), show_alert=True)
        await safe_edit(call.message, state, _("auth_checking_code"))
        await process_auth_code(call, state, current_code)
        return
    else:
        if len(current_code) < 5: current_code += action
    await state.update_data(entered_code=current_code)
    display_code = " ".join(list(current_code)) + " _" * (5 - len(current_code))
    phone = data.get("phone", "Неизвестно")
    await safe_edit(call.message, state, _("auth_enter_code_display", display_code=display_code, phone=phone), get_numpad_kb(), parse_mode="HTML")

async def process_auth_code(call: types.CallbackQuery, state: FSMContext, code: str):
    data = await state.get_data()
    phone, client = data['phone'], auth_clients.get(call.from_user.id)
    try:
        await client.sign_in(phone, data['hash'], code)
        session_string = await client.export_session_string()
        await plugins.db.save_session(phone, session_string)
        await client.disconnect()
        del auth_clients[call.from_user.id]
        
        await plugins.start_userbot_cb(session_string)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")]])
        await safe_edit(call.message, state, _("auth_success_restart"), kb, parse_mode="HTML")
        await state.set_state(None)
    except SessionPasswordNeeded:
        await safe_edit(call.message, state, _("auth_2fa_required"), parse_mode="HTML")
        await state.set_state(AuthFSM.password)
    except Exception as e:
        await safe_edit(call.message, state, _("auth_error_options", e=e), get_auth_error_kb(), parse_mode="HTML")
        await client.disconnect()
        await state.set_state(None)

@router.message(AuthFSM.password)
async def auth_password(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    client = auth_clients.get(message.from_user.id)
    try:
        await client.check_password(message.text)
        session_string = await client.export_session_string()
        await plugins.db.save_session(data['phone'], session_string)
        await client.disconnect()
        del auth_clients[message.from_user.id]
        
        await plugins.start_userbot_cb(session_string)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")]])
        await safe_edit(message, state, _("auth_success_restart"), kb, parse_mode="HTML")
        await state.set_state(None)
    except Exception as e:
        await safe_edit(message, state, _("auth_error_options", e=e), get_auth_error_kb(), parse_mode="HTML")
        await client.disconnect()
        await state.set_state(None)

@router.callback_query(F.data == "set_edit_keys")
async def cb_edit_keys(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="global_settings")]])
    await state.set_state(BotSettingsFSM.keys)
    await safe_edit(call.message, state, _("ask_new_keys"), kb, parse_mode="HTML")

@router.message(BotSettingsFSM.keys)
async def save_new_keys(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    await plugins.db.update_global_config(api_keys=message.text.strip())
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]])
    await safe_edit(message, state, _("keys_updated"), kb)
    await state.set_state(None)

@router.callback_query(F.data == "set_edit_models")
async def cb_edit_models(call: types.CallbackQuery, state: FSMContext):
    config = await plugins.db.get_global_config()
    current = config.model_fallback_list or _("not_set")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="global_settings")]])
    await state.set_state(BotSettingsFSM.models)
    await safe_edit(call.message, state, _("ask_new_models", current_models=current), kb, parse_mode="HTML")

@router.message(BotSettingsFSM.models)
async def save_new_models(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    await plugins.db.update_global_config(model_fallback_list=message.text.strip())
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]])
    await safe_edit(message, state, _("models_updated"), kb)
    await state.set_state(None)

@router.callback_query(F.data == "set_edit_tz")
async def cb_edit_tz(call: types.CallbackQuery, state: FSMContext):
    config = await plugins.db.get_global_config()
    current = config.tz or "Europe/London"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="global_settings")]])
    await state.set_state(BotSettingsFSM.tz)
    await safe_edit(call.message, state, _("ask_new_tz", current_tz=current), kb, parse_mode="HTML")

@router.message(BotSettingsFSM.tz)
async def save_new_tz(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    new_tz = message.text.strip()
    
    try:
        zoneinfo.ZoneInfo(new_tz)
        await plugins.db.update_global_config(tz=new_tz)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]])
        await safe_edit(message, state, _("tz_updated"), kb, parse_mode="HTML")
        await state.set_state(None)
    except Exception:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="global_settings")]])
        await safe_edit(message, state, _("tz_error_setup"), kb, parse_mode="HTML")

@router.callback_query(F.data == "test_ai_config")
async def cb_test_ai(call: types.CallbackQuery, state: FSMContext):
    plugins.cache.delete(f"cancel_test_{call.from_user.id}")
    
    stop_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_stop_test"), callback_data="stop_ai_test")]])
    await safe_edit(call.message, state, _("test_running"), stop_kb, parse_mode="HTML")
    
    from core.services import test_ai_credentials
    
    async def progress_callback(text: str) -> bool:
        if plugins.cache.get(f"cancel_test_{call.from_user.id}"):
            return False
        await safe_edit(call.message, state, text, stop_kb, parse_mode="HTML")
        return True
        
    result_text = await test_ai_credentials(progress_cb=progress_callback)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]])
    await safe_edit(call.message, state, result_text, kb, parse_mode="HTML")

@router.callback_query(F.data == "stop_ai_test")
async def cb_stop_test(call: types.CallbackQuery, state: FSMContext):
    plugins.cache.set(f"cancel_test_{call.from_user.id}", True, ttl=60)
    await call.answer(_("test_stopping"), show_alert=False)

@router.callback_query(F.data == "full_reset")
async def ask_full_reset(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_confirm_reset"), callback_data="confirm_full_reset")],
        [InlineKeyboardButton(text=_("btn_cancel"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, _("ask_full_reset"), kb, parse_mode="HTML")

@router.callback_query(F.data == "confirm_full_reset")
async def do_full_reset(call: types.CallbackQuery, state: FSMContext):
    if plugins.stop_userbot_cb:
        await plugins.stop_userbot_cb()
    await plugins.db.full_reset()
    await safe_edit(call.message, state, _("auth_session_deleted"))
    await state.clear()

@router.callback_query(F.data == "yt_cookies_menu")
async def yt_cookies_menu(call: types.CallbackQuery, state: FSMContext):
    has_cookies = os.path.exists("data/cookies.txt")
    status = _("yt_status_loaded") if has_cookies else _("yt_status_missing")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_yt_cookies_upload"), callback_data="yt_cookies_upload")])
    
    if has_cookies:
        kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_yt_cookies_delete"), callback_data="yt_cookies_delete")])
        
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")])
    
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("yt_cookies_menu_text", status=status), kb, parse_mode="HTML")

@router.callback_query(F.data == "yt_cookies_upload")
async def yt_cookies_upload(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="yt_cookies_menu")]])
    await safe_edit(call.message, state, _("yt_upload_prompt"), kb, parse_mode="HTML")
    await state.set_state(BotSettingsFSM.yt_cookies)

@router.message(BotSettingsFSM.yt_cookies, F.document)
async def yt_cookies_doc_handler(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    doc = message.document
    if not doc.file_name.endswith('.txt'):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="yt_cookies_menu")]])
        await safe_edit(message, state, _("yt_invalid_format"), kb, parse_mode="HTML")
        return
        
    file = await message.bot.get_file(doc.file_id)
    await message.bot.download_file(file.file_path, "data/cookies.txt")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="yt_cookies_menu")]])
    await safe_edit(message, state, _("yt_cookies_updated"), kb, parse_mode="HTML")
    await state.set_state(None)

@router.callback_query(F.data == "yt_cookies_delete")
async def yt_cookies_delete(call: types.CallbackQuery, state: FSMContext):
    if os.path.exists("data/cookies.txt"):
        os.remove("data/cookies.txt")
    await call.answer(_("yt_cookies_deleted_alert"), show_alert=False)
    await yt_cookies_menu(call, state)