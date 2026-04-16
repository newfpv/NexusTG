import os
import asyncio
import random
import logging
import html
import time
from datetime import datetime, timedelta
import aiosqlite

from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from pyrogram import Client, filters
from pyrogram.types import User, ChatPrivileges
from pyrogram.enums import ChatType
from pyrogram.raw.functions.channels import ToggleForum
from pyrogram.raw.types import InputChannel

from core.utils import safe_edit, CoreAPI, get_cancel_kb, get_back_kb
from core.config import _

DB_FILE = "data/saver_cache.sqlite"
CACHE_DIR = "data/spy_cache/"

router = Router()
userbot_app = None

db_conn = None
db_lock = asyncio.Lock()
alert_queue = asyncio.Queue()
background_tasks = set()

class SaverStates(StatesGroup):
    wait_dump_chat = State()
    wait_targets = State()
    wait_blacklist = State()
    wait_delay = State()
    wait_limits = State()

async def cache_garbage_collector():
    while True:
        try:
            if not os.path.exists(CACHE_DIR):
                await asyncio.sleep(12 * 3600)
                continue

            valid_files = set()
            async with db_lock:
                if db_conn:
                    async with db_conn.execute("SELECT file_path FROM msg_cache WHERE file_path IS NOT NULL AND file_path != ''") as cursor:
                        async for row in cursor:
                            if row[0]:
                                valid_files.add(os.path.basename(row[0]))

            cleaned = 0
            for filename in os.listdir(CACHE_DIR):
                if filename not in valid_files:
                    file_path = os.path.join(CACHE_DIR, filename)
                    try:
                        if os.path.isfile(file_path):
                            os.remove(file_path)
                            cleaned += 1
                    except Exception:
                        pass

            if cleaned > 0:
                logging.info(f"[Saver GC] Removed {cleaned} orphaned files.")
        except Exception as e:
            logging.error(f"[Saver GC] Error: {e}")
        
        await asyncio.sleep(12 * 3600)

async def alert_worker(bot: Bot, app: Client):
    while True:
        try:
            task_data = await alert_queue.get()
            try:
                dump_id, u_id, topic_id, txt, f_path, m_type, del_after, is_ttl = task_data
                
                cfg = await _get_cfg()
                delay = random.uniform(cfg.get("delay_min", 1.0), cfg.get("delay_max", 5.0))
                
                if delay > 0:
                    logging.info(f"[Saver] Task triggered for user {u_id}. Waiting {delay:.1f} seconds before sending.")
                    await asyncio.sleep(delay)
                
                await execute_alert(bot, app, dump_id, u_id, topic_id, txt, f_path, m_type, del_after, is_ttl)
            except Exception as e:
                logging.error(f"[Saver] Execution Error: {e}")
            finally:
                alert_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f"[Saver] Queue Error: {e}")

async def on_startup():
    global db_conn
    os.makedirs("data", exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    db_conn = await aiosqlite.connect(DB_FILE, timeout=20.0)
    await db_conn.execute("PRAGMA journal_mode=WAL;")
    await db_conn.execute("PRAGMA synchronous=NORMAL;")
    
    async with db_lock:
        await db_conn.execute("""CREATE TABLE IF NOT EXISTS msg_cache (
                            message_id INTEGER,
                            chat_id INTEGER,
                            user_id INTEGER,
                            user_name TEXT,
                            text TEXT,
                            media_type TEXT,
                            file_path TEXT,
                            is_ttl INTEGER DEFAULT 0,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (message_id, chat_id))""")
        
        await db_conn.execute("""CREATE TABLE IF NOT EXISTS topics (
                            user_id INTEGER PRIMARY KEY,
                            topic_id INTEGER,
                            user_name TEXT)""")
        await db_conn.commit()

    gc_task = asyncio.create_task(cache_garbage_collector())
    background_tasks.add(gc_task)
    gc_task.add_done_callback(background_tasks.discard)

    logging.info("[Saver] Database connection established.")

async def _get_cfg():
    s = await CoreAPI.get_module_cfg("saver")
    return {
        "is_active": s.get("is_active", False),
        "dump_chat_id": s.get("dump_chat_id", ""),
        "save_deleted": s.get("save_deleted", True),
        "save_edited": s.get("save_edited", True),
        "save_ttl": s.get("save_ttl", True),
        "blacklist": s.get("blacklist", ""),
        "target_chats": s.get("target_chats", ""),
        "delay_min": s.get("delay_min", 1.0),
        "delay_max": s.get("delay_max", 5.0),
        "limit_reg": s.get("limit_reg", 20.0),
        "limit_ttl": s.get("limit_ttl", 50.0)
    }

async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg("saver", **kwargs)

async def get_main_menu_buttons():
    return [[InlineKeyboardButton(text=_("btn_saver_main"), callback_data="saver_main")]]

async def get_saver_kb():
    cfg = await _get_cfg()
    st_main = _("status_on") if cfg["is_active"] else _("status_off")
    st_del = _("status_on") if cfg["save_deleted"] else _("status_off")
    st_edit = _("status_on") if cfg["save_edited"] else _("status_off")
    st_ttl = _("status_on") if cfg["save_ttl"] else _("status_off")
    
    dump_chat = cfg["dump_chat_id"] or _("status_empty")
    
    t_chats = [x.strip() for x in cfg["target_chats"].split(',') if x.strip()]
    t_lbl = _("status_count_chats", count=len(t_chats)) if t_chats else _("status_everywhere")
    
    b_list = [x.strip() for x in cfg["blacklist"].split(',') if x.strip()]
    b_lbl = _("status_count_users", count=len(b_list)) if b_list else _("status_empty")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_saver_status", status=st_main), callback_data="saver_tgl_is_active")],
        [InlineKeyboardButton(text=_("btn_saver_dump", chat=dump_chat), callback_data="saver_edit_dump")],
        [InlineKeyboardButton(text=_("btn_saver_targets", targets=t_lbl), callback_data="saver_edit_targets"),
         InlineKeyboardButton(text=_("btn_saver_bl", bl=b_lbl), callback_data="saver_edit_bl")],
        [InlineKeyboardButton(text=_("btn_saver_del", status=st_del), callback_data="saver_tgl_save_deleted"),
         InlineKeyboardButton(text=_("btn_saver_edit", status=st_edit), callback_data="saver_tgl_save_edited")],
        [InlineKeyboardButton(text=_("btn_saver_ttl", status=st_ttl), callback_data="saver_tgl_save_ttl")],
        [InlineKeyboardButton(text=_("btn_saver_delay", min=cfg["delay_min"], max=cfg["delay_max"]), callback_data="saver_edit_delay")],
        [InlineKeyboardButton(text=_("btn_saver_limits", reg=cfg["limit_reg"], ttl=cfg["limit_ttl"]), callback_data="saver_edit_limits")],
        [InlineKeyboardButton(text=_("btn_back_main"), callback_data="main_menu")]
    ])

@router.callback_query(F.data == "saver_main")
async def saver_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("menu_saver_title"), await get_saver_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("saver_tgl_"))
async def saver_toggles(call: types.CallbackQuery, state: FSMContext):
    setting = call.data.replace("saver_tgl_", "")
    cfg = await _get_cfg()
    await _upd_cfg(**{setting: not cfg[setting]})
    await saver_menu(call, state)

async def _req_input(call: types.CallbackQuery, state: FSMContext, text_key: str, next_state: State):
    await safe_edit(call.message, state, _(text_key), get_cancel_kb("saver_main"), parse_mode="HTML")
    await state.set_state(next_state)

@router.callback_query(F.data == "saver_edit_dump")
async def saver_ed_dump(call: types.CallbackQuery, state: FSMContext):
    bot_info = await call.bot.get_me()
    bot_username = bot_info.username
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_saver_auto"), callback_data="saver_auto_setup")],
        [InlineKeyboardButton(text=_("btn_saver_select_chat"), callback_data="saver_list_dumps")],
        [InlineKeyboardButton(text=_("btn_saver_manual_dump"), callback_data="saver_manual_dump")],
        [InlineKeyboardButton(text=_("btn_cancel"), callback_data="saver_main")]
    ])
    await safe_edit(call.message, state, _("saver_dump_instruction", bot_username=bot_username), kb, parse_mode="HTML")

@router.callback_query(F.data == "saver_auto_setup")
async def saver_auto_setup(call: types.CallbackQuery, state: FSMContext):
    if not userbot_app or not userbot_app.is_connected:
        return await call.answer(_("err_userbot_not_connected_alert"), show_alert=True)
        
    await safe_edit(call.message, state, _("saver_auto_creating"), parse_mode="HTML")
    
    try:
        bot_info = await call.bot.get_me()
        bot_username = bot_info.username
        
        chat = await userbot_app.create_supergroup(_("saver_dump_title"), _("saver_dump_desc"))
        await asyncio.sleep(2.0)
        
        await userbot_app.add_chat_members(chat.id, bot_username)
        await asyncio.sleep(2.0)
        
        await userbot_app.promote_chat_member(
            chat.id, 
            bot_username, 
            privileges=ChatPrivileges(
                can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=True,
                can_restrict_members=True, can_promote_members=False, can_change_info=True,
                can_invite_users=True, can_pin_messages=True, can_manage_topics=True
            )
        )
        await asyncio.sleep(2.0)
        
        try:
            peer = await userbot_app.resolve_peer(chat.id)
            if hasattr(peer, 'channel_id'):
                channel = InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)
                try:
                    await userbot_app.invoke(ToggleForum(channel=channel, enabled=True, tabs=False))
                except TypeError:
                    await userbot_app.invoke(ToggleForum(channel=channel, enabled=True))
        except Exception as e:
            logging.error(f"[Saver] Error while toggling forum: {e}")
            
        await asyncio.sleep(3.0) 
            
        await _upd_cfg(dump_chat_id=str(chat.id))
        await call.answer(_("saver_auto_success"), show_alert=True)
        await saver_menu(call, state)
        
    except Exception as e:
        logging.error(f"[Saver] Auto-setup error: {e}")
        await safe_edit(call.message, state, _("saver_auto_error", e=str(e)), get_back_kb("saver_edit_dump"), parse_mode="HTML")

@router.callback_query(F.data == "saver_list_dumps")
async def saver_list_dumps(call: types.CallbackQuery, state: FSMContext):
    if not userbot_app or not userbot_app.is_connected:
        return await call.answer(_("err_userbot_not_connected_alert"), show_alert=True)
        
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    try:
        count = 0
        async for dialog in userbot_app.get_dialogs(limit=100):
            chat = dialog.chat
            if chat.type in [ChatType.SUPERGROUP, ChatType.GROUP]:
                name = chat.title or _("status_unknown")
                name = name[:30] + "..." if len(name) > 30 else name
                kb.inline_keyboard.append([InlineKeyboardButton(text=f"📁 {name}", callback_data=f"saver_set_dump_{chat.id}")])
                count += 1
                if count >= 15: break
    except Exception as e:
        logging.error(f"[Saver] Error fetching dialogs: {e}")
        
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back"), callback_data="saver_edit_dump")])
    await safe_edit(call.message, state, _("saver_select_dump_title"), kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("saver_set_dump_"))
async def saver_set_dump(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.replace("saver_set_dump_", ""))
    
    if userbot_app and userbot_app.is_connected:
        try:
            bot_info = await call.bot.get_me()
            try: 
                await userbot_app.add_chat_members(chat_id, bot_info.username)
                await asyncio.sleep(1.0)
            except Exception: pass
            
            try:
                await userbot_app.promote_chat_member(
                    chat_id, bot_info.username, 
                    privileges=ChatPrivileges(
                        can_manage_chat=True, can_delete_messages=True, can_manage_video_chats=True,
                        can_restrict_members=True, can_promote_members=False, can_change_info=True,
                        can_invite_users=True, can_pin_messages=True, can_manage_topics=True
                    )
                )
                await asyncio.sleep(1.0)
            except Exception: pass
            
            try:
                peer = await userbot_app.resolve_peer(chat_id)
                if hasattr(peer, 'channel_id'):
                    channel = InputChannel(channel_id=peer.channel_id, access_hash=peer.access_hash)
                    try:
                        await userbot_app.invoke(ToggleForum(channel=channel, enabled=True, tabs=False))
                    except TypeError:
                        await userbot_app.invoke(ToggleForum(channel=channel, enabled=True))
            except Exception as e:
                logging.error(f"[Saver] Forum toggle error: {e}")
        except Exception: pass

    await asyncio.sleep(2.0)
    await _upd_cfg(dump_chat_id=str(chat_id))
    await call.answer(_("saver_auto_success"), show_alert=False)
    await saver_menu(call, state)

@router.callback_query(F.data == "saver_manual_dump")
async def saver_manual_dump(call: types.CallbackQuery, state: FSMContext):
    await _req_input(call, state, "saver_ask_dump", SaverStates.wait_dump_chat)

@router.message(SaverStates.wait_dump_chat)
async def saver_sv_dump(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    await _upd_cfg(dump_chat_id=message.text.strip())
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"): await message.bot.edit_message_text(_("menu_saver_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_saver_kb(), parse_mode="HTML")

@router.callback_query(F.data == "saver_edit_targets")
async def saver_ed_tg(call: types.CallbackQuery, state: FSMContext):
    await _req_input(call, state, "saver_ask_targets", SaverStates.wait_targets)

@router.message(SaverStates.wait_targets)
async def saver_sv_tg(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    txt = message.text.strip()
    await _upd_cfg(target_chats="" if txt.lower() in [_("cmd_reset").lower(), "reset"] else txt)
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"): await message.bot.edit_message_text(_("menu_saver_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_saver_kb(), parse_mode="HTML")

@router.callback_query(F.data == "saver_edit_bl")
async def saver_ed_bl(call: types.CallbackQuery, state: FSMContext):
    await _req_input(call, state, "saver_ask_bl", SaverStates.wait_blacklist)

@router.message(SaverStates.wait_blacklist)
async def saver_sv_bl(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    txt = message.text.strip()
    await _upd_cfg(blacklist="" if txt.lower() in [_("cmd_reset").lower(), "reset"] else txt)
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"): await message.bot.edit_message_text(_("menu_saver_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_saver_kb(), parse_mode="HTML")

@router.callback_query(F.data == "saver_edit_delay")
async def saver_ed_del(call: types.CallbackQuery, state: FSMContext):
    await _req_input(call, state, "saver_ask_delay", SaverStates.wait_delay)

@router.message(SaverStates.wait_delay)
async def saver_sv_del(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    try:
        parts = message.text.replace("-", " ").split()
        d_min = float(parts[0])
        d_max = float(parts[1]) if len(parts) > 1 else d_min
        if d_min > d_max: d_min, d_max = d_max, d_min
        await _upd_cfg(delay_min=d_min, delay_max=d_max)
    except Exception: pass
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"): await message.bot.edit_message_text(_("menu_saver_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_saver_kb(), parse_mode="HTML")

@router.callback_query(F.data == "saver_edit_limits")
async def saver_ed_lim(call: types.CallbackQuery, state: FSMContext):
    await _req_input(call, state, "saver_ask_limits", SaverStates.wait_limits)

@router.message(SaverStates.wait_limits)
async def saver_sv_lim(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    try:
        parts = message.text.split()
        l_reg = float(parts[0])
        l_ttl = float(parts[1]) if len(parts) > 1 else l_reg
        await _upd_cfg(limit_reg=l_reg, limit_ttl=l_ttl)
    except Exception: pass
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"): await message.bot.edit_message_text(_("menu_saver_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_saver_kb(), parse_mode="HTML")

async def get_or_create_topic(app: Client, bot: Bot, dump_chat_id: int, user_id: int, user_obj: User = None) -> int:
    action_delay = 1.5 
    
    async with db_lock:
        if db_conn:
            cursor = await db_conn.execute("SELECT topic_id, user_name FROM topics WHERE user_id = ?", (user_id,))
            row = await cursor.fetchone()
        else:
            return None

    topic_id = row[0] if row else None
    db_user_name = row[1] if row and len(row) > 1 else ""

    if not user_obj:
        try:
            await asyncio.sleep(action_delay)
            user_obj = await app.get_users(user_id)
        except Exception: 
            pass

    full_name = _("status_unknown")
    if user_obj:
        full_name = user_obj.first_name or ""
        if user_obj.last_name: full_name += f" {user_obj.last_name}"
        full_name = full_name.strip() or _("status_unknown")

    topic_title = f"{full_name} [{user_id}]"[:128]

    if topic_id:
        if db_user_name != full_name:
            try:
                await asyncio.sleep(action_delay)
                await bot.edit_forum_topic(chat_id=dump_chat_id, message_thread_id=topic_id, name=topic_title)
                async with db_lock:
                    await db_conn.execute("UPDATE topics SET user_name = ? WHERE user_id = ?", (full_name, user_id))
                    await db_conn.commit()
            except Exception as e:
                if "NOT_MODIFIED" not in str(e).upper():
                    logging.error(f"[Saver] Error renaming topic: {e}")
        return topic_id

    try:
        await asyncio.sleep(action_delay)
        new_topic = await bot.create_forum_topic(chat_id=dump_chat_id, name=topic_title)
        topic_id = new_topic.message_thread_id

        async with db_lock:
            await db_conn.execute("INSERT INTO topics (user_id, topic_id, user_name) VALUES (?, ?, ?)", (user_id, topic_id, full_name))
            await db_conn.commit()

        username = f"@{user_obj.username}" if user_obj and user_obj.username else _("info_hidden_none")
        phone = f"+{user_obj.phone_number}" if user_obj and getattr(user_obj, "phone_number", None) else _("info_hidden_none")
        premium = _("info_yes_star") if user_obj and getattr(user_obj, "is_premium", False) else _("info_no")
        contact = _("info_yes_user") if user_obj and getattr(user_obj, "is_contact", False) else _("info_no")

        profile_text = _("saver_profile_text", name=html.escape(full_name), id=user_id, username=html.escape(username), phone=html.escape(phone), contact=contact, premium=premium)

        msg = None
        await asyncio.sleep(action_delay)
        if user_obj:
            try:
                photos = [p async for p in app.get_chat_photos(user_id, limit=1)]
                if photos:
                    photo_path = await app.download_media(photos[0].file_id)
                    if photo_path and os.path.exists(photo_path):
                        msg = await bot.send_photo(chat_id=dump_chat_id, message_thread_id=topic_id, photo=FSInputFile(photo_path), caption=profile_text, parse_mode="HTML")
                        os.remove(photo_path)
            except Exception:
                pass

        if not msg:
            msg = await bot.send_message(chat_id=dump_chat_id, message_thread_id=topic_id, text=profile_text, parse_mode="HTML")

        if msg:
            try:
                await asyncio.sleep(action_delay)
                await bot.pin_chat_message(chat_id=dump_chat_id, message_id=msg.message_id, disable_notification=True)
            except Exception:
                pass

        return topic_id
    except Exception as e:
        logging.error(f"[Saver] Error creating topic: {e}")
        return None

async def execute_alert(bot: Bot, app: Client, chat_id: int, user_id: int, topic_id: int, text: str, file_path: str, media_type: str, delete_file_after=False, is_ttl=False):
    async def _send(t_id):
        if file_path and os.path.exists(file_path):
            file_obj = FSInputFile(file_path)
            kwargs = {"chat_id": chat_id, "message_thread_id": t_id}
            
            if media_type == "video_note":
                await bot.send_video_note(video_note=file_obj, **kwargs)
                if text and text.strip():
                    await bot.send_message(chat_id=chat_id, message_thread_id=t_id, text=text, parse_mode="HTML")
                return
            
            if text and text.strip():
                kwargs["caption"] = text
            kwargs["parse_mode"] = "HTML"
            
            if media_type == "photo":
                if is_ttl: kwargs["has_spoiler"] = True
                return await bot.send_photo(photo=file_obj, **kwargs)
            elif media_type == "video":
                if is_ttl: kwargs["has_spoiler"] = True
                return await bot.send_video(video=file_obj, **kwargs)
            elif media_type == "voice":
                return await bot.send_voice(voice=file_obj, **kwargs)
            elif media_type == "document":
                return await bot.send_document(document=file_obj, **kwargs)
            else:
                msg_txt = text or ""
                if msg_txt.strip():
                    return await bot.send_message(chat_id=chat_id, message_thread_id=t_id, text=msg_txt, parse_mode="HTML")
        else:
            msg_txt = text or ""
            if media_type:
                if msg_txt:
                    msg_txt += "\n\n"
                msg_txt += _("saver_alert_no_media")
                
            if msg_txt.strip():
                return await bot.send_message(chat_id=chat_id, message_thread_id=t_id, text=msg_txt, parse_mode="HTML")

    try:
        await _send(topic_id)
    except Exception as e:
        if any(x in str(e).upper() for x in ["THREAD", "TOPIC", "PEER_ID_INVALID"]):
            async with db_lock:
                await db_conn.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                await db_conn.commit()
            new_topic_id = await get_or_create_topic(app, bot, chat_id, user_id)
            if new_topic_id:
                try: 
                    await _send(new_topic_id)
                except Exception as ex: 
                    logging.error(f"[Saver] Failed to send even after recreating topic: {ex}")
        else:
            logging.error(f"[Saver] Error sending alert to topic: {e}")
    finally:
        if delete_file_after and file_path and os.path.exists(file_path):
            try: 
                os.remove(file_path)
                dir_path = os.path.dirname(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path): os.rmdir(dir_path)
            except Exception: 
                pass

def register_userbot(app: Client, bot: Bot):
    global userbot_app
    userbot_app = app
    
    worker_task = asyncio.create_task(alert_worker(bot, app))
    background_tasks.add(worker_task)
    worker_task.add_done_callback(background_tasks.discard)
    
    async def process_caching(client, message, cfg):
        user = message.from_user
        text = message.text or message.caption or ""
        is_ttl, media_type, media_obj = False, None, None
        
        for m_type in ["photo", "video", "voice", "video_note", "document"]:
            obj = getattr(message, m_type, None)
            if obj:
                media_type, media_obj = m_type, obj
                if getattr(obj, "ttl_seconds", None) or getattr(message, "ttl_seconds", None): is_ttl = True
                if getattr(obj, "view_once", False) or getattr(message, "view_once", False): is_ttl = True
                break
                
        size_mb = 0
        if media_obj and hasattr(media_obj, "file_size") and media_obj.file_size:
            size_mb = media_obj.file_size / (1024 * 1024)
            
        limit_mb = cfg["limit_ttl"] if is_ttl else cfg["limit_reg"]
        file_path = ""
        
        if media_obj and size_mb <= limit_mb:
            try: 
                target_path = os.path.join(CACHE_DIR, f"{message.chat.id}_{message.id}_{media_type}")
                file_path = await message.download(file_name=target_path)
            except Exception:
                pass
            
        if not is_ttl:
            async with db_lock:
                await db_conn.execute("INSERT OR REPLACE INTO msg_cache (message_id, chat_id, user_id, user_name, text, media_type, file_path, is_ttl) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", 
                                 (message.id, message.chat.id, user.id, user.first_name, text, media_type, file_path))
                
                if random.randint(1, 100) == 1:
                    await db_conn.execute("DELETE FROM msg_cache WHERE timestamp < datetime('now', '-180 days')")
                await db_conn.commit()
                
        if is_ttl and cfg["save_ttl"]:
            try: dump_chat_id = int(cfg["dump_chat_id"])
            except Exception: return
            
            async with db_lock:
                await db_conn.execute("INSERT OR REPLACE INTO msg_cache (message_id, chat_id, user_id, user_name, text, media_type, file_path, is_ttl) VALUES (?, ?, ?, ?, ?, ?, ?, 1)", 
                                 (message.id, message.chat.id, user.id, user.first_name, text, media_type, file_path))
                await db_conn.commit()

            if dump_chat_id:
                topic_id = await get_or_create_topic(app, bot, dump_chat_id, user.id, user_obj=user)
                if topic_id:
                    safe_ttl_txt = html.escape(text) if text else ""
                    await alert_queue.put((dump_chat_id, user.id, topic_id, safe_ttl_txt, file_path, media_type, True, is_ttl))
                    logging.info(f"[Saver] Saved TTL media {message.id} from user {user.id}")

    @app.on_message(filters.private & ~filters.bot & ~filters.me, group=1)
    async def incoming_messages_handler(client, message):
        if not message.chat or message.chat.type != ChatType.PRIVATE: return
        user = message.from_user
        if not user or user.is_bot or user.is_self: return
        cfg = await _get_cfg()
        if not cfg["is_active"]: return
        
        try: dump_id = int(cfg["dump_chat_id"])
        except Exception: return
        if message.chat.id == dump_id: return
        
        if str(user.id) in [x.strip() for x in cfg["blacklist"].split(",") if x.strip()]: return
        
        targets = cfg["target_chats"]
        if targets and str(message.chat.id) not in [x.strip() for x in targets.split(",") if x.strip()]: return
        
        task = asyncio.create_task(process_caching(client, message, cfg))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    @app.on_deleted_messages(group=2)
    async def handle_deleted_messages(client, messages):
        cfg = await _get_cfg()
        if not cfg["is_active"] or not cfg["save_deleted"]: return
        try: dump_id = int(cfg["dump_chat_id"])
        except Exception: return
        
        msgs_to_process = []
        async with db_lock:
            for msg in messages:
                if msg.chat and msg.chat.type != ChatType.PRIVATE: continue
                
                if msg.chat: cursor = await db_conn.execute("SELECT user_id, text, media_type, file_path, is_ttl FROM msg_cache WHERE message_id = ? AND chat_id = ?", (msg.id, msg.chat.id))
                else: cursor = await db_conn.execute("SELECT user_id, text, media_type, file_path, is_ttl FROM msg_cache WHERE message_id = ? ORDER BY timestamp DESC LIMIT 1", (msg.id,))
                
                row = await cursor.fetchone()
                if row:
                    msgs_to_process.append((msg, row))
                        
        for msg, row in msgs_to_process:
            u_id, txt, m_type, f_path, db_is_ttl = row
            
            if db_is_ttl == 1:
                continue
                
            topic_id = await get_or_create_topic(app, bot, dump_id, u_id)
            
            if topic_id:
                safe_txt = html.escape(txt) if txt else ""
                await alert_queue.put((dump_id, u_id, topic_id, safe_txt, f_path, m_type, True, False))
                logging.info(f"[Saver] Saved deleted message {msg.id} from user {u_id}")
                
                c_id = getattr(msg.chat, 'id', None)
                if c_id: 
                    async with db_lock:
                        await db_conn.execute("DELETE FROM msg_cache WHERE message_id = ? AND chat_id = ?", (msg.id, c_id))
                        await db_conn.commit()

    @app.on_edited_message(filters.private & ~filters.bot & ~filters.me, group=3)
    async def handle_edited_messages(client, message):
        if not message.chat or message.chat.type != ChatType.PRIVATE: return
        user = message.from_user
        if not user or user.is_bot or user.is_self: return
        cfg = await _get_cfg()
        if not cfg["is_active"] or not cfg["save_edited"]: return
        
        try: dump_id = int(cfg["dump_chat_id"])
        except Exception: return
        if message.chat.id == dump_id: return
        
        row = None
        async with db_lock:
            cursor = await db_conn.execute("SELECT text, media_type, file_path FROM msg_cache WHERE message_id = ? AND chat_id = ?", (message.id, message.chat.id))
            row = await cursor.fetchone()
                
        if row:
            old_t, m_type, f_path = row
            new_t = message.text or message.caption or ""
            if old_t != new_t:
                topic_id = await get_or_create_topic(app, bot, dump_id, user.id, user_obj=user)
                if topic_id:
                    alert_txt = _("saver_alert_edited", old=html.escape(old_t), new=html.escape(new_t))
                    await alert_queue.put((dump_id, user.id, topic_id, alert_txt, f_path, m_type, False, False))
                    logging.info(f"[Saver] Saved edited message {message.id} from user {user.id}")
                    
                async with db_lock:
                    await db_conn.execute("UPDATE msg_cache SET text = ? WHERE message_id = ? AND chat_id = ?", (new_t, message.id, message.chat.id))
                    await db_conn.commit()