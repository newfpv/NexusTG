import os
import re
import html
import asyncio
import logging
import aiosqlite
from pyrogram import Client, filters, enums

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from gemini_core import generate_ai_response, transcribe_media
from utils import simulate_typing
from modules.youtube import fetch_youtube_data_sync
from i18n import _

def md_to_html(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r'```(\w+)?\n?(.*?)```', r'<pre><code>\2</code></pre>', text, flags=re.DOTALL)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'^#+\s+(.*?)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    return text

# DATABASE LOGIC
DB_PATH = "data/ai_cmd.sqlite"

async def on_startup():
    if not os.path.exists("data"):
        os.makedirs("data")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS ai_msgs (chat_id INTEGER, msg_id INTEGER, PRIMARY KEY(chat_id, msg_id))")
        
        cursor = await db.execute("SELECT COUNT(*) FROM settings")
        count = (await cursor.fetchone())[0]
        if count == 0:
            defaults = [
                ("command", ".ai"),
                ("use_search", "1"),
                ("show_model", "0"),
                ("show_queries", "0"),
                ("use_quote", "0"),
                ("global_prompt", "Ты - Gemini. Отвечай кратко, по делу. Учитывай контекст.")
            ]
            await db.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", defaults)
        else:
            cursor = await db.execute("SELECT value FROM settings WHERE key = 'use_quote'")
            if not await cursor.fetchone():
                await db.execute("INSERT INTO settings (key, value) VALUES ('use_quote', '0')")
                
        await db.commit()

async def get_all_settings():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        return {k: v for k, v in rows}

async def get_setting(key, default=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return row[0] if row else default

async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

async def save_ai_msg(chat_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO ai_msgs (chat_id, msg_id) VALUES (?, ?)", (chat_id, msg_id))
        await db.commit()

async def is_ai_msg(chat_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM ai_msgs WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id))
        row = await cursor.fetchone()
        return row is not None

# AIOGRAM UI & SETTINGS
router = Router()

class AICmdFSM(StatesGroup):
    wait_command = State()
    wait_prompt = State()

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_ai_cmd_settings"), callback_data="aicmd_main")]]

def get_main_kb(cfg):
    status_search = _("status_on") if cfg.get("use_search") == "1" else _("status_off")
    status_model = _("status_on") if cfg.get("show_model") == "1" else _("status_off")
    status_queries = _("status_on") if cfg.get("show_queries") == "1" else _("status_off")
    status_quote = _("status_on") if cfg.get("use_quote") == "1" else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_cmd_trigger", cmd=cfg.get("command", ".ai")), callback_data="aicmd_edit_cmd")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_search", status=status_search), callback_data="aicmd_toggle_search")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_model", status=status_model), callback_data="aicmd_toggle_model")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_queries", status=status_queries), callback_data="aicmd_toggle_queries")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_quote", status=status_quote), callback_data="aicmd_toggle_quote")],
        [InlineKeyboardButton(text=_("btn_ai_cmd_prompt"), callback_data="aicmd_edit_prompt")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])

@router.callback_query(F.data == "aicmd_main")
async def aicmd_main_menu(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await state.update_data(menu_msg_id=call.message.message_id) 
    cfg = await get_all_settings()
    text = _("menu_ai_cmd_title", prompt=html.escape(cfg.get("global_prompt", "")))
    try:
        await call.message.edit_text(text, reply_markup=get_main_kb(cfg), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data.startswith("aicmd_toggle_"))
async def aicmd_toggles(call: types.CallbackQuery, state: FSMContext):
    cfg = await get_all_settings()
    action = call.data.split("_")[-1]
    
    if action == "search": 
        new_val = "0" if cfg.get("use_search") == "1" else "1"
        await set_setting("use_search", new_val)
    elif action == "model": 
        new_val = "0" if cfg.get("show_model") == "1" else "1"
        await set_setting("show_model", new_val)
    elif action == "queries": 
        new_val = "0" if cfg.get("show_queries") == "1" else "1"
        await set_setting("show_queries", new_val)
    elif action == "quote": 
        new_val = "0" if cfg.get("use_quote") == "1" else "1"
        await set_setting("use_quote", new_val)
    
    cfg = await get_all_settings()
    text = _("menu_ai_cmd_title", prompt=html.escape(cfg.get("global_prompt", "")))
    try:
        await call.message.edit_text(text, reply_markup=get_main_kb(cfg), parse_mode="HTML")
    except Exception:
        pass

@router.callback_query(F.data == "aicmd_edit_cmd")
async def aicmd_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="aicmd_main")]])
    try:
        await call.message.edit_text(_("ai_cmd_enter_cmd"), reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await state.set_state(AICmdFSM.wait_command)

@router.message(AICmdFSM.wait_command)
async def aicmd_save_cmd(message: types.Message, state: FSMContext):
    new_cmd = message.text.strip().split()[0]
    await set_setting("command", new_cmd)
    
    try: await message.delete()
    except: pass
    
    data = await state.get_data()
    await state.set_state(None) 
    
    menu_msg_id = data.get("menu_msg_id")
    if menu_msg_id:
        cfg = await get_all_settings()
        text = _("menu_ai_cmd_title", prompt=html.escape(cfg.get("global_prompt", "")))
        try: 
            await message.bot.edit_message_text(text, chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=get_main_kb(cfg), parse_mode="HTML")
        except Exception:
            pass

@router.callback_query(F.data == "aicmd_edit_prompt")
async def aicmd_edit_prompt(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="aicmd_main")]])
    try:
        await call.message.edit_text(_("ai_cmd_enter_prompt"), reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await state.set_state(AICmdFSM.wait_prompt)

@router.message(AICmdFSM.wait_prompt)
async def aicmd_save_prompt(message: types.Message, state: FSMContext):
    new_prompt = message.text.strip()
    await set_setting("global_prompt", new_prompt)
    
    try: await message.delete()
    except: pass
    
    data = await state.get_data()
    await state.set_state(None)
    
    menu_msg_id = data.get("menu_msg_id")
    if menu_msg_id:
        cfg = await get_all_settings()
        text = _("menu_ai_cmd_title", prompt=html.escape(cfg.get("global_prompt", "")))
        try: 
            await message.bot.edit_message_text(text, chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=get_main_kb(cfg), parse_mode="HTML")
        except Exception:
            pass

# PYROGRAM LOGIC
def register_userbot(app: Client):
    
    async def is_ai_target(_, __, message):
        cmd = await get_setting("command", ".ai")
        
        if message.from_user and message.from_user.is_self:
            if message.text and re.match(rf"^{re.escape(cmd)}(?:\s+|$)", message.text):
                return True
        
        if message.reply_to_message:
            is_ai_reply = await is_ai_msg(message.chat.id, message.reply_to_message.id)
            if is_ai_reply:
                if message.text and not message.text.startswith(cmd):
                    return True
        return False

    ai_trigger = filters.create(is_ai_target)

    @app.on_message(ai_trigger)
    async def handle_ai_request(client, message):
        try:
            cfg = await get_all_settings()
            cmd = cfg.get("command", ".ai")
            use_quote = (cfg.get("use_quote") == "1")
            
            is_me = bool(message.from_user and message.from_user.is_self)
            is_cmd = bool(message.text and message.text.startswith(cmd))
            
            query = ""
            if is_cmd:
                match = re.match(rf"^{re.escape(cmd)}(?:\s+(.*))?", message.text or message.caption or "", flags=re.DOTALL)
                if match and match.group(1):
                    query = match.group(1).strip()
            else:
                query = message.text or message.caption or ""

            use_search = (cfg.get("use_search") == "1")
            sys_prompt = cfg.get("global_prompt", "")
            
            if cfg.get("show_queries") == "1":
                sys_prompt += "\n\n[SYSTEM RULE]: Если ты использовал Google Search для ответа, ОБЯЗАТЕЛЬНО напиши в самом конце ответа с новой строки: '🔍 Поиск: [перечисли твои поисковые запросы]'."
            if cfg.get("show_model") == "1":
                sys_prompt += "\n\n[SYSTEM RULE]: ОБЯЗАТЕЛЬНО напиши в самом конце ответа с новой строки: '🤖 Модель: [напиши версию твоей модели Gemini]'."

            status_msg = None
            if is_me:
                status_msg = await message.edit(_("cmd_ai_thinking"), parse_mode=enums.ParseMode.HTML)
            else:
                status_msg = await message.reply(_("cmd_ai_thinking"), parse_mode=enums.ParseMode.HTML)
            
            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            
            target_msg = message.reply_to_message if (is_cmd and message.reply_to_message) else message
            
            media_path = None
            transcript = ""
            yt_context = ""

            media_ext = ""
            if target_msg.photo: media_ext = ".jpg"
            elif target_msg.voice: media_ext = ".ogg"
            elif target_msg.video or target_msg.video_note: media_ext = ".mp4"
            elif target_msg.audio: media_ext = ".mp3"
            elif target_msg.document: 
                ext = target_msg.document.file_name.split('.')[-1].lower() if target_msg.document.file_name and '.' in target_msg.document.file_name else "file"
                media_ext = f".{ext}"
            
            if media_ext:
                logging.info(_("cmd_ai_downloading", media_ext=media_ext))
                media_path = await target_msg.download(file_name=f"data/ai_auto_{message.id}{media_ext}")
                if media_path and media_path.lower().endswith((".ogg", ".oga", ".mp4", ".mov", ".avi", ".mp3", ".wav", ".m4a")):
                    transcript = await transcribe_media(media_path)

            text_to_search = target_msg.text or target_msg.caption or ""
            if text_to_search:
                yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s]+)', text_to_search)
                if yt_links:
                    logging.info(_("cmd_ai_yt_found"))
                    _dur, yt_context = await asyncio.to_thread(fetch_youtube_data_sync, yt_links[0])

            hist = []
            async for m in client.get_chat_history(message.chat.id, limit=6):
                if m.id == message.id: continue
                sender = _("me_sender") if (m.from_user and m.from_user.is_self) else _("other_sender")
                hist.append(f"{sender}: {m.text or m.caption or _('media_file_placeholder')}")
            hist.reverse()
            hist_str = "\n".join(hist)
            
            full_query = _("cmd_ai_context_dialogue", hist_str=hist_str)
            
            if is_cmd and message.reply_to_message:
                orig_sender = _("me_sender") if (target_msg.from_user and target_msg.from_user.is_self) else _("other_sender")
                full_query += _("cmd_ai_context_reply", orig_sender=orig_sender, text_to_search=text_to_search)
            
            if transcript:
                full_query += _("cmd_ai_context_transcript", transcript=transcript)
            elif media_path and not transcript:
                full_query += _("cmd_ai_context_media_sys")
                
            if yt_context:
                full_query += _("cmd_ai_context_yt", yt_context=yt_context)
            
            if not query:
                query = _("cmd_ai_default_query")
                
            full_query += _("cmd_ai_task_query", query=query)
            
            reply = await generate_ai_response(
                full_query, 
                media_path=media_path,
                custom_prompt=sys_prompt,
                search_enabled=use_search
            )
            
            typing_task.cancel()

            if not reply or not reply.strip() or reply == "⏳":
                err_text = _("ai_cmd_error_empty")
                await status_msg.edit(err_text, parse_mode=enums.ParseMode.HTML)
                return
            
            parts = []
            current_part = ""
            for line in reply.split('\n'):
                if len(current_part) + len(line) < 3800:
                    current_part += line + '\n'
                else:
                    parts.append(current_part.strip())
                    current_part = line + '\n'
            if current_part.strip():
                parts.append(current_part.strip())
            
            for i, part in enumerate(parts):
                html_part = md_to_html(part)
                
                if i == 0:
                    safe_query = html.escape(query if query != _("cmd_ai_default_query") else _("cmd_ai_safe_query_fallback"))
                    if use_quote:
                        text = f"<blockquote><i>{safe_query}</i></blockquote>\n<blockquote expandable>{html_part}</blockquote>"
                    else:
                        text = f"<blockquote><i>{safe_query}</i></blockquote>\n{html_part}"
                else:
                    if use_quote:
                        text = f"<blockquote expandable>{html_part}</blockquote>"
                    else:
                        text = html_part
                
                if i == 0:
                    await status_msg.edit(text, parse_mode=enums.ParseMode.HTML)
                    await save_ai_msg(message.chat.id, status_msg.id)
                else:
                    sent_msg = await client.send_message(message.chat.id, text, reply_to_message_id=status_msg.id, parse_mode=enums.ParseMode.HTML)
                    await save_ai_msg(message.chat.id, sent_msg.id)

        except Exception as e:
            logging.error(_("cmd_ai_log_error", e=e))
            if 'status_msg' in locals() and status_msg:
                await status_msg.edit(_("cmd_ai_error_msg", e=e), parse_mode=enums.ParseMode.HTML)
        finally:
            if 'media_path' in locals() and media_path and os.path.exists(media_path):
                try: os.remove(media_path)
                except: pass
