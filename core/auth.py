import asyncio
import base64
import os
import logging
import re
import time
import zoneinfo
from io import BytesIO
from html import escape
from datetime import datetime
from aiogram import Router, F, types
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from pyrogram import Client, raw
from pyrogram.errors import SessionPasswordNeeded

from core.db import AsyncSessionLocal, CoreRepository
from core.utils import plugins, safe_edit
from core.config import _

router = Router()
auth_clients = {}
_active_credential_tests = {}

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
        [InlineKeyboardButton(text=_("btn_qr_auth"), callback_data="auth_qr_start")],
        [InlineKeyboardButton(text=_("btn_change_phone"), callback_data="auth_change_phone")]
    ])

def get_qr_auth_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_qr_check"), callback_data="auth_qr_check")],
        [InlineKeyboardButton(text=_("btn_change_phone"), callback_data="auth_change_phone")]
    ])

def get_auth_error_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_retry_auth"), callback_data="auth_start")],
        [InlineKeyboardButton(text=_("btn_reenter_all"), callback_data="full_reset")]
    ])

def _code_delivery_name(value) -> str:
    if not value:
        return "unknown"

    raw_parts = [
        getattr(value, "name", None),
        getattr(value, "value", None),
        str(value),
        value.__class__.__name__,
    ]
    delivery_names = {
        "app": "Telegram app",
        "sms": "SMS",
        "call": "phone call",
        "flashcall": "flash call",
        "fragment": "Fragment SMS",
        "firebase": "Firebase SMS",
        "email": "email",
    }

    for raw in raw_parts:
        if raw is None:
            continue
        raw_lower = str(raw).lower()
        for marker, name in delivery_names.items():
            if marker in raw_lower:
                return name
    return str(value)

def _sent_code_details(sent_code) -> str:
    delivery = _code_delivery_name(getattr(sent_code, "type", None))
    next_delivery = _code_delivery_name(getattr(sent_code, "next_type", None))
    timeout = getattr(sent_code, "timeout", None)

    lines = [_("auth_delivery_method", delivery=escape(delivery))]
    if next_delivery != "unknown":
        lines.append(_("auth_delivery_next", delivery=escape(next_delivery)))
    if timeout:
        lines.append(_("auth_delivery_timeout", seconds=int(timeout)))
    return "\n".join(lines)

def _login_token_url(token: bytes) -> str:
    encoded = base64.urlsafe_b64encode(token).decode().rstrip("=")
    return f"tg://login?token={encoded}"

def _qr_png_bytes(data: str) -> bytes:
    import qrcode

    qr = qrcode.QRCode(border=3, box_size=10)
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()

async def _export_qr_login_token(client: Client, config):
    return await client.invoke(raw.functions.auth.ExportLoginToken(
        api_id=config.api_id,
        api_hash=config.api_hash,
        except_ids=[]
    ))

def _raw_type_name(value) -> str:
    return value.__class__.__name__

async def _delete_qr_message(state: FSMContext):
    data = await state.get_data()
    qr_msg_id = data.get("qr_msg_id")
    qr_chat_id = data.get("qr_chat_id")
    if not qr_msg_id or not qr_chat_id:
        return

    try:
        await plugins.bot.delete_message(qr_chat_id, qr_msg_id)
    except Exception:
        pass
    await state.update_data(qr_msg_id=None, qr_chat_id=None)

async def _send_qr_login_message(message, state: FSMContext, login_url: str, phone: str, text_key: str):
    await _delete_qr_message(state)
    photo = BufferedInputFile(_qr_png_bytes(login_url), filename="telegram-login-qr.png")
    qr_msg = await message.answer_photo(
        photo=photo,
        caption=_(text_key, phone=phone),
        reply_markup=get_qr_auth_kb(),
        parse_mode="HTML",
    )
    await state.update_data(qr_msg_id=qr_msg.message_id, qr_chat_id=qr_msg.chat.id)

async def _finish_authorized_client(client: Client, phone: str, message, state: FSMContext):
    await _delete_qr_message(state)
    try:
        me = await client.get_me()
        if getattr(me, "phone_number", None):
            phone = f"+{me.phone_number}".replace("++", "+")
    except Exception:
        pass

    session_string = await client.export_session_string()
    await plugins.db.save_session(phone, session_string)
    await client.disconnect()

    await plugins.start_userbot_cb(session_string)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")]])
    await safe_edit(message, state, _("auth_success_restart"), kb, parse_mode="HTML")
    await state.set_state(None)
    from modules.setup_wizard import begin
    wizard_cfg = await plugins.db.get_module_cfg("setup_wizard")
    if not wizard_cfg.get("completed", False):
        await begin(message, state)

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
    except Exception: pass
    new_tz = (message.text or "").strip()
    try:
        zoneinfo.ZoneInfo(new_tz)
    except (ValueError, KeyError):
        return await safe_edit(message, state, _("tz_error_setup"), parse_mode="HTML")
    await plugins.db.update_global_config(tz=new_tz)
    # Optional cookies and the remaining settings are covered after Telegram login.
    await finish_setup(message, state)

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
    await _delete_qr_message(state)
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
        delivery_details = _sent_code_details(sent_code)
        logging.info(
            "[Auth] Code requested for %s: type=%r next_type=%r timeout=%s",
            phone,
            getattr(sent_code, "type", None),
            getattr(sent_code, "next_type", None),
            getattr(sent_code, "timeout", None),
        )
        auth_clients[message.from_user.id] = client
        await safe_edit(message, state, _("auth_code_sent", phone=phone, delivery_details=delivery_details), get_numpad_kb(), parse_mode="HTML")
        await state.update_data(phone=phone, hash=sent_code.phone_code_hash, entered_code="")
        await state.set_state(AuthFSM.code)
    except Exception as e:
        await _delete_qr_message(state)
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

@router.callback_query(F.data == "auth_qr_start", AuthFSM.code)
async def auth_qr_start(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get("phone", "")
    client = auth_clients.get(call.from_user.id)
    if not client:
        return await safe_edit(call.message, state, _("auth_error_options", e="Temporary auth client not found"), get_auth_error_kb(), parse_mode="HTML")

    try:
        config = await plugins.db.get_global_config()
        result = await _export_qr_login_token(client, config)
        result_type = _raw_type_name(result)
        if result_type == "LoginTokenSuccess":
            await _finish_authorized_client(client, phone, call.message, state)
            auth_clients.pop(call.from_user.id, None)
            return
        if result_type == "LoginTokenMigrateTo":
            return await safe_edit(call.message, state, _("auth_qr_migrate", dc=result.dc_id), get_auth_error_kb(), parse_mode="HTML")

        login_url = _login_token_url(result.token)
        await safe_edit(call.message, state, _("auth_qr_sent", phone=phone), get_numpad_kb(), parse_mode="HTML")
        await _send_qr_login_message(call.message, state, login_url, phone, "auth_qr_prompt")
    except SessionPasswordNeeded:
        await _delete_qr_message(state)
        await safe_edit(call.message, state, _("auth_2fa_required"), parse_mode="HTML")
        await state.set_state(AuthFSM.password)
    except Exception as e:
        await safe_edit(call.message, state, _("auth_error_options", e=e), get_auth_error_kb(), parse_mode="HTML")

@router.callback_query(F.data == "auth_qr_check", AuthFSM.code)
async def auth_qr_check(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    phone = data.get("phone", "")
    client = auth_clients.get(call.from_user.id)
    if not client:
        return await safe_edit(call.message, state, _("auth_error_options", e="Temporary auth client not found"), get_auth_error_kb(), parse_mode="HTML")

    try:
        config = await plugins.db.get_global_config()
        result = await _export_qr_login_token(client, config)
        result_type = _raw_type_name(result)
        if result_type == "LoginTokenSuccess":
            await _finish_authorized_client(client, phone, call.message, state)
            auth_clients.pop(call.from_user.id, None)
            return
        if result_type == "LoginTokenMigrateTo":
            return await safe_edit(call.message, state, _("auth_qr_migrate", dc=result.dc_id), get_auth_error_kb(), parse_mode="HTML")

        login_url = _login_token_url(result.token)
        await safe_edit(call.message, state, _("auth_qr_sent", phone=phone), get_numpad_kb(), parse_mode="HTML")
        await _send_qr_login_message(call.message, state, login_url, phone, "auth_qr_waiting")
    except SessionPasswordNeeded:
        await _delete_qr_message(state)
        await safe_edit(call.message, state, _("auth_2fa_required"), parse_mode="HTML")
        await state.set_state(AuthFSM.password)
    except Exception as e:
        await safe_edit(call.message, state, _("auth_error_options", e=e), get_auth_error_kb(), parse_mode="HTML")

async def process_auth_code(call: types.CallbackQuery, state: FSMContext, code: str):
    data = await state.get_data()
    phone, client = data['phone'], auth_clients.get(call.from_user.id)
    try:
        await client.sign_in(phone, data['hash'], code)
        await _finish_authorized_client(client, phone, call.message, state)
        auth_clients.pop(call.from_user.id, None)
    except SessionPasswordNeeded:
        await safe_edit(call.message, state, _("auth_2fa_required"), parse_mode="HTML")
        await state.set_state(AuthFSM.password)
    except Exception as e:
        await _delete_qr_message(state)
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
        await _finish_authorized_client(client, data['phone'], message, state)
        auth_clients.pop(message.from_user.id, None)
    except Exception as e:
        await _delete_qr_message(state)
        await safe_edit(message, state, _("auth_error_options", e=e), get_auth_error_kb(), parse_mode="HTML")
        await client.disconnect()
        await state.set_state(None)

@router.callback_query(F.data == "set_edit_keys")
async def cb_edit_keys(call: types.CallbackQuery, state: FSMContext):
    from modules.api_keys_info import keys_info
    await keys_info(call, state)

@router.callback_query(F.data == "api_keys_edit")
async def ask_edit_keys(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id, api_test_running=False)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="set_edit_keys")]])
    await state.set_state(BotSettingsFSM.keys)
    await safe_edit(call.message, state, _("ask_new_keys"), kb, parse_mode="HTML")

@router.message(BotSettingsFSM.keys)
async def save_new_keys(message: types.Message, state: FSMContext):
    try: await message.delete()
    except Exception: pass
    from core.services import _split_csv, _INVALID_KEYS
    keys = list(dict.fromkeys(_split_csv((message.text or "").replace("\n", ","))))
    if not keys:
        return
    await plugins.db.update_global_config(api_keys=",".join(keys))
    _INVALID_KEYS.difference_update(keys)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="set_edit_keys")]])
    await state.set_state(None)
    await safe_edit(message, state, _("keys_updated"), kb)

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

@router.callback_query(F.data.in_({"test_ai_config", "api_keys_test"}))
async def cb_test_ai(call: types.CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    config = await plugins.db.get_global_config()
    if uid != config.admin_id or call.message.chat.id != uid:
        return await call.answer(_("auth_not_authorized"), show_alert=True)
    if uid in _active_credential_tests:
        return await call.answer(_("api_test_busy"), show_alert=False)
    from core.api_key_tests import CredentialTestRun
    job = CredentialTestRun("keys" if call.data == "api_keys_test" else "models")
    _active_credential_tests[uid] = job
    await state.update_data(menu_msg_id=call.message.message_id, api_test_running=True)
    if _active_credential_tests.get(uid) is not job:
        job.stop()
        return
    stop_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_stop_test"), callback_data="stop_ai_test")]])
    async def progress_callback(text):
        if _active_credential_tests.get(uid) is not job or not (await state.get_data()).get("api_test_running"):
            return False
        await safe_edit(call.message, state, text, stop_kb, parse_mode="HTML")
        return True
    async def execute_test():
        try:
            await job.execute(progress_callback)
            if _active_credential_tests.get(uid) is job and (await state.get_data()).get("api_test_running"):
                from modules.api_keys_info import build_keys_info
                text, kb = await build_keys_info(results=True)
                await safe_edit(call.message, state, text, kb, parse_mode="HTML")
        except asyncio.CancelledError:
            job.stop()
            raise
        except Exception as exc:
            logging.error("[API tests] Test worker failed: %s", type(exc).__name__)
        finally:
            if _active_credential_tests.get(uid) is job:
                _active_credential_tests.pop(uid, None)
                await state.update_data(api_test_running=False)
    job.task = asyncio.create_task(execute_test(), name="api-credential-test")

@router.callback_query(F.data == "stop_ai_test")
async def cb_stop_test(call: types.CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    config = await plugins.db.get_global_config()
    if uid != config.admin_id or call.message.chat.id != uid:
        return await call.answer(_("auth_not_authorized"), show_alert=True)
    job = _active_credential_tests.pop(uid, None)
    if job:
        job.stop()
    await state.update_data(api_test_running=False)
    try:
        async with asyncio.timeout(1):
            await call.answer()
    except Exception:
        pass
    if job:
        from core.api_key_tests import _save_report
        await _save_report(job.report)
    from modules.api_keys_info import keys_info
    await keys_info(call, state)

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

async def get_limits_text(repo: CoreRepository, api_keys: list[str]) -> str:
    if not api_keys:
        return _("limits_empty")
    
    current_time = time.time()
    res = ""
    for key in api_keys:
        hidden_key = f"{key[:4]}***{key[-4:]}"
        res += _("limits_key_header", key=hidden_key)
        
        state = await repo.get_ai_key_state(key)
        has_bans = False
        
        if state.unban_time and state.unban_time > current_time:
            has_bans = True
            dt = datetime.fromtimestamp(state.unban_time).strftime('%d.%m %H:%M:%S')
            res += _("limits_global_ban", time=dt)
            
        if state.exhausted_models:
            for mod, unban_ts in state.exhausted_models.items():
                if unban_ts > current_time:
                    has_bans = True
                    dt = datetime.fromtimestamp(unban_ts).strftime('%d.%m %H:%M:%S')
                    res += _("limits_model_ban", model=mod, time=dt)
                    
        if state.search_exhausted_models:
            for mod, unban_ts in state.search_exhausted_models.items():
                if unban_ts > current_time:
                    has_bans = True
                    dt = datetime.fromtimestamp(unban_ts).strftime('%d.%m %H:%M:%S')
                    res += _("limits_search_ban", model=mod, time=dt)
                    
        if not has_bans:
            res += _("limits_ok")
        res += "\n"
        
    return res

@router.callback_query(F.data == "sys_limits_menu")
async def limits_menu(call: types.CallbackQuery, state: FSMContext):
    config = await plugins.db.get_global_config()
    keys = [k.strip() for k in (config.api_keys or "").split(",") if k.strip()]
    
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        text_info = await get_limits_text(repo, keys)
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_reset_limits"), callback_data="sys_limits_reset")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, _("limits_menu_title", limits_text=text_info), kb, parse_mode="HTML")

@router.callback_query(F.data == "sys_limits_reset")
async def limits_reset(call: types.CallbackQuery, state: FSMContext):
    await plugins.db.reset_all_limits()
    await call.answer(_("limits_reset_alert"), show_alert=True)
    await limits_menu(call, state)