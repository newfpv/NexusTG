"""Primary-owner controls: named profiles, quotas, and safe diagnostics."""
import asyncio
import html
import io
import logging
import secrets
from decimal import Decimal, InvalidOperation
from aiogram import Bot, F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from core import managed_accounts as service
from core.archive_storage import GIB, MAX_LIMIT
from core.config import _, LANG_FILE
from core.diagnostics import recent, format_rows
from core.utils import plugins, safe_edit

router = Router()
_storage_job = None
STORAGE_SUMMARY_TIMEOUT = 2.0


async def storage_summary():
    global _storage_job
    if _storage_job is None or _storage_job.done():
        _storage_job = asyncio.create_task(asyncio.to_thread(service.storage_totals))
        _storage_job.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
    return await asyncio.wait_for(asyncio.shield(_storage_job),STORAGE_SUMMARY_TIMEOUT)


class AddBot(StatesGroup):
    token = State()


class EditBot(StatesGroup):
    name = State()
    quota = State()


async def allowed(user_id, chat_id):
    if not service.is_manager() or user_id != chat_id:
        return False
    cfg = await plugins.db.get_global_config()
    return user_id == cfg.admin_id


async def get_core_settings_buttons():
    if not service.is_manager():
        return []
    return [[Button(text=_('managed_stats_button'), callback_data='managed_stats_0')]]


def back(name=None):
    return Markup(inline_keyboard=[[Button(text=_('btn_cancel'), callback_data='managed_bot_' + name if name else 'managed_cancel')]])


def label(row, number=1):
    return row.get('display_name') or ('@' + row['username'] if row.get('username') else _('managed_bot_number', number=number))


def gib(value):
    return f'{value / GIB:.3f}'


async def validate_token(token):
    if not service.TOKEN.fullmatch(token):
        raise ValueError('Invalid token')
    bot = None
    try:
        bot = Bot(token=token)
        async with asyncio.timeout(15):
            me = await bot.get_me()
            hook = await bot.get_webhook_info()
        if hook.url:
            raise RuntimeError('webhook')
        return me
    finally:
        if bot:
            await bot.session.close()


async def show_stats(message, state, page=0):
    snapshot = await asyncio.to_thread(service.status)
    bots, total = snapshot['bots'], len(snapshot['bots'])
    working = sum(bool(row['running'] and row['ready'] and row['enabled']) for row in bots)
    disabled = sum(not row['enabled'] for row in bots)
    text = _('managed_stats', total=total, working=working, disabled=disabled)
    try:
        totals = await storage_summary()
    except (OSError,asyncio.TimeoutError):
        totals = None
        logging.warning('[Managed bots] Storage summary timed out or failed')
    if totals:
        text += '\n\n' + _('managed_storage_summary', media=gib(totals['media_bytes']), text=gib(totals['text_db_bytes']), free=gib(totals['disk_free']), used=gib(totals['disk_used']))
    else:
        text += '\n\n' + _('managed_storage_delayed')
    if not snapshot['at']:
        text += '\n' + _('managed_status_stale')
    page = max(0, min(page, max(0, (total - 1) // 8)))
    rows = []
    for index, row in enumerate(bots[page * 8:page * 8 + 8], start=page * 8 + 1):
        icon = '🗑' if row.get('deleting') else '⏸' if not row['enabled'] else '🟢' if row['running'] and row['ready'] else '🟡'
        suffix = ' · ' + _('managed_primary') if row['primary'] else ''
        rows.append([Button(text=icon + ' ' + label(row, index) + suffix, callback_data='managed_bot_' + row['name'])])
    if total > 8:
        rows.append([Button(text='‹', callback_data=f'managed_stats_{max(0,page-1)}'), Button(text=f'{page+1}/{(total+7)//8}', callback_data=f'managed_stats_{page}'), Button(text='›', callback_data=f'managed_stats_{min((total-1)//8,page+1)}')])
    rows.append([Button(text=_('managed_add_button'), callback_data='managed_add')])
    rows.append([Button(text=_('managed_refresh'), callback_data=f'managed_stats_{page}')])
    rows.append([Button(text=_('btn_back'), callback_data='global_settings')])
    await safe_edit(message, state, text, Markup(inline_keyboard=rows))


async def show_bot(message, state, name):
    row = next((r for r in service.status()['bots'] if r['name'] == name), None)
    if not row:
        return await show_stats(message, state)
    usage = await asyncio.to_thread(service.storage_info, name)
    text = _('managed_bot_card', name=html.escape(label(row)))
    text += '\n\n' + _('managed_storage_card', media=gib(usage['media_bytes']), limit=gib(usage['limit_bytes']), text=gib(usage['text_db_bytes']), files=usage['files'])
    if not usage['at']:
        text += '\n' + _('managed_storage_pending')
    rows = [[Button(text=_('managed_rename'), callback_data='managed_rename_' + name)],
            [Button(text=_('managed_quota'), callback_data='managed_quota_' + name)],
            [Button(text=_('managed_logs'), callback_data='managed_logs_' + name)]]
    if row.get('deleting'):
        return await safe_edit(message,state,text+'\n\n'+_('managed_deleting'),Markup(inline_keyboard=[[Button(text=_('managed_refresh'),callback_data='managed_bot_'+name)],[Button(text=_('btn_back'),callback_data='managed_stats_0')]]))
    if not row['primary']:
        action = 'disable' if row['enabled'] else 'enable'
        toggle_key = 'managed_turn_off' if row['enabled'] else 'managed_turn_on'
        rows.append([Button(text=_(toggle_key), callback_data=f'managed_{action}_{name}')])
        if row['enabled']:
            rows.append([Button(text=_('managed_restart'), callback_data=f'managed_restart_{name}')])
        rows.append([Button(text=_('managed_delete'), callback_data='managed_delete_'+name)])
    rows.append([Button(text=_('btn_back'), callback_data='managed_stats_0')])
    await safe_edit(message, state, text, Markup(inline_keyboard=rows))


async def show_logs(message, state, name):
    path = service.profile(name)
    rows = await asyncio.to_thread(recent, path, 60)
    text = _('managed_logs_title') + '\n\n' + _('managed_logs_privacy')
    preview = format_rows(rows[-10:])
    text += '\n\n<pre>' + html.escape(preview[-2600:] if preview else _('managed_logs_empty')) + '</pre>'
    kb = Markup(inline_keyboard=[[Button(text=_('managed_refresh'), callback_data='managed_logs_' + name)],
                                [Button(text=_('managed_logs_file'), callback_data='managed_logsfile_' + name)],
                                [Button(text=_('btn_back'), callback_data='managed_bot_' + name)]])
    await safe_edit(message, state, text, kb)


@router.callback_query(F.data.startswith('managed_'))
async def callbacks(call: types.CallbackQuery, state: FSMContext):
    if not await allowed(call.from_user.id, call.message.chat.id):
        return await call.answer(_('auth_not_authorized'), show_alert=True)
    if call.data == 'managed_cancel' or call.data.startswith(('managed_stats_','managed_bot_','managed_logs_')):
        try:await asyncio.wait_for(call.answer(),1)
        except Exception:logging.debug('[Managed bots] Could not acknowledge navigation')
    await state.update_data(menu_msg_id=call.message.message_id)
    data = call.data
    if data.startswith('managed_remove_'):
        stored=await state.get_data()
        confirmation=stored.get('managed_delete_confirmation') or {}
        if not secrets.compare_digest(data[len('managed_remove_'):],confirmation.get('nonce','invalid')):
            return await call.answer(_('managed_action_error'),show_alert=True)
        await state.update_data(managed_delete_confirmation=None)
        try:
            await asyncio.to_thread(service.request_delete,confirmation['name'],confirmation['bot_id'])
        except (PermissionError,ValueError,OSError,KeyError):
            return await call.answer(_('managed_delete_error'),show_alert=True)
        await call.answer(_('managed_deleting'))
        return await show_bot(call.message,state,confirmation['name'])
    if data.startswith('managed_delete_'):
        name=data[len('managed_delete_'):]
        try:
            path=service.profile(name)
            if name==service.manager_name(path.parent):raise PermissionError()
            nonce=secrets.token_hex(8)
            await state.update_data(managed_delete_confirmation={'name':name,'bot_id':service.token_identity(path/'.env'),'nonce':nonce})
            row=next(row for row in service.status()['bots'] if row['name']==name)
        except (PermissionError,ValueError,OSError,StopIteration):
            return await call.answer(_('managed_action_error'),show_alert=True)
        await call.answer()
        return await safe_edit(call.message,state,_('managed_delete_confirm',name=html.escape(label(row))),Markup(inline_keyboard=[[Button(text=_('managed_delete_yes'),callback_data='managed_remove_'+nonce)],[Button(text=_('btn_cancel'),callback_data='managed_bot_'+name)]]))
    await state.update_data(managed_delete_confirmation=None)
    if data == 'managed_add':
        await state.update_data(managed_token=None)
        await state.set_state(AddBot.token)
        return await safe_edit(call.message, state, _('managed_token_prompt'), back())
    if data == 'managed_cancel' or data.startswith('managed_stats_'):
        await state.update_data(managed_token=None, managed_profile=None)
        await state.set_state(None)
        try:
            page = int(data.rsplit('_', 1)[1])
        except ValueError:
            page = 0
        return await show_stats(call.message, state, page)
    if data.startswith(('managed_enable_', 'managed_disable_', 'managed_restart_')):
        prefix_name, action, name = data.split('_', 2)
        try:
            service.change_account(name, action)
        except (PermissionError, ValueError, OSError):
            return await call.answer(_('managed_action_error'), show_alert=True)
        await call.answer(_('managed_action_done'))
        return await show_bot(call.message, state, name)
    operations = ('bot', 'rename', 'quota', 'logs', 'logsfile')
    for operation in operations:
        prefix = 'managed_' + operation + '_'
        if not data.startswith(prefix):
            continue
        name = data[len(prefix):]
        try:
            path = service.profile(name)
        except (PermissionError, ValueError, OSError):
            return await call.answer(_('managed_action_error'), show_alert=True)
        if operation == 'bot':
            await state.set_state(None)
            await state.update_data(managed_profile=None)
            return await show_bot(call.message, state, name)
        if operation == 'rename':
            await state.update_data(managed_profile=name)
            await state.set_state(EditBot.name)
            return await safe_edit(call.message, state, _('managed_name_prompt'), back(name))
        if operation == 'quota':
            await state.update_data(managed_profile=name)
            await state.set_state(EditBot.quota)
            return await safe_edit(call.message, state, _('managed_quota_prompt'), back(name))
        if operation == 'logs':
            return await show_logs(call.message, state, name)
        if operation == 'logsfile':
            rows = await asyncio.to_thread(recent, path, 100)
            document = types.BufferedInputFile((format_rows(rows) or _('managed_logs_empty')).encode('utf-8'), filename='diagnostics.txt')
            return await call.message.answer_document(document, caption=_('managed_logs_privacy'))


@router.message(AddBot.token)
async def receive_token(message: types.Message, state: FSMContext):
    if not await allowed(message.from_user.id, message.chat.id):
        return
    try:
        await message.delete()
    except Exception:
        pass
    token = (message.text or '').strip()
    if message.document:
        if not (message.document.file_name or '').lower().endswith('.txt') or (message.document.file_size or 0) > 8192:
            return await safe_edit(message, state, _('managed_token_error'), back())
        buffer = io.BytesIO()
        try:
            async with asyncio.timeout(15):
                await plugins.bot.download(message.document, destination=buffer)
            token = buffer.getvalue().decode('utf-8-sig', errors='replace').strip()
        except Exception:
            return await safe_edit(message, state, _('managed_token_error'), back())
    try:
        me = await validate_token(token)
    except RuntimeError:
        return await safe_edit(message, state, _('managed_webhook_error'), back())
    except Exception:
        return await safe_edit(message, state, _('managed_token_error'), back())
    try:
        service.create_account(token, me.id, me.username, language='EN' if 'language_EN' in LANG_FILE else 'RU', display_name=getattr(me, 'full_name', None))
    except FileExistsError:
        return await safe_edit(message, state, _('managed_duplicate'), back())
    except (ValueError, PermissionError, OSError):
        return await safe_edit(message, state, _('managed_create_error'), back())
    await state.update_data(managed_token=None, managed_profile=None)
    await state.set_state(None)
    kb = Markup(inline_keyboard=[[Button(text=_('managed_stats_button'), callback_data='managed_stats_0')]])
    await safe_edit(message, state, _('managed_added', username=html.escape(me.username)), kb)
    service.request_promotion()


@router.message(EditBot.name)
async def receive_name(message: types.Message, state: FSMContext):
    if not await allowed(message.from_user.id, message.chat.id):
        return
    name = (await state.get_data()).get('managed_profile')
    try:
        await message.delete()
    except Exception:
        pass
    try:
        value = service.clean_name(message.text)
        if not value or len(message.text or '') > 128:
            raise ValueError('Invalid name')
        service.rename_account(name, value)
    except (ValueError, PermissionError, OSError):
        return await safe_edit(message, state, _('managed_name_prompt'), back(name))
    await state.set_state(None)
    await show_bot(message, state, name)


@router.message(EditBot.quota)
async def receive_quota(message: types.Message, state: FSMContext):
    if not await allowed(message.from_user.id, message.chat.id):
        return
    name = (await state.get_data()).get('managed_profile')
    try:
        await message.delete()
    except Exception:
        pass
    try:
        amount = Decimal((message.text or '').strip().replace(',', '.'))
        if not amount.is_finite() or not 0 <= amount <= MAX_LIMIT / GIB:
            raise ValueError('Invalid quota')
        service.set_archive_limit(name, int(amount * GIB))
    except (InvalidOperation, ValueError, PermissionError, OSError):
        return await safe_edit(message, state, _('managed_quota_prompt'), back(name))
    await state.set_state(None)
    await show_bot(message, state, name)
