import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters

from core.utils import safe_edit, safe_delete, get_cancel_kb, CoreAPI
from core.config import _
from core.services import generate_ai_response

router = Router()
MODULE_NAME = "text_struct"

class TextStructFSM(StatesGroup):
    wait_cmd = State()
    wait_prompt = State()

async def _get_cfg() -> dict:
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    return {
        "is_active": cfg.get("is_active", False),
        "command": cfg.get("command", ".fix"),
        "prompt": cfg.get("prompt", _("ts_default_prompt"))
    }

async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg(MODULE_NAME, **kwargs)

async def get_settings_buttons() -> list:
    return [[InlineKeyboardButton(text=_("btn_ts_main"), callback_data="ts_main")]]

async def _get_menu_kb() -> InlineKeyboardMarkup:
    cfg = await _get_cfg()
    status = _("status_on") if cfg["is_active"] else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("ts_btn_status", status=status), callback_data="ts_toggle")],
        [InlineKeyboardButton(text=_("ts_btn_cmd", cmd=cfg["command"]), callback_data="ts_edit_cmd")],
        [InlineKeyboardButton(text=_("ts_btn_prompt"), callback_data="ts_edit_prompt")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])

@router.callback_query(F.data == "ts_main")
async def ts_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("ts_menu_title"), await _get_menu_kb())

@router.callback_query(F.data == "ts_toggle")
async def ts_toggle(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await _upd_cfg(is_active=not cfg["is_active"])
    await ts_menu(call, state)

@router.callback_query(F.data == "ts_edit_cmd")
async def ts_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await safe_edit(call.message, state, _("ts_ask_cmd", cmd=cfg["command"]), get_cancel_kb("ts_main"))
    await state.set_state(TextStructFSM.wait_cmd)

@router.message(TextStructFSM.wait_cmd)
async def ts_save_cmd(message: types.Message, state: FSMContext):
    await safe_delete(message)
    cmd = message.text.strip().split()[0]
    await _upd_cfg(command=cmd)
    await state.set_state(None)
    
    data = await state.get_data()
    if msg_id := data.get("menu_msg_id"):
        await message.bot.edit_message_text(
            text=_("ts_menu_title"), 
            chat_id=message.chat.id, 
            message_id=msg_id, 
            reply_markup=await _get_menu_kb(),
            parse_mode="HTML"
        )

@router.callback_query(F.data == "ts_edit_prompt")
async def ts_edit_prompt(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await safe_edit(call.message, state, _("ts_ask_prompt", prompt=cfg["prompt"]), get_cancel_kb("ts_main"))
    await state.set_state(TextStructFSM.wait_prompt)

@router.message(TextStructFSM.wait_prompt)
async def ts_save_prompt(message: types.Message, state: FSMContext):
    await safe_delete(message)
    await _upd_cfg(prompt=message.text.strip())
    await state.set_state(None)
    
    data = await state.get_data()
    if msg_id := data.get("menu_msg_id"):
        await message.bot.edit_message_text(
            text=_("ts_menu_title"), 
            chat_id=message.chat.id, 
            message_id=msg_id, 
            reply_markup=await _get_menu_kb(),
            parse_mode="HTML"
        )

def register_userbot(app: Client):
    @app.on_message(filters.me & filters.text)
    async def handle_struct_cmd(client: Client, message):
        cfg = await _get_cfg()
        if not cfg["is_active"]: 
            return
            
        cmd = cfg["command"]
        if not message.text.startswith(cmd): 
            return
            
        raw_text = message.text[len(cmd):].strip()
        target_msg = message
        is_reply_to_other = False

        if not raw_text and message.reply_to_message:
            raw_text = message.reply_to_message.text or message.reply_to_message.caption or ""
            target_msg = message.reply_to_message
            is_reply_to_other = not (target_msg.from_user and target_msg.from_user.is_self)
            await safe_delete(message) 
            
        if not raw_text: 
            return
            
        try:
            if not is_reply_to_other:
                await target_msg.edit_text(raw_text)
            
            ai_text = await generate_ai_response(
                prompt_context=raw_text,
                custom_prompt=cfg["prompt"],
                search_enabled=False 
            )
            
            if ai_text and ai_text != "⏳":
                if not is_reply_to_other:
                    await target_msg.edit_text(ai_text)
                else:
                    await client.send_message(message.chat.id, ai_text, reply_to_message_id=target_msg.id)
            else:
                if not is_reply_to_other:
                    await target_msg.edit_text(f"{raw_text}\n\n{_('ts_error')}")
                
        except Exception as e:
            logging.error(_("log_module_error", module_name=MODULE_NAME, e=e))