from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded

import database
from utils import safe_edit, plugins
from i18n import _

router = Router()
auth_clients = {}

class AuthFSM(StatesGroup):
    phone = State()
    code = State()
    password = State()

def get_numpad_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data="num_1"), InlineKeyboardButton(text="2", callback_data="num_2"), InlineKeyboardButton(text="3", callback_data="num_3")],
        [InlineKeyboardButton(text="4", callback_data="num_4"), InlineKeyboardButton(text="5", callback_data="num_5"), InlineKeyboardButton(text="6", callback_data="num_6")],
        [InlineKeyboardButton(text="7", callback_data="num_7"), InlineKeyboardButton(text="8", callback_data="num_8"), InlineKeyboardButton(text="9", callback_data="num_9")],
        [InlineKeyboardButton(text=_("btn_numpad_del"), callback_data="num_del"), InlineKeyboardButton(text="0", callback_data="num_0"), InlineKeyboardButton(text=_("btn_numpad_submit"), callback_data="num_submit")]
    ])

@router.callback_query(F.data == "logout_confirm")
async def logout_confirm_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_logout_yes"), callback_data="logout")],
        [InlineKeyboardButton(text=_("btn_logout_no"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, _("logout_confirm_text"), kb)

@router.callback_query(F.data == "logout")
async def logout(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    await database.delete_session()
    
    if plugins.stop_userbot_cb:
        await plugins.stop_userbot_cb()
        
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_auth_start"), callback_data="auth_start")]])
    await safe_edit(call.message, state, _("auth_session_deleted"), kb)

@router.callback_query(F.data == "auth_start")
async def auth_start(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    await state.set_state(AuthFSM.phone)
    await safe_edit(call.message, state, _("auth_enter_phone"))

@router.message(AuthFSM.phone)
async def auth_phone(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    phone = message.text.strip()
    client = Client(f"temp_{message.from_user.id}", api_id=plugins.api_id, api_hash=plugins.api_hash, in_memory=True)
    await client.connect()
    
    try:
        sent_code = await client.send_code(phone)
        auth_clients[message.from_user.id] = client
        
        await safe_edit(message, state, _("auth_code_sent"), get_numpad_kb())
        await state.update_data(phone=phone, hash=sent_code.phone_code_hash, entered_code="")
        await state.set_state(AuthFSM.code)
    except Exception as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_try_again"), callback_data="auth_start")]])
        await safe_edit(message, state, _("auth_send_code_error", e=e), kb)
        await client.disconnect()
        await state.set_state(None)

@router.callback_query(F.data.startswith("num_"), AuthFSM.code)
async def process_numpad(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    action = call.data.split("_")[1]
    data = await state.get_data()
    current_code = data.get("entered_code", "")
    
    if action == "del":
        current_code = current_code[:-1]
    elif action == "submit":
        if len(current_code) < 5:
            return await call.answer(_("auth_code_too_short"), show_alert=True)
        await safe_edit(call.message, state, _("auth_checking_code"))
        await process_auth_code(call, state, current_code)
        return
    else:
        if len(current_code) < 5:
            current_code += action
        
    await state.update_data(entered_code=current_code)
    display_code = " ".join(list(current_code)) + " _" * (5 - len(current_code))
    await safe_edit(call.message, state, _("auth_code_entered", display_code=display_code), get_numpad_kb())

async def process_auth_code(call: types.CallbackQuery, state: FSMContext, code: str):
    data = await state.get_data()
    phone = data['phone']
    
    client = auth_clients.get(call.from_user.id)
    if not client:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_to_start"), callback_data="auth_start")]])
        await safe_edit(call.message, state, _("auth_session_expired"), kb)
        await state.set_state(None)
        return
    
    try:
        await client.sign_in(phone, data['hash'], code)
        session_string = await client.export_session_string()
        await database.save_session(phone, session_string)
        
        await safe_edit(call.message, state, _("auth_success_starting"))
        await plugins.start_userbot_cb(session_string)
        
        text, kb = await plugins.generate_menu_cb()
        await safe_edit(call.message, state, _("auth_bot_started", text=text), kb)
        
        await client.disconnect()
        del auth_clients[call.from_user.id]
        await state.set_state(None)
        
    except SessionPasswordNeeded:
        await safe_edit(call.message, state, _("auth_2fa_required"))
        await state.set_state(AuthFSM.password)
        
    except Exception as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_to_start"), callback_data="auth_start")]])
        await safe_edit(call.message, state, _("auth_error", e=e), kb)
        await client.disconnect()
        if call.from_user.id in auth_clients:
            del auth_clients[call.from_user.id]
        await state.set_state(None)

@router.message(AuthFSM.password)
async def auth_password(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    data = await state.get_data()
    phone = data['phone']
    client = auth_clients.get(message.from_user.id)
    if not client:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_to_start"), callback_data="auth_start")]])
        await safe_edit(message, state, _("auth_session_expired"), kb)
        await state.set_state(None)
        return
    
    try:
        await client.check_password(message.text)
        session_string = await client.export_session_string()
        await database.save_session(phone, session_string)
        
        await safe_edit(message, state, _("auth_success_starting"))
        await plugins.start_userbot_cb(session_string)
        
        text, kb = await plugins.generate_menu_cb()
        await safe_edit(message, state, _("auth_bot_started", text=text), kb)
        
        await client.disconnect()
        del auth_clients[message.from_user.id]
        await state.set_state(None)
        
    except Exception as e:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_to_start"), callback_data="auth_start")]])
        await safe_edit(message, state, _("auth_wrong_password", e=e), kb)
        await client.disconnect()
        if message.from_user.id in auth_clients:
            del auth_clients[message.from_user.id]
        await state.set_state(None)