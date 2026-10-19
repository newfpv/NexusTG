import asyncio
import html
import logging
import re
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions
from sqlalchemy.orm.attributes import flag_modified

from core.db import AsyncSessionLocal, CoreRepository
from core.services import generate_ai_response
from core.utils import safe_edit
from core.config import _

router = Router()

class FactCheckerStates(StatesGroup):
    waiting_for_command = State()

def parse_markdown_to_html(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'(?:•|\-|\*)\s*(.+?)\s*\((https?://[^\s\)]+)\)', r'• <a href="\2">\1</a>', text)
    text = re.sub(r'(?<!=")(https?://[^\s\)]+)', r'<a href="\1">\1</a>', text)
    text = re.sub(r'\*\*([^\*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'^\s*\*\s+', '• ', text, flags=re.MULTILINE)
    
    return text

async def _get_cfg():
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        v = c.module_settings.get("fact_checker", {})
        logging.debug("[Fact] Global configuration loaded")
        return {
            "is_active": v.get("is_active", True),
            "command": v.get("command", ".fact")
        }

async def _upd_cfg(**kwargs):
    logging.info(f"[Fact] Updating global configuration: {list(kwargs.keys())}")
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        v = dict(c.module_settings.get("fact_checker", {}))
        v.update(kwargs)
        new_settings = dict(c.module_settings)
        new_settings["fact_checker"] = v
        c.module_settings = new_settings
        flag_modified(c, "module_settings")
        await session.commit()

async def get_settings_buttons():
    return []

async def get_fc_kb():
    cfg = await _get_cfg()
    st_active = _("status_on") if cfg["is_active"] else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_fc_status", status=st_active), callback_data="fc_tgl_active")],
        [InlineKeyboardButton(text=_("btn_fc_command", cmd=cfg["command"]), callback_data="fc_edit_cmd")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])

@router.callback_query(F.data == "fc_main")
async def fc_menu(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]])
    await safe_edit(call.message, state, _("fc_temporarily_disabled"), kb)

@router.callback_query(F.data == "fc_tgl_active")
async def fc_toggle(call: types.CallbackQuery, state: FSMContext):
    await fc_menu(call, state)

@router.callback_query(F.data == "fc_edit_cmd")
async def fc_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Fact] User {call.from_user.id} requested command edit")
    cfg = await _get_cfg()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="fc_main")]])
    await safe_edit(call.message, state, _("fc_enter_command", cmd=cfg["command"]), kb, parse_mode="HTML")
    await state.set_state(FactCheckerStates.waiting_for_command)

@router.message(FactCheckerStates.waiting_for_command)
async def fc_save_cmd(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    cmd = message.text.strip().split()[0]
    logging.info(f"[Fact] User {message.from_user.id} saved new command: {cmd}")
    await _upd_cfg(command=cmd)
    
    await state.set_state(None)
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    msg = await message.answer(_("fc_command_changed", cmd=cmd), parse_mode="HTML")
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass
    
    if menu_msg_id:
        try:
            await message.bot.edit_message_text(text=_("menu_fc_title"), chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=await get_fc_kb(), parse_mode="HTML")
        except: pass

def register_userbot(app: Client):
    # Temporarily disabled; preserve configuration for a future redesign.
    return
