import html
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from pyrogram import Client

from core.utils import safe_edit
from core.config import _

router = Router()
userbot_app = None

def register_userbot(app: Client):
    global userbot_app
    userbot_app = app

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_my_info"), callback_data="my_info")]]

async def get_chat_menu_buttons(chat_id: int):
    return [[InlineKeyboardButton(text=_("btn_user_info"), callback_data=f"userinfo_{chat_id}")]]

@router.callback_query(F.data == "my_info")
async def show_my_info(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    if not userbot_app or not userbot_app.is_connected: 
        return await call.answer(_("err_userbot_not_connected_alert"), show_alert=True)
        
    try:
        me = await userbot_app.get_me()
        chat = await userbot_app.get_chat(me.id)
        
        full_name = f"{html.escape(me.first_name or '')} {html.escape(me.last_name or '')}".strip() or _("no_name")
        username = f"@{html.escape(me.username)}" if me.username else _("info_hidden_none")
        phone = f"+{me.phone_number}" if getattr(me, 'phone_number', None) else _("info_hidden_none")
        bio_text = html.escape(getattr(chat, 'bio', None) or _("info_not_filled"))
        
        vs = getattr(me, 'verification_status', None)
        check_scam = vs.is_scam if vs else False
        check_fake = vs.is_fake if vs else False
        check_verified = vs.is_verified if vs else False

        is_premium = _("info_yes_star") if getattr(me, 'is_premium', False) else _("info_no")
        is_scam = _("info_yes_warn") if (check_scam or check_fake) else _("info_no")
        is_verified = _("info_yes_check") if check_verified else _("info_no")
        is_restricted = _("info_yes_stop") if getattr(me, 'is_restricted', False) else _("info_no")
        dc_id_text = str(getattr(me, 'dc_id', _("info_unknown")))

        info_text = _(
            "info_my_profile", 
            id=me.id, full_name=full_name, username=username, phone=phone, 
            bio_text=bio_text, is_premium=is_premium, is_verified=is_verified, 
            is_scam=is_scam, is_restricted=is_restricted, dc_id_text=dc_id_text
        )
    except Exception as e:
        info_text = _("info_error", e=e)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]])
    await safe_edit(call.message, state, info_text, kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("userinfo_"))
async def show_user_info(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[1])
    await state.update_data(menu_msg_id=call.message.message_id)
    if not userbot_app or not userbot_app.is_connected: 
        return await call.answer(_("err_userbot_not_connected_alert"), show_alert=True)
        
    try:
        user = await userbot_app.get_users(chat_id)
        chat = await userbot_app.get_chat(chat_id)
        
        full_name = f"{html.escape(user.first_name or '')} {html.escape(user.last_name or '')}".strip() or _("no_name")
        username = f"@{html.escape(user.username)}" if user.username else _("info_hidden_none")
        phone = f"+{user.phone_number}" if getattr(user, 'phone_number', None) else _("info_hidden_none")
        bio_text = html.escape(getattr(chat, 'bio', None) or _("info_not_filled"))
        
        vs = getattr(user, 'verification_status', None)
        check_scam = vs.is_scam if vs else False
        check_fake = vs.is_fake if vs else False
        check_verified = vs.is_verified if vs else False

        is_premium = _("info_yes_star") if getattr(user, 'is_premium', False) else _("info_no")
        is_scam = _("info_yes_warn") if (check_scam or check_fake) else _("info_no")
        is_verified = _("info_yes_check") if check_verified else _("info_no")
        is_deleted = _("info_yes_ghost") if getattr(user, 'is_deleted', False) else _("info_no")
        is_restricted = _("info_yes_stop") if getattr(user, 'is_restricted', False) else _("info_no")
        is_contact = _("info_yes_user") if getattr(user, 'is_contact', False) else _("info_no")
        is_mutual = _("info_yes_handshake") if getattr(user, 'is_mutual_contact', False) else _("info_no")
        dc_id_text = str(getattr(user, 'dc_id', _("info_unknown")))

        info_text = _(
            "info_user_profile", 
            id=user.id, full_name=full_name, username=username, phone=phone, 
            bio_text=bio_text, is_contact=is_contact, is_mutual=is_mutual, 
            is_premium=is_premium, is_verified=is_verified, is_scam=is_scam, 
            is_deleted=is_deleted, is_restricted=is_restricted, dc_id_text=dc_id_text
        )
    except Exception as e:
        info_text = _("info_error", e=e)

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]])
    await safe_edit(call.message, state, info_text, kb, parse_mode="HTML")