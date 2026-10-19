"""Local quota observations and removal audit, never raw API credentials."""
import hashlib
import html
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.config import _
from core.db import AsyncSessionLocal, CoreRepository, AIKeyState
from core.services import _split_csv, _MODEL_CANONICAL
from core.utils import safe_edit

router = Router()
PAGE_SIZE = 4


def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:10]


def _model_ready(state, model, search, now):
    if state is None:
        return True
    until = state.search_unban_time if search else state.unban_time
    exhausted = (state.search_exhausted_models if search else state.exhausted_models) or {}
    return max(until or 0, exhausted.get(model, 0), exhausted.get(_MODEL_CANONICAL.get(model, model), 0)) <= now


async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_api_keys_info"), callback_data="api_keys_info_0")]]


async def build_keys_info(page=0):
    now = time.time()
    async with AsyncSessionLocal() as session:
        cfg = await CoreRepository(session).get_global_config()
        keys = list(dict.fromkeys(_split_csv(cfg.api_keys)))
        models = list(dict.fromkeys(_split_csv(cfg.model_fallback_list)))
        events = (cfg.module_settings or {}).get("api_keys_audit", {}).get("removed", [])
        try:
            tz = ZoneInfo(cfg.tz or "UTC")
        except (ValueError, KeyError):
            tz = ZoneInfo("UTC")
        pages = max(1, (len(keys) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        lines = [_("api_keys_info_title", count=len(keys), page=page + 1, pages=pages),
                 _("api_keys_info_explanation")]
        if models:
            names = ", ".join(models)
            lines.append(_("api_keys_info_models", models=html.escape(names[:600] + ("…" if len(names) > 600 else ""))))
        for index, key in enumerate(keys[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], page * PAGE_SIZE + 1):
            state = await session.get(AIKeyState, key)
            lines.append(_("api_keys_info_key", index=index, fingerprint=fingerprint(key)))
            if state is None:
                lines.append(_("api_keys_info_unknown"))
            for search in (False, True):
                ready = sum(_model_ready(state, model, search, now) for model in models)
                lines.append(_("api_keys_info_ready", mode=_("api_keys_info_search" if search else "api_keys_info_regular"),
                               ready=ready, total=len(models)))
            if state:
                blocked = []
                for model in models:
                    until = max(state.unban_time or 0, (state.exhausted_models or {}).get(model, 0),
                                (state.exhausted_models or {}).get(_MODEL_CANONICAL.get(model, model), 0))
                    if until > now:
                        blocked.append(html.escape(model[:80]) + ' → ' + datetime.fromtimestamp(until, tz).strftime('%d.%m %H:%M'))
                if blocked:
                    items = "; ".join(blocked[:3]) + (" …" if len(blocked) > 3 else "")
                    lines.append(_("api_keys_info_wait", items=items, tz=html.escape(str(tz))))
        if events:
            lines.append(_("api_keys_info_removed_title"))
            for event in reversed(events[-5:]):
                try:
                    stamp = datetime.fromisoformat(event["at"]).astimezone(tz).strftime('%d.%m %H:%M')
                except (KeyError, ValueError, TypeError):
                    stamp = "?"
                lines.append(f'{stamp} · <code>{html.escape(str(event.get("fingerprint", "?")))}</code> · API_KEY_INVALID')
    buttons = []
    if pages > 1:
        buttons.append([InlineKeyboardButton(text="‹", callback_data=f"api_keys_info_{(page - 1) % pages}"),
                        InlineKeyboardButton(text="›", callback_data=f"api_keys_info_{(page + 1) % pages}")])
    buttons.append([InlineKeyboardButton(text=_("btn_menu_refresh"), callback_data=f"api_keys_info_{page}")])
    buttons.append([InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")])
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("api_keys_info_"))
async def keys_info(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    await state.update_data(menu_msg_id=call.message.message_id)
    try:
        page = int(call.data.rsplit("_", 1)[1])
    except ValueError:
        page = 0
    text, kb = await build_keys_info(page)
    await safe_edit(call.message, state, text, kb)
