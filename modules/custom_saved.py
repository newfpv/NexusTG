import asyncio
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters

from core.utils import safe_edit, CoreAPI, get_cancel_kb
from core.config import _

router = Router()
MODULE_NAME = "custom_saved"

class CustomSavedFSM(StatesGroup):
    waiting_for_dest = State()
    waiting_for_command = State()

async def _get_cfg() -> dict:
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    return {
        "is_active": cfg.get("is_active", False),
        "target_chat_id": cfg.get("target_chat_id", ""),
        "target_topic_id": cfg.get("target_topic_id", ""),
        "trigger_command": cfg.get("trigger_command", ".save")
    }

async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg(MODULE_NAME, **kwargs)

async def get_main_menu_buttons() -> list:
    return [[InlineKeyboardButton(text=_("cs_btn_main"), callback_data="cs_main_menu")]]

@router.callback_query(F.data == "cs_main_menu")
async def render_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_cfg()
    
    status = _("status_on") if cfg["is_active"] else _("status_off")
    dest_str = _("cs_not_set")
    if cfg["target_chat_id"]:
        dest_str = f"{cfg['target_chat_id']}:{cfg['target_topic_id']}" if cfg["target_topic_id"] else str(cfg["target_chat_id"])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{_('cs_btn_toggle')}: {status}", callback_data="cs_toggle")],
        [InlineKeyboardButton(text=f"{_('cs_btn_dest')}: {dest_str}", callback_data="cs_set_dest")],
        [InlineKeyboardButton(text=f"{_('cs_btn_cmd')}: {cfg['trigger_command']}", callback_data="cs_set_cmd")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])
    
    await safe_edit(call.message, state, _("cs_menu_text"), kb, parse_mode="HTML")

@router.callback_query(F.data == "cs_toggle")
async def toggle_module(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await _upd_cfg(is_active=not cfg["is_active"])
    await render_menu(call, state)

@router.callback_query(F.data == "cs_set_dest")
async def ask_dest(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    current = f"{cfg['target_chat_id']}:{cfg['target_topic_id']}" if cfg['target_topic_id'] else (cfg['target_chat_id'] or _("status_empty"))
    await safe_edit(call.message, state, _("cs_ask_dest", current=current), get_cancel_kb("cs_main_menu"), parse_mode="HTML")
    await state.set_state(CustomSavedFSM.waiting_for_dest)

@router.callback_query(F.data == "cs_set_cmd")
async def ask_cmd(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await safe_edit(call.message, state, _("cs_ask_cmd", current=cfg['trigger_command']), get_cancel_kb("cs_main_menu"), parse_mode="HTML")
    await state.set_state(CustomSavedFSM.waiting_for_command)

@router.message(CustomSavedFSM.waiting_for_dest)
@router.message(CustomSavedFSM.waiting_for_command)
async def process_fsm_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    txt = message.text.strip()
    
    try: 
        await message.delete()
    except: 
        pass
    
    if current_state == CustomSavedFSM.waiting_for_dest.state:
        if txt.lower() in [_("cs_cmd_reset").lower(), "reset"]:
            await _upd_cfg(target_chat_id="", target_topic_id="")
        else:
            clean_txt = txt.replace(" ", "")
            if ":" in clean_txt:
                c_id, t_id = clean_txt.split(":", 1)
                await _upd_cfg(target_chat_id=c_id, target_topic_id=t_id)
            else:
                await _upd_cfg(target_chat_id=clean_txt, target_topic_id="")
                
    elif current_state == CustomSavedFSM.waiting_for_command.state:
        cmd = txt.split()[0]
        await _upd_cfg(trigger_command=cmd)

    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        mock_call = types.CallbackQuery(id="", from_user=message.from_user, chat_instance="", message=types.Message(message_id=data["menu_msg_id"], chat=message.chat, date=message.date))
        await render_menu(mock_call, state)

def register_userbot(app: Client):
    @app.on_message(filters.me & filters.reply, group=20)
    async def command_trigger(client: Client, message):
        cfg = await _get_cfg()
        if not cfg["is_active"] or not message.text or not cfg["target_chat_id"]:
            return
            
        if message.text.strip().lower() == cfg["trigger_command"].lower():
            asyncio.create_task(message.delete())
            try:
                await client.forward_messages(
                    chat_id=int(cfg["target_chat_id"]),
                    from_chat_id=message.chat.id,
                    message_ids=message.reply_to_message_id,
                    message_thread_id=int(cfg["target_topic_id"]) if cfg["target_topic_id"] else None
                )
            except Exception as e:
                logging.error(f"{_('cs_log_fw_fail')}: {e}")

async def on_startup():
    logging.info(_("cs_log_init"))