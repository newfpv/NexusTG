"""Resumable guided setup using the existing settings editors."""
import html
import os
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.fsm.storage.base import StorageKey
from core.config import _
from core.utils import plugins, safe_edit

router = Router()
MODULE = 'setup_wizard'
active = False
current_step = 0
# Each editor remains the single source of truth for its settings.
STEPS = [
    ('account', 'my_info'), ('prompt', 'ai_g_set_prompt'), ('history', 'ai_history_budget'),
    ('ai_scope', 'ai_global_settings'), ('human', 'ai_human_settings_global'),
    ('delays', 'ai_g_set_delays'), ('typing', 'ai_g_set_typing'), ('sleep', 'ai_settings_sleep'),
    ('search', 'ai_toggle_search_global'), ('debug', 'ai_toggle_debug'),
    ('youtube_cookies', 'yt_cookies_menu'), ('media_cookies', 'md_cookie_menu'),
    ('voice', 'voice_main'), ('download', 'md_main'), ('youtube', 'ys_main_menu'),
    ('saver', 'saver_main'), ('saved', 'cs_main_menu'), ('shop', 'shop_main'),
    ('format', 'ts_main'), ('command', 'aicmd_main'), ('online', None), ('chats', 'chats_list'),
]


async def _config():
    return await plugins.db.get_module_cfg(MODULE)


async def on_startup():
    global active, current_step
    cfg = await _config()
    active = bool(cfg.get('active', False))
    current_step = _resume_index(cfg)


def _resume_index(cfg):
    index = _step_index(cfg.get('step', 0))
    saved_key=cfg.get('step_key')
    if saved_key:
        index=next((position for position,(key,_) in enumerate(STEPS) if key==saved_key),index)
    elif not cfg.get('version'):
        # Migrate old 25-step bookmarks without restarting the walkthrough.
        old_index=max(0,int(cfg.get('step',0))) if str(cfg.get('step',0)).isdigit() else 0
        index=_step_index(max(1,old_index-3) if old_index else 0)
    return index


def _step_index(value):
    try:return max(0, min(len(STEPS) - 1, int(value)))
    except (ValueError, TypeError):return 0


class WizardNavigationMiddleware(BaseRequestMiddleware):
    """Keep navigation on the owner's menu when editors edit Telegram directly."""
    def __init__(self, storage):
        self.storage = storage

    async def __call__(self, make_request, bot, method):
        if active and getattr(method, 'reply_markup', None) is not None and getattr(method, 'message_id', None):
            cfg = await plugins.db.get_global_config()
            if getattr(method, 'chat_id', None) == cfg.admin_id:
                data = await self.storage.get_data(StorageKey(bot_id=bot.id, chat_id=cfg.admin_id, user_id=cfg.admin_id))
                if data.get('menu_msg_id') == method.message_id:
                    method.reply_markup = attach_navigation(method.reply_markup)
        return await make_request(bot, method)


def attach_navigation(markup):
    """Retain wizard navigation even when a native editor clears its FSM."""
    if not active:
        return markup
    rows = [list(row) for row in getattr(markup, 'inline_keyboard', [])]
    if any((button.callback_data or '').startswith(('wizard_step_', 'wizard_skip_')) or button.callback_data == 'wizard_pause' for row in rows for button in row):
        return markup
    rows.append([Button(text=_("wizard_back_to_step"), callback_data=f'wizard_step_{current_step}'),
                 Button(text=_("wizard_next"), callback_data=f'wizard_step_{current_step + 1}')])
    return Markup(inline_keyboard=rows)


async def _save(**changes):
    global active, current_step
    await plugins.db.update_module_cfg(MODULE, **changes)
    if 'active' in changes:
        active = bool(changes['active'])
    if 'step' in changes:
        current_step = int(changes['step'])


def _status(value):
    return _('status_on') if value else _('status_off')


def current_value(key, cfg):
    settings = cfg.module_settings or {}
    ai = settings.get('ai_engine', {})
    if key == 'account':
        return _('wizard_connected') if cfg.session_string else _('wizard_not_connected')
    if key == 'keys':
        return str(len([item for item in (cfg.api_keys or '').split(',') if item.strip()]))
    if key == 'models':
        return html.escape((cfg.model_fallback_list or _('wizard_not_set'))[:180])
    if key == 'timezone':return html.escape(cfg.tz or 'UTC')
    if key == 'prompt':return html.escape((cfg.global_prompt or _('wizard_default_prompt'))[:180])
    if key == 'history':return str(ai.get('history_token_budget', 65536))
    if key == 'ai_scope':return _status(cfg.global_ai_active)
    if key == 'human':return _status(ai.get('h_smart', True))
    if key == 'delays':return f'{ai.get("db_min",1)}–{ai.get("db_max",3)} / {ai.get("da_min",1)}–{ai.get("da_max",3)} s'
    if key == 'typing':return str(cfg.typing_speed)
    if key == 'sleep':return f'{cfg.sleep_start}–{cfg.sleep_end}' if cfg.sleep_start and cfg.sleep_end else _('status_off')
    if key == 'search':return _status(cfg.google_search)
    if key == 'debug':return _status(ai.get('ai_debug', True))
    if key == 'youtube_cookies':return _('wizard_loaded') if os.path.isfile('data/cookies.txt') else _('wizard_not_loaded')
    if key == 'online':return _status(settings.get('always_online', {}).get('enabled', False))
    return _('wizard_open_editor')


async def show_step(message, state, index):
    from core.auth import _active_credential_tests
    job = _active_credential_tests.pop(message.chat.id, None)
    if job:
        job.stop()
    await state.update_data(api_test_running=False)
    await state.set_state(None)
    if index >= len(STEPS):
        return await finish(message, state)
    index = max(0, index)
    await _save(step=index, step_key=STEPS[index][0], version=2, active=True, completed=False)
    cfg = await plugins.db.get_global_config()
    key, callback = STEPS[index]
    if key == 'online':
        callback = 'online_set_off' if (cfg.module_settings or {}).get('always_online', {}).get('enabled', False) else 'online_set_on'
    title_key, description_key, action_key = ('wizard_title_' + key, 'wizard_desc_' + key, 'wizard_action_' + key)
    text = _('wizard_card', number=index + 1, total=len(STEPS), title=_(title_key),
             description=_(description_key), value=current_value(key, cfg))
    rows = [[Button(text=_(action_key), callback_data=callback)]]
    if key in {'youtube_cookies', 'media_cookies'}:
        guide = 'https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies' if key == 'youtube_cookies' else 'https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp'
        rows.append([Button(text=_('wizard_cookie_guide'), url=guide)])
    if key == 'account' and not cfg.session_string:
        rows[0] = [Button(text=_("btn_login_userbot"), callback_data='auth_start')]
    rows.append([Button(text=_("wizard_previous"), callback_data=f'wizard_step_{max(0,index-1)}'),
                 Button(text=_("wizard_next"), callback_data=f'wizard_step_{index+1}')])
    rows.append([Button(text=_("wizard_skip"), callback_data=f'wizard_skip_{index+1}'),
                 Button(text=_("wizard_pause"), callback_data='wizard_pause')])
    await safe_edit(message, state, text, Markup(inline_keyboard=rows))


async def begin(message, state):
    cfg = await _config()
    index = 0 if cfg.get('completed') else _resume_index(cfg)
    await show_step(message, state, index)


async def finish(message, state):
    cfg = await plugins.db.get_global_config()
    await _save(active=False, completed=True, step=0)
    text = _('wizard_complete', keys=current_value('keys',cfg), timezone=current_value('timezone',cfg),
             ai=current_value('ai_scope',cfg), budget=current_value('history',cfg))
    kb = Markup(inline_keyboard=[[Button(text=_("btn_back_main"), callback_data='main_menu')],
                                [Button(text=_("wizard_restart"), callback_data='wizard_restart')]])
    await safe_edit(message, state, text, kb)


@router.callback_query(F.data.in_({'wizard_start','wizard_restart','wizard_pause'}) | F.data.startswith('wizard_step_') | F.data.startswith('wizard_skip_'))
async def navigate(call: types.CallbackQuery, state: FSMContext):
    cfg = await plugins.db.get_global_config()
    if call.from_user.id != cfg.admin_id or call.message.chat.id != cfg.admin_id:
        return await call.answer(_("auth_not_authorized"), show_alert=True)
    await state.update_data(menu_msg_id=call.message.message_id)
    await state.set_state(None)
    if call.data == 'wizard_pause':
        await _save(active=False)
        text, kb = await plugins.generate_menu_cb()
        return await safe_edit(call.message, state, text, kb)
    if call.data == 'wizard_restart':
        return await show_step(call.message, state, 0)
    if call.data == 'wizard_start':
        return await begin(call.message, state)
    try:index = int(call.data.rsplit('_',1)[1])
    except ValueError:index = current_step
    return await show_step(call.message, state, min(max(index,0),len(STEPS)))
