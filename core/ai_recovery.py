"""Durable reply checkpoints and recovery after missed Telegram updates."""
import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select, text

from core.db import AsyncSessionLocal, ChatConfig
from core.utils import plugins
from core.config import _

CHECK_INTERVAL = 30  # Local state checks; this is not an API polling interval.
NETWORK_PROBE_INTERVAL = 300
HISTORY_SCAN_INTERVAL = 300
MAX_HISTORY_FALLBACKS = 20
scan_cursor = 0
MAX_MESSAGE_AGE = 24 * 3600
TERMINAL_STAGES = {"sent", "reaction", "ignored", "disabled", "excluded", "no_text"}
recovery_task = None
schema_ready = False


async def init_checkpoints():
    global schema_ready
    if schema_ready:
        return
    async with AsyncSessionLocal() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_reply_checkpoint (
                chat_id BIGINT PRIMARY KEY, message_id BIGINT NOT NULL,
                stage TEXT NOT NULL, updated_at REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1, retry_at REAL NOT NULL DEFAULT 0
            )
        """))
        await session.commit()
    schema_ready = True


async def save_checkpoint(chat_id, message_id, stage, new_attempt=False, retry_at=0):
    await init_checkpoints()
    async with AsyncSessionLocal() as session:
        await session.execute(text("""
            INSERT INTO ai_reply_checkpoint (chat_id, message_id, stage, updated_at, attempts, retry_at)
            VALUES (:chat_id, :message_id, :stage, :updated_at, 1, :retry_at)
            ON CONFLICT(chat_id) DO UPDATE SET
                message_id=excluded.message_id, stage=excluded.stage, updated_at=excluded.updated_at,
                retry_at=excluded.retry_at,
                attempts=CASE WHEN ai_reply_checkpoint.message_id != excluded.message_id THEN 1
                    ELSE ai_reply_checkpoint.attempts + :increment END
            WHERE excluded.message_id >= ai_reply_checkpoint.message_id
        """), dict(chat_id=chat_id, message_id=message_id, stage=stage,
                    updated_at=time.time(), increment=int(new_attempt), retry_at=retry_at))
        await session.commit()


async def get_checkpoint(chat_id):
    await init_checkpoints()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(text(
            "SELECT message_id, stage, updated_at, attempts, retry_at FROM ai_reply_checkpoint WHERE chat_id=:cid"
        ), {"cid": chat_id})).mappings().first()
        return dict(row) if row else None


def should_recover(message, checkpoint, now=None):
    """History is authoritative: never reply when the latest message is outgoing."""
    from pyrogram.enums import ChatType
    now = time.time() if now is None else now
    if (not message or message.chat.type != ChatType.PRIVATE or message.outgoing
            or not message.from_user or message.from_user.is_self or message.from_user.is_bot
            or message.from_user.id == 777000 or message.service or message.empty):
        return False
    stamp = message.date
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    if now - stamp.timestamp() > MAX_MESSAGE_AGE:
        return False
    if checkpoint and checkpoint.get("retry_at", 0) > now:
        return False
    if checkpoint and checkpoint["message_id"] >= message.id:
        if checkpoint["message_id"] > message.id or checkpoint["stage"] in TERMINAL_STAGES:
            return False
        # Persistent backoff avoids a restart storm when Gemini or Telegram is unavailable.
        retry_after = min(900, 60 * 2 ** min(max(checkpoint["attempts"] - 1, 0), 4))
        if now - checkpoint["updated_at"] < retry_after:
            return False
    return True


async def notify_owner(key):
    try:
        config = await plugins.db.get_global_config()
        if config.admin_id and plugins.bot:
            await asyncio.wait_for(plugins.bot.send_message(config.admin_id, _(key)), timeout=15)
            return True
    except Exception:
        logging.warning("[AI Recovery] Owner notification failed", exc_info=True)
    return False


async def recover_chat(client, chat_id, message=None):
    from modules import ai_twin
    current = ai_twin.active_reply_tasks.get(chat_id)
    if current and not current.done():
        return
    cfg = await ai_twin._get_c_cfg(chat_id)
    global_cfg = await ai_twin._get_g_cfg()
    if not (cfg["is_active"] or (global_cfg["is_active"] and not cfg["is_ignored"])):
        return
    if message is None:
        async for item in client.get_chat_history(chat_id, limit=1):
            message = item
    checkpoint = await get_checkpoint(chat_id)
    if not should_recover(message, checkpoint):
        return
    # A new live update may have started a task while history was being fetched.
    started = await ai_twin.schedule_reply(client, message, recovery=True)
    if started:
        logging.warning("[AI Recovery] Recovered pending incoming message %s in chat %s",
                        message.id, chat_id)


async def scan_pending(client):
    from modules import ai_twin
    global_cfg = await ai_twin._get_g_cfg()
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(ChatConfig.chat_id).where(ChatConfig.is_active.is_(True)))
        candidates = {cid: None for cid in rows.scalars()}
    global scan_cursor
    if not candidates and not global_cfg["is_active"]:
        return
    # One dialog-list pass supplies latest messages for recent chats, avoiding
    # one get_history request for every chat. Cold active chats rotate in batches.
    async for dialog in client.get_dialogs(limit=100):
        if dialog.chat.type.value == "private" and (global_cfg["is_active"] or dialog.chat.id in candidates):
            candidates[dialog.chat.id] = dialog.top_message
    cold = [cid for cid, msg in candidates.items() if msg is None]
    selected = set()
    if cold:
        start = scan_cursor % len(cold)
        selected = {(cold + cold)[i] for i in range(start, start + min(MAX_HISTORY_FALLBACKS, len(cold)))}
        scan_cursor = (start + MAX_HISTORY_FALLBACKS) % len(cold)
    for cid, msg in candidates.items():
        if msg is None and cid not in selected:
            continue
        if msg is None:
            await asyncio.sleep(3)  # Pace extra history RPCs; never launch a parallel burst.
        try:
            await asyncio.wait_for(recover_chat(client, cid, msg), timeout=20)
        except Exception as exc:
            logging.warning("[AI Recovery] Chat %s history check failed: %s", cid, type(exc).__name__)
            if type(exc).__name__ == "FloodWait":
                raise


async def recovery_loop():
    from modules import ai_twin
    failures = 0
    outage_reported = False
    last_alert = 0
    last_restart = 0
    last_probe = 0
    last_scan = 0
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)
            config = await plugins.db.get_global_config()
            client = ai_twin.userbot_client
            if not config.session_string or not client:
                continue  # Deliberately signed out; do not restore a deleted session.
            try:
                if not client.is_connected:
                    raise ConnectionError("Telegram disconnected")
                if failures or time.time() - last_probe >= NETWORK_PROBE_INTERVAL:
                    await asyncio.wait_for(client.get_me(), timeout=15)
                    last_probe = time.time()
            except Exception as probe_error:
                if type(probe_error).__name__ == "FloodWait":
                    raise
                failures += 1
                logging.warning("[AI Recovery] Telegram probe failed (%s)", failures)
                if failures >= 3:
                    if time.time() - last_alert >= 1800:
                        if await notify_owner("ai_recovery_disconnected"):
                            outage_reported = True
                            last_alert = time.time()
                    # Existing main.py handles a visibly disconnected client. Also repair
                    # a stalled connection which still reports is_connected=True.
                    if client.is_connected and time.time() - last_restart >= 120:
                        last_restart = time.time()
                        config = await plugins.db.get_global_config()
                        if config.session_string and plugins.stop_userbot_cb and plugins.start_userbot_cb:
                            await asyncio.wait_for(plugins.stop_userbot_cb(), timeout=30)
                            await asyncio.wait_for(plugins.start_userbot_cb(config.session_string), timeout=45)
                continue
            reconnected = failures > 0
            failures = 0
            if outage_reported:
                if await notify_owner("ai_recovery_connected"):
                    outage_reported = False
            if reconnected or time.time() - last_scan >= HISTORY_SCAN_INTERVAL:
                last_scan = time.time()
                await asyncio.wait_for(scan_pending(client), timeout=180)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logging.exception("[AI Recovery] Watchdog iteration failed")
            if type(exc).__name__ == "FloodWait":
                # Telegram-directed backoff. Other tasks keep running during this sleep.
                await asyncio.sleep(max(getattr(exc, "value", 30), 1))


async def start_recovery():
    global recovery_task
    await init_checkpoints()
    if recovery_task is None or recovery_task.done():
        recovery_task = asyncio.create_task(recovery_loop(), name="ai-reply-recovery")
        logging.info("[AI Recovery] Durable reply recovery and connection probes enabled")
