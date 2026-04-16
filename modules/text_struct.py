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
    data = await state.get_data()
    msg_id = data.get("menu_msg_id")
    if msg_id:
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
        
    curr_state = await state.get_state()
    if curr_state == TextStructFSM.wait_cmd.state:
        await CoreAPI.update_module_cfg(MODULE_NAME, command=message.text.strip().split()[0])
    else:
        await CoreAPI.update_module_cfg(MODULE_NAME, prompt=message.text.strip())
    await state.set_state(None)
    await _ret_menu(message, state)

async def _apply_edit(msg_to_edit: Message, original_text: str, ai_text: str):
    if not ai_text or ai_text == "⏳" or ai_text == _("status_waiting"):
        html_text = f"{original_text}\n\n{_('ts_error')}"
        fallback_text = html_text
    else:
        html_text = md_to_html(ai_text)
        fallback_text = ai_text

    no_preview = LinkPreviewOptions(is_disabled=True)

    try:
        if msg_to_edit.media:
            await msg_to_edit.edit_caption(html_text, parse_mode=enums.ParseMode.HTML)
        else:
            await msg_to_edit.edit_text(html_text, parse_mode=enums.ParseMode.HTML, link_preview_options=no_preview)
    except Exception:
        try:
            if msg_to_edit.media:
                await msg_to_edit.edit_caption(fallback_text)
            else:
                await msg_to_edit.edit_text(fallback_text, link_preview_options=no_preview)
        except Exception:
            pass

def register_userbot(app: Client):
    @app.on_message(filters.me & (filters.text | filters.caption), group=-5)
    @safe_userbot_handler
    async def handle_struct_cmd(client: Client, message: Message):
        cfg = await _get_cfg()
        if not cfg.get("is_active"):
            return
            
        cmd = str(cfg.get("command")).strip()
        msg_text = message.text or message.caption or ""
        
        if not re.match(rf"^{re.escape(cmd)}(?:\s+|$)", msg_text):
            return

        query = msg_text[len(cmd):].strip()
        is_reply = bool(message.reply_to_message)
        
        if not is_reply:
            if not query:
                return
            
            try:
                if message.media:
                    await message.edit_caption(query)
                else:
                    await message.edit_text(query, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except Exception:
                pass

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            try:
                ai_text = await generate_ai_response(query, custom_prompt=cfg.get("prompt"), search_enabled=False)
            finally:
                typing_task.cancel()

            await _apply_edit(message, query, ai_text)
            return

        target_msg = message.reply_to_message
        text_to_process = target_msg.text or target_msg.caption or ""
        
        if not text_to_process.strip():
            return
            
        is_ours = getattr(target_msg, "outgoing", False) or (getattr(target_msg, "from_user", None) and target_msg.from_user.is_self)
        
        prompt_to_use = cfg.get("prompt")
        if query:
            prompt_to_use += f"\n\n{query}"

        if is_ours:
            try:
                await message.delete()
            except Exception:
                pass

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            try:
                ai_text = await generate_ai_response(text_to_process, custom_prompt=prompt_to_use, search_enabled=False)
            finally:
                typing_task.cancel()

            await _apply_edit(target_msg, text_to_process, ai_text)
            
        else:
            try:
                if message.media:
                    await message.edit_caption(text_to_process)
                else:
                    await message.edit_text(text_to_process, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except Exception:
                pass

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            try:
                ai_text = await generate_ai_response(text_to_process, custom_prompt=prompt_to_use, search_enabled=False)
            finally:
                typing_task.cancel()

            await _apply_edit(message, text_to_process, ai_text)