"""Compact API key panel; full credentials are revealed only to the owner."""
import hashlib
import html
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from core.config import _
from core.db import AsyncSessionLocal, CoreRepository, AIKeyState
from core.services import _split_csv, _MODEL_CANONICAL
from core.utils import safe_edit

router = Router()
PAGE_SIZE = 6

def fingerprint(key):
    return hashlib.sha256(key.encode()).hexdigest()[:10]

def _model_ready(state, model, search, now):
    if state is None:
        return True
    until = state.search_unban_time if search else state.unban_time
    exhausted = (state.search_exhausted_models if search else state.exhausted_models) or {}
    return max(until or 0, exhausted.get(model, 0), exhausted.get(_MODEL_CANONICAL.get(model, model), 0)) <= now

def test_summary(rows):
    statuses = [item['status'] for row in rows for item in row.get('results', [])]
    return f'🟢 {statuses.count("ok")} · ⏳ {statuses.count("quota")} · ❌ {sum(s not in {"ok", "quota"} for s in statuses)}'

async def build_keys_info(page=0, reveal=False, results=False):
    now = time.time()
    async with AsyncSessionLocal() as session:
        cfg = await CoreRepository(session).get_global_config()
        keys = list(dict.fromkeys(_split_csv(cfg.api_keys)))
        models = list(dict.fromkeys(_split_csv(cfg.model_fallback_list)))
        audit = (cfg.module_settings or {}).get('api_keys_audit', {})
        test = audit.get('last_test', {})
        entries = test.get('rows', []) if results else keys
        page_size = 2 if results else PAGE_SIZE
        pages = max(1, (len(entries) + page_size - 1) // page_size)
        page = max(0, min(page, pages - 1))
        lines = [_("api_compact_title", count=len(keys), models=len(models))]
        if results:
            lines.append(test_summary(entries))
            if test.get('cancelled'):
                lines.append(_("api_test_cancelled"))
            for row in entries[page * page_size:(page + 1) * page_size]:
                lines.append(f'<code>{html.escape(row["fingerprint"])}</code> · {test_summary([row])}')
                for status, icon in [('ok', '🟢'), ('quota', '⏳'), ('invalid', '🗑'), ('forbidden', '🚫'), ('timeout', '⌛'), ('error', '❌')]:
                    names = [item['model'].removeprefix('gemini-') for item in row.get('results', []) if item['status'] == status]
                    if names:
                        text = ', '.join(names)
                        lines.append(icon + ' ' + html.escape(text[:160] + ('…' if len(text) > 160 else '')))
        else:
            for index, key in enumerate(keys[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], page * PAGE_SIZE + 1):
                state = await session.get(AIKeyState, key)
                ready = sum(_model_ready(state, model, False, now) for model in models)
                search = sum(_model_ready(state, model, True, now) for model in models)
                value = html.escape(key) if reveal else fingerprint(key)
                lines.append(f'{index}. <code>{value}</code> · 🟢 {ready}/{len(models)} · 🔎 {search}/{len(models)}')
            lines.append(_("api_compact_hint"))
            if test:
                try:
                    tz = ZoneInfo(cfg.tz or 'UTC')
                    stamp = datetime.fromisoformat(test['at']).astimezone(tz).strftime('%d.%m %H:%M')
                except (KeyError, TypeError, ValueError):
                    stamp = '?'
                lines.append(_("api_compact_last_test", at=stamp, summary=test_summary(test.get('rows', []))))
            if audit.get('removed'):
                lines.append(_("api_compact_removed", count=len(audit['removed'])))
    mode = 'results' if results else ('show' if reveal else 'info')
    buttons = []
    if pages > 1:
        buttons.append([Button(text='‹', callback_data=f'api_keys_{mode}_{(page - 1) % pages}'),
                        Button(text=f'{page + 1}/{pages}', callback_data=f'api_keys_{mode}_{page}'),
                        Button(text='›', callback_data=f'api_keys_{mode}_{(page + 1) % pages}')])
    if results:
        buttons.append([Button(text=_("btn_back"), callback_data='set_edit_keys')])
    else:
        buttons.append([Button(text=_("api_hide" if reveal else "api_show"), callback_data=f'api_keys_{"info" if reveal else "show"}_{page}'),
                        Button(text=_("api_edit"), callback_data='api_keys_edit')])
        buttons.append([Button(text=_("api_test_keys"), callback_data='api_keys_test'),
                        Button(text=_("api_test_models"), callback_data='test_ai_config')])
        if test:
            buttons.append([Button(text=_("api_test_results"), callback_data='api_keys_results_0')])
        buttons.append([Button(text=_("api_refresh"), callback_data=f'api_keys_info_{page}'),
                        Button(text=_("btn_back"), callback_data='global_settings')])
    return '\n'.join(lines), Markup(inline_keyboard=buttons)

@router.callback_query(F.data.startswith('api_keys_info_') | F.data.startswith('api_keys_show_') | F.data.startswith('api_keys_results_'))
async def keys_info(call: types.CallbackQuery, state: FSMContext):
    async with AsyncSessionLocal() as session:
        cfg = await CoreRepository(session).get_global_config()
        if not cfg.admin_id or call.from_user.id != cfg.admin_id or call.message.chat.id != cfg.admin_id:
            return await call.answer(_("auth_not_authorized"), show_alert=True)
    await state.set_state(None)
    await state.update_data(menu_msg_id=call.message.message_id, api_test_running=False)
    try:
        page = int(call.data.rsplit('_', 1)[1])
    except ValueError:
        page = 0
    text, kb = await build_keys_info(page, reveal=call.data.startswith('api_keys_show_'), results=call.data.startswith('api_keys_results_'))
    await safe_edit(call.message, state, text, kb)
