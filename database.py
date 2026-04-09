import aiosqlite
import os

DB_PATH = "data/database.db"

async def init_db():
    os.makedirs("data", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                id INTEGER PRIMARY KEY DEFAULT 1,
                phone TEXT,
                session_string TEXT,
                sleep_start TEXT,
                sleep_end TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                is_active BOOLEAN DEFAULT 0,
                custom_prompt TEXT,
                delay_before_min INTEGER,
                delay_before_max INTEGER,
                delay_after_min INTEGER,
                delay_after_max INTEGER,
                is_ignored BOOLEAN DEFAULT 0
            )
        """)
        
        new_columns = [
            "global_prompt TEXT",
            "typing_speed REAL",
            "g_delay_before_min INTEGER",
            "g_delay_before_max INTEGER",
            "g_delay_after_min INTEGER",
            "g_delay_after_max INTEGER",
            "g_split_chance INTEGER DEFAULT 30",
            "g_split_min INTEGER DEFAULT 1",
            "global_ai_active BOOLEAN DEFAULT 0",
            "google_search BOOLEAN DEFAULT 1",
            "custom_reaction TEXT DEFAULT '👍'",
            "h_typing BOOLEAN DEFAULT 1",
            "h_ignore_chance INTEGER DEFAULT 10",
            "h_smart_read BOOLEAN DEFAULT 1",
            "h_smart_mul REAL DEFAULT 0.05",
            "h_typ_tmin REAL DEFAULT 1.5",
            "h_typ_tmax REAL DEFAULT 3.5",
            "h_typ_pmin REAL DEFAULT 0.5",
            "h_typ_pmax REAL DEFAULT 2.0",
            "v_auto_my BOOLEAN DEFAULT 0",
            "v_auto_other BOOLEAN DEFAULT 0",
            "v_allow_cmd BOOLEAN DEFAULT 0",
            "v_summarize BOOLEAN DEFAULT 1",
            "v_command TEXT DEFAULT '.text'",
            "ai_debug_log BOOLEAN DEFAULT 0"
        ]
        for col in new_columns:
            try: await db.execute(f"ALTER TABLE config ADD COLUMN {col}")
            except aiosqlite.OperationalError: pass 
                
        chat_cols = [
            "is_ignored BOOLEAN DEFAULT 0",
            "google_search BOOLEAN DEFAULT 1",
            "h_typing INTEGER DEFAULT 2",
            "h_ignore_chance INTEGER DEFAULT -1",
            "h_smart_read INTEGER DEFAULT 2",
            "h_smart_mul REAL DEFAULT NULL",
            "h_typ_tmin REAL DEFAULT NULL",
            "h_typ_tmax REAL DEFAULT NULL",
            "h_typ_pmin REAL DEFAULT NULL",
            "h_typ_pmax REAL DEFAULT NULL",
            "v_auto_my INTEGER DEFAULT 2",
            "v_auto_other INTEGER DEFAULT 2",
            "v_allow_cmd INTEGER DEFAULT 2"
        ]
        for col in chat_cols:
            try: await db.execute(f"ALTER TABLE chats ADD COLUMN {col}")
            except aiosqlite.OperationalError: pass
                
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ignored_msgs (
                chat_id INTEGER,
                msg_id INTEGER,
                PRIMARY KEY (chat_id, msg_id)
            )
        """)
                
        await db.commit()

async def save_session(phone, session_string):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM config WHERE id = 1") as cursor:
            row = await cursor.fetchone()
        if row:
            await db.execute("UPDATE config SET phone = ?, session_string = ? WHERE id = 1", (phone, session_string))
        else:
            await db.execute("INSERT INTO config (id, phone, session_string) VALUES (1, ?, ?)", (phone, session_string))
        await db.commit()

async def get_config():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT phone, session_string, sleep_start, sleep_end, global_prompt, typing_speed, g_delay_before_min, g_delay_before_max, g_delay_after_min, g_delay_after_max, g_split_chance, g_split_min, global_ai_active, google_search, custom_reaction, h_typing, h_ignore_chance, h_smart_read, h_smart_mul, h_typ_tmin, h_typ_tmax, h_typ_pmin, h_typ_pmax, v_auto_my, v_auto_other, v_allow_cmd, v_summarize, v_command, ai_debug_log FROM config WHERE id = 1") as cursor:
            return await cursor.fetchone()

async def toggle_ai_debug_log():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET ai_debug_log = CASE WHEN ai_debug_log = 1 THEN 0 ELSE 1 END WHERE id = 1")
        await db.commit()

async def set_sleep_hours(start_time, end_time):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET sleep_start = ?, sleep_end = ? WHERE id = 1", (start_time, end_time))
        await db.commit()

async def delete_session():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET phone = NULL, session_string = NULL WHERE id = 1")
        await db.commit()

async def set_global_prompt(prompt):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET global_prompt = ? WHERE id = 1", (prompt,))
        await db.commit()

async def set_global_delays(db_min, db_max, da_min, da_max):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET g_delay_before_min=?, g_delay_before_max=?, g_delay_after_min=?, g_delay_after_max=? WHERE id = 1", 
                         (db_min, db_max, da_min, da_max))
        await db.commit()

async def set_global_typing_speed(speed):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET typing_speed = ? WHERE id = 1", (speed,))
        await db.commit()

async def toggle_global_ai():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET global_ai_active = CASE WHEN global_ai_active = 1 THEN 0 ELSE 1 END WHERE id = 1")
        await db.commit()

async def toggle_global_search():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET google_search = CASE WHEN google_search = 1 THEN 0 ELSE 1 END WHERE id = 1")
        await db.commit()

async def set_search_all_chats(state: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        val = 1 if state else 0
        await db.execute("UPDATE chats SET google_search = ?", (val,))
        await db.commit()

async def set_custom_reaction(reaction_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET custom_reaction = ? WHERE id = 1", (reaction_id,))
        await db.commit()

async def toggle_global_h_typing():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET h_typing = CASE WHEN h_typing = 1 THEN 0 ELSE 1 END WHERE id = 1")
        await db.commit()

async def toggle_global_h_smart_read():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET h_smart_read = CASE WHEN h_smart_read = 1 THEN 0 ELSE 1 END WHERE id = 1")
        await db.commit()

async def set_global_h_ignore(chance: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET h_ignore_chance = ? WHERE id = 1", (chance,))
        await db.commit()

async def set_global_h_smart_mul(mul: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET h_smart_mul = ? WHERE id = 1", (mul,))
        await db.commit()

async def set_global_h_typing_cfg(tmin: float, tmax: float, pmin: float, pmax: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET h_typ_tmin=?, h_typ_tmax=?, h_typ_pmin=?, h_typ_pmax=? WHERE id = 1", (tmin, tmax, pmin, pmax))
        await db.commit()

async def toggle_v_setting_global(setting_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE config SET {setting_name} = CASE WHEN {setting_name} = 1 THEN 0 ELSE 1 END WHERE id = 1")
        await db.commit()

async def set_v_command_global(cmd: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE config SET v_command = ? WHERE id = 1", (cmd,))
        await db.commit()

async def get_chat_settings(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_active, custom_prompt, delay_before_min, delay_before_max, delay_after_min, delay_after_max, is_ignored, google_search, h_typing, h_ignore_chance, h_smart_read, h_smart_mul, h_typ_tmin, h_typ_tmax, h_typ_pmin, h_typ_pmax, v_auto_my, v_auto_other, v_allow_cmd FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
            return await cursor.fetchone()

async def toggle_chat(chat_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chats (chat_id, is_active) 
            VALUES (?, 1) 
            ON CONFLICT(chat_id) 
            DO UPDATE SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
        """, (chat_id,))
        await db.commit()

async def toggle_chat_ignore(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chats (chat_id, is_ignored) 
            VALUES (?, 1) 
            ON CONFLICT(chat_id) 
            DO UPDATE SET is_ignored = CASE WHEN is_ignored = 1 THEN 0 ELSE 1 END
        """, (chat_id,))
        await db.commit()

async def toggle_chat_search(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO chats (chat_id, google_search) 
            VALUES (?, 1) 
            ON CONFLICT(chat_id) 
            DO UPDATE SET google_search = CASE WHEN google_search = 1 THEN 0 ELSE 1 END
        """, (chat_id,))
        await db.commit()

async def set_custom_prompt(chat_id, prompt):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chats (chat_id, custom_prompt) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET custom_prompt = ?",
            (chat_id, prompt, prompt)
        )
        await db.commit()

async def set_chat_delays(chat_id, db_min, db_max, da_min, da_max):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO chats (chat_id, delay_before_min, delay_before_max, delay_after_min, delay_after_max) 
               VALUES (?, ?, ?, ?, ?) 
               ON CONFLICT(chat_id) 
               DO UPDATE SET delay_before_min = ?, delay_before_max = ?, delay_after_min = ?, delay_after_max = ?""",
            (chat_id, db_min, db_max, da_min, da_max, db_min, db_max, da_min, da_max)
        )
        await db.commit()

async def toggle_chat_h_typing(chat_id: int):
    cfg = await get_chat_settings(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        if not cfg:
            await db.execute("INSERT INTO chats (chat_id, h_typing) VALUES (?, 1)", (chat_id,))
        else:
            curr = cfg[8] if cfg[8] is not None else 2
            nxt = 1 if curr == 2 else (0 if curr == 1 else 2)
            await db.execute("UPDATE chats SET h_typing = ? WHERE chat_id = ?", (nxt, chat_id))
        await db.commit()

async def toggle_chat_h_smart_read(chat_id: int):
    cfg = await get_chat_settings(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        if not cfg:
            await db.execute("INSERT INTO chats (chat_id, h_smart_read) VALUES (?, 1)", (chat_id,))
        else:
            curr = cfg[10] if cfg[10] is not None else 2
            nxt = 1 if curr == 2 else (0 if curr == 1 else 2)
            await db.execute("UPDATE chats SET h_smart_read = ? WHERE chat_id = ?", (nxt, chat_id))
        await db.commit()

async def set_chat_h_ignore(chat_id: int, chance: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO chats (chat_id, h_ignore_chance) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET h_ignore_chance = ?", (chat_id, chance, chance))
        await db.commit()

async def set_chat_h_smart_mul(chat_id: int, mul):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO chats (chat_id, h_smart_mul) VALUES (?, ?) ON CONFLICT(chat_id) DO UPDATE SET h_smart_mul = ?", (chat_id, mul, mul))
        await db.commit()

async def set_chat_h_typing_cfg(chat_id: int, tmin, tmax, pmin, pmax):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO chats (chat_id, h_typ_tmin, h_typ_tmax, h_typ_pmin, h_typ_pmax) VALUES (?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET h_typ_tmin=?, h_typ_tmax=?, h_typ_pmin=?, h_typ_pmax=?", (chat_id, tmin, tmax, pmin, pmax, tmin, tmax, pmin, pmax))
        await db.commit()

async def toggle_v_setting_chat(chat_id: int, setting_index: int, setting_name: str):
    cfg = await get_chat_settings(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        if not cfg:
            await db.execute(f"INSERT INTO chats (chat_id, {setting_name}) VALUES (?, 1)", (chat_id,))
        else:
            curr = cfg[setting_index] if len(cfg) > setting_index and cfg[setting_index] is not None else 2
            nxt = 1 if curr == 2 else (0 if curr == 1 else 2)
            await db.execute(f"UPDATE chats SET {setting_name} = ? WHERE chat_id = ?", (nxt, chat_id))
        await db.commit()

async def add_ignored_msg(chat_id: int, msg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO ignored_msgs (chat_id, msg_id) VALUES (?, ?)", (chat_id, msg_id))
        await db.commit()

async def is_ignored_msg(chat_id: int, msg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM ignored_msgs WHERE chat_id = ? AND msg_id = ?", (chat_id, msg_id))
        row = await cursor.fetchone()
        return row is not None
