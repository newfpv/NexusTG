import asyncio
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions

from core.utils import safe_edit, safe_delete, get_cancel_kb, CoreAPI, md_to_html, simulate_typing, safe_userbot_handler
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

async def _ret_menu(msg: types.Message, state: FSMContext):
    """Генерация и обновление главного меню модуля"""
    if msg_id := (await state.get_data()).get("menu_msg_id"):
        cfg = await _get_cfg()
        status = _("status_on") if cfg["is_active"] else _("status_off")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=_("ts_btn_status", status=status), callback_data="ts_toggle")],
            [InlineKeyboardButton(text=_("ts_btn_cmd", cmd=cfg["command"]), callback_data="ts_edit_cmd")],
            [InlineKeyboardButton(text=_("ts_btn_prompt"), callback_data="ts_edit_prompt")],
            [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
        ])
        await msg.bot.edit_message_text(_("ts_menu_title"), msg.chat.id, msg_id, reply_markup=kb, parse_mode="HTML")

async def get_settings_buttons() -> list:
    return [[InlineKeyboardButton(text=_("btn_ts_main"), callback_data="ts_main")]]

@router.callback_query(F.data.in_({"ts_main", "ts_toggle"}))
async def ts_menu_or_toggle(call: types.CallbackQuery, state: FSMContext):
    if call.data == "ts_toggle":
    cfg = await _get_cfg()
    await CoreAPI.update_module_cfg(MODULE_NAME, is_active=not cfg["is_active"])
        
    await state.update_data(menu_msg_id=call.message.message_id)
    await _ret_menu(call.message, state)

@router.callback_query(F.data.in_({"ts_edit_cmd", "ts_edit_prompt"}))
async def ts_ask_input(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    is_cmd = (call.data == "ts_edit_cmd")
    
    text = _("ts_ask_cmd", cmd=cfg["command"]) if is_cmd else _("ts_ask_prompt", prompt=cfg["prompt"])
    await state.set_state(TextStructFSM.wait_cmd if is_cmd else TextStructFSM.wait_prompt)
    await safe_edit(call.message, state, text, get_cancel_kb("ts_main"))

@router.message(TextStructFSM.wait_cmd)
@router.message(TextStructFSM.wait_prompt)
async def ts_save_input(message: types.Message, state: FSMContext):
    await safe_delete(message)
    
    if await state.get_state() == TextStructFSM.wait_cmd.state:
        await CoreAPI.update_module_cfg(MODULE_NAME, command=message.text.strip().split()[0])
    else:
        await CoreAPI.update_module_cfg(MODULE_NAME, prompt=message.text.strip())
        
    await state.set_state(None)
    await _ret_menu(message, state)

def register_userbot(app: Client):
    @app.on_message(filters.me & filters.text)
    @safe_userbot_handler
    async def handle_struct_cmd(client: Client, message):
        cfg = await _get_cfg()
        cmd = cfg["command"]
        
        if not cfg["is_active"] or not message.text.startswith(cmd): 
            return
            
        raw_text = message.text[len(cmd):].strip()
        if not raw_text: return
            
        no_preview = LinkPreviewOptions(is_disabled=True)
        
        await message.edit_text(raw_text, link_preview_options=no_preview)

        typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
        ai_text = await generate_ai_response(raw_text, custom_prompt=cfg["prompt"], search_enabled=False)
        typing_task.cancel()

        if ai_text and ai_text != "⏳":
            await message.edit_text(md_to_html(ai_text), parse_mode=enums.ParseMode.HTML, link_preview_options=no_preview)
        else:
            await message.edit_text(f"{raw_text}\n\n{_('ts_error')}", parse_mode=enums.ParseMode.HTML, link_preview_options=no_preview)