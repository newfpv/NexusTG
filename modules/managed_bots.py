"""Owner-only account management, exposed exclusively by the primary bot."""
import asyncio
import html
import io
from aiogram import Bot,F,Router,types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State,StatesGroup
from aiogram.types import InlineKeyboardButton as Button,InlineKeyboardMarkup as Markup
from core import managed_accounts as service
from core.config import _,LANG_FILE
from core.utils import plugins,safe_edit

router=Router()


class AddBot(StatesGroup):
    token=State()
    owner=State()


async def allowed(user_id,chat_id):
    if not service.is_manager() or user_id!=chat_id:return False
    cfg=await plugins.db.get_global_config()
    return user_id==cfg.admin_id


async def get_settings_buttons():
    if not service.is_manager():return []
    return [[Button(text=_('managed_add_button'),callback_data='managed_add'),Button(text=_('managed_stats_button'),callback_data='managed_stats_0')]]


def back():return Markup(inline_keyboard=[[Button(text=_('btn_cancel'),callback_data='managed_cancel')]])


async def validate_token(token):
    if not service.TOKEN.fullmatch(token):raise ValueError('Invalid token')
    bot=None
    try:
        bot=Bot(token=token)
        async with asyncio.timeout(15):
            me=await bot.get_me()
            hook=await bot.get_webhook_info()
        if hook.url:raise RuntimeError('webhook')
        return me
    finally:
        if bot:await bot.session.close()


async def show_stats(message,state,page=0):
    snapshot=service.status();bots=snapshot['bots'];total=len(bots)
    working=sum(bool(row['running'] and row['ready'] and row['enabled']) for row in bots)
    disabled=sum(not row['enabled'] for row in bots)
    text=_('managed_stats',total=total,working=working,disabled=disabled)
    if not snapshot['at']:text+='\n'+_('managed_status_stale')
    page=max(0,min(page,max(0,(total-1)//8)));rows=[]
    for index,row in enumerate(bots[page*8:page*8+8],start=page*8+1):
        status_key='managed_disabled' if not row['enabled'] else 'managed_running' if row['running'] and row['ready'] else 'managed_starting'
        label='@'+row['username'] if row['username'] else _('managed_bot_number',number=index)
        suffix=' · '+_('managed_primary') if row['primary'] else ''
        text+='\n'+html.escape(label)+suffix+' — '+_(status_key)
        if not row['primary']:rows.append([Button(text=label,callback_data='managed_bot_'+row['name'])])
    if total>8:
        rows.append([Button(text='‹',callback_data=f'managed_stats_{max(0,page-1)}'),Button(text=f'{page+1}/{(total+7)//8}',callback_data=f'managed_stats_{page}'),Button(text='›',callback_data=f'managed_stats_{min((total-1)//8,page+1)}')])
    rows.append([Button(text=_('managed_add_button'),callback_data='managed_add'),Button(text=_('managed_refresh'),callback_data=f'managed_stats_{page}')])
    rows.append([Button(text=_('btn_back_main'),callback_data='main_menu')])
    await safe_edit(message,state,text,Markup(inline_keyboard=rows))


async def finish_add(message,state,owner_id):
    data=await state.get_data()
    if not data.get('managed_token'):return await show_stats(message,state)
    try:
        service.create_account(data['managed_token'],data['managed_id'],data['managed_username'],owner_id,'EN' if 'language_EN' in LANG_FILE else 'RU')
    except FileExistsError:
        return await safe_edit(message,state,_('managed_duplicate'),back())
    except (ValueError,PermissionError,OSError):
        return await safe_edit(message,state,_('managed_create_error'),back())
    username=data['managed_username']
    await state.update_data(managed_token=None,managed_id=None,managed_username=None)
    await state.set_state(None)
    kb=Markup(inline_keyboard=[[Button(text=_('managed_open'),url='https://t.me/'+username+'?start=setup')],
                              [Button(text=_('managed_stats_button'),callback_data='managed_stats_0')]])
    await safe_edit(message,state,_('managed_added',username=html.escape(username)),kb)
    service.request_promotion()


@router.callback_query(F.data.startswith('managed_'))
async def callbacks(call:types.CallbackQuery,state:FSMContext):
    if not await allowed(call.from_user.id,call.message.chat.id):return await call.answer(_('auth_not_authorized'),show_alert=True)
    await state.update_data(menu_msg_id=call.message.message_id)
    if call.data=='managed_add':
        await state.update_data(managed_token=None,managed_id=None,managed_username=None)
        await state.set_state(AddBot.token)
        return await safe_edit(call.message,state,_('managed_token_prompt'),back())
    if call.data=='managed_cancel':
        await state.update_data(managed_token=None,managed_id=None,managed_username=None)
        await state.set_state(None)
        return await show_stats(call.message,state)
    if call.data=='managed_owner_self':
        if await state.get_state()!=AddBot.owner.state:return await call.answer()
        return await finish_add(call.message,state,call.from_user.id)
    if call.data=='managed_owner_other':
        if await state.get_state()!=AddBot.owner.state:return await call.answer()
        return await safe_edit(call.message,state,_('managed_owner_prompt'),back())
    if call.data.startswith('managed_stats_'):
        await state.update_data(managed_token=None,managed_id=None,managed_username=None)
        await state.set_state(None)
        try:page=int(call.data.rsplit('_',1)[1])
        except ValueError:page=0
        return await show_stats(call.message,state,page)
    if call.data.startswith(('managed_enable_','managed_disable_','managed_restart_')):
        prefix,action,name=call.data.split('_',2)
        try:service.change_account(name,action)
        except (PermissionError,ValueError,OSError):return await call.answer(_('managed_action_error'),show_alert=True)
        await call.answer(_('managed_action_done'))
        return await show_stats(call.message,state)
    if call.data.startswith('managed_bot_'):
        name=call.data[len('managed_bot_'):]
        row=next((r for r in service.status()['bots'] if r['name']==name),None)
        if not row or row['primary']:return await call.answer()
        label='@'+row['username'] if row['username'] else _('managed_bot_number',number=1)
        action='disable' if row['enabled'] else 'enable'
        toggle_key='managed_turn_off' if row['enabled'] else 'managed_turn_on'
        rows=[[Button(text=_(toggle_key),callback_data=f'managed_{action}_{name}')]]
        if row['enabled']:rows.append([Button(text=_('managed_restart'),callback_data=f'managed_restart_{name}')])
        if row['username']:rows.append([Button(text=_('managed_open'),url='https://t.me/'+row['username'])])
        rows.append([Button(text=_('btn_back'),callback_data='managed_stats_0')])
        await safe_edit(call.message,state,_('managed_bot_card',name=html.escape(label)),Markup(inline_keyboard=rows))


@router.message(AddBot.token)
async def receive_token(message:types.Message,state:FSMContext):
    if not await allowed(message.from_user.id,message.chat.id):return
    try:await message.delete()
    except Exception:pass
    token=(message.text or '').strip()
    if message.document:
        if not (message.document.file_name or '').lower().endswith('.txt') or (message.document.file_size or 0)>8192:
            return await safe_edit(message,state,_('managed_token_error'),back())
        buffer=io.BytesIO()
        await plugins.bot.download(message.document,destination=buffer)
        token=buffer.getvalue().decode('utf-8-sig',errors='replace').strip()
    try:
        me=await validate_token(token)
    except RuntimeError:
        return await safe_edit(message,state,_('managed_webhook_error'),back())
    except Exception:
        return await safe_edit(message,state,_('managed_token_error'),back())
    await state.update_data(managed_token=token,managed_id=me.id,managed_username=me.username)
    await state.set_state(AddBot.owner)
    rows=[[Button(text=_('managed_owner_self'),callback_data='managed_owner_self'),Button(text=_('managed_owner_other'),callback_data='managed_owner_other')],
          [Button(text=_('btn_cancel'),callback_data='managed_cancel')]]
    await safe_edit(message,state,_('managed_owner_choice',username=html.escape(me.username)),Markup(inline_keyboard=rows))


@router.message(AddBot.owner)
async def receive_owner(message:types.Message,state:FSMContext):
    if not await allowed(message.from_user.id,message.chat.id):return
    origin=getattr(message,'forward_origin',None)
    sender=getattr(origin,'sender_user',None) or getattr(message,'forward_from',None)
    text=(message.text or '').strip()
    owner_id=sender.id if sender and not sender.is_bot else int(text) if text.isdigit() and 0<len(text)<20 else 0
    try:await message.delete()
    except Exception:pass
    if not 0<owner_id<2**63:return await safe_edit(message,state,_('managed_owner_prompt'),back())
    await finish_add(message,state,owner_id)
