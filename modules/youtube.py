import os
import re
import json
import logging
import requests

import yt_dlp

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from utils import safe_edit
from i18n import _

router = Router()
COOKIES_PATH = "data/cookies.txt" 

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
async def on_startup():
    os.makedirs("data", exist_ok=True)
    if os.path.exists(COOKIES_PATH):
        logging.info(_("yt_cookies_found"))
    else:
        logging.warning(_("yt_cookies_not_found"))

async def get_settings_buttons(): # <--- Теперь возвращает в Настройки
    return [
        [InlineKeyboardButton(text=_("btn_yt_cookies_menu"), callback_data="yt_cookies_menu")]
    ]

# ==========================================
# FSM И МЕНЮ УПРАВЛЕНИЯ COOKIES
# ==========================================
class YTCookiesFSM(StatesGroup):
    wait_for_document = State()

@router.callback_query(F.data == "yt_nop")
async def yt_nop(call: types.CallbackQuery):
    await call.answer()

@router.callback_query(F.data == "yt_cookies_menu")
async def yt_cookies_menu(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(None) # <--- ИСПРАВЛЕНО, меню теперь не исчезает
    
    has_cookies = os.path.exists(COOKIES_PATH)
    status = _("yt_status_loaded") if has_cookies else _("yt_status_missing")
    
    text = _("yt_cookies_menu_text", status=status)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_yt_cookies_upload"), callback_data="yt_cookies_upload")],
    ])
    
    if has_cookies:
        kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_yt_cookies_delete"), callback_data="yt_cookies_delete")])
        
    kb.inline_keyboard.append([InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")])
    
    await safe_edit(call.message, state, text, kb)

@router.callback_query(F.data == "yt_cookies_upload")
async def yt_cookies_upload(call: types.CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="yt_cookies_menu")]])
    await safe_edit(call.message, state, _("yt_upload_prompt"), kb)
    await state.set_state(YTCookiesFSM.wait_for_document)

@router.message(YTCookiesFSM.wait_for_document, F.document)
async def yt_cookies_doc_handler(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    doc = message.document
    if not doc.file_name.endswith('.txt'):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back"), callback_data="yt_cookies_menu")]])
        msg = await message.answer(_("yt_invalid_format"), reply_markup=kb)
        await state.update_data(menu_msg_id=msg.message_id)
        return
        
    msg = await message.answer(_("yt_saving_file"))
    await state.update_data(menu_msg_id=msg.message_id)
    
    file = await message.bot.get_file(doc.file_id)
    await message.bot.download_file(file.file_path, COOKIES_PATH)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_back_to_menu"), callback_data="yt_cookies_menu")]])
    await safe_edit(msg, state, _("yt_cookies_updated"), kb)
    await state.set_state(None) # <--- ИСПРАВЛЕНО

@router.callback_query(F.data == "yt_cookies_delete")
async def yt_cookies_delete(call: types.CallbackQuery, state: FSMContext):
    if os.path.exists(COOKIES_PATH):
        os.remove(COOKIES_PATH)
    await call.answer(_("yt_cookies_deleted_alert"), show_alert=False)
    await yt_cookies_menu(call, state)

# ==========================================
# ЛОГИКА РАБОТЫ С YOUTUBE URL
# ==========================================
def extract_youtube_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", 
        r"youtu\.be\/([0-9A-Za-z_-]{11})", 
        r"shorts\/([0-9A-Za-z_-]{11})"
    ]
    for p in patterns:
        match = re.search(p, url)
        if match: return match.group(1)
    return None

def parse_subtitles_text(raw_text: str, ext: str) -> str:
    if not raw_text:
        return ""
        
    clean_text = ""
    try:
        if ext == 'json3':
            data = json.loads(raw_text)
            events = data.get('events', [])
            for event in events:
                for seg in event.get('segs', []):
                    utf8_text = seg.get('utf8', '').replace('\n', ' ')
                    if utf8_text.strip():
                        clean_text += utf8_text + " "
        else:
            lines = raw_text.split('\n')
            for line in lines:
                if '-->' in line or line.startswith(('WEBVTT', 'Kind:', 'Language:', 'Style:')):
                    continue
                clean_line = re.sub(r'<[^>]+>', '', line.strip())
                if clean_line and not clean_line.isdigit():
                    clean_text += clean_line + " "
                    
    except Exception as e:
        logging.error(_("yt_sub_clean_error", e=e))
        return raw_text[:10000] 
        
    return re.sub(r'\s+', ' ', clean_text).strip()

def fetch_youtube_data_sync(url: str):
    video_id = extract_youtube_id(url)
    duration = 0
    context = ""
    
    if not video_id: 
        return 0, f"URL: {url}"

    ydl_opts = {
        'quiet': True,
        'skip_download': True,
        'no_warnings': True,
        'extract_flat': False,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['ru', 'en'],
        'subtitlesformat': 'json3/vtt/best',
        'ignore_no_formats_error': True, 
        'extractor_args': {
            'youtube': ['player_client=android,web'] 
        }
    }
    
    if os.path.exists(COOKIES_PATH):
        ydl_opts['cookiefile'] = COOKIES_PATH
        logging.info(_("yt_using_cookies"))
    else:
        logging.warning(_("yt_no_cookies_warning"))
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', '')
            description = info.get('description', '')
            duration = info.get('duration', 0)
            
            context += _("yt_video_title", title=title)
            if description:
                context += _("yt_video_desc", description=description)
            logging.info(_("yt_data_fetched", video_id=video_id))
            
            requested_subs = info.get('requested_subtitles')
            subs_text = ""
            
            if requested_subs:
                for lang in ['ru', 'en']:
                    if lang in requested_subs:
                        sub_info = requested_subs[lang]
                        sub_url = sub_info.get('url')
                        sub_ext = sub_info.get('ext', 'json3')
                        
                        if sub_url:
                            logging.info(_("yt_downloading_subs", lang=lang, sub_ext=sub_ext))
                            
                            try:
                                resp = requests.get(
                                    sub_url, 
                                    headers={"User-Agent": "Mozilla/5.0"}, 
                                    timeout=10
                                )
                                if resp.status_code == 200:
                                    subs_text = parse_subtitles_text(resp.text, sub_ext)
                                    logging.info(_("yt_subs_success", lang=lang))
                                    break
                            except Exception as dl_e:
                                logging.error(_("yt_subs_dl_error", dl_e=dl_e))
                                
            if subs_text:
                context += _("yt_subs_text", text=subs_text[:75000])
            else:
                logging.warning(_("yt_subs_missing", video_id=video_id))
                
    except Exception as e:
        logging.error(_("yt_ytdlp_error", video_id=video_id, e=e))
        
    logging.info(_("yt_final_context_log", context=context))
        
    return duration, context