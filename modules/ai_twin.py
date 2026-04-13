import os
import io
import re
import random
import asyncio
import logging
import html
import traceback
from datetime import datetime, timezone

from aiogram import Router, F, types, Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.orm.attributes import flag_modified

from pyrogram import Client, filters, enums
from pyrogram.enums import ChatType
from pyrogram.raw import functions
from pyrogram.types import ReplyParameters

from core.db import AsyncSessionLocal, CoreRepository
from core.services import generate_ai_response, transcribe_media, get_youtube_context, build_dialog_context
from core.utils import safe_edit, plugins, simulate_human_typing, introduce_typo
from core.config import _

router = Router()
skip_video_timers = set()
active_reply_tasks = {}

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

async def _get_g_cfg():
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        s = c.module_settings or {}
        a = s.get("ai_engine", {})
        
        s_start = getattr(c, "sleep_start", None)
        if str(s_start).lower() in ["none", "null", ""]: s_start = None
        s_end = getattr(c, "sleep_end", None)
        if str(s_end).lower() in ["none", "null", ""]: s_end = None
        
        return {
            "is_active": getattr(c, "global_ai_active", False),
            "search_enabled": getattr(c, "google_search", True),
            "prompt": getattr(c, "global_prompt", ""),
            "typing_speed": getattr(c, "typing_speed", 0.08),
            "sleep_start": s_start,
            "sleep_end": s_end,
            "db_min": a.get("db_min", 1),
            "db_max": a.get("db_max", 3),
            "da_min": a.get("da_min", 1),
            "da_max": a.get("da_max", 3),
            "reaction": a.get("reaction", "👍"),
            "h_typing": a.get("h_typing", True),
            "h_ignore": a.get("h_ignore", 10),
            "h_smart": a.get("h_smart", True),
            "s_mul": a.get("s_mul", 0.05),
            "tmin": a.get("tmin", 1.5),
            "tmax": a.get("tmax", 3.5),
            "pmin": a.get("pmin", 0.5),
            "pmax": a.get("pmax", 2.0),
            "ai_debug": a.get("ai_debug", True) 
        }

async def _upd_g_cfg(db_fields=None, **kwargs):
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        if db_fields:
            for k, v in db_fields.items():
                setattr(c, k, v)
        s = c.module_settings or {}
        a = dict(s.get("ai_engine", {}))
        a.update(kwargs)
        new_settings = dict(s)
        new_settings["ai_engine"] = a
        c.module_settings = new_settings
        flag_modified(c, "module_settings")
        await session.commit()

async def _get_c_cfg(chat_id: int):
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_chat_config(chat_id)
        m = c.module_data or {}
        a = m.get("ai_engine", {})
        return {
            "is_active": getattr(c, "is_active", False),
            "prompt": getattr(c, "custom_prompt", None),
            "db_min": getattr(c, "delay_before_min", None),
            "db_max": getattr(c, "delay_before_max", None),
            "da_min": getattr(c, "delay_after_min", None),
            "da_max": getattr(c, "delay_after_max", None),
            "is_ignored": getattr(c, "is_ignored", False),
            "h_typing": a.get("h_typing", 2),
            "h_ignore": a.get("h_ignore", -1),
            "h_smart": a.get("h_smart", 2),
            "s_mul": a.get("s_mul", None),
            "tmin": a.get("tmin", None),
            "tmax": a.get("tmax", None),
            "pmin": a.get("pmin", None),
            "pmax": a.get("pmax", None),
            "search_enabled": a.get("search_enabled", True)
        }

async def _upd_c_cfg(chat_id: int, db_fields=None, **kwargs):
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_chat_config(chat_id)
        if db_fields:
            for k, v in db_fields.items():
                setattr(c, k, v)
        m = c.module_data or {}
        a = dict(m.get("ai_engine", {}))
        a.update(kwargs)
        new_settings = dict(m)
        new_settings["ai_engine"] = a
        c.module_data = new_settings
        flag_modified(c, "module_data")
        await session.commit()

async def generate_media_description(media_path: str) -> str:
    desc_prompt = _("ai_media_desc_prompt")
    logging.info("="*50)
    logging.info(_("log_llm_req_media_desc"))
    logging.info(_("log_prompt", prompt=desc_prompt))
    logging.info(_("log_attached_file", path=media_path))
    try:
        res = await generate_ai_response(desc_prompt, media_path, custom_prompt="", search_enabled=False)
        if not res or res == "⏳":
            logging.warning(_("log_api_overload_desc"))
            return _("ai_media_desc_unavailable")
        logging.info(_("log_llm_res_desc", res=res))
        return res
    except Exception as e:
        logging.error(_("log_desc_gen_error", e=str(e)))
        return _("ai_media_desc_failed")

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_ai_twin_settings"), callback_data="ai_global_settings")]]

@router.callback_query(F.data == "ai_global_settings")
async def global_settings_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_g_cfg()
    
    g_ai_status = _("status_on") if cfg["is_active"] else _("status_off")
    search_status = _("status_on") if cfg["search_enabled"] else _("status_off")
    sleep_text = _("ai_sleep_text", start=cfg["sleep_start"], end=cfg["sleep_end"]) if cfg["sleep_start"] else _("ai_sleep_off_text")
    prompt_short = (cfg["prompt"] or "")[:250]
    debug_status = _("status_on") if cfg["ai_debug"] else _("status_off")

    text = _("ai_g_settings_text", sleep_text=sleep_text, typing=cfg["typing_speed"], db_min=cfg["db_min"], db_max=cfg["db_max"], da_min=cfg["da_min"], da_max=cfg["da_max"], prompt=prompt_short)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_mode", g_ai_status=g_ai_status), callback_data="ai_toggle_global"),
         InlineKeyboardButton(text=_("btn_ai_search_status", status=search_status), callback_data="ai_toggle_search_global")],
        [InlineKeyboardButton(text=_("btn_ai_human_settings"), callback_data="ai_human_settings_global")],
        [InlineKeyboardButton(text=_("btn_ai_g_sleep"), callback_data="ai_settings_sleep"),
         InlineKeyboardButton(text=_("btn_ai_g_delays"), callback_data="ai_g_set_delays")],
        [InlineKeyboardButton(text=_("btn_ai_g_typing"), callback_data="ai_g_set_typing"),
         InlineKeyboardButton(text=_("btn_ai_debug", status=debug_status), callback_data="ai_toggle_debug")],
        [InlineKeyboardButton(text=_("btn_ai_g_prompt"), callback_data="ai_g_set_prompt")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])
    await safe_edit(call.message, state, text, kb)

async def get_chat_menu_buttons(chat_id: int):
    cfg = await _get_c_cfg(chat_id)
    status_text = _("status_on") if cfg["is_active"] else _("status_off")
    return [[InlineKeyboardButton(text=_("btn_ai_chat_menu", status=status_text), callback_data=f"ai_chat_menu_{chat_id}")]]

@router.callback_query(F.data.startswith("ai_chat_menu_"))
async def chat_settings_menu(call: types.CallbackQuery, state: FSMContext, chat_id: int = None):
    if chat_id is None:
        chat_id = int(call.data.split("_")[3])
        
    await state.update_data(menu_msg_id=call.message.message_id, chat_id=chat_id)
    cfg = await _get_c_cfg(chat_id)
    g_cfg = await _get_g_cfg()
    
    status_text = _("ai_chat_status_on") if cfg["is_active"] else _("ai_chat_status_off")
    prompt = _("ai_prompt_custom") if cfg["prompt"] else _("ai_prompt_global_only")
    ignore_btn_text = _("ai_ignore_on") if cfg["is_ignored"] else _("ai_ignore_off")
    c_search_status = _("status_on") if cfg["search_enabled"] else _("status_off")
    
    c_db_min = cfg["db_min"] if cfg["db_min"] is not None else g_cfg["db_min"]
    c_db_max = cfg["db_max"] if cfg["db_max"] is not None else g_cfg["db_max"]
    c_da_min = cfg["da_min"] if cfg["da_min"] is not None else g_cfg["da_min"]
    c_da_max = cfg["da_max"] if cfg["da_max"] is not None else g_cfg["da_max"]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=status_text, callback_data=f"ai_toggle_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_search_status", status=c_search_status), callback_data=f"ai_toggle_search_{chat_id}")],
        [InlineKeyboardButton(text=ignore_btn_text, callback_data=f"ai_ignore_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_prompt", prompt=prompt), callback_data=f"ai_prompt_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_delays", c_db_min=c_db_min, c_db_max=c_db_max, c_da_min=c_da_min, c_da_max=c_da_max), callback_data=f"ai_delays_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_human_settings"), callback_data=f"ai_human_chat_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_ai_skip_video"), callback_data=f"skipwait_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]
    ])
    await safe_edit(call.message, state, _("ai_chat_menu_text", chat_id=chat_id), kb)

@router.callback_query(F.data.regexp(r"^ai_toggle_search_-?\d+$"))
async def toggle_chat_search_cb(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[3])
    cfg = await _get_c_cfg(chat_id)
    await _upd_c_cfg(chat_id, search_enabled=not cfg["search_enabled"])
    await chat_settings_menu(call, state, chat_id=chat_id)
    await call.answer(_("ai_c_search_changed_alert"))

@router.callback_query(F.data == "ai_toggle_search_global")
async def toggle_search_global_cb(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_g_cfg()
    new_state = not cfg["search_enabled"]
    await _upd_g_cfg(db_fields={"google_search": new_state})
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        await repo.force_update_all_chats_search(new_state)
    await global_settings_menu(call, state)
    try: await call.answer(_("ai_g_search_applied_alert"), show_alert=True)
    except: pass

@router.callback_query(F.data.regexp(r"^ai_toggle_-?\d+$"))
async def toggle_chat(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[2])
    cfg = await _get_c_cfg(chat_id)
    await _upd_c_cfg(chat_id, db_fields={"is_active": not cfg["is_active"]})
    await chat_settings_menu(call, state, chat_id=chat_id)
    try: await call.answer()
    except: pass

@router.callback_query(F.data.startswith("ai_ignore_"))
async def toggle_ignore(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[2])
    cfg = await _get_c_cfg(chat_id)
    await _upd_c_cfg(chat_id, db_fields={"is_ignored": not cfg["is_ignored"]})
    await chat_settings_menu(call, state, chat_id=chat_id)
    try: await call.answer()
    except: pass

@router.callback_query(F.data == "ai_human_settings_global")
async def human_settings_global(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_g_cfg()
    t_status = _("status_on") if cfg["h_typing"] else _("status_off")
    s_status = _("status_on") if cfg["h_smart"] else _("status_off")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_g_reaction"), callback_data="ai_h_set_reaction")],
        [InlineKeyboardButton(text=_("btn_h_typing", status=t_status), callback_data="ai_h_toggle_typing_g"), InlineKeyboardButton(text="⚙️", callback_data="ai_h_cfg_typing_g")],
        [InlineKeyboardButton(text=_("btn_h_smart_read", status=s_status), callback_data="ai_h_toggle_smart_g"), InlineKeyboardButton(text="⚙️", callback_data="ai_h_cfg_smart_g")],
        [InlineKeyboardButton(text=_("btn_h_ignore", chance=cfg["h_ignore"]), callback_data="ai_h_set_ignore_g")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="ai_global_settings")]
    ])
    await safe_edit(call.message, state, _("ai_human_g_text"), kb)

@router.callback_query(F.data.startswith("ai_human_chat_"))
async def human_settings_chat(call: types.CallbackQuery, state: FSMContext, chat_id: int = None):
    if chat_id is None: chat_id = int(call.data.split("_")[3])
    await state.update_data(menu_msg_id=call.message.message_id, chat_id=chat_id)
    
    cfg = await _get_c_cfg(chat_id)
    t_status = _("status_global") if cfg["h_typing"] == 2 else (_("status_on") if cfg["h_typing"] == 1 else _("status_off"))
    s_status = _("status_global") if cfg["h_smart"] == 2 else (_("status_on") if cfg["h_smart"] == 1 else _("status_off"))
    i_status = _("status_global") if cfg["h_ignore"] == -1 else cfg["h_ignore"]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_h_typing", status=t_status), callback_data=f"ai_h_toggle_typing_c_{chat_id}"), InlineKeyboardButton(text="⚙️", callback_data=f"ai_h_cfg_typing_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_h_smart_read", status=s_status), callback_data=f"ai_h_toggle_smart_c_{chat_id}"), InlineKeyboardButton(text="⚙️", callback_data=f"ai_h_cfg_smart_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_h_ignore", chance=i_status), callback_data=f"ai_h_set_ignore_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"ai_chat_menu_{chat_id}")]
    ])
    await safe_edit(call.message, state, _("ai_human_c_text"), kb)

@router.callback_query(F.data == "ai_toggle_global")
async def toggle_global_ai_cb(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_g_cfg()
    await _upd_g_cfg(db_fields={"global_ai_active": not cfg["is_active"]})
    await global_settings_menu(call, state)
    try: await call.answer()
    except: pass
    
@router.callback_query(F.data == "ai_toggle_debug")
async def toggle_debug_cb(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_g_cfg()
    await _upd_g_cfg(ai_debug=not cfg["ai_debug"])
    await global_settings_menu(call, state)
    try: await call.answer()
    except: pass

@router.callback_query(F.data == "ai_h_toggle_typing_g")
async def toggle_typ_g(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_g_cfg()
    await _upd_g_cfg(h_typing=not cfg["h_typing"])
    await human_settings_global(call, state)
    try: await call.answer()
    except: pass

@router.callback_query(F.data == "ai_h_toggle_smart_g")
async def toggle_smart_g(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_g_cfg()
    await _upd_g_cfg(h_smart=not cfg["h_smart"])
    await human_settings_global(call, state)
    try: await call.answer()
    except: pass

@router.callback_query(F.data.startswith("ai_h_toggle_typing_c_"))
async def toggle_typ_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    cfg = await _get_c_cfg(chat_id)
    nxt = 1 if cfg["h_typing"] == 2 else (0 if cfg["h_typing"] == 1 else 2)
    await _upd_c_cfg(chat_id, h_typing=nxt)
    await human_settings_chat(call, state, chat_id=chat_id)
    try: await call.answer()
    except: pass

@router.callback_query(F.data.startswith("ai_h_toggle_smart_c_"))
async def toggle_smart_c(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[5])
    cfg = await _get_c_cfg(chat_id)
    nxt = 1 if cfg["h_smart"] == 2 else (0 if cfg["h_smart"] == 1 else 2)
    await _upd_c_cfg(chat_id, h_smart=nxt)
    await human_settings_chat(call, state, chat_id=chat_id)
    try: await call.answer()
    except: pass

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

@router.callback_query(F.data == "ai_g_set_prompt")
async def ask_g_prompt(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    cfg = await _get_g_cfg()
    curr_prompt = html.escape(cfg["prompt"]) if cfg["prompt"] else _("ai_empty")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_cancel"), callback_data="ai_global_settings")]
    ])
    await safe_edit(call.message, state, _("ai_g_prompt_request", prompt=curr_prompt), kb)
    await state.set_state(AISettingsFSM.global_prompt)

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

@router.callback_query(F.data.startswith("ai_prompt_"))
async def ask_prompt(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    chat_id = int(call.data.split("_")[2])
    await state.update_data(chat_id=chat_id)
    
    cfg = await _get_c_cfg(chat_id)
    curr_prompt = html.escape(cfg["prompt"]) if cfg["prompt"] else _("ai_not_set")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"ai_chat_menu_{chat_id}")]
    ])
    await safe_edit(call.message, state, _("ai_c_prompt_request", prompt=curr_prompt), kb)
    await state.set_state(AISettingsFSM.custom_prompt)

@router.callback_query(F.data.startswith("ai_delays_"))
async def ask_delays(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    chat_id = int(call.data.split("_")[2])
    await state.update_data(chat_id=chat_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data=f"ai_chat_menu_{chat_id}")]])
    await safe_edit(call.message, state, _("ai_c_delays_request"), kb)
    await state.set_state(AISettingsFSM.delays)

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
    if reaction_val == "👍": reaction_val = message.text.strip()
    await _upd_g_cfg(reaction=reaction_val)
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
            await _upd_g_cfg(h_ignore=val)
            await safe_edit(message, state, _("ai_ignore_saved"), kb)
        else: raise ValueError
    except: await safe_edit(message, state, _("ai_ignore_error"), kb)
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
        val = -1 if text == _("cmd_reset").lower() else int(text)
        if val == -1 or (0 <= val <= 100):
            await _upd_c_cfg(chat_id, h_ignore=val)
            await safe_edit(message, state, _("ai_ignore_saved"), kb)
        else: raise ValueError
    except: await safe_edit(message, state, _("ai_ignore_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.g_h_smart_cfg)
async def save_cfg_smart_g(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    try:
        val = 0.05 if message.text.lower() == _("cmd_reset").lower() else float(message.text.replace(",", "."))
        await _upd_g_cfg(s_mul=val)
        await safe_edit(message, state, _("ai_h_cfg_smart_saved"), kb)
    except: await safe_edit(message, state, _("ai_format_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.c_h_smart_cfg)
async def save_cfg_smart_c(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data=f"ai_human_chat_{chat_id}")]])
    try:
        val = None if message.text.lower() == _("cmd_reset").lower() else float(message.text.replace(",", "."))
        await _upd_c_cfg(chat_id, s_mul=val)
        await safe_edit(message, state, _("ai_h_cfg_smart_saved"), kb)
    except: await safe_edit(message, state, _("ai_format_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.g_h_typing_cfg)
async def save_cfg_typ_g(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    try:
        if message.text.lower() == _("cmd_reset").lower(): 
            await _upd_g_cfg(tmin=1.5, tmax=3.5, pmin=0.5, pmax=2.0)
        else:
            tmin, tmax, pmin, pmax = map(float, message.text.replace(",", ".").split())
            await _upd_g_cfg(tmin=tmin, tmax=tmax, pmin=pmin, pmax=pmax)
        await safe_edit(message, state, _("ai_h_cfg_typing_saved"), kb)
    except: await safe_edit(message, state, _("ai_format_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.c_h_typing_cfg)
async def save_cfg_typ_c(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data=f"ai_human_chat_{chat_id}")]])
    try:
        if message.text.lower() == _("cmd_reset").lower(): 
            await _upd_c_cfg(chat_id, tmin=None, tmax=None, pmin=None, pmax=None)
        else:
            tmin, tmax, pmin, pmax = map(float, message.text.replace(",", ".").split())
            await _upd_c_cfg(chat_id, tmin=tmin, tmax=tmax, pmin=pmin, pmax=pmax)
        await safe_edit(message, state, _("ai_h_cfg_typing_saved"), kb)
    except: await safe_edit(message, state, _("ai_format_error"), kb)
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
        elif message.text: prompt_text = message.text
        else: return await safe_edit(message, state, _("ai_g_prompt_error_format"), kb)
        await _upd_g_cfg(db_fields={"global_prompt": prompt_text})
        await safe_edit(message, state, _("ai_g_prompt_saved"), kb)
    except Exception as e: await safe_edit(message, state, _("ai_general_error", e=e), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.global_delays)
async def save_g_delays(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    try:
        db_m, db_mx, da_m, da_mx = map(int, message.text.split())
        await _upd_g_cfg(db_min=db_m, db_max=db_mx, da_min=da_m, da_max=da_mx)
        await safe_edit(message, state, _("ai_g_delays_saved"), kb)
    except: await safe_edit(message, state, _("ai_g_delays_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.global_typing)
async def save_g_typing(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    try:
        speed = float(message.text.replace(",", "."))
        await _upd_g_cfg(db_fields={"typing_speed": speed})
        await safe_edit(message, state, _("ai_g_typing_saved"), kb)
    except: await safe_edit(message, state, _("ai_g_typing_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.sleep_hours)
async def save_sleep_hours(message: types.Message, state: FSMContext):
    try: await message.delete() 
    except: pass
    text = message.text.lower().strip()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_global_settings")]])
    try:
        if text == _("cmd_off").lower():
            await _upd_g_cfg(db_fields={"sleep_start": None, "sleep_end": None})
            await safe_edit(message, state, _("ai_sleep_disabled"), kb)
        else:
            try:
                start, end = message.text.split()
                start_fmt = datetime.strptime(start, "%H:%M").strftime("%H:%M")
                end_fmt = datetime.strptime(end, "%H:%M").strftime("%H:%M")
                await _upd_g_cfg(db_fields={"sleep_start": start_fmt, "sleep_end": end_fmt})
                await safe_edit(message, state, _("ai_sleep_saved", start=start_fmt, end=end_fmt), kb)
            except: await safe_edit(message, state, _("ai_sleep_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.custom_prompt)
async def save_prompt(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_chat"), callback_data=f"ai_chat_menu_{chat_id}")]])
    prompt_text = ""
    try:
        bot = plugins.bot
        if message.document:
            buffer = io.BytesIO()
            await bot.download(message.document, destination=buffer)
            raw_data = buffer.getvalue()
            try: prompt_text = raw_data.decode('utf-8')
            except UnicodeDecodeError: prompt_text = raw_data.decode('cp1251', errors='ignore')
        elif message.text: prompt_text = message.text
        final_prompt = None if prompt_text.lower() == _("cmd_reset").lower() else prompt_text
        await _upd_c_cfg(chat_id, db_fields={"custom_prompt": final_prompt})
        await safe_edit(message, state, _("ai_c_prompt_saved"), kb)
    except Exception as e: await safe_edit(message, state, _("ai_c_prompt_error", e=e), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.delays)
async def save_delays(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_chat"), callback_data=f"ai_chat_menu_{chat_id}")]])
    try:
        if message.text.lower() == _("cmd_reset").lower():
            await _upd_c_cfg(chat_id, db_fields={"delay_before_min": None, "delay_before_max": None, "delay_after_min": None, "delay_after_max": None})
            await safe_edit(message, state, _("ai_c_delays_reset"), kb)
            return
        db_min, db_max, da_min, da_max = map(int, message.text.split())
        await _upd_c_cfg(chat_id, db_fields={"delay_before_min": db_min, "delay_before_max": db_max, "delay_after_min": da_min, "delay_after_max": da_max})
        await safe_edit(message, state, _("ai_c_delays_saved"), kb)
    except: await safe_edit(message, state, _("ai_c_delays_error"), kb)
    finally: await state.set_state(None)

def register_userbot(app: Client, bot: Bot):
    async def process_reply(client, message):
        media_paths_to_cleanup = []
        try:
            chat_id = message.chat.id
            g_cfg = await _get_g_cfg()
            c_cfg = await _get_c_cfg(chat_id)
            
            is_global_ai = g_cfg["is_active"]
            chat_is_active = c_cfg["is_active"]
            is_ignored = c_cfg["is_ignored"]
            
            if g_cfg["ai_debug"]:
                logging.info(f"[AI Twin] MSG from {message.from_user.id if message.from_user else 'unknown'}. ChatActive={chat_is_active}, GlobActive={is_global_ai}")
                
            if not is_global_ai and not chat_is_active: return 
            if is_global_ai and not chat_is_active and is_ignored: return 
            
            sleep_start, sleep_end = g_cfg["sleep_start"], g_cfg["sleep_end"]
            if sleep_start and sleep_end:
                now_str = datetime.now().strftime("%H:%M")
                if sleep_start <= sleep_end:
                    if sleep_start <= now_str <= sleep_end: 
                        if g_cfg["ai_debug"]: logging.info(_("ai_log_skip_sleep", chat_id=chat_id))
                        return
                else:
                    if now_str >= sleep_start or now_str <= sleep_end: 
                        if g_cfg["ai_debug"]: logging.info(_("ai_log_skip_sleep", chat_id=chat_id))
                        return
            
            use_h_typing = g_cfg["h_typing"] if c_cfg["h_typing"] == 2 else bool(c_cfg["h_typing"])
            use_h_smart = g_cfg["h_smart"] if c_cfg["h_smart"] == 2 else bool(c_cfg["h_smart"])
            use_h_ignore = g_cfg["h_ignore"] if c_cfg["h_ignore"] == -1 else c_cfg["h_ignore"]
            
            search_enabled = c_cfg["search_enabled"] and g_cfg["search_enabled"]
                
            final_prompt = g_cfg["prompt"] or ""
            if c_cfg["prompt"]: final_prompt += _("ai_additional_rules", prompt=c_cfg["prompt"])
            
            text_to_search = message.text or message.caption or ""
            yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', text_to_search)
            all_links = re.findall(r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)', text_to_search)
            non_yt_links = [l for l in all_links if not any(yt in l for yt in yt_links)]
            
            if non_yt_links:
                search_enabled = True
                link_rule = _("ai_link_rule")
                final_prompt = (final_prompt + link_rule) if final_prompt else link_rule

            if search_enabled:
                search_rule = f"\n\n{_('ai_prompt_rule_search')}"
                final_prompt = (final_prompt + search_rule) if final_prompt else search_rule

            chat_name = message.from_user.first_name if message.from_user else (message.chat.title or _("other_sender"))
            
            history_str, new_paths, latest_media_duration, video_too_long = await build_dialog_context(client, chat_id, limit=50, target_msg_id=message.id, chat_name=chat_name)
            media_paths_to_cleanup.extend(new_paths)

            live_media_path = None
            if message.photo or message.video:
                ext = ".jpg" if message.photo else ".mp4"
                live_media_path = await message.download(file_name=f"data/live_{message.id}{ext}")
                if live_media_path: media_paths_to_cleanup.append(live_media_path)

            full_history_str = _("ai_dialog_context_header", me=_("me_sender"), other=chat_name) + history_str
            
            if message.reply_to_message:
                orig = message.reply_to_message
                orig_sender = _("me_sender") if (orig.from_user and orig.from_user.is_self) else chat_name
                orig_text = orig.text or orig.caption or _("ai_msg_file")
                if len(orig_text) > 400: orig_text = orig_text[:400] + "..."
                full_history_str += _("ai_reply_alert", text=text_to_search, sender=orig_sender, orig=orig_text)

            if use_h_smart:
                if video_too_long:
                    full_history_str += _("ai_video_too_long_alert", mins=int(latest_media_duration/60))
                elif latest_media_duration > 0:
                    if g_cfg["ai_debug"]: logging.info(_("log_timings_unread_media", dur=latest_media_duration))

            full_history_str += _("ai_sys_instructions", prompt=final_prompt)

            if g_cfg["ai_debug"]:
                logging.info("="*50)
                logging.info(_("log_llm_req_main_chat"))
                logging.info(_("log_full_prompt", prompt=full_history_str))
                if live_media_path:
                    logging.info(_("log_attached_live_media", path=live_media_path))
                logging.info("="*50)

            ai_generate_task = asyncio.create_task(
                generate_ai_response(full_history_str, live_media_path, custom_prompt="", search_enabled=search_enabled)
            )

            c_db_min = c_cfg["db_min"] if c_cfg["db_min"] is not None else g_cfg["db_min"]
            c_db_max = c_cfg["db_max"] if c_cfg["db_max"] is not None else g_cfg["db_max"]
            c_da_min = c_cfg["da_min"] if c_cfg["da_min"] is not None else g_cfg["da_min"]
            c_da_max = c_cfg["da_max"] if c_cfg["da_max"] is not None else g_cfg["da_max"]

            delay_before = random.randint(c_db_min, c_db_max)
            if delay_before > 0: await asyncio.sleep(delay_before)

            is_question = bool(re.search(_("ai_question_words_regex"), text_to_search.lower()))
            if not is_question and use_h_ignore > 0:
                if random.randint(1, 100) <= use_h_ignore:
                    ai_generate_task.cancel() 
                    try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
                    except: pass
                    await asyncio.sleep(1.0)
                    try:
                        await client.read_chat_history(chat_id)
                        if message.voice or message.video_note or message.video:
                            await client.invoke(functions.messages.ReadMessageContents(id=[message.id]))
                    except: pass
                    if random.random() < 0.5:
                        try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=(int(g_cfg["reaction"]) if g_cfg["reaction"].isdigit() else g_cfg["reaction"]))
                        except: pass
                    if g_cfg["ai_debug"]: logging.info(_("ai_log_ignored", chat_id=chat_id, chance=use_h_ignore))
                    return

            try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
            except: pass
            await asyncio.sleep(1.0)
            try:
                await client.read_chat_history(chat_id)
                if message.voice or message.video_note or message.video:
                    await client.invoke(functions.messages.ReadMessageContents(id=[message.id]))
            except: pass

            c_s_mul = c_cfg["s_mul"] if c_cfg["s_mul"] is not None else g_cfg["s_mul"]
            smart_delay = 0
            if use_h_smart:
                if video_too_long: smart_delay = len(text_to_search) * c_s_mul
                elif latest_media_duration > 0: smart_delay = latest_media_duration
                else: smart_delay = len(text_to_search) * c_s_mul

            if smart_delay > 0:
                elapsed_wait = 0
                while elapsed_wait < smart_delay:
                    if chat_id in skip_video_timers:
                        skip_video_timers.remove(chat_id)
                        if g_cfg["ai_debug"]: logging.info(_("log_skip_delay", chat_id=chat_id))
                        break
                    await asyncio.sleep(1)
                    elapsed_wait += 1

            try: reply = await ai_generate_task
            except asyncio.CancelledError: return
            except Exception: reply = None

            if not reply or reply == "⏳": return
            if g_cfg["ai_debug"]: logging.info(_("log_llm_res_main", reply=reply))

            reply_upper = reply.upper().strip()
            if reply_upper.startswith("[LIKE]"):
                try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=(int(g_cfg["reaction"]) if g_cfg["reaction"].isdigit() else g_cfg["reaction"]))
                except: pass
                return
                
            if "[LIKE]" in reply_upper:
                try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=(int(g_cfg["reaction"]) if g_cfg["reaction"].isdigit() else g_cfg["reaction"]))
                except: pass
                reply = re.sub(r'(?i)\[LIKE\]', '', reply).strip()

            if not reply: return 

            delay_after = random.randint(c_da_min, c_da_max)
            if delay_after > 0: await asyncio.sleep(delay_after)

            parts = []
            for p in reply.split('\n'):
                p = p.strip()
                if p:
                    while len(p) > 4000:
                        parts.append(p[:4000])
                        p = p[4000:]
                    if p: parts.append(p)

            use_reply = random.random() < 0.25 
            c_tmin = c_cfg["tmin"] if c_cfg["tmin"] is not None else g_cfg["tmin"]
            c_tmax = c_cfg["tmax"] if c_cfg["tmax"] is not None else g_cfg["tmax"]
            c_pmin = c_cfg["pmin"] if c_cfg["pmin"] is not None else g_cfg["pmin"]
            c_pmax = c_cfg["pmax"] if c_cfg["pmax"] is not None else g_cfg["pmax"]

            for i, part in enumerate(parts):
                typing_time = min(len(part) * float(g_cfg["typing_speed"]), 10.0) 
                await simulate_human_typing(client, chat_id, typing_time, use_h_typing, c_tmin, c_tmax, c_pmin, c_pmax)
                
                use_typo = random.random() < 0.05
                final_part = introduce_typo(part) if use_typo else part
                
                reply_params = ReplyParameters(message_id=message.id) if (i == 0 and use_reply) else None
                sent_msg = await client.send_message(chat_id, final_part, reply_parameters=reply_params)
                
                try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
                except: pass
                
                if use_typo and final_part != part:
                    await asyncio.sleep(random.uniform(3, 10.0))
                    try: await sent_msg.edit_text(part)
                    except: pass
                
                if i < len(parts) - 1:
                    await asyncio.sleep(random.uniform(0.5, 2.0)) 

        except asyncio.CancelledError: pass
        except Exception as e:
            logging.error(_("log_ai_critical_error", e=e))
            traceback.print_exc()
            try:
                logging.error(_("ai_log_chat_error", chat_id=message.chat.id, e=str(e)))
            except: pass
        finally:
            for p in media_paths_to_cleanup:
                if p and os.path.exists(p):
                    try: os.remove(p)
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