import os
import io
import re
import random
import asyncio
import logging
import html
from datetime import datetime, timezone

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from pyrogram import Client, filters, enums
from pyrogram.enums import ChatType
from pyrogram.raw import functions

import database
from gemini_core import generate_ai_response, transcribe_media
from utils import simulate_typing, plugins, safe_edit
from modules.youtube import fetch_youtube_data_sync
from i18n import _

active_reply_tasks = {}
skip_video_timers = set()
router = Router()

# ==========================================
# МАШИНА СОСТОЯНИЙ (FSM)
# ==========================================
class AISettingsFSM(StatesGroup):
    custom_prompt = State()
    delays = State()
    sleep_hours = State()
    global_prompt = State()
    global_delays = State()
    global_typing = State()
    
    custom_reaction = State()
    g_ignore_chance = State()
    c_ignore_chance = State()
    
    g_h_smart_cfg = State()
    c_h_smart_cfg = State()
    g_h_typing_cfg = State()
    c_h_typing_cfg = State()

# ==========================================
# ХУКИ ДЛЯ ИНТЕРФЕЙСА (КНОПКИ)
# ==========================================
async def get_settings_buttons():
    config = await database.get_config()
    g_ai_active = config[12] if (config and len(config) > 12) else False
    g_ai_status = _("ai_g_status_on") if g_ai_active else _("ai_g_status_off")
    
    g_search_active = config[13] if (config and len(config) > 13) else True
    search_status = _("ai_search_on") if g_search_active else _("ai_search_off")
    
    return [
        [InlineKeyboardButton(text=_("btn_ai_mode", g_ai_status=g_ai_status), callback_data="ai_toggle_global")],
        [InlineKeyboardButton(text=_("btn_ai_search_status", status=search_status), callback_data="ai_toggle_search_global")],
        [InlineKeyboardButton(text=_("btn_ai_human_settings"), callback_data="ai_human_settings_global")],
        [InlineKeyboardButton(text=_("btn_ai_global_settings"), callback_data="ai_global_settings")]
    ]

async def get_chat_menu_buttons(chat_id: int):
    config = await database.get_chat_settings(chat_id)
    glob_config = await database.get_config()
    
    is_active = config[0] if config else False
    status_text = _("ai_chat_status_on") if is_active else _("ai_chat_status_off")
    prompt = _("ai_prompt_custom") if (config and config[1]) else _("ai_prompt_global_only")
    
    is_ignored = config[6] if (config and len(config) > 6) else False
    ignore_btn_text = _("ai_ignore_on") if is_ignored else _("ai_ignore_off")
    
    c_search_active = config[7] if (config and len(config) > 7) else True
    c_search_status = _("ai_search_on") if c_search_active else _("ai_search_off")
    
    glob_db_min = glob_config[6] if glob_config and len(glob_config) > 6 and glob_config[6] is not None else 1
    glob_db_max = glob_config[7] if glob_config and len(glob_config) > 7 and glob_config[7] is not None else 3
    glob_da_min = glob_config[8] if glob_config and len(glob_config) > 8 and glob_config[8] is not None else 1
    glob_da_max = glob_config[9] if glob_config and len(glob_config) > 9 and glob_config[9] is not None else 3
    
    c_db_min = config[2] if (config and config[2] is not None) else glob_db_min
    c_db_max = config[3] if (config and config[3] is not None) else glob_db_max
    c_da_min = config[4] if (config and config[4] is not None) else glob_da_min
    c_da_max = config[5] if (config and config[5] is not None) else glob_da_max
    
    return [
        [InlineKeyboardButton(text=status_text, callback_data=f"ai_toggle_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_search_status", status=c_search_status), callback_data=f"ai_toggle_search_{chat_id}")],
        [InlineKeyboardButton(text=ignore_btn_text, callback_data=f"ai_ignore_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_prompt", prompt=prompt), callback_data=f"ai_prompt_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_delays", c_db_min=c_db_min, c_db_max=c_db_max, c_da_min=c_da_min, c_da_max=c_da_max), callback_data=f"ai_delays_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_human_settings"), callback_data=f"ai_human_chat_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_skip_video"), callback_data=f"skipwait_{chat_id}")]
    ]

# ==========================================
# ЧЕЛОВЕЧНОСТЬ (МЕНЮ И НАСТРОЙКИ)
# ==========================================
@router.callback_query(F.data == "ai_human_settings_global")
async def human_settings_global(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    config = await database.get_config()
    
    h_typing = bool(config[15]) if len(config) > 15 else True
    h_ignore = config[16] if len(config) > 16 else 10
    h_smart = bool(config[17]) if len(config) > 17 else True
    
    t_status = _("status_on") if h_typing else _("status_off")
    s_status = _("status_on") if h_smart else _("status_off")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_g_reaction"), callback_data="ai_h_set_reaction")],
        [
            InlineKeyboardButton(text=_("btn_h_typing", status=t_status), callback_data="ai_h_toggle_typing_g"),
            InlineKeyboardButton(text="⚙️", callback_data="ai_h_cfg_typing_g")
        ],
        [
            InlineKeyboardButton(text=_("btn_h_smart_read", status=s_status), callback_data="ai_h_toggle_smart_g"),
            InlineKeyboardButton(text="⚙️", callback_data="ai_h_cfg_smart_g")
        ],
        [InlineKeyboardButton(text=_("btn_h_ignore", chance=h_ignore), callback_data="ai_h_set_ignore_g")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, _("ai_human_g_text"), kb)

@router.callback_query(F.data.startswith("ai_human_chat_"))
async def human_settings_chat(call: types.CallbackQuery, state: FSMContext, chat_id: int = None):
    if chat_id is None:
        chat_id = int(call.data.split("_")[3])
        
    await state.update_data(menu_msg_id=call.message.message_id, chat_id=chat_id)
    cfg = await database.get_chat_settings(chat_id)
    
    c_typing = cfg[8] if (cfg and cfg[8] is not None) else 2
    c_ignore = cfg[9] if (cfg and cfg[9] is not None) else -1
    c_smart = cfg[10] if (cfg and cfg[10] is not None) else 2
    
    t_status = _("status_global") if c_typing == 2 else (_("status_on") if c_typing == 1 else _("status_off"))
    s_status = _("status_global") if c_smart == 2 else (_("status_on") if c_smart == 1 else _("status_off"))
    i_status = _("status_global") if c_ignore == -1 else c_ignore

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=_("btn_h_typing", status=t_status), callback_data=f"ai_h_toggle_typing_c_{chat_id}"),
            InlineKeyboardButton(text="⚙️", callback_data=f"ai_h_cfg_typing_c_{chat_id}")
        ],
        [
            InlineKeyboardButton(text=_("btn_h_smart_read", status=s_status), callback_data=f"ai_h_toggle_smart_c_{chat_id}"),
            InlineKeyboardButton(text="⚙️", callback_data=f"ai_h_cfg_smart_c_{chat_id}")
        ],
        [InlineKeyboardButton(text=_("btn_h_ignore", chance=i_status), callback_data=f"ai_h_set_ignore_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]
    ])
    await safe_edit(call.message, state, _("ai_human_c_text"), kb)

@router.callback_query(F.data == "ai_h_toggle_typing_g")
async def toggle_typ_g(call: types.CallbackQuery, state: FSMContext):
    await database.toggle_global_h_typing()
    await human_settings_global(call, state)

@router.callback_query(F.data == "ai_h_toggle_smart_g")
async def toggle_smart_g(call: types.CallbackQuery, state: FSMContext):
    await database.toggle_global_h_smart_read()
    await human_settings_global(call, state)

@router.callback_query(F.data.startswith("ai_h_toggle_typing_c_"))
async def toggle_typ_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    await database.toggle_chat_h_typing(chat_id)
    await human_settings_chat(call, state, chat_id=chat_id) 

@router.callback_query(F.data.startswith("ai_h_toggle_smart_c_"))
async def toggle_smart_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    await database.toggle_chat_h_smart_read(chat_id)
    await human_settings_chat(call, state, chat_id=chat_id) 

@router.callback_query(F.data == "ai_h_set_reaction")
async def ask_reaction(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_human_settings_global")]])
    await safe_edit(call.message, state, _("ai_g_reaction_request"), kb)
    await state.set_state(AISettingsFSM.custom_reaction)

@router.callback_query(F.data == "ai_h_set_ignore_g")
async def ask_ign_g(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_human_settings_global")]])
    await safe_edit(call.message, state, _("ai_ignore_request"), kb)
    await state.set_state(AISettingsFSM.g_ignore_chance)

@router.callback_query(F.data == "ai_h_cfg_smart_g")
async def cfg_smart_g(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_human_settings_global")]])
    await safe_edit(call.message, state, _("ai_h_cfg_smart_req"), kb)
    await state.set_state(AISettingsFSM.g_h_smart_cfg)

@router.callback_query(F.data == "ai_h_cfg_typing_g")
async def cfg_typ_g(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_human_settings_global")]])
    await safe_edit(call.message, state, _("ai_h_cfg_typing_req"), kb)
    await state.set_state(AISettingsFSM.g_h_typing_cfg)

@router.callback_query(F.data.startswith("ai_h_set_ignore_c_"))
async def ask_ign_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"ai_human_chat_{chat_id}")]])
    await safe_edit(call.message, state, _("ai_ignore_request"), kb)
    await state.set_state(AISettingsFSM.c_ignore_chance)

@router.callback_query(F.data.startswith("ai_h_cfg_smart_c_"))
async def cfg_smart_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"ai_human_chat_{chat_id}")]])
    await safe_edit(call.message, state, _("ai_h_cfg_smart_req"), kb)
    await state.set_state(AISettingsFSM.c_h_smart_cfg)

@router.callback_query(F.data.startswith("ai_h_cfg_typing_c_"))
async def cfg_typ_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"ai_human_chat_{chat_id}")]])
    await safe_edit(call.message, state, _("ai_h_cfg_typing_req"), kb)
    await state.set_state(AISettingsFSM.c_h_typing_cfg)

@router.callback_query(F.data == "ai_global_settings")
async def global_settings_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    config = await database.get_config()
    typing_val = config[5] if config and len(config) > 5 and config[5] is not None else 0.08
    db_min = config[6] if config and len(config) > 6 and config[6] is not None else 1
    db_max = config[7] if config and len(config) > 7 and config[7] is not None else 3
    da_min = config[8] if config and len(config) > 8 and config[8] is not None else 1
    da_max = config[9] if config and len(config) > 9 and config[9] is not None else 3
    
    sleep_text = _("ai_sleep_text", start=config[2], end=config[3]) if (config and config[2]) else _("ai_sleep_off_text")
    prompt_short = config[4][:250] if config and len(config) > 4 and config[4] else ""
    text = _("ai_g_settings_text", sleep_text=sleep_text, typing=typing_val, db_min=db_min, db_max=db_max, da_min=da_min, da_max=da_max, prompt=prompt_short)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_g_sleep"), callback_data="ai_settings_sleep")],
        [InlineKeyboardButton(text=_("btn_ai_g_prompt"), callback_data="ai_g_set_prompt")],
        [InlineKeyboardButton(text=_("btn_ai_g_delays"), callback_data="ai_g_set_delays")],
        [InlineKeyboardButton(text=_("btn_ai_g_typing"), callback_data="ai_g_set_typing")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, text, kb)

@router.callback_query(F.data == "ai_g_set_prompt")
async def ask_g_prompt(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_g_view_prompt"), callback_data="ai_g_view_prompt")],
        [InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_global_settings")]
    ])
    await safe_edit(call.message, state, _("ai_g_prompt_request"), kb)
    await state.set_state(AISettingsFSM.global_prompt)

@router.callback_query(F.data == "ai_g_view_prompt")
async def view_g_prompt(call: types.CallbackQuery, state: FSMContext):
    config = await database.get_config()
    prompt = config[4] if config and len(config) > 4 and config[4] else _("ai_empty")
    await call.message.answer(_("ai_g_prompt_current", prompt=html.escape(prompt)), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data == "ai_g_set_delays")
async def ask_g_delays(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_global_settings")]])
    await safe_edit(call.message, state, _("ai_g_delays_request"), kb)
    await state.set_state(AISettingsFSM.global_delays)

@router.callback_query(F.data == "ai_g_set_typing")
async def ask_g_typing(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_global_settings")]])
    await safe_edit(call.message, state, _("ai_g_typing_request"), kb)
    await state.set_state(AISettingsFSM.global_typing)

@router.callback_query(F.data == "ai_settings_sleep")
async def settings_sleep(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    now_time = datetime.now().strftime('%H:%M')
    text = _("ai_sleep_request", time=now_time)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="ai_global_settings")]])
    await safe_edit(call.message, state, text, kb)
    await state.set_state(AISettingsFSM.sleep_hours)

@router.callback_query(F.data.startswith("skipwait_"))
async def skip_wait_timer(call: types.CallbackQuery):
    chat_id = int(call.data.split("_")[1])
    skip_video_timers.add(chat_id)
    await call.answer(_("ai_skip_video_alert"), show_alert=True)

@router.callback_query(F.data == "ai_toggle_global")
async def toggle_global_ai_cb(call: types.CallbackQuery):
    if hasattr(database, "toggle_global_ai"):
        await database.toggle_global_ai()
    elif hasattr(database, "toggle_global"):
        await database.toggle_global()
        
    config = await database.get_config()
    g_ai_active = config[12] if (config and len(config) > 12) else False
    g_ai_status = _("ai_g_status_on") if g_ai_active else _("ai_g_status_off")
    
    markup = call.message.reply_markup
    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "ai_toggle_global":
                    btn.text = _("btn_ai_mode", g_ai_status=g_ai_status)
        try:
            await call.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            pass
    await call.answer()

@router.callback_query(F.data == "ai_toggle_search_global")
async def toggle_search_global_cb(call: types.CallbackQuery):
    if hasattr(database, "toggle_global_search"):
        await database.toggle_global_search()
        
    config = await database.get_config()
    g_search_active = config[13] if (config and len(config) > 13) else True
    search_status = _("ai_search_on") if g_search_active else _("ai_search_off")
    
    markup = call.message.reply_markup
    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "ai_toggle_search_global":
                    btn.text = _("btn_ai_search_status", status=search_status)
        try:
            await call.message.edit_reply_markup(reply_markup=markup)
        except Exception:
            pass
    await call.answer()

@router.callback_query(F.data.regexp(r"^ai_toggle_search_-?\d+$"))
async def toggle_chat_search_cb(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[3])
    await database.toggle_chat_search(chat_id)
    text, kb = await plugins.generate_chat_menu_cb(chat_id)
    await safe_edit(call.message, state, text, kb)
    await call.answer(_("ai_c_search_changed_alert"))

@router.callback_query(F.data.regexp(r"^ai_toggle_-?\d+$"))
async def toggle_chat(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[2])
    await database.toggle_chat(chat_id)
    text, kb = await plugins.generate_chat_menu_cb(chat_id)
    await safe_edit(call.message, state, text, kb)

@router.callback_query(F.data.startswith("ai_ignore_"))
async def toggle_ignore(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[2])
    await database.toggle_chat_ignore(chat_id)
    text, kb = await plugins.generate_chat_menu_cb(chat_id)
    await safe_edit(call.message, state, text, kb)

@router.callback_query(F.data.startswith("ai_prompt_"))
async def ask_prompt(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    chat_id = int(call.data.split("_")[2])
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_view_prompt_chat"), callback_data=f"ai_view_prompt_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"chat_{chat_id}")]
    ])
    await safe_edit(call.message, state, _("ai_c_prompt_request"), kb)
    await state.set_state(AISettingsFSM.custom_prompt)

@router.callback_query(F.data.startswith("ai_view_prompt_"))
async def view_c_prompt(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[3])
    cfg = await database.get_chat_settings(chat_id)
    prompt = cfg[1] if (cfg and cfg[1]) else _("ai_not_set")
    await call.message.answer(_("ai_c_prompt_current", chat_id=chat_id, prompt=html.escape(prompt)), parse_mode="HTML")
    await call.answer()

@router.callback_query(F.data.startswith("ai_delays_"))
async def ask_delays(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    chat_id = int(call.data.split("_")[2])
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"chat_{chat_id}")]])
    await safe_edit(call.message, state, _("ai_c_delays_request"), kb)
    await state.set_state(AISettingsFSM.delays)

# ==========================================
# ОБРАБОТЧИКИ MESSAGES ДЛЯ FSM
# ==========================================
@router.message(AISettingsFSM.custom_reaction)
async def save_reaction(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    reaction_val = "👍"
    if message.entities:
        for ent in message.entities:
            if ent.type == "custom_emoji":
                reaction_val = ent.custom_emoji_id
                break
    if reaction_val == "👍":
        reaction_val = message.text.strip()
        
    await database.set_custom_reaction(reaction_val)
    await safe_edit(message, state, _("ai_g_reaction_saved"), kb)
    await state.set_state(None)

@router.message(AISettingsFSM.g_ignore_chance)
async def save_ign_g(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    try:
        val = int(message.text.strip())
        if 0 <= val <= 100:
            await database.set_global_h_ignore(val)
            await safe_edit(message, state, _("ai_ignore_saved"), kb)
        else: raise ValueError
    except:
        await safe_edit(message, state, _("ai_ignore_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.c_ignore_chance)
async def save_ign_c(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data=f"ai_human_chat_{chat_id}")]])
    try:
        text = message.text.lower().strip()
        val = -1 if text == "сброс" else int(text)
        if val == -1 or (0 <= val <= 100):
            await database.set_chat_h_ignore(chat_id, val)
            await safe_edit(message, state, _("ai_ignore_saved"), kb)
        else: raise ValueError
    except:
        await safe_edit(message, state, _("ai_ignore_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.g_h_smart_cfg)
async def save_cfg_smart_g(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    try:
        val = 0.05 if message.text.lower() == "сброс" else float(message.text.replace(",", "."))
        await database.set_global_h_smart_mul(val)
        await safe_edit(message, state, _("ai_h_cfg_smart_saved"), kb)
    except:
        await safe_edit(message, state, "❌ Ошибка формата.", kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.c_h_smart_cfg)
async def save_cfg_smart_c(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data=f"ai_human_chat_{chat_id}")]])
    try:
        val = None if message.text.lower() == "сброс" else float(message.text.replace(",", "."))
        await database.set_chat_h_smart_mul(chat_id, val)
        await safe_edit(message, state, _("ai_h_cfg_smart_saved"), kb)
    except:
        await safe_edit(message, state, "❌ Ошибка формата.", kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.g_h_typing_cfg)
async def save_cfg_typ_g(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    try:
        if message.text.lower() == "сброс":
            await database.set_global_h_typing_cfg(1.5, 3.5, 0.5, 2.0)
        else:
            tmin, tmax, pmin, pmax = map(float, message.text.replace(",", ".").split())
            await database.set_global_h_typing_cfg(tmin, tmax, pmin, pmax)
        await safe_edit(message, state, _("ai_h_cfg_typing_saved"), kb)
    except:
        await safe_edit(message, state, "❌ Ошибка формата.", kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.c_h_typing_cfg)
async def save_cfg_typ_c(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data=f"ai_human_chat_{chat_id}")]])
    try:
        if message.text.lower() == "сброс":
            await database.set_chat_h_typing_cfg(chat_id, None, None, None, None)
        else:
            tmin, tmax, pmin, pmax = map(float, message.text.replace(",", ".").split())
            await database.set_chat_h_typing_cfg(chat_id, tmin, tmax, pmin, pmax)
        await safe_edit(message, state, _("ai_h_cfg_typing_saved"), kb)
    except:
        await safe_edit(message, state, "❌ Ошибка формата.", kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.global_prompt)
async def save_g_prompt(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    prompt_text = ""
    try:
        bot = plugins.bot
        if message.document:
            buffer = io.BytesIO()
            await bot.download(message.document, destination=buffer)
            raw_data = buffer.getvalue()
            try: prompt_text = raw_data.decode('utf-8')
            except UnicodeDecodeError: prompt_text = raw_data.decode('cp1251', errors='ignore')
        elif message.text:
            prompt_text = message.text
        else:
            return await safe_edit(message, state, _("ai_g_prompt_error_format"), kb)
        await database.set_global_prompt(prompt_text)
        await safe_edit(message, state, _("ai_g_prompt_saved"), kb)
    except Exception as e:
        await safe_edit(message, state, _("ai_general_error", e=e), kb)
    finally:
        await state.set_state(None)

@router.message(AISettingsFSM.global_delays)
async def save_g_delays(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    try:
        db_m, db_mx, da_m, da_mx = map(int, message.text.split())
        await database.set_global_delays(db_m, db_mx, da_m, da_mx)
        await safe_edit(message, state, _("ai_g_delays_saved"), kb)
    except:
        await safe_edit(message, state, _("ai_g_delays_error"), kb)
    finally:
        await state.set_state(None)

@router.message(AISettingsFSM.global_typing)
async def save_g_typing(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    try:
        speed = float(message.text.replace(",", "."))
        await database.set_global_typing_speed(speed)
        await safe_edit(message, state, _("ai_g_typing_saved"), kb)
    except:
        await safe_edit(message, state, _("ai_g_typing_error"), kb)
    finally:
        await state.set_state(None)

@router.message(AISettingsFSM.sleep_hours)
async def save_sleep_hours(message: types.Message, state: FSMContext):
    try: await message.delete() 
    except: pass
    text = message.text.lower().strip()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    try:
        if text == "выкл":
            await database.set_sleep_hours(None, None)
            await safe_edit(message, state, _("ai_sleep_disabled"), kb)
        else:
            try:
                start, end = message.text.split()
                datetime.strptime(start, "%H:%M")
                datetime.strptime(end, "%H:%M")
                await database.set_sleep_hours(start, end)
                await safe_edit(message, state, _("ai_sleep_saved", start=start, end=end), kb)
            except:
                await safe_edit(message, state, _("ai_sleep_error"), kb)
    finally:
        await state.set_state(None)

@router.message(AISettingsFSM.custom_prompt)
async def save_prompt(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_chat"), callback_data=f"chat_{chat_id}")]])
    prompt_text = ""
    try:
        bot = plugins.bot
        if message.document:
            buffer = io.BytesIO()
            await bot.download(message.document, destination=buffer)
            raw_data = buffer.getvalue()
            try: prompt_text = raw_data.decode('utf-8')
            except UnicodeDecodeError: prompt_text = raw_data.decode('cp1251', errors='ignore')
        elif message.text:
            prompt_text = message.text
        final_prompt = None if prompt_text.lower() == 'сброс' else prompt_text
        await database.set_custom_prompt(chat_id, final_prompt)
        await safe_edit(message, state, _("ai_c_prompt_saved"), kb)
    except Exception as e:
        await safe_edit(message, state, _("ai_c_prompt_error", e=e), kb)
    finally:
        await state.set_state(None)

@router.message(AISettingsFSM.delays)
async def save_delays(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_chat"), callback_data=f"chat_{chat_id}")]])
    try:
        if message.text.lower() == "сброс":
            await database.set_chat_delays(chat_id, None, None, None, None)
            await safe_edit(message, state, _("ai_c_delays_reset"), kb)
            return
        db_min, db_max, da_min, da_max = map(int, message.text.split())
        await database.set_chat_delays(chat_id, db_min, db_max, da_min, da_max)
        await safe_edit(message, state, _("ai_c_delays_saved"), kb)
    except:
        await safe_edit(message, state, _("ai_c_delays_error"), kb)
    finally:
        await state.set_state(None)

# ==========================================
# ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ (ОПЕЧАТКИ И ПЕЧАТЬ)
# ==========================================
def introduce_typo(text):
    if len(text) < 5: return text
    words = text.split()
    candidates = [i for i, w in enumerate(words) if len(w) >= 5 and w.isalpha()]
    if not candidates: return text
    
    idx = random.choice(candidates)
    word = list(words[idx])
    char_idx = random.randint(1, len(word) - 2)
    
    if word[char_idx].isupper() or word[char_idx+1].isupper():
        return text
        
    word[char_idx], word[char_idx+1] = word[char_idx+1], word[char_idx]
    words[idx] = "".join(word)
    return " ".join(words)

async def simulate_human_typing(client, chat_id, total_time, is_human_mode, t_min=1.5, t_max=3.5, p_min=0.5, p_max=2.0):
    if not is_human_mode or total_time < 3.0:
        await simulate_typing(client, chat_id, total_time)
        return
    
    elapsed = 0
    while elapsed < total_time:
        t_type = min(random.uniform(t_min, t_max), total_time - elapsed)
        try: await client.send_chat_action(chat_id, enums.ChatAction.TYPING)
        except: pass
        await asyncio.sleep(t_type)
        elapsed += t_type
        
        if elapsed >= total_time: break
        
        t_pause = min(random.uniform(p_min, p_max), total_time - elapsed)
        try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
        except: pass
        await asyncio.sleep(t_pause)
        elapsed += t_pause

# ==========================================
# PYROGRAM: ГЛАВНЫЙ ПРОЦЕСС АВТООТВЕТА
# ==========================================
def register_userbot(app: Client):
    async def process_reply(client, message):
        media_path = None
        try:
            chat_id = message.chat.id
            if message.date:
                now = datetime.now(timezone.utc)
                msg_date = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
                if (now - msg_date).total_seconds() > 8 * 3600:
                    logging.info(_("ai_log_skip_old", chat_id=chat_id))
                    return

            config_db = await database.get_config()
            if not config_db: return

            if config_db:
                sleep_start, sleep_end = config_db[2], config_db[3]
                if sleep_start and sleep_end:
                    now_str = datetime.now().strftime("%H:%M")
                    if sleep_start <= sleep_end:
                        if sleep_start <= now_str <= sleep_end: 
                            logging.info(_("ai_log_skip_sleep", chat_id=chat_id))
                            return
                    else:
                        if now_str >= sleep_start or now_str <= sleep_end: 
                            logging.info(_("ai_log_skip_sleep", chat_id=chat_id))
                            return

            is_global_ai = config_db[12] if (config_db and len(config_db) > 12) else False
            chat_cfg = await database.get_chat_settings(chat_id)
            chat_is_active = chat_cfg[0] if chat_cfg else False
            is_ignored = chat_cfg[6] if (chat_cfg and len(chat_cfg) > 6) else False
            
            if not is_global_ai and not chat_is_active: 
                logging.info(_("ai_log_skip_disabled", chat_id=chat_id))
                return 
            if is_global_ai and not chat_is_active and is_ignored: 
                logging.info(_("ai_log_skip_ignored", chat_id=chat_id))
                return
            
            # --- Вручную собираем глобальные настройки (обход utils.py) ---
            typing_speed = config_db[5] if len(config_db) > 5 and config_db[5] is not None else 0.08
            glob_db_min = config_db[6] if len(config_db) > 6 and config_db[6] is not None else 1
            glob_db_max = config_db[7] if len(config_db) > 7 and config_db[7] is not None else 3
            glob_da_min = config_db[8] if len(config_db) > 8 and config_db[8] is not None else 1
            glob_da_max = config_db[9] if len(config_db) > 9 and config_db[9] is not None else 3
            
            g_reaction = config_db[14] if len(config_db) > 14 and config_db[14] else "👍"
            g_h_typing = bool(config_db[15]) if len(config_db) > 15 else True
            g_h_ignore = config_db[16] if len(config_db) > 16 else 10
            g_h_smart = bool(config_db[17]) if len(config_db) > 17 else True
            g_s_mul = config_db[18] if len(config_db) > 18 and config_db[18] is not None else 0.05
            g_tmin = config_db[19] if len(config_db) > 19 and config_db[19] is not None else 1.5
            g_tmax = config_db[20] if len(config_db) > 20 and config_db[20] is not None else 3.5
            g_pmin = config_db[21] if len(config_db) > 21 and config_db[21] is not None else 0.5
            g_pmax = config_db[22] if len(config_db) > 22 and config_db[22] is not None else 2.0

            if chat_cfg:
                c_h_typing = chat_cfg[8] if chat_cfg[8] is not None else 2
                c_h_ignore = chat_cfg[9] if chat_cfg[9] is not None else -1
                c_h_smart = chat_cfg[10] if chat_cfg[10] is not None else 2
                c_s_mul = chat_cfg[11] if len(chat_cfg) > 11 and chat_cfg[11] is not None else g_s_mul
                c_tmin = chat_cfg[12] if len(chat_cfg) > 12 and chat_cfg[12] is not None else g_tmin
                c_tmax = chat_cfg[13] if len(chat_cfg) > 13 and chat_cfg[13] is not None else g_tmax
                c_pmin = chat_cfg[14] if len(chat_cfg) > 14 and chat_cfg[14] is not None else g_pmin
                c_pmax = chat_cfg[15] if len(chat_cfg) > 15 and chat_cfg[15] is not None else g_pmax
            else:
                c_h_typing, c_h_ignore, c_h_smart = 2, -1, 2
                c_s_mul, c_tmin, c_tmax, c_pmin, c_pmax = g_s_mul, g_tmin, g_tmax, g_pmin, g_pmax
                
            use_h_typing = g_h_typing if c_h_typing == 2 else bool(c_h_typing)
            use_h_smart = g_h_smart if c_h_smart == 2 else bool(c_h_smart)
            use_h_ignore = g_h_ignore if c_h_ignore == -1 else c_h_ignore
            
            if is_global_ai:
                search_enabled = config_db[13] if (config_db and len(config_db) > 13) else True
            else:
                search_enabled = chat_cfg[7] if (chat_cfg and len(chat_cfg) > 7) else True
                
            # --- Вручную собираем промпт (обход utils.py) ---
            global_prompt = config_db[4] if len(config_db) > 4 and config_db[4] else ""
            c_prompt = chat_cfg[1] if chat_cfg and len(chat_cfg) > 1 and chat_cfg[1] else ""
            final_prompt = global_prompt
            if c_prompt:
                final_prompt += f"\n\n[ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА И КОНТЕКСТ ДЛЯ ДАННОГО ДИАЛОГА]:\n{c_prompt}"
            
            text_to_search = message.text or message.caption or ""
            
            yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', text_to_search)
            all_links = re.findall(r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)', text_to_search)
            
            non_yt_links = [l for l in all_links if not any(yt in l for yt in yt_links)]
            has_non_yt_link = len(non_yt_links) > 0
            
            if has_non_yt_link:
                search_enabled = True
                link_rule = "\n\n[СИСТЕМНОЕ ПРАВИЛО]: В сообщении собеседника есть внешняя ссылка. ТЕБЕ ДОСТУПЕН GOOGLE SEARCH. ОБЯЗАТЕЛЬНО используй поиск, чтобы изучить информацию по этой ссылке. СТРОГИЙ ЗАПРЕТ НА ГАЛЛЮЦИНАЦИИ: отвечай ТОЛЬКО на основе реального текста со страницы. НИЧЕГО НЕ ВЫДУМЫВАЙ (никаких интерфейсов, красных кнопок, меню и т.д.). Не объясняй собеседнику, что такое ссылка."
                final_prompt = (final_prompt + link_rule) if final_prompt else link_rule

            if search_enabled:
                search_rule = f"\n\n{_('ai_prompt_rule_search')}"
                final_prompt = (final_prompt + search_rule) if final_prompt else search_rule
                log_msg = "ON (Форсировано ссылкой)" if has_non_yt_link else "ON"
                logging.info(_("ai_log_search_state", chat_id=chat_id, state=log_msg))
            else:
                logging.info(_("ai_log_search_state", chat_id=chat_id, state="OFF"))
            
            c_db_min = chat_cfg[2] if (chat_cfg and chat_cfg[2] is not None) else glob_db_min
            c_db_max = chat_cfg[3] if (chat_cfg and chat_cfg[3] is not None) else glob_db_max
            c_da_min = chat_cfg[4] if (chat_cfg and chat_cfg[4] is not None) else glob_da_min
            c_da_max = chat_cfg[5] if (chat_cfg and chat_cfg[5] is not None) else glob_da_max

            delay_before = random.randint(c_db_min, c_db_max)
            logging.info(_("ai_log_wait_before", chat_id=chat_id, delay_before=delay_before))
            await asyncio.sleep(delay_before)
            
            if use_h_smart:
                smart_delay = 0
                if message.voice: smart_delay = getattr(message.voice, 'duration', 5)
                elif message.video_note: smart_delay = getattr(message.video_note, 'duration', 5)
                elif text_to_search: smart_delay = len(text_to_search) * c_s_mul
                
                smart_delay = min(smart_delay, 30)
                if smart_delay > 0:
                    logging.info(f"⏳ Умное чтение для чата {chat_id}: ждем еще {smart_delay:.1f} сек...")
                    await asyncio.sleep(smart_delay)
            
            try:
                await client.read_chat_history(chat_id)
                if message.voice or message.video_note:
                    await client.invoke(functions.messages.ReadMessageContents(id=[message.id]))
            except: pass
            
            is_question = bool(re.search(r'\?|как|почему|зачем|что|где|когда|чей|кого|кому', text_to_search.lower()))
            if not is_question and use_h_ignore > 0:
                if random.randint(1, 100) <= use_h_ignore:
                    logging.info(_("ai_log_ignored", chat_id=chat_id, chance=use_h_ignore))
                    if random.random() < 0.5:
                        emoji_to_send = int(g_reaction) if g_reaction.isdigit() else g_reaction
                        try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=emoji_to_send)
                        except: pass
                    return
            
            media_ext = ""
            if message.photo: media_ext = ".jpg"
            elif message.voice: media_ext = ".ogg"
            elif message.video or message.video_note: media_ext = ".mp4"
            
            if media_ext:
                media_path = await message.download(file_name=f"data/{message.id}_media{media_ext}")

            transcript = ""
            if media_path and media_path.endswith((".ogg", ".oga", ".mp4", ".mov", ".avi", ".mp3")):
                transcript = await transcribe_media(media_path)

            yt_context = ""
            long_video_rule = ""
            if text_to_search:
                if yt_links:
                    try:
                        target_url = yt_links[-1] 
                        video_duration, yt_context = await asyncio.to_thread(fetch_youtube_data_sync, target_url)
                        
                        if video_duration > 1800:
                            if "Субтитры:" in yt_context:
                                yt_context = yt_context.split("Субтитры:")[0].strip()
                            elif "Subtitles:" in yt_context:
                                yt_context = yt_context.split("Subtitles:")[0].strip()
                            
                            long_video_rule = "\n\n[СИСТЕМНОЕ ПРАВИЛО]: Пользователь прислал длинное YouTube видео (более 30 минут). В контексте есть только его название и описание. Кратко отреагируй на суть видео (о чем оно), но обязательно скажи своими словами, что оно слишком длинное и ты посмотришь его полностью чуть позже."
                        
                        elif video_duration > 0:
                            watch_time = min(video_duration, 900) 
                            try: await client.invoke(functions.account.UpdateStatus(offline=True))
                            except: pass
                            skip_video_timers.discard(chat_id)
                            for _wt in range(int(watch_time)):
                                if chat_id in skip_video_timers:
                                    skip_video_timers.discard(chat_id)
                                    break
                                await asyncio.sleep(1)
                    except: pass

            delay_after = random.randint(c_da_min, c_da_max)
            logging.info(_("ai_log_wait_after", chat_id=chat_id, delay_after=delay_after))
            await asyncio.sleep(delay_after)

            chat_name = message.from_user.first_name if message.from_user else (message.chat.title or _("other_sender"))

            history = []
            async for msg in client.get_chat_history(chat_id, limit=12):
                if msg.date and message.date:
                    msg_date_safe = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                    curr_date_safe = message.date if message.date.tzinfo else message.date.replace(tzinfo=timezone.utc)
                    if (curr_date_safe - msg_date_safe).total_seconds() > 86400:
                        if msg.id != message.id: break 
                sender = _("me_sender") if (msg.from_user and msg.from_user.is_self) else chat_name
                text = msg.text or msg.caption
                if not text:
                    if msg.voice: text = _("ai_msg_voice") + (f": {transcript}" if msg.id == message.id and transcript else "")
                    elif msg.video_note: text = _("ai_msg_video_note") + (f": {transcript}" if msg.id == message.id and transcript else "")
                    elif msg.photo: text = _("ai_msg_photo")
                    elif msg.video: text = _("ai_msg_video")
                    elif msg.sticker: text = _("ai_msg_sticker", emoji=msg.sticker.emoji if hasattr(msg.sticker, 'emoji') and msg.sticker.emoji else "")
                    else: text = _("ai_msg_file")
                
                history.append(f"{sender}: {text}")
                
            history.reverse()
            
            if history:
                history[-1] = f">>> [ТЕКУЩЕЕ СООБЩЕНИЕ]: {history[-1]}"
                
            history_join = "\n".join(history)
            
            history_str = f"[КОНТЕКСТ ДИАЛОГА. Твои предыдущие ответы помечены как '{_('me_sender')}', сообщения собеседника как '{chat_name}']. Отвечай ТОЛЬКО от лица '{_('me_sender')}'.\n\n{history_join}"
            
            if message.reply_to_message:
                orig = message.reply_to_message
                orig_sender = _("me_sender") if (orig.from_user and orig.from_user.is_self) else chat_name
                orig_text = orig.text or orig.caption or _("media_file_placeholder")
                history_str += _("ai_reply_context", orig_sender=orig_sender, orig_text=orig_text)
            if media_path and not transcript: 
                history_str += _("ai_media_context")
            if yt_context:
                history_str += _("ai_yt_context", yt_context=yt_context)

            if long_video_rule:
                final_prompt = (final_prompt + long_video_rule) if final_prompt else long_video_rule

            reply = await generate_ai_response(history_str, media_path, custom_prompt=final_prompt, search_enabled=search_enabled)

            if reply and reply != "⏳":
                if "[LIKE]" in reply.upper():
                    try: 
                        emoji_to_send = int(g_reaction) if g_reaction.isdigit() else g_reaction
                        await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=emoji_to_send)
                    except: pass
                    return

                parts = []
                for p in reply.split('\n'):
                    p = p.strip()
                    if p:
                        while len(p) > 4000:
                            parts.append(p[:4000])
                            p = p[4000:]
                        if p: parts.append(p)

                use_reply = random.random() < 0.25 
                for i, part in enumerate(parts):
                    typing_time = min(len(part) * float(typing_speed), 10.0) 
                    
                    await simulate_human_typing(client, chat_id, typing_time, use_h_typing, c_tmin, c_tmax, c_pmin, c_pmax)
                    
                    use_typo = random.random() < 0.05
                    final_part = introduce_typo(part) if use_typo else part
                    
                    reply_id = message.id if (i == 0 and use_reply) else None
                    sent_msg = await client.send_message(chat_id, final_part, reply_to_message_id=reply_id)
                    
                    if use_typo and final_part != part:
                        await asyncio.sleep(random.uniform(1.5, 3.0))
                        try: await sent_msg.edit_text(part)
                        except: pass
                    
                    await asyncio.sleep(random.uniform(0.5, 2.0)) 

        except asyncio.CancelledError: pass
        except Exception as e: logging.error(_("ai_log_chat_error", chat_id=message.chat.id, e=e))
        finally:
            if media_path and os.path.exists(media_path):
                try: os.remove(media_path)
                except: pass
            if active_reply_tasks.get(message.chat.id) == asyncio.current_task():
                del active_reply_tasks[message.chat.id]

    @app.on_message(filters.private & ~filters.me)
    async def ai_auto_reply(client, message):
        if message.chat.type != ChatType.PRIVATE: return
        if message.from_user and message.from_user.is_bot: return
        if message.from_user and message.from_user.id == 777000: return
        chat_id = message.chat.id
        if chat_id in active_reply_tasks:
            active_reply_tasks[chat_id].cancel()
        task = asyncio.create_task(process_reply(client, message))
        active_reply_tasks[chat_id] = task
