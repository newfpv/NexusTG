import os
import asyncio
import random
import logging
import html
from datetime import datetime, timedelta
import aiosqlite
from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters
from pyrogram.types import Message, User
from pyrogram.enums import ChatType

# ==========================================
# ISOLATED DB AND MODULE CACHE
# ==========================================
DB_FILE = "data/saver_db.sqlite"
CACHE_DIR = "data/spy_cache/"

async def init_saver_db():
    if not os.path.exists("data"):
        os.makedirs("data")
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
        
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS settings (
                            key TEXT PRIMARY KEY,
                            value TEXT)""")
        
        await db.execute("""CREATE TABLE IF NOT EXISTS msg_cache (
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
        
        try:
            await db.execute("ALTER TABLE msg_cache ADD COLUMN is_ttl INTEGER DEFAULT 0")
        except: pass

        await db.execute("""CREATE TABLE IF NOT EXISTS topics (
                            user_id INTEGER PRIMARY KEY,
                            topic_id INTEGER,
                            user_name TEXT)""")
        
        await db.commit()

        cursor = await db.execute("SELECT COUNT(*) FROM settings")
        count = await cursor.fetchone()
        if count[0] == 0:
            defaults = [
                ("is_active", "0"),
                ("dump_chat_id", ""),
                ("save_deleted", "1"),
                ("save_edited", "1"),
                ("save_ttl", "1"),
                ("blacklist", ""),
                ("target_chats", ""),
                ("delay_min", "1"),
                ("delay_max", "5"),
                ("limit_reg", "20"),
                ("limit_ttl", "50")
            ]
            await db.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", defaults)
            await db.commit()

async def get_config():
    await init_saver_db()
    cfg = {}
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT key, value FROM settings") as cursor:
            async for row in cursor:
                cfg[row[0]] = row[1]
    return cfg

async def set_config(key: str, value: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

# --- SMART TOPIC CREATION AND UPDATE VIA BOT API ---
async def get_or_create_topic(app: Client, bot: Bot, dump_chat_id: int, user_id: int, user_obj: User = None) -> int:
    action_delay = 1.5 
    
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT topic_id, user_name FROM topics WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()

    topic_id = row[0] if row else None
    db_user_name = row[1] if row and len(row) > 1 else ""

    if not user_obj:
        try:
            await asyncio.sleep(action_delay)
            user_obj = await app.get_users(user_id)
        except: pass

    full_name = "Unknown"
    if user_obj:
        full_name = user_obj.first_name or ""
        if user_obj.last_name:
            full_name += f" {user_obj.last_name}"
        full_name = full_name.strip() or "Unknown"

    topic_title = f"{full_name} [{user_id}]"[:128]

    if topic_id:
        if db_user_name != full_name:
            try:
                await asyncio.sleep(action_delay)
                await bot.edit_forum_topic(chat_id=dump_chat_id, message_thread_id=topic_id, name=topic_title)
                async with aiosqlite.connect(DB_FILE) as db:
                    await db.execute("UPDATE topics SET user_name = ? WHERE user_id = ?", (full_name, user_id))
                    await db.commit()
            except Exception as e:
                if "NOT_MODIFIED" not in str(e).upper():
                    logging.error(f"Topic rename error: {e}")
        return topic_id

    try:
        await asyncio.sleep(action_delay)
        new_topic = await bot.create_forum_topic(chat_id=dump_chat_id, name=topic_title)
        topic_id = new_topic.message_thread_id

        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("INSERT INTO topics (user_id, topic_id, user_name) VALUES (?, ?, ?)", (user_id, topic_id, full_name))
            await db.commit()

        username = f"@{user_obj.username}" if user_obj and user_obj.username else "No"
        phone = f"+{user_obj.phone_number}" if user_obj and getattr(user_obj, "phone_number", None) else "Hidden"
        premium = "Yes 🌟" if user_obj and getattr(user_obj, "is_premium", False) else "No"
        contact = "Yes 📇" if user_obj and getattr(user_obj, "is_contact", False) else "No"

        profile_text = (
            f"📁 <b>USER DOSSIER</b>\n\n"
            f"👤 <b>Name:</b> {html.escape(full_name)}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
            f"🔗 <b>Username:</b> {html.escape(username)}\n"
            f"📱 <b>Phone:</b> <code>{html.escape(phone)}</code>\n"
            f"📇 <b>In contacts:</b> {contact}\n"
            f"🌟 <b>Premium:</b> {premium}\n"
        )

        msg = None
        await asyncio.sleep(action_delay)
        if user_obj:
            try:
                photos = [p async for p in app.get_chat_photos(user_id, limit=1)]
                if photos:
                    photo_path = await app.download_media(photos[0].file_id)
                    msg = await bot.send_photo(chat_id=dump_chat_id, message_thread_id=topic_id, photo=FSInputFile(photo_path), caption=profile_text, parse_mode="HTML")
                    os.remove(photo_path)
            except Exception as e:
                logging.warning(f"Avatar fetch error: {e}")

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
        logging.error(f"Save Module: Topic create error: {e}")
        return None

# --- SENDING QUEUE ---
async def send_alert_delayed(bot: Bot, app: Client, chat_id: int, user_id: int, topic_id: int, text: str, file_path: str, media_type: str, d_min: float, d_max: float, delete_file_after=False, is_ttl=False, parse_mode=None):
    delay_sec = random.randint(int(d_min * 60), int(d_max * 60))
    await asyncio.sleep(delay_sec)
    
    async def _send(t_id):
        kwargs = {"chat_id": chat_id, "message_thread_id": t_id, "caption": text} if media_type else {"chat_id": chat_id, "message_thread_id": t_id, "text": text}
        if parse_mode: kwargs["parse_mode"] = parse_mode
        if is_ttl and media_type in ["photo", "video"]: kwargs["has_spoiler"] = True
            
        if file_path and os.path.exists(file_path):
            file_obj = FSInputFile(file_path)
            if media_type == "photo": return await bot.send_photo(photo=file_obj, **kwargs)
            elif media_type == "video": return await bot.send_video(video=file_obj, **kwargs)
            elif media_type == "voice": return await bot.send_voice(voice=file_obj, **kwargs)
            elif media_type == "video_note": return await bot.send_video_note(video_note=file_obj, **kwargs)
            elif media_type == "document": return await bot.send_document(document=file_obj, **kwargs)
            else: return await bot.send_message(**kwargs)
        else:
            msg_txt = text
            if file_path: msg_txt += "\n\n<i>(Media file not saved)</i>"
            return await bot.send_message(chat_id=chat_id, message_thread_id=t_id, text=msg_txt, parse_mode=parse_mode)

    try:
        await _send(topic_id)
    except Exception as e:
        if any(x in str(e).upper() for x in ["THREAD", "TOPIC", "PEER_ID_INVALID"]):
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("DELETE FROM topics WHERE user_id = ?", (user_id,))
                await db.commit()
            new_topic_id = await get_or_create_topic(app, bot, chat_id, user_id)
            if new_topic_id:
                try: await _send(new_topic_id)
                except: pass
    finally:
        if delete_file_after and file_path and os.path.exists(file_path):
            try: 
                os.remove(file_path)
                dir_path = os.path.dirname(file_path)
                if os.path.exists(dir_path) and not os.listdir(dir_path): os.rmdir(dir_path)
            except: pass

# ==========================================
# AIOGRAM SETTINGS (UI WITH HTML)
# ==========================================
router = Router()

class SaverStates(StatesGroup):
    waiting_for_dump_chat = State()
    waiting_for_blacklist = State()
    waiting_for_targets = State()
    waiting_for_delay = State()
    waiting_for_limits = State()

async def safe_html_edit(message: types.Message, text: str, kb: InlineKeyboardMarkup = None):
    try:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass

async def get_settings_buttons():
    return [[InlineKeyboardButton(text="🕵️ Spy Settings", callback_data="saver_main_menu")]]

async def get_saver_keyboard():
    cfg = await get_config()
    st_main = "ON ✅" if cfg.get("is_active") == "1" else "OFF ❌"
    dump_chat = cfg.get("dump_chat_id") or "Not set ❌"
    
    t_chats = cfg.get("target_chats", "")
    t_count = len([x for x in t_chats.split(',') if x.strip()])
    t_chats_lbl = f"{t_count} chats" if t_count > 0 else "EVERYWHERE ⚠️"
    
    b_list = cfg.get("blacklist", "")
    b_count = len([x for x in b_list.split(',') if x.strip()])
    b_list_lbl = f"{b_count} users" if b_count > 0 else "Empty"
    
    d_min, d_max = cfg.get("delay_min", "1"), cfg.get("delay_max", "5")
    l_reg, l_ttl = cfg.get("limit_reg", "20"), cfg.get("limit_ttl", "50")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Status: {st_main}", callback_data="saver_tgl_main")],
        [InlineKeyboardButton(text=f"🔔 Chat: {dump_chat}", callback_data="saver_edit_dump")],
        [InlineKeyboardButton(text=f"🎯 Parsing: {t_chats_lbl}", callback_data="saver_edit_targets"),
         InlineKeyboardButton(text=f"🚫 Ignore: {b_list_lbl}", callback_data="saver_edit_bl")],
        [InlineKeyboardButton(text=f"Deleted: {'✅' if cfg.get('save_deleted')=='1' else '❌'}", callback_data="saver_tgl_del"),
         InlineKeyboardButton(text=f"Edited: {'✅' if cfg.get('save_edited')=='1' else '❌'}", callback_data="saver_tgl_edit"),
         InlineKeyboardButton(text=f"TTL: {'✅' if cfg.get('save_ttl')=='1' else '❌'}", callback_data="saver_tgl_ttl")],
        [InlineKeyboardButton(text=f"⏳ Delay: {d_min}-{d_max} min", callback_data="saver_edit_delay")],
        [InlineKeyboardButton(text=f"💾 Limits: {l_reg}MB | TTL {l_ttl}MB", callback_data="saver_edit_limits")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="global_settings")]
    ])

@router.callback_query(F.data == "saver_main_menu")
async def saver_menu_handler(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await safe_html_edit(call.message, "🕵️ <b>Spy Settings</b>", await get_saver_keyboard())

@router.callback_query(F.data.startswith("saver_tgl_"))
async def saver_toggles_handler(call: types.CallbackQuery, state: FSMContext):
    cfg = await get_config()
    key_map = {"saver_tgl_main": "is_active", "saver_tgl_del": "save_deleted", "saver_tgl_edit": "save_edited", "saver_tgl_ttl": "save_ttl"}
    k = key_map.get(call.data)
    if k: await set_config(k, "0" if cfg.get(k) == "1" else "1")
    
    try: await call.answer()
    except: pass
    
    await safe_html_edit(call.message, "🕵️ <b>Spy Settings</b>", await get_saver_keyboard())

async def request_input(call: types.CallbackQuery, state: FSMContext, text: str, target_state: State):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Cancel", callback_data="saver_main_menu")]])
    await safe_html_edit(call.message, text, kb)
    await state.update_data(edit_msg_id=call.message.message_id)
    await state.set_state(target_state)

async def finish_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    msg_id = data.get("edit_msg_id")
    
    try: await message.delete()
    except: pass
    
    await state.set_state(None)
    
    text = "🕵️ <b>Spy Settings</b>"
    kb = await get_saver_keyboard()
    
    if msg_id:
        try:
            await message.bot.edit_message_text(text=text, chat_id=message.chat.id, message_id=msg_id, reply_markup=kb, parse_mode="HTML")
            return
        except Exception: pass

@router.callback_query(F.data == "saver_edit_dump")
async def edit_dump(call, state): await request_input(call, state, "Group ID:", SaverStates.waiting_for_dump_chat)
@router.message(SaverStates.waiting_for_dump_chat)
async def save_dump(message, state): await set_config("dump_chat_id", message.text.strip()); await finish_input(message, state)

@router.callback_query(F.data == "saver_edit_targets")
async def edit_targets(call, state): await request_input(call, state, "Chat IDs (comma-separated) or <code>reset</code>:", SaverStates.waiting_for_targets)
@router.message(SaverStates.waiting_for_targets)
async def save_targets(message, state): await set_config("target_chats", "" if message.text.lower() == "reset" else message.text.strip()); await finish_input(message, state)

@router.callback_query(F.data == "saver_edit_bl")
async def edit_bl(call, state): await request_input(call, state, "Blacklist IDs or <code>reset</code>:", SaverStates.waiting_for_blacklist)
@router.message(SaverStates.waiting_for_blacklist)
async def save_bl(message, state): await set_config("blacklist", "" if message.text.lower() == "reset" else message.text.strip()); await finish_input(message, state)

@router.callback_query(F.data == "saver_edit_delay")
async def edit_delay(call, state): await request_input(call, state, "Delay (min-max):", SaverStates.waiting_for_delay)
@router.message(SaverStates.waiting_for_delay)
async def save_delay(message, state):
    txt = message.text.replace("-", " ").split()
    try:
        d_min = float(txt[0]); d_max = float(txt[1]) if len(txt) > 1 else d_min
        if d_min > d_max: d_min, d_max = d_max, d_min
        await set_config("delay_min", str(d_min)); await set_config("delay_max", str(d_max))
    except: pass
    await finish_input(message, state)

@router.callback_query(F.data == "saver_edit_limits")
async def edit_limits(call, state): await request_input(call, state, "Limits (reg TTL):", SaverStates.waiting_for_limits)
@router.message(SaverStates.waiting_for_limits)
async def save_limits(message, state):
    txt = message.text.split()
    try:
        l_reg = float(txt[0]); l_ttl = float(txt[1]) if len(txt) > 1 else l_reg
        await set_config("limit_reg", str(l_reg)); await set_config("limit_ttl", str(l_ttl))
    except: pass
    await finish_input(message, state)

# ==========================================
# USERBOT LOGIC
# ==========================================
def register_userbot(app: Client, bot: Bot):
    async def process_caching(client, message, cfg):
        user = message.from_user
        text = message.text or message.caption or ""
        is_ttl, media_type, media_obj = False, None, None
        for m_type in ["photo", "video", "voice", "video_note", "document"]:
            obj = getattr(message, m_type, None)
            if obj:
                media_type, media_obj = m_type, obj
                if getattr(obj, "ttl_seconds", None) or getattr(message, "ttl_seconds", None): is_ttl = True
                break
        size_mb = getattr(media_obj, "file_size", 0) / (1024 * 1024) if media_obj else 0
        limit_mb = float(cfg.get("limit_ttl", "50")) if is_ttl else float(cfg.get("limit_reg", "20"))
        file_path = ""
        if media_obj and size_mb <= limit_mb:
            try: file_path = await message.download(file_name=f"{CACHE_DIR}{message.chat.id}_{message.id}/")
            except: pass
        if not is_ttl:
            async with aiosqlite.connect(DB_FILE) as db:
                await db.execute("INSERT OR REPLACE INTO msg_cache (message_id, chat_id, user_id, user_name, text, media_type, file_path, is_ttl) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (message.id, message.chat.id, user.id, user.first_name, text, media_type, file_path))
                if random.randint(1, 100) == 1:
                    cutoff = datetime.now() - timedelta(days=180)
                    async with db.execute("SELECT file_path FROM msg_cache WHERE timestamp < ?", (cutoff,)) as cursor:
                        async for row in cursor:
                            if row[0] and os.path.exists(row[0]):
                                try:
                                    os.remove(row[0])
                                    dir_p = os.path.dirname(row[0])
                                    if os.path.exists(dir_p) and not os.listdir(dir_p):
                                        os.rmdir(dir_p)
                                except:
                                    pass
                    await db.execute("DELETE FROM msg_cache WHERE timestamp < ?", (cutoff,))
                await db.commit()
        if is_ttl and cfg.get("save_ttl") == "1":
            dump_chat_id = int(cfg.get("dump_chat_id", "0"))
            if dump_chat_id:
                topic_id = await get_or_create_topic(app, bot, dump_chat_id, user.id, user_obj=user)
                asyncio.create_task(send_alert_delayed(bot, app, dump_chat_id, user.id, topic_id, "🔥", file_path, media_type, float(cfg.get("delay_min", "1")), float(cfg.get("delay_max", "5")), delete_file_after=True, is_ttl=True, parse_mode="HTML"))

    @app.on_message(filters.private & ~filters.bot & ~filters.me, group=-5)
    async def incoming_messages_handler(client, message):
        if not message.chat or message.chat.type != ChatType.PRIVATE: return
        user = message.from_user
        if not user or user.is_bot or user.is_self: return
        cfg = await get_config()
        if cfg.get("is_active") != "1": return
        try: dump_id = int(cfg.get("dump_chat_id", ""))
        except: return
        if message.chat.id == dump_id: return
        if str(user.id) in [x.strip() for x in cfg.get("blacklist", "").split(",") if x.strip()]: return
        targets = cfg.get("target_chats", "")
        if targets and str(message.chat.id) not in [x.strip() for x in targets.split(",") if x.strip()]: return
        asyncio.create_task(process_caching(client, message, cfg))

    @app.on_deleted_messages()
    async def handle_deleted_messages(client, messages):
        cfg = await get_config()
        if cfg.get("is_active") != "1" or cfg.get("save_deleted") != "1": return
        try: dump_id = int(cfg.get("dump_chat_id", ""))
        except: return
        async with aiosqlite.connect(DB_FILE) as db:
            for msg in messages:
                if msg.chat and msg.chat.type != ChatType.PRIVATE: continue
                if msg.chat: cursor = await db.execute("SELECT user_id, text, media_type, file_path, is_ttl FROM msg_cache WHERE message_id = ? AND chat_id = ?", (msg.id, msg.chat.id))
                else: cursor = await db.execute("SELECT user_id, text, media_type, file_path, is_ttl FROM msg_cache WHERE message_id = ? ORDER BY timestamp DESC LIMIT 1", (msg.id,))
                row = await cursor.fetchone()
                if row and row[4] != 1:
                    u_id, txt, m_type, f_path, _ = row
                    topic_id = await get_or_create_topic(app, bot, dump_id, u_id)
                    safe_txt = html.escape(txt) if txt else ""
                    alert_txt = f"<i>{safe_txt}</i>" if safe_txt else "<i>(media)</i>"
                    async def delayed_clean(m_id, c_id):
                        await send_alert_delayed(bot, app, dump_id, u_id, topic_id, alert_txt, f_path, m_type, float(cfg.get("delay_min", "1")), float(cfg.get("delay_max", "5")), delete_file_after=True, is_ttl=False, parse_mode="HTML")
                        if c_id: 
                            async with aiosqlite.connect(DB_FILE) as db2: 
                                await db2.execute("DELETE FROM msg_cache WHERE message_id = ? AND chat_id = ?", (m_id, c_id))
                                await db2.commit()
                    asyncio.create_task(delayed_clean(msg.id, getattr(msg.chat, 'id', None)))

    @app.on_edited_message(filters.private & ~filters.bot & ~filters.me)
    async def handle_edited_messages(client, message):
        if not message.chat or message.chat.type != ChatType.PRIVATE: return
        user = message.from_user
        if not user or user.is_bot or user.is_self: return
        cfg = await get_config()
        if cfg.get("is_active") != "1" or cfg.get("save_edited") != "1": return
        try: dump_id = int(cfg.get("dump_chat_id", ""))
        except: return
        if message.chat.id == dump_id: return
        async with aiosqlite.connect(DB_FILE) as db:
            cursor = await db.execute("SELECT text, media_type, file_path FROM msg_cache WHERE message_id = ? AND chat_id = ?", (message.id, message.chat.id))
            row = await cursor.fetchone()
            if row:
                old_t, m_type, f_path = row
                new_t = message.text or message.caption or ""
                if old_t != new_t:
                    topic_id = await get_or_create_topic(app, bot, dump_id, user.id, user_obj=user)
                    alert_txt = f"<s>{html.escape(old_t)}</s>\n{html.escape(new_t)}"
                    asyncio.create_task(send_alert_delayed(bot, app, dump_id, user.id, topic_id, alert_txt, f_path, m_type, float(cfg.get("delay_min", "1")), float(cfg.get("delay_max", "5")), delete_file_after=False, is_ttl=False, parse_mode="HTML"))
                    await db.execute("UPDATE msg_cache SET text = ? WHERE message_id = ? AND chat_id = ?", (new_t, message.id, message.chat.id))
                    await db.commit()
