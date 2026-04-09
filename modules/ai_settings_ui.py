import io
import html
from datetime import datetime

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database
from utils import plugins, safe_edit
from modules.ai_utils import skip_video_timers
from i18n import _

router = Router()

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

async def get_settings_buttons():
    config = await database.get_config()
    g_ai_active = config[12] if (config and len(config) > 12) else False
    g_ai_status = _("status_on") if g_ai_active else _("status_off")
    g_search_active = config[13] if (config and len(config) > 13) else True
    search_status = _("status_on") if g_search_active else _("status_off")
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
    c_search_status = _("status_on") if c_search_active else _("status_off")
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

# HUMANITY AND SETTINGS
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
        [InlineKeyboardButton(text=_("btn_h_typing", status=t_status), callback_data="ai_h_toggle_typing_g"), InlineKeyboardButton(text="⚙️", callback_data="ai_h_cfg_typing_g")],
        [InlineKeyboardButton(text=_("btn_h_smart_read", status=s_status), callback_data="ai_h_toggle_smart_g"), InlineKeyboardButton(text="⚙️", callback_data="ai_h_cfg_smart_g")],
        [InlineKeyboardButton(text=_("btn_h_ignore", chance=h_ignore), callback_data="ai_h_set_ignore_g")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, _("ai_human_g_text"), kb)

@router.callback_query(F.data.startswith("ai_human_chat_"))
async def human_settings_chat(call: types.CallbackQuery, state: FSMContext, chat_id: int = None):
    if chat_id is None: chat_id = int(call.data.split("_")[3])
    await state.update_data(menu_msg_id=call.message.message_id, chat_id=chat_id)
    cfg = await database.get_chat_settings(chat_id)
    c_typing = cfg[8] if (cfg and cfg[8] is not None) else 2
    c_ignore = cfg[9] if (cfg and cfg[9] is not None) else -1
    c_smart = cfg[10] if (cfg and cfg[10] is not None) else 2
    t_status = _("status_global") if c_typing == 2 else (_("status_on") if c_typing == 1 else _("status_off"))
    s_status = _("status_global") if c_smart == 2 else (_("status_on") if c_smart == 1 else _("status_off"))
    i_status = _("status_global") if c_ignore == -1 else c_ignore
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_h_typing", status=t_status), callback_data=f"ai_h_toggle_typing_c_{chat_id}"), InlineKeyboardButton(text="⚙️", callback_data=f"ai_h_cfg_typing_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_h_smart_read", status=s_status), callback_data=f"ai_h_toggle_smart_c_{chat_id}"), InlineKeyboardButton(text="⚙️", callback_data=f"ai_h_cfg_smart_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_h_ignore", chance=i_status), callback_data=f"ai_h_set_ignore_c_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]
    ])
    await safe_edit(call.message, state, _("ai_human_c_text"), kb)

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
    
    ai_debug = config[28] if len(config) > 28 else False
    debug_status = _("status_on") if ai_debug else _("status_off")

    text = _("ai_g_settings_text", sleep_text=sleep_text, typing=typing_val, db_min=db_min, db_max=db_max, da_min=da_min, da_max=da_max, prompt=prompt_short)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_ai_g_sleep"), callback_data="ai_settings_sleep")],
        [InlineKeyboardButton(text=_("btn_ai_g_prompt"), callback_data="ai_g_set_prompt")],
        [InlineKeyboardButton(text=_("btn_ai_g_delays"), callback_data="ai_g_set_delays")],
        [InlineKeyboardButton(text=_("btn_ai_g_typing"), callback_data="ai_g_set_typing")],
        [InlineKeyboardButton(text=_("btn_ai_debug", status=debug_status), callback_data="ai_toggle_debug")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])
    await safe_edit(call.message, state, text, kb)

# BUTTON HANDLERS
@router.callback_query(F.data == "ai_toggle_debug")
async def toggle_debug_cb(call: types.CallbackQuery, state: FSMContext):
    await database.toggle_ai_debug_log()
    await global_settings_menu(call, state)

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
    await database.toggle_global_ai()
    config = await database.get_config()
    g_ai_active = config[12] if (config and len(config) > 12) else False
    g_ai_status = _("status_on") if g_ai_active else _("status_off")
    markup = call.message.reply_markup
    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "ai_toggle_global": btn.text = _("btn_ai_mode", g_ai_status=g_ai_status)
        try: await call.message.edit_reply_markup(reply_markup=markup)
        except: pass
    await call.answer()

@router.callback_query(F.data == "ai_toggle_search_global")
async def toggle_search_global_cb(call: types.CallbackQuery):
    await database.toggle_global_search()
    config = await database.get_config()
    g_search_active = config[13] if (config and len(config) > 13) else True
    search_status = _("status_on") if g_search_active else _("status_off")
    await database.set_search_all_chats(g_search_active)
    markup = call.message.reply_markup
    if markup:
        for row in markup.inline_keyboard:
            for btn in row:
                if btn.callback_data == "ai_toggle_search_global": btn.text = _("btn_ai_search_status", status=search_status)
        try: await call.message.edit_reply_markup(reply_markup=markup)
        except: pass
    await call.answer(_("ai_g_search_applied_alert"))

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

# SAVING FSM DATA
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
            await database.set_chat_h_ignore(chat_id, val)
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
        await database.set_global_h_smart_mul(val)
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
        await database.set_chat_h_smart_mul(chat_id, val)
        await safe_edit(message, state, _("ai_h_cfg_smart_saved"), kb)
    except: await safe_edit(message, state, _("ai_format_error"), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.g_h_typing_cfg)
async def save_cfg_typ_g(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_settings"), callback_data="ai_human_settings_global")]])
    try:
        if message.text.lower() == _("cmd_reset").lower(): await database.set_global_h_typing_cfg(1.5, 3.5, 0.5, 2.0)
        else:
            tmin, tmax, pmin, pmax = map(float, message.text.replace(",", ".").split())
            await database.set_global_h_typing_cfg(tmin, tmax, pmin, pmax)
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
        if message.text.lower() == _("cmd_reset").lower(): await database.set_chat_h_typing_cfg(chat_id, None, None, None, None)
        else:
            tmin, tmax, pmin, pmax = map(float, message.text.replace(",", ".").split())
            await database.set_chat_h_typing_cfg(chat_id, tmin, tmax, pmin, pmax)
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
        await database.set_global_prompt(prompt_text)
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
        await database.set_global_delays(db_m, db_mx, da_m, da_mx)
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
        await database.set_global_typing_speed(speed)
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
            await database.set_sleep_hours(None, None)
            await safe_edit(message, state, _("ai_sleep_disabled"), kb)
        else:
            try:
                start, end = message.text.split()
                datetime.strptime(start, "%H:%M")
                datetime.strptime(end, "%H:%M")
                await database.set_sleep_hours(start, end)
                await safe_edit(message, state, _("ai_sleep_saved", start=start, end=end), kb)
            except: await safe_edit(message, state, _("ai_sleep_error"), kb)
    finally: await state.set_state(None)

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
        elif message.text: prompt_text = message.text
        final_prompt = None if prompt_text.lower() == _("cmd_reset").lower() else prompt_text
        await database.set_custom_prompt(chat_id, final_prompt)
        await safe_edit(message, state, _("ai_c_prompt_saved"), kb)
    except Exception as e: await safe_edit(message, state, _("ai_c_prompt_error", e=e), kb)
    finally: await state.set_state(None)

@router.message(AISettingsFSM.delays)
async def save_delays(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    data = await state.get_data()
    chat_id = data['chat_id']
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_chat"), callback_data=f"chat_{chat_id}")]])
    try:
        if message.text.lower() == _("cmd_reset").lower():
            await database.set_chat_delays(chat_id, None, None, None, None)
            await safe_edit(message, state, _("ai_c_delays_reset"), kb)
            return
        db_min, db_max, da_min, da_max = map(int, message.text.split())
        await database.set_chat_delays(chat_id, db_min, db_max, da_min, da_max)
        await safe_edit(message, state, _("ai_c_delays_saved"), kb)
    except: await safe_edit(message, state, _("ai_c_delays_error"), kb)
    finally: await state.set_state(None)
