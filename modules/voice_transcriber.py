import os
import json
import asyncio
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import ReplyParameters
from pyrogram.enums import ChatType

from gemini_core import transcribe_media, generate_ai_response
from utils import safe_edit

# MODULE CONFIG AND LOCALIZATION
CONFIG_FILE = "data/voice_to_text_config.json"
LANG_FILE = "data/voice_to_text_lang.json"

DEFAULT_LANG = {
    "menu_title": "🎙 **Global Voice-to-Text Settings**",
    "menu_chat_title": "🎙 **Voice-to-Text for chat** `{}`\nOverride global settings:",
    "btn_settings_main": "🎙 Voice-to-Text",
    "btn_chat_settings": "🎙 Voice-to-Text Settings",
    "btn_auto_my": "Auto (My voice): {}",
    "btn_auto_other": "Auto (Others): {}",
    "btn_cmd_allow_others": "Cmd globally: {}",
    "btn_c_cmd_allow": "Cmd for them: {}",
    "btn_summarize": "Smart Summary (>2m): {}",
    "btn_command": "Command: {}",
    "btn_back": "🔙 Back",
    "btn_cancel": "🔙 Cancel",
    "status_on": "ON ✅",
    "status_off": "OFF ❌",
    "status_global": "🌍 Global",
    "enter_command": "Enter new command for voice-to-text (e.g. .text or .voice):",
    "command_changed": "✅ Command successfully changed to `{}`",
    "voice_prefix": "",
    "process_error": "❌ Failed to process media.",
    "summary_prefix": "📝 Brief Summary:\n{summary}",
    "summary_prompt": "Make a very brief summary (the most important points in 1-3 sentences) of this text:\n\n",
    "status_processing": "<i>⏳ Processing...</i>",
    "error_processing_file": "❌ Error processing file.",
    "log_error_processing": "Voice processing error: {}"
}

def load_voice_lang():
    if not os.path.exists("data"): os.makedirs("data")
    if not os.path.exists(LANG_FILE):
        with open(LANG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_LANG, f, indent=4, ensure_ascii=False)
        return DEFAULT_LANG
    try:
        with open(LANG_FILE, "r", encoding="utf-8") as f:
            user_lang = json.load(f)
        updated = False
        for k, v in DEFAULT_LANG.items():
            if k not in user_lang:
                user_lang[k] = v
                updated = True
        if updated:
            with open(LANG_FILE, "w", encoding="utf-8") as f:
                json.dump(user_lang, f, indent=4, ensure_ascii=False)
        return user_lang
    except: return DEFAULT_LANG

def load_voice_config():
    defaults = {
        "auto_my": False,
        "auto_other": False,
        "allow_cmd_others": False,
        "summarize_long": True,
        "summary_threshold": 120,
        "command": ".text",
        "chats": {}
    }
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(defaults, f, indent=4, ensure_ascii=False)
        return defaults
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        updated = False
        for k, v in defaults.items():
            if k not in user_cfg:
                user_cfg[k] = v
                updated = True
        if "chats" not in user_cfg:
            user_cfg["chats"] = {}
            updated = True
        if updated:
            save_voice_config(user_cfg)
        return user_cfg
    except: return defaults

def save_voice_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)

# SETTINGS INTERFACE (AIOGRAM)
router = Router()

class VoiceStates(StatesGroup):
    waiting_for_command = State()

async def on_startup():
    load_voice_lang()
    load_voice_config()

async def get_settings_buttons():
    lang = load_voice_lang()
    return [[InlineKeyboardButton(text=lang["btn_settings_main"], callback_data="voice_main")]]

async def get_chat_menu_buttons(chat_id: int):
    lang = load_voice_lang()
    return [[InlineKeyboardButton(text=lang["btn_chat_settings"], callback_data=f"v_chat_main_{chat_id}")]]

def get_voice_kb(cfg, lang):
    st_auto_my = lang["status_on"] if cfg["auto_my"] else lang["status_off"]
    st_auto_oth = lang["status_on"] if cfg["auto_other"] else lang["status_off"]
    st_allow_cmd = lang["status_on"] if cfg["allow_cmd_others"] else lang["status_off"]
    st_summ = lang["status_on"] if cfg["summarize_long"] else lang["status_off"]
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=lang["btn_auto_my"].format(st_auto_my), callback_data="v_tgl_automy"),
         InlineKeyboardButton(text=lang["btn_auto_other"].format(st_auto_oth), callback_data="v_tgl_autooth")],
        [InlineKeyboardButton(text=lang["btn_cmd_allow_others"].format(st_allow_cmd), callback_data="v_tgl_cmdoth")],
        [InlineKeyboardButton(text=lang["btn_summarize"].format(st_summ), callback_data="v_tgl_summ")],
        [InlineKeyboardButton(text=lang["btn_command"].format(cfg["command"]), callback_data="v_edit_cmd")],
        [InlineKeyboardButton(text=lang["btn_back"], callback_data="global_settings")]
    ])

def get_chat_voice_kb(chat_id, cfg, lang):
    chat_cfg = cfg.get("chats", {}).get(str(chat_id), {})
    
    def get_lbl(key, template_name):
        val = chat_cfg.get(key)
        if val is None: st = lang["status_global"]
        elif val: st = lang["status_on"]
        else: st = lang["status_off"]
        return lang[template_name].format(st)
        
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_lbl("auto_my", "btn_auto_my"), callback_data=f"v_c_tgl_my_{chat_id}"),
         InlineKeyboardButton(text=get_lbl("auto_other", "btn_auto_other"), callback_data=f"v_c_tgl_oth_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl("allow_cmd_others", "btn_c_cmd_allow"), callback_data=f"v_c_tgl_cmd_{chat_id}")],
        [InlineKeyboardButton(text=lang["btn_back"], callback_data=f"chat_{chat_id}")]
    ])

@router.callback_query(F.data == "voice_main")
async def voice_menu(call: types.CallbackQuery, state: FSMContext):
    cfg = load_voice_config()
    lang = load_voice_lang()
    await safe_edit(call.message, state, lang["menu_title"], get_voice_kb(cfg, lang))

@router.callback_query(F.data.startswith("v_chat_main_"))
async def voice_chat_menu(call: types.CallbackQuery, state: FSMContext):
    chat_id = call.data.split("_")[3]
    cfg = load_voice_config()
    lang = load_voice_lang()
    await safe_edit(call.message, state, lang["menu_chat_title"].format(chat_id), get_chat_voice_kb(chat_id, cfg, lang))

@router.callback_query(F.data.startswith("v_tgl_"))
async def voice_global_toggles(call: types.CallbackQuery, state: FSMContext):
    cfg = load_voice_config()
    action = call.data.split("_")[2]
    
    if action == "automy": cfg["auto_my"] = not cfg["auto_my"]
    elif action == "autooth": cfg["auto_other"] = not cfg["auto_other"]
    elif action == "cmdoth": cfg["allow_cmd_others"] = not cfg["allow_cmd_others"]
    elif action == "summ": cfg["summarize_long"] = not cfg["summarize_long"]
    
    save_voice_config(cfg)
    lang = load_voice_lang()
    await safe_edit(call.message, state, lang["menu_title"], get_voice_kb(cfg, lang))

@router.callback_query(F.data.startswith("v_c_tgl_"))
async def voice_chat_toggles(call: types.CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    action = parts[3] 
    chat_id = parts[4]
    
    cfg = load_voice_config()
    if str(chat_id) not in cfg["chats"]: cfg["chats"][str(chat_id)] = {}
    
    if action == "my": key = "auto_my"
    elif action == "oth": key = "auto_other"
    else: key = "allow_cmd_others"
    
    current = cfg["chats"][str(chat_id)].get(key)
    
    if current is None: cfg["chats"][str(chat_id)][key] = True
    elif current is True: cfg["chats"][str(chat_id)][key] = False
    else: del cfg["chats"][str(chat_id)][key]
    
    if not cfg["chats"][str(chat_id)]: del cfg["chats"][str(chat_id)]
    
    save_voice_config(cfg)
    lang = load_voice_lang()
    await safe_edit(call.message, state, lang["menu_chat_title"].format(chat_id), get_chat_voice_kb(chat_id, cfg, lang))

@router.callback_query(F.data == "v_edit_cmd")
async def voice_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    lang = load_voice_lang()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=lang["btn_cancel"], callback_data="voice_main")]])
    await safe_edit(call.message, state, lang["enter_command"], kb)
    await state.set_state(VoiceStates.waiting_for_command)

@router.message(VoiceStates.waiting_for_command)
async def voice_save_cmd(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    cfg = load_voice_config()
    lang = load_voice_lang()
    
    cfg["command"] = message.text.strip().split()[0]
    save_voice_config(cfg)
    
    await state.set_state(None)
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    msg = await message.answer(lang["command_changed"].format(cfg["command"]))
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass
    
    if menu_msg_id:
        try:
            await message.bot.edit_message_text(text=lang["menu_title"], chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=get_voice_kb(cfg, lang))
        except: pass

# USERBOT LOGIC (PYROGRAM)
def register_userbot(app: Client):
    
    async def process_voice_media(client, message, target_msg, lang, cfg, is_manual=False):
        media_path = None
        status_msg = None
        is_me = message.from_user and message.from_user.is_self

        if is_manual and is_me:
            status_msg = await message.edit(lang["status_processing"], parse_mode=enums.ParseMode.HTML)
            
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
                    clean_prefix = lang.get('voice_prefix', '').replace('**', '')
                    content_inside_quote = text
                    
                    if cfg.get("summarize_long") and duration >= cfg.get("summary_threshold", 120):
                        summary_prompt = f"{lang['summary_prompt']}{text}"
                        summary_text = await generate_ai_response(summary_prompt, search_enabled=False)
                        
                        if summary_text and summary_text != "⏳":
                            formatted_summary = lang["summary_prefix"].format(summary=summary_text).replace('**', '')
                            content_inside_quote = f"<b>{formatted_summary}</b>\n\n{text}"
 
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
                        else:
                            reply_id = target_msg.id if i == 0 else (status_msg.id if status_msg else message.id)
                            await client.send_message(
                                chat_id=message.chat.id,
                                text=part,
                                reply_parameters=ReplyParameters(message_id=reply_id),
                                parse_mode=enums.ParseMode.HTML
                            )
                else:
                    err_txt = lang["process_error"]
                    if is_manual:
                        if is_me and status_msg: await status_msg.edit(err_txt, parse_mode=enums.ParseMode.HTML)
                        else: await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id), parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            logging.error(lang["log_error_processing"].format(e))
            if status_msg:
                try: await status_msg.edit(lang["error_processing_file"], parse_mode=enums.ParseMode.HTML)
                except: pass
        finally:
            if media_path and os.path.exists(media_path):
                try: os.remove(media_path)
                except: pass

    @app.on_message((filters.voice | filters.video_note) & filters.private, group=-1)
    async def auto_voice_handler(client, message):
        cfg = load_voice_config()
        chat_id_str = str(message.chat.id)
        chat_cfg = cfg.get("chats", {}).get(chat_id_str, {})
        is_me = message.from_user and message.from_user.is_self
        
        if is_me:
            should_auto = chat_cfg.get("auto_my", cfg["auto_my"])
        else:
            should_auto = chat_cfg.get("auto_other", cfg["auto_other"])
            
        if should_auto:
            lang = load_voice_lang()
            asyncio.create_task(process_voice_media(client, message, message, lang, cfg, is_manual=False))

    @app.on_message(filters.text & filters.reply & filters.private, group=-2)
    async def cmd_voice_handler(client, message):
        cfg = load_voice_config()
        cmd = cfg.get("command", ".text")
        chat_id_str = str(message.chat.id)
        chat_cfg = cfg.get("chats", {}).get(chat_id_str, {})
        
        if message.text.lower().startswith(cmd.lower()):
            is_me = message.from_user and message.from_user.is_self
            
            allow_others = chat_cfg.get("allow_cmd_others", cfg.get("allow_cmd_others", False))
            
            if is_me or allow_others:
                target = message.reply_to_message
                if target and (target.voice or target.video_note or target.video or target.audio):
                    lang = load_voice_lang()
                    asyncio.create_task(process_voice_media(client, message, target, lang, cfg, is_manual=True))
