import re
import asyncio
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions, Message

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
    if msg_id := (await state.get_data()).get("menu_msg_id"):
        cfg = await _get_cfg()
        status = _("status_on") if cfg["is_active"] else _("status_off")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=_("ts_btn_status", status=status), callback_data="ts_toggle")],
            [InlineKeyboardButton(text=_("ts_btn_cmd", cmd=cfg["command"]), callback_data="ts_edit_cmd")],
            [InlineKeyboardButton(text=_("ts_btn_prompt"), callback_data="ts_edit_prompt")],
            [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
        ])
        await msg.bot.edit_message_text(
            text=_("ts_menu_title"), 
            chat_id=msg.chat.id, 
            message_id=msg_id, 
            reply_markup=kb, 
            parse_mode="HTML"
        )

async def get_settings_buttons() -> list:
    return [[InlineKeyboardButton(text=_("btn_ts_main"), callback_data="ts_main")]]

@router.callback_query(F.data.in_({"ts_main", "ts_toggle"}))
async def ts_menu_or_toggle(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    if call.data == "ts_toggle":
        new_status = not cfg["is_active"]
        await CoreAPI.update_module_cfg(MODULE_NAME, is_active=new_status)
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
    try:
        await message.delete()
    except Exception:
        pass
        
    if await state.get_state() == TextStructFSM.wait_cmd.state:
        await CoreAPI.update_module_cfg(MODULE_NAME, command=message.text.strip().split()[0])
    else:
        await CoreAPI.update_module_cfg(MODULE_NAME, prompt=message.text.strip())
    await state.set_state(None)
    await _ret_menu(message, state)

def register_userbot(app: Client):
    @app.on_message(filters.me & filters.text)
    @safe_userbot_handler
    async def handle_struct_cmd(client: Client, message: Message):
        cfg = await _get_cfg()
        if not cfg.get("is_active"):
            return
            
        cmd = str(cfg.get("command")).strip()
        if not message.text.startswith(cmd):
            return

        # ========================================================
        # ЛОГИКА СТРОГО ИЗ ai_command.py
        # ========================================================
        match = re.match(rf"^{re.escape(cmd)}(?:\s+(.*))?", message.text or message.caption or "", flags=re.DOTALL)
        query = match.group(1).strip() if match and match.group(1) else ""
        
        target_msg = message.reply_to_message if message.reply_to_message else message
        is_reply = bool(message.reply_to_message)
        
        raw_text = ""

        if not is_reply:
            raw_text = query
            if not raw_text:
                return
            # Сценарий 1: Прячем команду
            try:
                await message.edit_text(raw_text, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except Exception:
                pass
        else:
            raw_text = target_msg.text or target_msg.caption or ""
            if not raw_text.strip():
                return
            # Удаляем триггер .fix мгновенно
            try:
                await message.delete()
            except Exception:
                pass

        typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
        
        try:
            ai_text = await generate_ai_response(
                raw_text, 
                custom_prompt=cfg.get("prompt"), 
                search_enabled=False
            )
        finally:
            typing_task.cancel()

        status_waiting = _("status_waiting")
        if not ai_text or ai_text == status_waiting:
            html_text = f"{raw_text}\n\n{_('ts_error')}"
            fallback_text = html_text
        else:
            html_text = md_to_html(ai_text)
            fallback_text = ai_text

        no_preview = LinkPreviewOptions(is_disabled=True)

        if not is_reply:
            # СЦЕНАРИЙ 1
            try:
                await message.edit_text(html_text, parse_mode=enums.ParseMode.HTML, link_preview_options=no_preview)
            except Exception:
                await message.edit_text(fallback_text, link_preview_options=no_preview)
        else:
            is_ours = bool(getattr(target_msg, "outgoing", False) or (target_msg.from_user and target_msg.from_user.is_self))
            if is_ours:
                # СЦЕНАРИЙ 2
                try:
                    await target_msg.edit_text(html_text, parse_mode=enums.ParseMode.HTML, link_preview_options=no_preview)
                except Exception:
                    await target_msg.edit_text(fallback_text, link_preview_options=no_preview)
            else:
                # СЦЕНАРИЙ 3
                try:
                    await target_msg.reply_text(html_text, parse_mode=enums.ParseMode.HTML, quote=True, link_preview_options=no_preview)
                except Exception:
                    await target_msg.reply_text(fallback_text, quote=True, link_preview_options=no_preview)