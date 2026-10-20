"""Owner-only acknowledgement of an account restriction; no automatic bypass."""
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from core.config import _
from core.utils import safe_edit, plugins

router=Router()
userbot_app=None

def register_userbot(app):
    global userbot_app
    userbot_app=app

async def get_core_settings_buttons():
    guard=getattr(userbot_app,'telegram_guard',None)
    if guard and guard.paused:
        return [[InlineKeyboardButton(text=_('btn_telegram_resume'),callback_data='telegram_resume')]]
    return []

@router.callback_query(F.data=='telegram_resume')
async def resume(call:types.CallbackQuery,state:FSMContext):
    guard=getattr(userbot_app,'telegram_guard',None)
    if guard:guard.resume()
    await call.answer(_('telegram_resumed'),show_alert=True)
    if plugins.generate_menu_cb:
        text,kb=await plugins.generate_menu_cb()
        await safe_edit(call.message,state,text,kb)
