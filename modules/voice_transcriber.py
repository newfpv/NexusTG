import os
import asyncio
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import ReplyParameters
from sqlalchemy.orm.attributes import flag_modified

from core.db import AsyncSessionLocal, CoreRepository
from core.services import transcribe_media, generate_ai_response
from core.utils import download_media_checked, is_bot_dialog, safe_edit
from core.config import _

router = Router()

class VoiceStates(StatesGroup):
    waiting_for_command = State()

async def _get_g_cfg():
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        v = c.module_settings.get("voice", {})
        logging.debug("[Voice Transcriber] Global configuration loaded")
        return {
            "auto_my": v.get("auto_my", False),
            "auto_other": v.get("auto_other", False),
            "allow_cmd": v.get("allow_cmd", False),
            "summarize": v.get("summarize", True),
            "summary_only": v.get("summary_only", False),
            "command": v.get("command", ".text")
        }

async def _upd_g_cfg(**kwargs):
    logging.info(f"[Voice Transcriber] Updating global configuration: {list(kwargs.keys())}")
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        v = dict(c.module_settings.get("voice", {}))
        v.update(kwargs)
        new_settings = dict(c.module_settings)
        new_settings["voice"] = v
        c.module_settings = new_settings
        flag_modified(c, "module_settings")
        await session.commit()

async def _get_c_cfg(chat_id):
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_chat_config(chat_id)
        v = c.module_data.get("voice", {})
        logging.debug(f"[Voice Transcriber] Chat {chat_id} configuration loaded")
        return {
            "auto_my": v.get("auto_my", 2),
            "auto_other": v.get("auto_other", 2),
            "allow_cmd": v.get("allow_cmd", 2),
            "summary_only": v.get("summary_only", 2)
        }

async def _upd_c_cfg(chat_id, **kwargs):
    logging.info(f"[Voice Transcriber] Updating chat {chat_id} configuration: {list(kwargs.keys())}")
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_chat_config(chat_id)
        v = dict(c.module_data.get("voice", {}))
        v.update(kwargs)
        new_data = dict(c.module_data)
        new_data["voice"] = v
        c.module_data = new_data
        flag_modified(c, "module_data")
        await session.commit()

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_v_settings_main"), callback_data="voice_main")]]

async def get_chat_menu_buttons(chat_id: int):
    return [[InlineKeyboardButton(text=_("btn_v_chat_settings"), callback_data=f"v_chat_main_{chat_id}")]]

async def get_voice_kb():
    cfg = await _get_g_cfg()
    st_auto_my = _("status_on") if cfg["auto_my"] else _("status_off")
    st_auto_oth = _("status_on") if cfg["auto_other"] else _("status_off")
    st_allow_cmd = _("status_on") if cfg["allow_cmd"] else _("status_off")
    st_summ = _("status_on") if cfg["summarize"] else _("status_off")
    st_summary_only = _("status_on") if cfg["summary_only"] else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_v_auto_my", status=st_auto_my), callback_data="v_tgl_g_auto_my"),
         InlineKeyboardButton(text=_("btn_v_auto_other", status=st_auto_oth), callback_data="v_tgl_g_auto_other")],
        [InlineKeyboardButton(text=_("btn_v_cmd_allow_others", status=st_allow_cmd), callback_data="v_tgl_g_allow_cmd")],
        [InlineKeyboardButton(text=_("btn_v_summarize", status=st_summ), callback_data="v_tgl_g_summarize")],
        [InlineKeyboardButton(text=_("btn_v_summary_only", status=st_summary_only), callback_data="v_tgl_g_summary_only")],
        [InlineKeyboardButton(text=_("btn_v_command", cmd=cfg["command"]), callback_data="v_edit_cmd")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])

async def get_chat_voice_kb(chat_id):
    chat_cfg = await _get_c_cfg(chat_id)
    def get_lbl(val, template_name):
        if val == 2: st = _("status_global")
        elif val == 1: st = _("status_on")
        else: st = _("status_off")
        return _(template_name, status=st)
        
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_lbl(chat_cfg["auto_my"], "btn_v_auto_my"), callback_data=f"v_c_tgl_auto_my_{chat_id}"),
         InlineKeyboardButton(text=get_lbl(chat_cfg["auto_other"], "btn_v_auto_other"), callback_data=f"v_c_tgl_auto_other_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["allow_cmd"], "btn_v_c_cmd_allow"), callback_data=f"v_c_tgl_allow_cmd_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["summary_only"], "btn_v_summary_only"), callback_data=f"v_c_tgl_summary_only_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]
    ])

@router.callback_query(F.data == "voice_main")
async def voice_menu(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Voice Transcriber] User {call.from_user.id} accessed main menu")
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("menu_v_title"), await get_voice_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("v_chat_main_"))
async def voice_chat_menu(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[3])
    logging.info(f"[Voice Transcriber] User {call.from_user.id} accessed chat menu for {chat_id}")
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("menu_v_chat_title", chat_id=chat_id), await get_chat_voice_kb(chat_id), parse_mode="HTML")
    try: await call.answer()
    except: pass

@router.callback_query(F.data.startswith("v_tgl_g_"))
async def voice_global_toggles(call: types.CallbackQuery, state: FSMContext):
    setting = "_".join(call.data.split("_")[3:])
    logging.info(f"[Voice Transcriber] User {call.from_user.id} toggled global setting: {setting}")
    cfg = await _get_g_cfg()
    new_val = not cfg.get(setting, False)
    await _upd_g_cfg(**{setting: new_val})
    await safe_edit(call.message, state, _("menu_v_title"), await get_voice_kb(), parse_mode="HTML")
    try: await call.answer()
    except: pass

@router.callback_query(F.data.startswith("v_c_tgl_"))
async def voice_chat_toggles(call: types.CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    chat_id = int(parts[-1])
    setting = "_".join(parts[3:-1])
    
    logging.info(f"[Voice Transcriber] User {call.from_user.id} toggled chat setting: {setting} for chat {chat_id}")
    chat_cfg = await _get_c_cfg(chat_id)
    curr = chat_cfg.get(setting, 2)
    nxt = 1 if curr == 2 else (0 if curr == 1 else 2)
    
    await _upd_c_cfg(chat_id, **{setting: nxt})
    await safe_edit(call.message, state, _("menu_v_chat_title", chat_id=chat_id), await get_chat_voice_kb(chat_id), parse_mode="HTML")
    try: await call.answer()
    except: pass

@router.callback_query(F.data == "v_edit_cmd")
async def voice_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Voice Transcriber] User {call.from_user.id} requested command edit")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="voice_main")]])
    cfg = await _get_g_cfg()
    await safe_edit(call.message, state, _("v_enter_command", cmd=cfg["command"]), kb, parse_mode="HTML")
    await state.set_state(VoiceStates.waiting_for_command)
    try: await call.answer()
    except: pass

@router.message(VoiceStates.waiting_for_command)
async def voice_save_cmd(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    cmd = message.text.strip().split()[0]
    logging.info(f"[Voice Transcriber] User {message.from_user.id} updated transcriber command to: {cmd}")
    await _upd_g_cfg(command=cmd)
    
    await state.set_state(None)
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    msg = await message.answer(_("v_command_changed", cmd=cmd), parse_mode="HTML")
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass
    
    if menu_msg_id:
        try:
            await message.bot.edit_message_text(text=_("menu_v_title"), chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=await get_voice_kb(), parse_mode="HTML")
        except: pass

def _apply_chat_voice_cfg(global_cfg: dict, chat_cfg: dict) -> dict:
    cfg = dict(global_cfg)
    summary_only = chat_cfg.get("summary_only", 2)
    cfg["summary_only"] = global_cfg["summary_only"] if summary_only == 2 else bool(summary_only)
    return cfg

def _build_voice_chunks(raw_text: str, summary_content: str, summary_only: bool) -> list[str]:
    safe_tg_limit = 3800
    tags_wrapper = "<blockquote expandable>{}</blockquote>"
    chunks = []

    if summary_only:
        content = summary_content.strip()
        if not content:
            content = _("v_process_error")
    else:
        content = raw_text.strip()

    is_first = True
    while content or is_first:
        prefix = "" if summary_only else (summary_content if is_first else "")
        overhead_length = len(prefix) + len(tags_wrapper.format(""))
        available_space = safe_tg_limit - overhead_length

        if len(content) > available_space:
            split_index = content.rfind(" ", 0, available_space)
            if split_index == -1:
                split_index = available_space
            chunk = content[:split_index]
            content = content[split_index:].strip()
            logging.debug(f"[Voice Transcriber] Splitting text chunk at index {split_index}")
        else:
            chunk = content
            content = ""

        chunks.append(tags_wrapper.format(prefix + chunk))
        is_first = False

    return chunks

def register_userbot(app: Client):
    voice_queues = {}
    voice_workers = {}

    async def process_voice_media(client, message, target_msg, cfg, is_manual=False, append_state=None):
        logging.info(f"[Voice Transcriber] Initiating transcription task for message {target_msg.id} in chat {message.chat.id}")
        media_path = None
        status_msg = None
        is_me = (message.from_user and message.from_user.is_self)

        async def _add_ignored(c_id, m_id):
            async with AsyncSessionLocal() as session:
                repo = CoreRepository(session)
                await repo.add_ignored_msg(c_id, m_id)

        if is_manual and is_me:
            status_msg = await message.edit(_("v_status_processing"), parse_mode=enums.ParseMode.HTML)
            await _add_ignored(message.chat.id, status_msg.id)
            
        try:
            m_ext = ".ogg"
            if target_msg.video_note or target_msg.video: m_ext = ".mp4"
            elif target_msg.audio: m_ext = ".mp3"

            logging.info(f"[Voice Transcriber] Downloading media from message {target_msg.id}")
            media_path = await download_media_checked(
                client,
                target_msg,
                file_name=f"data/v_{target_msg.id}{m_ext}",
                timeout=90.0,
            )
            
            duration = 0
            for attr in ["voice", "video_note", "video", "audio"]:
                obj = getattr(target_msg, attr, None)
                if obj:
                    duration = getattr(obj, "duration", 0)
                    break
            
            logging.debug(f"[Voice Transcriber] Media downloaded to {media_path}. Extracted duration: {duration}s")

            if media_path and os.path.exists(media_path):
                logging.info("[Voice Transcriber] Executing transcription API call")
                
                max_retries = 3
                retry_delay = 60  
                raw_text = None

                for attempt in range(max_retries):
                    raw_text = await transcribe_media(media_path)
                    
                    if raw_text and raw_text != _("status_waiting"):
                        break 
                        
                    if attempt < max_retries - 1:
                        logging.warning(f"[Voice Transcriber] API overloaded. Retrying in {retry_delay}s... (Attempt {attempt + 1}/{max_retries})")
                        if is_manual and is_me and status_msg:
                            try:
                                await status_msg.edit(_("v_status_waiting"), parse_mode=enums.ParseMode.HTML)
                            except Exception:
                                pass
                        await asyncio.sleep(retry_delay)
                
                if not raw_text or raw_text == _("status_waiting"):
                    logging.error(f"[Voice Transcriber] API returned empty or waiting status persistently for message {target_msg.id}")
                    raise TimeoutError("API Overload") 

                logging.info(f"[Voice Transcriber] Transcription successful. Length: {len(raw_text)}")
                summary_content = ""
                should_summarize = cfg["summary_only"] or (cfg["summarize"] and duration >= 60)

                if should_summarize:
                    logging.info("[Voice Transcriber] Triggering summarization AI")
                    summary_prompt = _("v_summary_prompt") + raw_text

                    raw_summary = await generate_ai_response(summary_prompt, search_enabled=False)
                    if raw_summary and raw_summary != _("status_waiting"):
                        logging.info(f"[Voice Transcriber] Summarization successful. Length: {len(raw_summary)}")
                        summary_content = _("v_summary_prefix", summary=raw_summary.strip()) + "\n\n"
                    else:
                        logging.warning("[Voice Transcriber] Summarization failed or timed out")

                chunks = _build_voice_chunks(raw_text, summary_content, cfg["summary_only"])
                for idx, formatted_msg in enumerate(chunks):
                    edited_append = False

                    if idx == 0 and not is_manual and append_state and append_state.get("message"):
                        combined_text = f"{append_state['text']}\n\n{formatted_msg}"
                        if len(combined_text) <= 3800:
                            try:
                                await append_state["message"].edit(combined_text, parse_mode=enums.ParseMode.HTML)
                                append_state["text"] = combined_text
                                edited_append = True
                                logging.info("[Voice Transcriber] Appended transcription to previous auto message via edit")
                            except Exception as edit_err:
                                logging.warning(f"[Voice Transcriber] Append edit failed: {edit_err}. Falling back to send_message.")

                    if edited_append:
                        continue

                    if idx == 0 and is_manual and is_me:
                        try:
                            await status_msg.edit(formatted_msg, parse_mode=enums.ParseMode.HTML)
                            await _add_ignored(message.chat.id, status_msg.id)
                            logging.info(f"[Voice Transcriber] Replaced status message with transcription result")
                        except Exception as edit_err:
                            logging.warning(f"[Voice Transcriber] Status edit failed: {edit_err}. Falling back to send_message.")
                            sent_msg = await client.send_message(
                                chat_id=message.chat.id,
                                text=formatted_msg,
                                reply_parameters=ReplyParameters(message_id=target_msg.id),
                                parse_mode=enums.ParseMode.HTML
                            )
                            await _add_ignored(message.chat.id, sent_msg.id)
                            if append_state is not None:
                                append_state["message"] = sent_msg
                                append_state["text"] = formatted_msg
                    else:
                        sent_msg = await client.send_message(
                            chat_id=message.chat.id,
                            text=formatted_msg,
                            reply_parameters=ReplyParameters(message_id=target_msg.id),
                            parse_mode=enums.ParseMode.HTML
                        )
                        await _add_ignored(message.chat.id, sent_msg.id)
                        if append_state is not None:
                            append_state["message"] = sent_msg
                            append_state["text"] = formatted_msg
                        logging.info(f"[Voice Transcriber] Sent transcription block as new message")

        except TimeoutError as e:
            logging.error(f"[Voice Transcriber] API Timeout/Overload: {e}. Silencing output to chat.")
            if is_manual and is_me and status_msg:
                try:
                    await status_msg.delete()
                    logging.info("[Voice Transcriber] Removed pending status message due to API overload.")
                except Exception as del_err:
                    logging.warning(f"[Voice Transcriber] Failed to delete status message: {del_err}")

        except Exception as e:
            logging.error(f"[Voice Transcriber] Error processing media: {e}", exc_info=True)
            err_txt = _("v_process_error")
            if is_manual:
                if is_me and status_msg: 
                    try: await status_msg.edit(err_txt)
                    except: await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id))
                else: await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id))
        
        finally:
            if media_path and os.path.exists(media_path):
                try: 
                    os.remove(media_path)
                    logging.debug(f"[Voice Transcriber] Cleaned up temporary file: {media_path}")
                except Exception as cleanup_err:
                    logging.warning(f"[Voice Transcriber] Failed to clean up file {media_path}: {cleanup_err}")

    async def enqueue_voice_media(client, message, target_msg, cfg, is_manual=False):
        chat_id = message.chat.id
        queue = voice_queues.setdefault(chat_id, asyncio.Queue())
        await queue.put((client, message, target_msg, cfg, is_manual))

        worker = voice_workers.get(chat_id)
        if not worker or worker.done():
            voice_workers[chat_id] = asyncio.create_task(voice_chat_worker(chat_id))

    async def voice_chat_worker(chat_id):
        queue = voice_queues[chat_id]
        append_state = None

        try:
            while True:
                idle_timeout = 3.0 if append_state else 60.0
                try:
                    client, message, target_msg, cfg, is_manual = await asyncio.wait_for(queue.get(), timeout=idle_timeout)
                except asyncio.TimeoutError:
                    if append_state:
                        append_state = None
                        continue
                    break

                try:
                    if is_manual:
                        append_state = None
                        await process_voice_media(client, message, target_msg, cfg, is_manual=True)
                    else:
                        if append_state is None:
                            append_state = {"message": None, "text": ""}
                        await process_voice_media(client, message, target_msg, cfg, is_manual=False, append_state=append_state)
                finally:
                    queue.task_done()
        finally:
            if queue.empty():
                voice_queues.pop(chat_id, None)
                voice_workers.pop(chat_id, None)

    @app.on_message((filters.voice | filters.video_note) & filters.private, group=11)
    async def auto_voice_handler(client, message):
        if is_bot_dialog(message):
            logging.info(f"[Voice Transcriber] Skipping bot dialog message {message.id} in chat {message.chat.id}")
            return

        cfg = await _get_g_cfg()
        chat_cfg = await _get_c_cfg(message.chat.id)
        is_me = (message.from_user and message.from_user.is_self)
        c_my, c_oth = chat_cfg["auto_my"], chat_cfg["auto_other"]
        should_my = cfg["auto_my"] if c_my == 2 else bool(c_my)
        should_oth = cfg["auto_other"] if c_oth == 2 else bool(c_oth)
        
        if (is_me and should_my) or (not is_me and should_oth):
            logging.info(f"[Voice Transcriber] Auto-trigger activated for message {message.id} in chat {message.chat.id}")
            effective_cfg = _apply_chat_voice_cfg(cfg, chat_cfg)
            await enqueue_voice_media(client, message, message, effective_cfg, is_manual=False)

    @app.on_message(filters.text & filters.reply & filters.private, group=23)
    async def cmd_voice_handler(client, message):
        if is_bot_dialog(message):
            logging.info(f"[Voice Transcriber] Skipping bot dialog command in chat {message.chat.id}")
            return

        cfg = await _get_g_cfg()
        if message.text.lower().startswith(cfg["command"].lower()):
            chat_cfg = await _get_c_cfg(message.chat.id)
            c_allow = chat_cfg["allow_cmd"]
            allow_others = cfg["allow_cmd"] if c_allow == 2 else bool(c_allow)
            
            if (message.from_user and message.from_user.is_self) or allow_others:
                target = message.reply_to_message
                if target and (target.voice or target.video_note or target.video or target.audio):
                    logging.info(f"[Voice Transcriber] Command trigger activated for message {target.id} in chat {message.chat.id}")
                    effective_cfg = _apply_chat_voice_cfg(cfg, chat_cfg)
                    await enqueue_voice_media(client, message, target, effective_cfg, is_manual=True)
