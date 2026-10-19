"""Optional, persistent online status with one lightweight task per account."""
import asyncio
import logging

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait
from pyrogram.raw.functions.account import UpdateStatus

from core.config import _
from core.utils import plugins, safe_edit

router = Router()
HEARTBEAT_SECONDS = 60
userbot_app = None
_task = None
_binding = None
_configure_lock = asyncio.Lock()


async def _heartbeat(app):
    started = False
    failures = 0
    while True:
        delay = HEARTBEAT_SECONDS
        initialized = bool(getattr(app, "is_initialized", False))
        if initialized:
            started = True
        elif started:
            return  # A stopped client must not keep a background task alive.
        if initialized and app.is_connected:
            try:
                async with asyncio.timeout(20):
                    await app.invoke(UpdateStatus(offline=False), sleep_threshold=0)
                failures = 0
            except FloodWait as exc:
                delay = max(HEARTBEAT_SECONDS, float(exc.value) + 1)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                failures += 1
                delay = min(300, HEARTBEAT_SECONDS * 2 ** min(failures, 2))
            except Exception as exc:
                logging.warning("[Always Online] Status update failed: %s", type(exc).__name__)
                delay = 300
        await asyncio.sleep(delay if initialized else 1)


async def _configure(app, enabled):
    global _task
    async with _configure_lock:
        previous, _task = _task, None
        if previous and not previous.done():
            previous.cancel()
            try:
                await previous
            except asyncio.CancelledError:
                pass
        if enabled and app is userbot_app and app is not None:
            _task = asyncio.create_task(_heartbeat(app), name="always-online")


async def _bind(app):
    cfg = await plugins.db.get_module_cfg("always_online")
    if app is userbot_app:
        await _configure(app, bool(cfg.get("enabled", False)))


def register_userbot(app):
    global userbot_app, _binding
    userbot_app = app
    if _binding and not _binding.done():
        _binding.cancel()
    _binding = asyncio.create_task(_bind(app), name="always-online-bind")


async def get_settings_buttons():
    cfg = await plugins.db.get_module_cfg("always_online")
    status = _("status_on") if cfg.get("enabled", False) else _("status_off")
    return [[InlineKeyboardButton(text=_("btn_always_online", status=status), callback_data="online_menu")]]


@router.callback_query(F.data == "online_menu")
async def online_menu(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await plugins.db.get_module_cfg("always_online")
    enabled = bool(cfg.get("enabled", False))
    connected = userbot_app and userbot_app.is_connected and userbot_app.is_initialized
    text = _("online_menu", status=_("status_on") if enabled else _("status_off"),
             connection=_("online_connected") if connected else _("online_disconnected"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("online_disable") if enabled else _("online_enable"),
                              callback_data="online_set_off" if enabled else "online_set_on")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")],
    ])
    await safe_edit(call.message, state, text, kb)


@router.callback_query(F.data.in_({"online_set_on", "online_set_off"}))
async def set_online(call: types.CallbackQuery, state: FSMContext):
    enabled = call.data == "online_set_on"
    # Finish startup binding first so a stale DB read cannot undo this choice.
    if _binding and not _binding.done():
        await _binding
    await plugins.db.update_module_cfg("always_online", enabled=enabled)
    await _configure(userbot_app, enabled)
    await online_menu(call, state)
