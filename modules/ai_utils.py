import os
import random
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from pyrogram import enums

from gemini_core import generate_ai_response
from utils import simulate_typing
from i18n import _

# GENERAL CONDITION (Video reset timers)
skip_video_timers = set()

# ISOLATED MEDIA MEMORY BASE
MEDIA_DB_PATH = "data/memory_cache.db"

def init_media_db():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(MEDIA_DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS memory_cache (
            msg_id INTEGER PRIMARY KEY,
            type TEXT, 
            content TEXT,
            timestamp DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def clean_old_memory_cache():
    conn = sqlite3.connect(MEDIA_DB_PATH)
    c = conn.cursor()
    two_days_ago = datetime.now() - timedelta(days=2)
    c.execute('DELETE FROM memory_cache WHERE timestamp < ?', (two_days_ago,))
    conn.commit()
    conn.close()

def get_cached_media_data(msg_id, m_type):
    conn = sqlite3.connect(MEDIA_DB_PATH)
    c = conn.cursor()
    c.execute('SELECT content FROM memory_cache WHERE msg_id = ? AND type = ?', (msg_id, m_type))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def save_media_data(msg_id, m_type, content):
    conn = sqlite3.connect(MEDIA_DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO memory_cache (msg_id, type, content, timestamp) VALUES (?, ?, ?, ?)',
              (msg_id, m_type, content, datetime.now()))
    conn.commit()
    conn.close()

init_media_db()

# AUXILIARY AI FUNCTIONS
def introduce_typo(text):
    if len(text) < 5: return text
    words = text.split()
    candidates = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    if not candidates: return text
    idx = random.choice(candidates)
    word = list(words[idx])
    char_idx = random.randint(1, len(word) - 2)
    if word[char_idx].isupper() or word[char_idx+1].isupper(): return text
    word[char_idx], word[char_idx+1] = word[char_idx+1], word[char_idx]
    words[idx] = "".join(word)
    return " ".join(words)

async def simulate_human_typing(client, chat_id, total_time, is_human_mode, t_min=1.5, t_max=3.5, p_min=0.5, p_max=2.0):
    if not is_human_mode or total_time < 3.0:
        await simulate_typing(client, chat_id, total_time)
        try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
        except: pass
        return
    elapsed = 0
    while elapsed < total_time:
        t_type = min(random.uniform(t_min, t_max), total_time - elapsed)
        try: await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        except: pass
        await asyncio.sleep(t_type)
        elapsed += t_type
        if elapsed >= total_time: break
        t_pause = min(random.uniform(p_min, p_max), total_time - elapsed)
        try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
        except: pass
        await asyncio.sleep(t_pause)
        elapsed += t_pause
    try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
    except: pass

async def generate_media_description(media_path: str) -> str:
    desc_prompt = _("ai_media_desc_prompt")
    logging.info("="*50)
    logging.info(_("log_llm_req_media_desc"))
    logging.info(_("log_prompt", prompt=desc_prompt))
    logging.info(_("log_attached_file", path=media_path))
    try:
        res = await generate_ai_response(desc_prompt, media_path, custom_prompt="", search_enabled=False)
        if not res or res == "⏳":
            logging.warning(_("log_api_overload_desc"))
            return _("ai_media_desc_unavailable")
        logging.info(_("log_llm_res_desc", res=res))
        return res
    except Exception as e:
        logging.error(_("log_desc_gen_error", e=e))
        return _("ai_media_desc_failed")
