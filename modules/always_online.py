"""Optional, persistent online status with one lightweight task per account."""
import asyncio
import logging
import random
import time
import json
import os
from pathlib import Path
from pyrogram.raw.functions.help import GetConfig
from pyrogram.raw.functions.users import GetUsers
from pyrogram.raw.types import InputUserSelf

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


def heartbeat_range(recommended, observed_ttl=None):
    # Honor the configured refresh period. An observed expiry is a ceiling,
    # never a guarantee: another logged-in client can change presence sooner.
    upper = min(90.0, max(1.0, float(recommended) * .85))
    if observed_ttl and observed_ttl > 5:
        upper = min(upper, max(1.0, observed_ttl - max(10, observed_ttl * .15)))
    return min(60.0, upper * .8), upper


def _save_presence(data):
    path = Path('data/presence_timing.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data), encoding='utf-8')
    os.chmod(temp, 0o600)
    os.replace(temp, path)


async def _heartbeat(app):
    started = False
    failures = 0
    config_until = 0
    recommendation = HEARTBEAT_SECONDS
    observed = None
    inspect_at = 0
    metrics = {}
    while app is userbot_app:
        low, high = heartbeat_range(recommendation, observed)
        delay = random.uniform(low, high)
        initialized = bool(getattr(app, 'is_initialized', False))
        if initialized:
            started = True
        elif started:
            return
        if initialized and app.is_connected:
            try:
                if time.time() >= config_until:
                    config = await app.invoke(GetConfig(), sleep_threshold=0)
                    recommendation = max(1.0, config.online_update_period_ms / 1000)
                    server_now = int(config.date)
                    config_until = time.time() + max(60, int(config.expires)-server_now)
                    metrics = dict(recommended_seconds=recommendation,
                        offline_idle_seconds=config.offline_idle_timeout_ms/1000,
                        config_at=time.time())
                await app.invoke(UpdateStatus(offline=False), sleep_threshold=0)
                failures = 0
                if time.time() >= inspect_at:
                    users = await app.invoke(GetUsers(id=[InputUserSelf()]), sleep_threshold=0)
                    status = getattr(users[0], 'status', None) if users else None
                    expires = getattr(status, 'expires', None)
                    if expires:
                        # Server clock is derived from help.getConfig rather
                        # than assuming the host's wall clock is identical.
                        observed = max(0, int(expires) - (server_now + int(time.time()-metrics['config_at'])))
                        metrics['observed_expiry_seconds'] = observed
                    inspect_at = time.time()+3600
                low, high = heartbeat_range(recommendation, observed)
                metrics.update(interval_min_seconds=low, interval_max_seconds=high, updated_at=time.time())
                _save_presence(metrics)
                delay = random.uniform(low, high)
            except FloodWait as exc:
                delay = max(HEARTBEAT_SECONDS, float(exc.value)+1)
            except (OSError, ConnectionError, asyncio.TimeoutError):
                failures += 1
                delay = min(300, HEARTBEAT_SECONDS * 2 ** min(failures, 2))
            except Exception as exc:
                logging.warning('[Always Online] Status update failed: %s', type(exc).__name__)
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
    enabled = bool(cfg.get("enabled", False))
    status = _("status_on") if enabled else _("status_off")
    return [[InlineKeyboardButton(text=_("btn_always_online", status=status),
        callback_data="online_set_off" if enabled else "online_set_on")]]


@router.callback_query(F.data == "online_menu")
async def online_menu(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await state.update_data(menu_msg_id=call.message.message_id)
    if plugins.generate_menu_cb:
        text, kb = await plugins.generate_menu_cb()
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
