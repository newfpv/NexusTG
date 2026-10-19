import asyncio
import html
import logging
import re
import time
from datetime import timezone
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import text

from core.db import AsyncSessionLocal, CoreRepository
from core.services import generate_ai_response
from core.utils import safe_edit, CoreAPI, get_cancel_kb
from core.config import _

router = Router()
MODULE_NAME = "custom_saved"
_transfer_lock = asyncio.Lock()
_pending_transfers = set()

class CustomSavedFSM(StatesGroup):
    waiting_for_dest = State()
    waiting_for_command = State()

async def _get_cfg() -> dict:
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    logging.debug("[CSaver] Configuration loaded")
    return {
        "is_active": cfg.get("is_active", False),
        "transfer_saved": cfg.get("transfer_saved", False),
        "transfer_since": cfg.get("transfer_since", 0),
        "target_chat_id": cfg.get("target_chat_id", ""),
        "target_topic_id": cfg.get("target_topic_id", ""),
        "trigger_command": cfg.get("trigger_command", ".save")
    }

async def _upd_cfg(**kwargs):
    logging.info(f"[CSaver] Updating configuration: {list(kwargs.keys())}")
    await CoreAPI.update_module_cfg(MODULE_NAME, **kwargs)

async def get_main_menu_buttons() -> list:
    return [[InlineKeyboardButton(text=_("cs_btn_main"), callback_data="cs_main_menu")]]

@router.callback_query(F.data == "cs_main_menu")
async def render_menu(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[CSaver] User {call.from_user.id} accessed main menu")
    await state.clear()
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_cfg()
    
    status = _("status_on") if cfg["is_active"] else _("status_off")
    dest_str = _("cs_not_set")
    if cfg["target_chat_id"]:
        dest_str = f"{cfg['target_chat_id']}:{cfg['target_topic_id']}" if cfg["target_topic_id"] else str(cfg["target_chat_id"])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{_('cs_btn_toggle')}: {status}", callback_data="cs_toggle")],
        [InlineKeyboardButton(text=f"{_('cs_btn_dest')}: {dest_str}", callback_data="cs_set_dest")],
        [InlineKeyboardButton(text=f"{_('cs_btn_cmd')}: {cfg['trigger_command']}", callback_data="cs_set_cmd")],
        [InlineKeyboardButton(text=_("cs_transfer_toggle", status=_("status_on") if cfg["transfer_saved"] else _("status_off")), callback_data="cs_transfer_toggle")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])
    
    await safe_edit(call.message, state, _("cs_menu_text") + "\n\n" + _("cs_transfer_hint"), kb, parse_mode="HTML")

@router.callback_query(F.data == "cs_transfer_toggle")
async def toggle_saved_transfer(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    if not cfg["transfer_saved"] and not cfg["target_chat_id"]:
        return await call.answer(_("cs_transfer_need_destination"), show_alert=True)
    await _upd_cfg(transfer_saved=not cfg["transfer_saved"], transfer_since=int(time.time()) if not cfg["transfer_saved"] else cfg["transfer_since"])
    await render_menu(call, state)

async def _transfer_journal():
    async with AsyncSessionLocal() as session:
        await session.execute(text('''CREATE TABLE IF NOT EXISTS custom_saved_transfers (
            source_chat BIGINT NOT NULL, message_id BIGINT NOT NULL,
            destination BIGINT NOT NULL, destination_message BIGINT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
            PRIMARY KEY (source_chat, message_id))'''))
        await session.commit()

async def transfer_saved_message(client, message):
    """Move only new Saved Messages; originals survive every forwarding failure."""
    owner_id = getattr(getattr(client, "me", None), "id", None)
    if not owner_id or message.chat.id != owner_id or getattr(message, "service", None) or getattr(message, "empty", False):
        return
    group_id = getattr(message, "media_group_id", None)
    job = (owner_id, group_id or message.id)
    if job in _pending_transfers:
        return
    _pending_transfers.add(job)
    try:
        async with _transfer_lock:
            cfg = await _get_cfg()
            if not cfg["is_active"] or not cfg["transfer_saved"] or not cfg["target_chat_id"]:
                return
            since = cfg.get("transfer_since", 0)
            if since:
                stamp = getattr(message, "date", None)
                if stamp is None:
                    return
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp.timestamp() < since:
                    return
            if getattr(message, "reply_to_message_id", None) and (getattr(message, "text", None) or "").strip().lower() == cfg["trigger_command"].lower():
                return
            destination = int(cfg["target_chat_id"])
            if destination == owner_id:
                return
            topic = int(cfg["target_topic_id"]) if cfg["target_topic_id"] else None
            if group_id:
                await asyncio.sleep(0.7)
                messages = await client.get_media_group(owner_id, message.id)
                # Never delete an album unless Telegram returned its actual group.
                if not messages or message.id not in {item.id for item in messages} or any(
                        getattr(item, "media_group_id", None) != group_id or item.chat.id != owner_id for item in messages):
                    return
            else:
                messages = [message]
            ids = sorted({item.id for item in messages})
            await _transfer_journal()
            async with AsyncSessionLocal() as session:
                records = (await session.execute(text('SELECT message_id, completed FROM custom_saved_transfers WHERE source_chat=:source'),
                                                  {'source': owner_id})).all()
            delivered = {mid for mid, done in records}
            completed = {mid for mid, done in records if done}
            pending = [mid for mid in ids if mid not in delivered]
            if pending:
                forwarded = await client.forward_messages(chat_id=destination, from_chat_id=owner_id,
                    message_ids=pending, message_thread_id=topic)
                forwarded = forwarded if isinstance(forwarded, list) else [forwarded]
                if len(forwarded) != len(pending) or any(not item or getattr(item, "empty", False) or not getattr(item, "id", None) for item in forwarded):
                    return
                # Commit the receipt before deleting any source messages.
                async with AsyncSessionLocal() as session:
                    for source_id, target in zip(pending, forwarded):
                        await session.execute(text('''INSERT INTO custom_saved_transfers
                            (source_chat, message_id, destination, destination_message, completed, updated_at)
                            VALUES (:source, :mid, :dest, :target, 0, :at)
                            ON CONFLICT(source_chat, message_id) DO NOTHING'''),
                            {'source': owner_id, 'mid': source_id, 'dest': destination, 'target': target.id, 'at': time.time()})
                    await session.commit()
            to_delete = [mid for mid in ids if mid not in completed]
            if to_delete:
                deleted = await client.delete_messages(owner_id, to_delete)
                if deleted is False or deleted is None or isinstance(deleted, int) and deleted < len(to_delete):
                    return
                async with AsyncSessionLocal() as session:
                    for mid in to_delete:
                        await session.execute(text('UPDATE custom_saved_transfers SET completed=1, updated_at=:at WHERE source_chat=:source AND message_id=:mid'),
                                              {'source': owner_id, 'mid': mid, 'at': time.time()})
                    await session.execute(text('''DELETE FROM custom_saved_transfers WHERE completed=1 AND rowid NOT IN
                        (SELECT rowid FROM custom_saved_transfers WHERE completed=1 ORDER BY updated_at DESC LIMIT 1000)'''))
                    await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logging.warning("[CSaver] Saved transfer failed: %s; source messages retained when possible", type(exc).__name__)
    finally:
        _pending_transfers.discard(job)

@router.callback_query(F.data == "cs_toggle")
async def toggle_module(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    new_val = not cfg["is_active"]
    logging.info(f"[CSaver] User {call.from_user.id} toggled activity to {new_val}")
    await _upd_cfg(is_active=new_val)
    await render_menu(call, state)

@router.callback_query(F.data == "cs_set_dest")
async def ask_dest(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[CSaver] User {call.from_user.id} requested destination edit")
    cfg = await _get_cfg()
    current = f"{cfg['target_chat_id']}:{cfg['target_topic_id']}" if cfg['target_topic_id'] else (cfg['target_chat_id'] or _("status_empty"))
    await safe_edit(call.message, state, _("cs_ask_dest", current=current), get_cancel_kb("cs_main_menu"), parse_mode="HTML")
    await state.set_state(CustomSavedFSM.waiting_for_dest)

@router.callback_query(F.data == "cs_set_cmd")
async def ask_cmd(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[CSaver] User {call.from_user.id} requested command edit")
    cfg = await _get_cfg()
    await safe_edit(call.message, state, _("cs_ask_cmd", current=cfg['trigger_command']), get_cancel_kb("cs_main_menu"), parse_mode="HTML")
    await state.set_state(CustomSavedFSM.waiting_for_command)

@router.message(CustomSavedFSM.waiting_for_dest)
@router.message(CustomSavedFSM.waiting_for_command)
async def process_fsm_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    txt = message.text.strip()
    
    try: 
        await message.delete()
    except Exception as e: 
        logging.debug(f"[CSaver] Failed to delete user message: {e}")
    
    if current_state == CustomSavedFSM.waiting_for_dest.state:
        if txt.lower() in [_("cs_cmd_reset").lower(), "reset"]:
            logging.info(f"[CSaver] User {message.from_user.id} reset the destination")
            await _upd_cfg(target_chat_id="", target_topic_id="")
        else:
            clean_txt = txt.replace(" ", "")
            if ":" in clean_txt:
                c_id, t_id = clean_txt.split(":", 1)
                logging.info(f"[CSaver] User {message.from_user.id} updated destination to chat {c_id}, topic {t_id}")
                await _upd_cfg(target_chat_id=c_id, target_topic_id=t_id)
            else:
                logging.info(f"[CSaver] User {message.from_user.id} updated destination to chat {clean_txt}")
                await _upd_cfg(target_chat_id=clean_txt, target_topic_id="")
                
    elif current_state == CustomSavedFSM.waiting_for_command.state:
        cmd = txt.split()[0]
        logging.info(f"[CSaver] User {message.from_user.id} updated trigger command to: {cmd}")
        await _upd_cfg(trigger_command=cmd)

    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        mock_call = types.CallbackQuery(id="", from_user=message.from_user, chat_instance="", message=types.Message(message_id=data["menu_msg_id"], chat=message.chat, date=message.date))
        await render_menu(mock_call, state)

def register_userbot(app: Client):
    @app.on_message(filters.private, group=21)
    async def saved_transfer_handler(client, message):
        await transfer_saved_message(client, message)

    @app.on_message(filters.me & filters.reply, group=20)
    async def command_trigger(client: Client, message):
        cfg = await _get_cfg()
        if not cfg["is_active"] or not message.text or not cfg["target_chat_id"]:
            return
            
        if message.text.strip().lower() == cfg["trigger_command"].lower():
            logging.info(f"[CSaver] Command '{cfg['trigger_command']}' triggered in chat {message.chat.id}")
            asyncio.create_task(message.delete())
            try:
                target_topic = int(cfg["target_topic_id"]) if cfg["target_topic_id"] else None
                await client.forward_messages(
                    chat_id=int(cfg["target_chat_id"]),
                    from_chat_id=message.chat.id,
                    message_ids=message.reply_to_message_id,
                    message_thread_id=target_topic
                )
                logging.info(f"[CSaver] Successfully forwarded message {message.reply_to_message_id} to {cfg['target_chat_id']}:{target_topic}")
            except Exception as e:
                logging.error(f"[CSaver] Failed to forward message: {e}", exc_info=True)

async def on_startup():
    logging.info("[CSaver] Module initialized")
