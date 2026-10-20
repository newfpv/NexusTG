from core.job_queue import serial_events
from core.telegram_guard import delivery_failure, delivery_context, retain_result
import os
import re
import html
import asyncio
import logging
import time
from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions
from pyrogram.errors import FloodWait

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from core.db import AsyncSessionLocal, CoreRepository
from core.utils import safe_edit, simulate_typing, md_to_html, safe_delete, get_cancel_kb, CoreAPI
from core.config import _
from core.services import generate_ai_response_stream, build_dialog_context

router = Router()

class AICmdFSM(StatesGroup):
    wait_command = State()
    wait_prompt = State()

AI_CMD_TIMEOUT = 240

async def _get_cfg():
    a = await CoreAPI.get_module_cfg("ai_command")
    logging.debug("[AI Cmd] Configuration loaded")
    prompt = a.get("global_prompt") or _("ai_cmd_default_prompt")
    if prompt == _("default_prompt_env"):
        prompt = _("ai_cmd_default_prompt")
    return {
        "command": a.get("command", ".ai"),
        "use_search": a.get("use_search", True),
        "allow_others": a.get("allow_others", False),
        "show_debug": a.get("show_debug", False),
        "global_prompt": prompt
    }

async def _upd_cfg(**kwargs):
    logging.info(f"[AI Cmd] Updating configuration: {list(kwargs.keys())}")
    await CoreAPI.update_module_cfg("ai_command", **kwargs)

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_ai_cmd_settings"), callback_data="aicmd_main")]]

async def get_aicmd_kb():
    cfg = await _get_cfg()
    st_search = _("status_on") if cfg["use_search"] else _("status_off")
    st_others = _("status_on") if cfg["allow_others"] else _("status_off")
    st_debug = _("status_on") if cfg["show_debug"] else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_cmd_trigger", cmd=cfg["command"]), callback_data="aicmd_edit_cmd")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_allow_others", status=st_others), callback_data="aicmd_tgl_allow_others")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_search", status=st_search), callback_data="aicmd_tgl_use_search"),
         InlineKeyboardButton(text=_("btn_ai_debug", status=st_debug), callback_data="aicmd_tgl_show_debug")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_prompt"), callback_data="aicmd_edit_prompt")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])

@router.callback_query(F.data == "aicmd_main")
async def aicmd_menu(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[AI Cmd] User {call.from_user.id} opened main menu")
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_cfg()
    text = _("menu_ai_cmd_title", prompt=html.escape(cfg["global_prompt"]))
    await safe_edit(call.message, state, text, await get_aicmd_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("aicmd_tgl_"))
async def aicmd_toggles(call: types.CallbackQuery, state: FSMContext):
    setting = call.data.replace("aicmd_tgl_", "")
    logging.info(f"[AI Cmd] User {call.from_user.id} toggled setting: {setting}")
    cfg = await _get_cfg()
    await _upd_cfg(**{setting: not cfg[setting]})
    await aicmd_menu(call, state)

@router.callback_query(F.data == "aicmd_edit_cmd")
async def aicmd_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[AI Cmd] User {call.from_user.id} requested command edit")
    await safe_edit(call.message, state, _("ai_cmd_enter_cmd"), get_cancel_kb("aicmd_main"), parse_mode="HTML")
    await state.set_state(AICmdFSM.wait_command)

@router.message(AICmdFSM.wait_command)
async def aicmd_save_cmd(message: types.Message, state: FSMContext):
    await safe_delete(message)
    cmd = message.text.strip().split()[0]
    logging.info(f"[AI Cmd] User {message.from_user.id} saved new command: {cmd}")
    await _upd_cfg(command=cmd)
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        cfg = await _get_cfg()
        await message.bot.edit_message_text(_("menu_ai_cmd_title", prompt=html.escape(cfg["global_prompt"])), 
                                            message.chat.id, data["menu_msg_id"], reply_markup=await get_aicmd_kb(), parse_mode="HTML")

@router.callback_query(F.data == "aicmd_edit_prompt")
async def aicmd_edit_prompt(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[AI Cmd] User {call.from_user.id} requested prompt edit")
    cfg = await _get_cfg()
    text = _("ai_cmd_enter_prompt", prompt=html.escape(cfg["global_prompt"]))
    await safe_edit(call.message, state, text, get_cancel_kb("aicmd_main"), parse_mode="HTML")
    await state.set_state(AICmdFSM.wait_prompt)

@router.message(AICmdFSM.wait_prompt)
async def aicmd_save_prompt(message: types.Message, state: FSMContext):
    await safe_delete(message)
    logging.info(f"[AI Cmd] User {message.from_user.id} saved new global prompt")
    await _upd_cfg(global_prompt=message.text.strip())
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        cfg = await _get_cfg()
        await message.bot.edit_message_text(_("menu_ai_cmd_title", prompt=html.escape(cfg["global_prompt"])), 
                                            message.chat.id, data["menu_msg_id"], reply_markup=await get_aicmd_kb(), parse_mode="HTML")

def register_userbot(app: Client):
    async def ai_filter(flt, cli, m):
        is_me = bool(m.from_user and m.from_user.is_self)
        if m.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP) and not is_me:
            return False

        cfg = await _get_cfg()
        if (is_me or cfg["allow_others"]) and m.text and re.match(rf"^{re.escape(cfg['command'])}(?:\s+|$)", m.text):
            return True
        if m.reply_to_message:
            async with AsyncSessionLocal() as session:
                from sqlalchemy import select
                from core.db import AICmdTracker
                res = await session.execute(select(AICmdTracker).where(AICmdTracker.chat_id == m.chat.id, AICmdTracker.msg_id == m.reply_to_message.id))
                if res.scalar_one_or_none() and m.text and not m.text.startswith(cfg['command']):
                    if is_me or cfg["allow_others"]: 
                        return True
        return False

    @app.on_message(filters.create(ai_filter), group=32)
    @serial_events
    async def handle_ai_cmd(client, message):
        media_paths_to_cleanup = []
        start_time = time.time()
        
        logging.info(f"[AI Cmd] Triggered in chat {message.chat.id} by user {message.from_user.id if message.from_user else 'Unknown'}")
        
        try:
            cfg = await _get_cfg()
            is_me = bool(message.from_user and message.from_user.is_self)
            is_cmd = message.text and message.text.startswith(cfg["command"])
            
            query = ""
            if is_cmd:
                match = re.match(rf"^{re.escape(cfg['command'])}(?:\s+(.*))?", message.text or message.caption or "", flags=re.DOTALL)
                query = match.group(1).strip() if match and match.group(1) else ""
                logging.debug(f"[AI Cmd] Extracted query: {query[:50]}...")
            else:
                query = message.text or message.caption or ""
                logging.debug(f"[AI Cmd] Processing reply context as query")

            no_preview = LinkPreviewOptions(is_disabled=True)

            status_msg = await message.edit(_("cmd_ai_thinking"), link_preview_options=no_preview) if is_me else await message.reply(_("cmd_ai_thinking"), link_preview_options=no_preview)
            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 20))
            
            target_msg = message.reply_to_message if (is_cmd and message.reply_to_message) else message
            
            try:
                logging.info(f"[AI Cmd] Building dialog context for target message {target_msg.id}")
                hist_str, new_paths, dummy_dur, dummy_vid = await asyncio.wait_for(
                    build_dialog_context(client, message.chat.id, limit=30, target_msg_id=target_msg.id),
                    timeout=120.0
                )
                media_paths_to_cleanup.extend(new_paths)
                logging.info(f"[AI Cmd] Context built successfully. History length: {len(hist_str)}")
            except asyncio.TimeoutError:
                logging.error(f"[AI Cmd] Context Build Timeout in chat {message.chat.id}")
                await status_msg.edit("⏱ Context collection timeout", link_preview_options=no_preview)
                return
            
            if time.time() - start_time > AI_CMD_TIMEOUT:
                logging.warning(f"[AI Cmd] Total execution timeout exceeded before media download in chat {message.chat.id}")
                await status_msg.edit("⏱ Waiting time exceeded", link_preview_options=no_preview)
                return
            
            live_media_path = None
            if target_msg.photo or target_msg.video:
                try:
                    ext = ".jpg" if target_msg.photo else ".mp4"
                    logging.info(f"[AI Cmd] Attempting to download media from message {target_msg.id}")
                    live_media_path = await asyncio.wait_for(
                        target_msg.download(file_name=f"data/live_{target_msg.id}{ext}"),
                        timeout=60.0
                    )
                    if live_media_path: 
                        media_paths_to_cleanup.append(live_media_path)
                        logging.info(f"[AI Cmd] Media successfully downloaded to {live_media_path}")
                except asyncio.TimeoutError:
                    logging.warning(f"[AI Cmd] Media Download Timeout for message {target_msg.id}")

            full_query = _("cmd_ai_context_dialogue", hist_str=hist_str)
            if is_cmd and message.reply_to_message:
                orig_sender = _("me_sender") if target_msg.from_user and target_msg.from_user.is_self else _("other_sender")
                full_query += _("cmd_ai_context_reply", orig_sender=orig_sender, text_to_search=(target_msg.text or target_msg.caption or ""))
            
            full_query += _("cmd_ai_task_query", query=query or _("cmd_ai_default_query"))
            
            if cfg["show_debug"]:
                logging.debug("[AI Cmd] Printing debug header to stdout")
                print(_("log_debug_header"))
                if live_media_path: 
                    print(_("log_attached_file", path=live_media_path))
                print(_("log_full_prompt", prompt=full_query))

            full_reply = ""
            last_sent_text = ""
            last_ui_update = time.time()
            safe_q = html.escape(query[:50] + "..." if len(query) > 50 else (query or _("cmd_ai_safe_query_fallback")))
            
            prefix = f"<blockquote><i>{safe_q}</i></blockquote>\n"
            
            try:
                logging.info(f"[AI Cmd] Initiating AI stream generation for chat {message.chat.id}")
                stream_task = asyncio.create_task(
                    _consume_stream(
                        generate_ai_response_stream(
                            full_query, 
                            media_path=live_media_path, 
                            custom_prompt=cfg["global_prompt"], 
                            search_enabled=cfg["use_search"]
                        ),
                        status_msg,
                        prefix,
                        no_preview,
                        start_time,
                        AI_CMD_TIMEOUT
                    )
                )
                
                full_reply = await asyncio.wait_for(stream_task, timeout=180.0)
                logging.info(f"[AI Cmd] Stream fully consumed for chat {message.chat.id}")
                
                if not typing_task.done(): 
                    typing_task.cancel()
                
            except asyncio.TimeoutError:
                logging.error(f"[AI Cmd] Response Streaming Timeout in chat {message.chat.id}")
                if not full_reply:
                    await status_msg.edit("⏱ Response generation timeout", link_preview_options=no_preview)
                    return
            except Exception as e:
                if "MESSAGE_NOT_MODIFIED" not in str(e).upper():
                    logging.error(f"[AI Cmd] Stream editing error: {e}")
                    if not last_sent_text: 
                        await status_msg.edit(_("cmd_ai_error_msg", e=str(e)), link_preview_options=no_preview)
            
            if not typing_task.done(): 
                typing_task.cancel()
            
            if not full_reply or full_reply == "⏳":
                logging.warning(f"[AI Cmd] Empty or timeout response received from AI in chat {message.chat.id}")
                return await status_msg.edit(_("ai_cmd_error_empty"), link_preview_options=no_preview)

            if cfg["show_debug"]:
                logging.debug("[AI Cmd] Printing debug output to stdout")
                print(_("log_debug_output", reply=full_reply))
                print(_("log_debug_footer"))
            
            logging.info(f"[AI Cmd] Tracking AI message {status_msg.id} in DB")
            async with AsyncSessionLocal() as session:
                await CoreRepository(session).track_ai_message(message.chat.id, status_msg.id)

        except Exception as e:
            if delivery_failure(e):
                logging.warning("[AI Cmd] Telegram delivery interrupted",exc_info=True)
                return
            logging.error(f"[AI Cmd] Critical failure during AI command handling: {e}", exc_info=True)
            if 'status_msg' in locals() and status_msg: 
                try: 
                    await status_msg.edit(_("cmd_ai_error_msg", e=str(e)), link_preview_options=no_preview)
                except: 
                    pass
        finally:
            logging.info(f"[AI Cmd] Cleaning up {len(media_paths_to_cleanup)} temporary media files")
            for p in media_paths_to_cleanup:
                if os.path.exists(p):
                    try: 
                        os.remove(p)
                        logging.debug(f"[AI Cmd] Successfully removed {p}")
                    except Exception as e:
                        logging.warning(f"[AI Cmd] Failed to remove temporary file {p}: {e}")
            elapsed = time.time() - start_time
            logging.info(f"[AI Cmd] Processing completed in {elapsed:.1f}s for chat {message.chat.id}")

async def _consume_stream(stream_gen,status_msg,prefix,no_preview,start_time,timeout):
    # Consume Gemini independently: Telegram cooldowns must not stall the model
    # stream or discard its finished answer. Only one latest preview is pending.
    full_reply=''
    last_sent=''
    done=asyncio.Event()
    async def preview():
        nonlocal last_sent
        interval=2.5
        while not done.is_set():
            await asyncio.sleep(interval)
            if done.is_set():return
            current=f"{prefix}<blockquote expandable>{md_to_html(full_reply[:2500])}</blockquote>"
            if not full_reply or current==last_sent:continue
            try:
                await status_msg.edit(current,parse_mode=enums.ParseMode.HTML,link_preview_options=no_preview)
                last_sent=current
                interval=min(8,interval+.25)
            except asyncio.CancelledError:raise
            except Exception as exc:
                if delivery_failure(exc):return
                logging.debug('[AI Cmd] Preview edit rejected: %s',type(exc).__name__)
    task=asyncio.create_task(preview(),name='ai-command-preview')
    try:
        async for chunk in stream_gen:
            full_reply+=chunk
            if time.time()-start_time>timeout:break
    finally:
        done.set();task.cancel()
        try:await task
        except asyncio.CancelledError:pass
    if full_reply:
        result_path=retain_result(f'ai-command:{status_msg.chat.id}:{status_msg.id}',full_reply)
        token=delivery_context.set('ai-command-result')
        try:
            display=f"{prefix}<blockquote expandable>{md_to_html(full_reply)}</blockquote>"
            if len(display)<=3800:
                if display!=last_sent:
                    await status_msg.edit(display,parse_mode=enums.ParseMode.HTML,link_preview_options=no_preview)
            else:
                # Never silently truncate the final answer to fit a preview caption.
                for offset in range(0,len(full_reply),1800):
                    chunk=full_reply[offset:offset+1800]
                    if not offset:await status_msg.edit(chunk,parse_mode=enums.ParseMode.DISABLED,link_preview_options=no_preview)
                    else:await status_msg.reply(chunk,parse_mode=enums.ParseMode.DISABLED,link_preview_options=no_preview)
            result_path.unlink(missing_ok=True)
        finally:delivery_context.reset(token)
    return full_reply
