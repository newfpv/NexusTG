import os
import re
import asyncio
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions, Message

from core.utils import safe_edit, safe_delete, get_cancel_kb, CoreAPI, md_to_html, simulate_typing, safe_userbot_handler, download_media_checked
from core.config import _
from core.services import generate_ai_response, transcribe_media

router = Router()
MODULE_NAME = "text_struct"

class TextStructFSM(StatesGroup):
    wait_cmd = State()
    wait_prompt = State()

def _clean_text(value) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip()

def _extract_message_text(message: Message) -> str:
    return _clean_text(getattr(message, "text", None)) or _clean_text(getattr(message, "caption", None))

def _extract_reply_quote_text(message: Message) -> str:
    for attr in ("quote_text", "quoteText", "reply_quote_text"):
        quote_text = _clean_text(getattr(message, attr, None))
        if quote_text:
            return quote_text

    holders = []
    for attr in ("quote", "reply_quote", "text_quote", "reply_to", "reply_to_header", "reply_parameters", "reply_markup"):
        holder = getattr(message, attr, None)
        if holder:
            holders.append(holder)

    raw = getattr(message, "_raw", None) or getattr(message, "raw", None)
    if raw:
        holders.append(raw)
        raw_reply = getattr(raw, "reply_to", None)
        if raw_reply:
            holders.append(raw_reply)

    for holder in holders:
        if isinstance(holder, str):
            quote_text = _clean_text(holder)
            if quote_text:
                return quote_text

        for attr in ("quote_text", "quoteText", "reply_quote_text", "text"):
            quote_text = _clean_text(getattr(holder, attr, None))
            if quote_text:
                return quote_text

    return ""

def _is_transcribable_media(message: Message) -> bool:
    return any(getattr(message, attr, None) for attr in ("voice", "audio", "video_note"))

def _media_ext(message: Message) -> str:
    if getattr(message, "audio", None):
        return ".mp3"
    if getattr(message, "video_note", None):
        return ".mp4"
    return ".ogg"

async def _transcribe_struct_media(client: Client, message: Message) -> str:
    media_path = None
    try:
        os.makedirs("data", exist_ok=True)
        media_path = await download_media_checked(
            client,
            message,
            file_name=f"data/ts_fix_{message.id}{_media_ext(message)}",
            timeout=90.0,
        )
        text = await transcribe_media(media_path)
        if not text or text == _("status_waiting"):
            return ""
        return text.strip()
    finally:
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
            except OSError as exc:
                logging.debug("[Struct] Failed to remove temporary media file %s: %s", media_path, exc)

async def _get_cfg() -> dict:
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    logging.debug("[Struct] Configuration loaded")
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
        try:
            await msg.bot.edit_message_text(
                text=_("ts_menu_title"), 
                chat_id=msg.chat.id, 
                message_id=msg_id, 
                reply_markup=kb, 
                parse_mode="HTML"
            )
            logging.debug(f"[Struct] Menu rendered for chat {msg.chat.id}")
        except Exception as e:
            logging.warning(f"[Struct] Failed to edit menu message: {e}")

async def get_settings_buttons() -> list:
    return [[InlineKeyboardButton(text=_("btn_ts_main"), callback_data="ts_main")]]

@router.callback_query(F.data.in_({"ts_main", "ts_toggle"}))
async def ts_menu_or_toggle(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    if call.data == "ts_toggle":
        new_status = not cfg["is_active"]
        logging.info(f"[Struct] User {call.from_user.id} toggled activity status to {new_status}")
        await CoreAPI.update_module_cfg(MODULE_NAME, is_active=new_status)
    else:
        logging.info(f"[Struct] User {call.from_user.id} accessed main menu")
        
    await state.update_data(menu_msg_id=call.message.message_id)
    await _ret_menu(call.message, state)

@router.callback_query(F.data.in_({"ts_edit_cmd", "ts_edit_prompt"}))
async def ts_ask_input(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    is_cmd = (call.data == "ts_edit_cmd")
    logging.info(f"[Struct] User {call.from_user.id} requested to edit {'command' if is_cmd else 'prompt'}")
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
        new_cmd = message.text.strip().split()[0]
        logging.info(f"[Struct] User {message.from_user.id} updated command to: {new_cmd}")
        await CoreAPI.update_module_cfg(MODULE_NAME, command=new_cmd)
    else:
        new_prompt = message.text.strip()
        logging.info(f"[Struct] User {message.from_user.id} updated prompt")
        await CoreAPI.update_module_cfg(MODULE_NAME, prompt=new_prompt)
        
    await state.set_state(None)
    await _ret_menu(message, state)

async def _apply_edit(msg_to_edit: Message, original_text: str, ai_text: str):
    ai_ok = bool(ai_text and ai_text != "⏳" and ai_text != _("status_waiting"))
    if not ai_ok:
        logging.warning("[Struct] AI returned empty or waiting status, applying fallback error text")
        html_text = f"{original_text}\n\n{_('ts_error')}"
        fallback_text = html_text
    else:
        logging.debug("[Struct] Formatting AI text to HTML")
        html_text = md_to_html(ai_text)
        fallback_text = ai_text

    no_preview = LinkPreviewOptions(is_disabled=True)

    try:
        if msg_to_edit.media:
            await msg_to_edit.edit_caption(html_text, parse_mode=enums.ParseMode.HTML)
        else:
            await msg_to_edit.edit_text(html_text, parse_mode=enums.ParseMode.HTML, link_preview_options=no_preview)
        logging.info(f"[Struct] Successfully applied edit to message {msg_to_edit.id}")
        return ai_ok
    except Exception as e:
        logging.warning(f"[Struct] Failed to edit message with HTML parse mode: {e}. Attempting fallback.")
        try:
            if msg_to_edit.media:
                await msg_to_edit.edit_caption(fallback_text)
            else:
                await msg_to_edit.edit_text(fallback_text, link_preview_options=no_preview)
            logging.info(f"[Struct] Successfully applied fallback edit to message {msg_to_edit.id}")
            return ai_ok
        except Exception as fallback_e:
            logging.error(f"[Struct] Critical failure during message editing: {fallback_e}")
            return False

def register_userbot(app: Client):
    @app.on_message(filters.me & (filters.text | filters.caption), group=-5)
    @safe_userbot_handler
    async def handle_struct_cmd(client: Client, message: Message):
        cfg = await _get_cfg()
        if not cfg.get("is_active"):
            return
            
        cmd = str(cfg.get("command")).strip()
        msg_text = _extract_message_text(message)
        
        if not re.match(rf"^{re.escape(cmd)}(?:\s+|$)", msg_text):
            return

        logging.info(f"[Struct] Command '{cmd}' triggered in chat {message.chat.id}")

        query = msg_text[len(cmd):].strip()
        is_reply = bool(message.reply_to_message)
        
        if not is_reply:
            if not query:
                logging.debug("[Struct] Command triggered without query and not a reply. Ignoring.")
                return
            
            try:
                if message.media:
                    await message.edit_caption(query)
                else:
                    await message.edit_text(query, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except Exception as e:
                logging.debug(f"[Struct] Pre-edit failed: {e}")

            logging.info("[Struct] Generating structured response for self message")
            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            try:
                ai_text = await generate_ai_response(query, custom_prompt=cfg.get("prompt"), search_enabled=False)
            except Exception as e:
                logging.error(f"[Struct] AI Generation failed: {e}")
                ai_text = None
            finally:
                typing_task.cancel()

            await _apply_edit(message, query, ai_text)
            return

        target_msg = message.reply_to_message

        prompt_to_use = cfg.get("prompt") or ""
        if query:
            prompt_to_use += f"\n\n{query}"

        if _is_transcribable_media(target_msg):
            logging.info(f"[Struct] Generating structured response from media message {target_msg.id}")
            try:
                await message.edit_text(
                    _("v_status_processing"),
                    parse_mode=enums.ParseMode.HTML,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except Exception as e:
                logging.debug(f"[Struct] Failed to edit trigger message to media status: {e}")

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            text_to_process = ""
            try:
                text_to_process = await _transcribe_struct_media(client, target_msg)
                if not text_to_process:
                    raise ValueError("media transcription returned empty text")
                ai_text = await generate_ai_response(text_to_process, custom_prompt=prompt_to_use, search_enabled=False)
            except Exception as e:
                logging.error(f"[Struct] AI Generation failed for media message: {e}")
                ai_text = None
            finally:
                typing_task.cancel()

            edit_success = await _apply_edit(message, text_to_process or _("v_process_error"), ai_text)
            if edit_success:
                try:
                    await target_msg.delete()
                    logging.info(f"[Struct] Deleted source media message {target_msg.id} after successful structuring")
                except Exception as e:
                    logging.warning(f"[Struct] Structured text was created, but source media delete failed: {e}")
            return

        quote_text = _extract_reply_quote_text(message)
        text_to_process = quote_text or _extract_message_text(target_msg)
        if quote_text:
            logging.debug("[Struct] Using reply quote text as source")

        if not text_to_process.strip():
            logging.debug("[Struct] Target message and reply quote contain no processable text. Ignoring.")
            return

        is_ours = getattr(target_msg, "outgoing", False) or (getattr(target_msg, "from_user", None) and target_msg.from_user.is_self)

        logging.info(f"[Struct] Generating structured response for target message {target_msg.id}")

        if is_ours:
            try:
                await message.delete()
                logging.debug("[Struct] Trigger message deleted successfully")
            except Exception as e:
                logging.debug(f"[Struct] Failed to delete trigger message: {e}")

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            try:
                ai_text = await generate_ai_response(text_to_process, custom_prompt=prompt_to_use, search_enabled=False)
            except Exception as e:
                logging.error(f"[Struct] AI Generation failed for our message: {e}")
                ai_text = None
            finally:
                typing_task.cancel()

            await _apply_edit(target_msg, text_to_process, ai_text)
            
        else:
            try:
                if message.media:
                    await message.edit_caption(text_to_process)
                else:
                    await message.edit_text(text_to_process, link_preview_options=LinkPreviewOptions(is_disabled=True))
            except Exception as e:
                logging.debug(f"[Struct] Pre-edit failed for others message: {e}")

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            try:
                ai_text = await generate_ai_response(text_to_process, custom_prompt=prompt_to_use, search_enabled=False)
            except Exception as e:
                logging.error(f"[Struct] AI Generation failed for other's message: {e}")
                ai_text = None
            finally:
                typing_task.cancel()

            await _apply_edit(message, text_to_process, ai_text)
