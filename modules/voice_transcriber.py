import os
import asyncio
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import ReplyParameters
from pyrogram.enums import ChatType

import database
from gemini_core import transcribe_media, generate_ai_response
from utils import safe_edit
from i18n import _

# SETTINGS INTERFACE (AIOGRAM)
router = Router()

class VoiceStates(StatesGroup):
    waiting_for_command = State()

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_v_settings_main"), callback_data="voice_main")]]

async def get_chat_menu_buttons(chat_id: int):
    return [[InlineKeyboardButton(text=_("btn_v_chat_settings"), callback_data=f"v_chat_main_{chat_id}")]]

async def get_voice_kb():
    cfg = await database.get_config()
    st_auto_my = _("status_on") if cfg[23] else _("status_off")
    st_auto_oth = _("status_on") if cfg[24] else _("status_off")
    st_allow_cmd = _("status_on") if cfg[25] else _("status_off")
    st_summ = _("status_on") if cfg[26] else _("status_off")
    cmd = cfg[27] if cfg[27] else ".text"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_v_auto_my").format(st_auto_my), callback_data="v_tgl_v_auto_my"),
         InlineKeyboardButton(text=_("btn_v_auto_other").format(st_auto_oth), callback_data="v_tgl_v_auto_other")],
        [InlineKeyboardButton(text=_("btn_v_cmd_allow_others").format(st_allow_cmd), callback_data="v_tgl_v_allow_cmd")],
        [InlineKeyboardButton(text=_("btn_v_summarize").format(st_summ), callback_data="v_tgl_v_summarize")],
        [InlineKeyboardButton(text=_("btn_v_command").format(cmd), callback_data="v_edit_cmd")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])

async def get_chat_voice_kb(chat_id):
    chat_cfg = await database.get_chat_settings(chat_id)
    
    def get_lbl(index, template_name):
        val = chat_cfg[index] if chat_cfg and len(chat_cfg) > index and chat_cfg[index] is not None else 2
        if val == 2: st = _("status_global")
        elif val == 1: st = _("status_on")
        else: st = _("status_off")
        return _(template_name).format(st)
        
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_lbl(16, "btn_v_auto_my"), callback_data=f"v_c_tgl_v_auto_my_{chat_id}"),
         InlineKeyboardButton(text=get_lbl(17, "btn_v_auto_other"), callback_data=f"v_c_tgl_v_auto_other_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(18, "btn_v_c_cmd_allow"), callback_data=f"v_c_tgl_v_allow_cmd_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]
    ])

@router.callback_query(F.data == "voice_main")
async def voice_menu(call: types.CallbackQuery, state: FSMContext):
    await safe_edit(call.message, state, _("menu_v_title"), await get_voice_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("v_chat_main_"))
async def voice_chat_menu(call: types.CallbackQuery, state: FSMContext):
    chat_id = call.data.split("_")[3]
    await safe_edit(call.message, state, _("menu_v_chat_title").format(chat_id), await get_chat_voice_kb(chat_id), parse_mode="HTML")

@router.callback_query(F.data.startswith("v_tgl_"))
async def voice_global_toggles(call: types.CallbackQuery, state: FSMContext):
    setting = "_".join(call.data.split("_")[2:])
    await database.toggle_v_setting_global(setting)
    await safe_edit(call.message, state, _("menu_v_title"), await get_voice_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("v_c_tgl_"))
async def voice_chat_toggles(call: types.CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    chat_id = parts[-1]
    setting = "_".join(parts[3:-1])
    
    idx_map = {"v_auto_my": 16, "v_auto_other": 17, "v_allow_cmd": 18}
    await database.toggle_v_setting_chat(chat_id, idx_map[setting], setting)
    await safe_edit(call.message, state, _("menu_v_chat_title").format(chat_id), await get_chat_voice_kb(chat_id), parse_mode="HTML")

@router.callback_query(F.data == "v_edit_cmd")
async def voice_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="voice_main")]])
    await safe_edit(call.message, state, _("v_enter_command"), kb, parse_mode="HTML")
    await state.set_state(VoiceStates.waiting_for_command)

@router.message(VoiceStates.waiting_for_command)
async def voice_save_cmd(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    cmd = message.text.strip().split()[0]
    await database.set_v_command_global(cmd)
    
    await state.set_state(None)
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    msg = await message.answer(_("v_command_changed").format(cmd), parse_mode="HTML")
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass
    
    if menu_msg_id:
        try:
            await message.bot.edit_message_text(text=_("menu_v_title"), chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=await get_voice_kb(), parse_mode="HTML")
        except: pass

# USERBOT LOGIC (PYROGRAM)
def register_userbot(app: Client):
    
    async def process_voice_media(client, message, target_msg, cfg, chat_cfg, is_manual=False):
        media_path = None
        status_msg = None
        is_me = message.from_user and message.from_user.is_self

        if is_manual and is_me:
            status_msg = await message.edit(_("v_status_processing"), parse_mode=enums.ParseMode.HTML)
            await database.add_ignored_msg(message.chat.id, status_msg.id)
            
        try:
            m_ext = ".ogg" if target_msg.voice else ".mp4"
            media_path = await target_msg.download(file_name=f"data/v_{target_msg.id}{m_ext}")
            
            duration = getattr(target_msg.voice, "duration", 0) or \
                       getattr(target_msg.video_note, "duration", 0) or \
                       getattr(target_msg.video, "duration", 0) or \
                       getattr(target_msg.audio, "duration", 0)
            
            if media_path and os.path.exists(media_path):
                text = await transcribe_media(media_path)
                if text:
                    clean_prefix = _('v_voice_prefix')
                    content_inside_quote = text
                    
                    if cfg[26] and duration >= 60:
                        summary_prompt = f"{_('v_summary_prompt')}{text}"
                        summary_text = await generate_ai_response(summary_prompt, search_enabled=False)
                        
                        if summary_text and summary_text != "⏳":
                            formatted_summary = _("v_summary_prefix").format(summary=summary_text)
                            content_inside_quote = f"{formatted_summary}\n\n{text}"

                    if clean_prefix.strip():
                        final_text = f"<b>{clean_prefix}</b><blockquote expandable>{content_inside_quote}</blockquote>"
                    else:
                        final_text = f"<blockquote expandable>{content_inside_quote}</blockquote>"

                    parts = []
                    while len(final_text) > 4000:
                        parts.append(final_text[:4000])
                        final_text = final_text[4000:]
                    if final_text:
                        parts.append(final_text)

                    for i, part in enumerate(parts):
                        if i == 0 and is_manual and is_me:
                            await status_msg.edit(part, parse_mode=enums.ParseMode.HTML)
                            await database.add_ignored_msg(message.chat.id, status_msg.id)
                        else:
                            reply_id = target_msg.id if i == 0 else (status_msg.id if status_msg else message.id)
                            sent_msg = await client.send_message(
                                chat_id=message.chat.id,
                                text=part,
                                reply_parameters=ReplyParameters(message_id=reply_id),
                                parse_mode=enums.ParseMode.HTML
                            )
                            await database.add_ignored_msg(message.chat.id, sent_msg.id)
                else:
                    err_txt = _("v_process_error")
                    if is_manual:
                        if is_me and status_msg: 
                            await status_msg.edit(err_txt, parse_mode=enums.ParseMode.HTML)
                        else: 
                            err_msg = await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id), parse_mode=enums.ParseMode.HTML)
                            await database.add_ignored_msg(message.chat.id, err_msg.id)
        except Exception as e:
            logging.error(_("v_log_error_processing").format(e))
            if status_msg:
                try: await status_msg.edit(_("v_error_processing_file"), parse_mode=enums.ParseMode.HTML)
                except: pass
        finally:
            if media_path and os.path.exists(media_path):
                try: os.remove(media_path)
                except: pass

    @app.on_message((filters.voice | filters.video_note) & filters.private, group=-1)
    async def auto_voice_handler(client, message):
        cfg = await database.get_config()
        if not cfg: return
        chat_cfg = await database.get_chat_settings(message.chat.id)
        
        is_me = message.from_user and message.from_user.is_self
        
        c_my = chat_cfg[16] if chat_cfg and len(chat_cfg) > 16 and chat_cfg[16] is not None else 2
        c_oth = chat_cfg[17] if chat_cfg and len(chat_cfg) > 17 and chat_cfg[17] is not None else 2
        
        should_auto_my = bool(cfg[23]) if c_my == 2 else bool(c_my)
        should_auto_oth = bool(cfg[24]) if c_oth == 2 else bool(c_oth)
        
        if (is_me and should_auto_my) or (not is_me and should_auto_oth):
            asyncio.create_task(process_voice_media(client, message, message, cfg, chat_cfg, is_manual=False))

    @app.on_message(filters.text & filters.reply & filters.private, group=-2)
    async def cmd_voice_handler(client, message):
        cfg = await database.get_config()
        if not cfg: return
        cmd = cfg[27] if len(cfg) > 27 and cfg[27] else ".text"
        
        if message.text.lower().startswith(cmd.lower()):
            is_me = message.from_user and message.from_user.is_self
            
            chat_cfg = await database.get_chat_settings(message.chat.id)
            c_allow = chat_cfg[18] if chat_cfg and len(chat_cfg) > 18 and chat_cfg[18] is not None else 2
            allow_others = bool(cfg[25]) if c_allow == 2 else bool(c_allow)
            
            if is_me or allow_others:
                target = message.reply_to_message
                if target and (target.voice or target.video_note or target.video or target.audio):
                    asyncio.create_task(process_voice_media(client, message, target, cfg, chat_cfg, is_manual=True))
