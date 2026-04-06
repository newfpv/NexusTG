import time
import asyncio
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from pyrogram import Client
from pyrogram.enums import ChatAction

from utils import safe_edit, plugins
from i18n import _

router = Router()
userbot_app = None
active_fake_tasks = {}

class FakeActionFSM(StatesGroup):
    duration = State()

# --- ХУКИ ДЛЯ ЯДРА ---
def register_userbot(app: Client):
    global userbot_app
    userbot_app = app

async def get_chat_menu_buttons(chat_id: int):
    return [[InlineKeyboardButton(text=_("btn_fake_activity"), callback_data=f"fake_{chat_id}")]]

# --- ЛОГИКА ИНТЕРФЕЙСА ---
@router.callback_query(F.data.startswith("fake_"))
async def fake_action_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    chat_id = int(call.data.split("_")[1])
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_fake_typing"), callback_data=f"doact_typing_{chat_id}"),
         InlineKeyboardButton(text=_("btn_fake_photo"), callback_data=f"doact_upload_photo_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_fake_record_video"), callback_data=f"doact_record_video_{chat_id}"),
         InlineKeyboardButton(text=_("btn_fake_upload_video"), callback_data=f"doact_upload_video_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_fake_record_audio"), callback_data=f"doact_record_audio_{chat_id}"),
         InlineKeyboardButton(text=_("btn_fake_upload_audio"), callback_data=f"doact_upload_audio_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_fake_record_video_note"), callback_data=f"doact_record_video_note_{chat_id}"),
         InlineKeyboardButton(text=_("btn_fake_upload_video_note"), callback_data=f"doact_upload_video_note_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_fake_upload_document"), callback_data=f"doact_upload_document_{chat_id}"),
         InlineKeyboardButton(text=_("btn_fake_playing"), callback_data=f"doact_playing_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_fake_choose_sticker"), callback_data=f"doact_choose_sticker_{chat_id}")]
    ])
    
    if chat_id in active_fake_tasks and not active_fake_tasks[chat_id].done():
        kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_fake_stop"), callback_data=f"fakestop_{chat_id}")])
        
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"chat_{chat_id}")])
    await safe_edit(call.message, state, _("fake_menu_title"), kb)

@router.callback_query(F.data.startswith("fakestop_"))
async def stop_fake_action(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[1])
    if chat_id in active_fake_tasks:
        active_fake_tasks[chat_id].cancel()
        del active_fake_tasks[chat_id]
    
    if userbot_app and userbot_app.is_connected:
        try: await userbot_app.send_chat_action(chat_id, ChatAction.CANCEL)
        except: pass
        
    await call.answer(_("fake_stopped_alert"), show_alert=True)
    text, kb = await plugins.generate_chat_menu_cb(chat_id)
    await safe_edit(call.message, state, text, kb)

@router.callback_query(F.data.startswith("doact_"))
async def ask_fake_duration(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    parts = call.data.split("_")
    action_type = "_".join(parts[1:-1])
    chat_id = int(parts[-1])
    
    await state.update_data(fake_chat=chat_id, fake_action=action_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"fake_{chat_id}")]])
    await safe_edit(call.message, state, _("fake_enter_duration"), kb)
    await state.set_state(FakeActionFSM.duration)

async def fake_action_worker(app, chat_id, action, minutes):
    end_time = time.time() + (minutes * 60)
    try:
        while time.time() < end_time:
            await app.send_chat_action(chat_id, getattr(ChatAction, action.upper()))
            await asyncio.sleep(min(5, end_time - time.time()))
    except asyncio.CancelledError: pass 
    except: pass
    finally:
        try: await app.send_chat_action(chat_id, ChatAction.CANCEL)
        except: pass

@router.message(FakeActionFSM.duration)
async def start_fake_action(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['fake_chat']
    action = data['fake_action']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_chat"), callback_data=f"chat_{chat_id}")]])
    
    try:
        minutes = float(message.text.replace(",", "."))
        if userbot_app and userbot_app.is_connected:
            if chat_id in active_fake_tasks and not active_fake_tasks[chat_id].done():
                active_fake_tasks[chat_id].cancel()
                
            task = asyncio.create_task(fake_action_worker(userbot_app, chat_id, action, minutes))
            active_fake_tasks[chat_id] = task
            
            await safe_edit(message, state, _("fake_started", minutes=minutes), kb)
        else:
            await safe_edit(message, state, _("err_userbot_not_connected"), kb)
    except:
        await safe_edit(message, state, _("fake_duration_error"), kb)