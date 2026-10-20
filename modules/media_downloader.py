from core.job_queue import JobQueue
from core.telegram_guard import delivery_failure
import asyncio
import html
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import InputMediaPhoto, InputMediaVideo, ReplyParameters
from sqlalchemy.orm.attributes import flag_modified

from core.db import AsyncSessionLocal, CoreRepository
from core.utils import safe_edit
from core.config import _

router = Router()

URL_PATTERN = re.compile(
    r'(https?://(?:'
    r'(?:(?:www|m|vm|vt)\.)?tiktok\.com/'
    r'|(?:www\.)?instagram\.com/reels?/'
    r'|(?:(?:www|m)\.)?youtube\.com/shorts/'
    r'|(?:\w+\.)?pinterest\.[\w.]+/pin/'
    r'|pin\.it/'
    r')[^\s<>"\']+)',
    re.IGNORECASE,
)
COOKIES_PATH = "data/cookies.txt"
MAX_CAPTION_LENGTH = 1024
PHOTO_ALBUM_LIMIT = 10
GALLERY_TITLE_PREFIX = "__NEXUS_TITLE__"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


@dataclass(slots=True)
class DownloadedMedia:
    kind: str
    paths: list[str]
    title: str

class DownloadStates(StatesGroup):
    waiting_for_command = State()
    waiting_for_cookie_file = State()

async def _get_g_cfg():
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        v = c.module_settings.get("media_downloader", {})
        logging.debug("[Media Downloader] Global configuration loaded")
        return {
            "auto_my": v.get("auto_my", False),
            "auto_other": v.get("auto_other", False),
            "allow_cmd": v.get("allow_cmd", False),
            "enable_tiktok": v.get("enable_tiktok", True),
            "enable_reels": v.get("enable_reels", True),
            "enable_shorts": v.get("enable_shorts", True),
            "enable_pinterest": v.get("enable_pinterest", True),
            "replace_link": v.get("replace_link", True),
            "command": v.get("command", ".dl")
        }

async def _upd_g_cfg(**kwargs):
    logging.info(f"[Media Downloader] Updating global configuration: {list(kwargs.keys())}")
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_global_config()
        v = dict(c.module_settings.get("media_downloader", {}))
        v.update(kwargs)
        new_settings = dict(c.module_settings)
        new_settings["media_downloader"] = v
        c.module_settings = new_settings
        flag_modified(c, "module_settings")
        await session.commit()

async def _get_c_cfg(chat_id):
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_chat_config(chat_id)
        v = c.module_data.get("media_downloader", {})
        logging.debug(f"[Media Downloader] Chat {chat_id} configuration loaded")
        return {
            "auto_my": v.get("auto_my", 2),
            "auto_other": v.get("auto_other", 2),
            "allow_cmd": v.get("allow_cmd", 2),
            "enable_tiktok": v.get("enable_tiktok", 2),
            "enable_reels": v.get("enable_reels", 2),
            "enable_shorts": v.get("enable_shorts", 2),
            "enable_pinterest": v.get("enable_pinterest", 2),
            "replace_link": v.get("replace_link", 2)
        }

async def _upd_c_cfg(chat_id, **kwargs):
    logging.info(f"[Media Downloader] Updating chat {chat_id} configuration: {list(kwargs.keys())}")
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        c = await repo.get_chat_config(chat_id)
        v = dict(c.module_data.get("media_downloader", {}))
        v.update(kwargs)
        new_data = dict(c.module_data)
        new_data["media_downloader"] = v
        c.module_data = new_data
        flag_modified(c, "module_data")
        await session.commit()

def check_cookies_status():
    status = {"yt": False, "ig": False, "tt": False}
    if os.path.exists(COOKIES_PATH):
        try:
            with open(COOKIES_PATH, 'r', encoding='utf-8') as f:
                content = f.read()
                if '.youtube.com' in content: status['yt'] = True
                if '.instagram.com' in content: status['ig'] = True
                if '.tiktok.com' in content: status['tt'] = True
        except Exception as e:
            logging.error(f"[Media Downloader] Error reading cookies file: {e}")
    return status

def verify_cookies_file():
    results = {"yt": {"present": False, "valid": False, "count": 0},
               "ig": {"present": False, "valid": False, "count": 0},
               "tt": {"present": False, "valid": False, "count": 0}}

    if not os.path.exists(COOKIES_PATH):
        return results

    current_time = int(time.time())
    try:
        with open(COOKIES_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                
                if line.startswith('#HttpOnly_'):
                    line = line[10:]
                elif line.startswith('#'):
                    continue

                parts = line.split('\t')
                if len(parts) >= 7:
                    domain = parts[0]
                    try: exp = int(parts[4])
                    except: exp = 0

                    platform = None
                    if '.youtube.com' in domain: platform = 'yt'
                    elif '.instagram.com' in domain: platform = 'ig'
                    elif '.tiktok.com' in domain: platform = 'tt'

                    if platform:
                        results[platform]["present"] = True
                        results[platform]["count"] += 1
                        if exp > current_time or exp == 0:
                            results[platform]["valid"] = True
    except Exception as e:
        logging.error(f"[Media Downloader] Verification error: {e}")

    return results

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_md_settings_main"), callback_data="md_main")]]

async def get_chat_menu_buttons(chat_id: int):
    return [[InlineKeyboardButton(text=_("btn_md_chat_settings"), callback_data=f"md_chat_main_{chat_id}")]]

async def get_md_kb():
    cfg = await _get_g_cfg()
    st_auto_my = _("status_on") if cfg["auto_my"] else _("status_off")
    st_auto_oth = _("status_on") if cfg["auto_other"] else _("status_off")
    st_allow_cmd = _("status_on") if cfg["allow_cmd"] else _("status_off")
    st_tiktok = _("status_on") if cfg["enable_tiktok"] else _("status_off")
    st_reels = _("status_on") if cfg["enable_reels"] else _("status_off")
    st_shorts = _("status_on") if cfg["enable_shorts"] else _("status_off")
    st_pinterest = _("status_on") if cfg["enable_pinterest"] else _("status_off")
    st_replace_link = _("status_on") if cfg["replace_link"] else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_md_auto_my", status=st_auto_my), callback_data="md_tgl_g_auto_my"),
         InlineKeyboardButton(text=_("btn_md_auto_other", status=st_auto_oth), callback_data="md_tgl_g_auto_other")],
        [InlineKeyboardButton(text=_("btn_md_cmd_allow_others", status=st_allow_cmd), callback_data="md_tgl_g_allow_cmd")],
        [InlineKeyboardButton(text=_("btn_md_tiktok", status=st_tiktok), callback_data="md_tgl_g_enable_tiktok"),
         InlineKeyboardButton(text=_("btn_md_reels", status=st_reels), callback_data="md_tgl_g_enable_reels")],
        [InlineKeyboardButton(text=_("btn_md_shorts", status=st_shorts), callback_data="md_tgl_g_enable_shorts")],
        [InlineKeyboardButton(text=_("btn_md_pinterest", status=st_pinterest), callback_data="md_tgl_g_enable_pinterest")],
        [InlineKeyboardButton(text=_("btn_md_replace_link", status=st_replace_link), callback_data="md_tgl_g_replace_link")],
        [InlineKeyboardButton(text=_("btn_md_command", cmd=cfg["command"]), callback_data="md_edit_cmd")],
        [InlineKeyboardButton(text=_("btn_md_cookie_manager"), callback_data="md_cookie_menu")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="global_settings")]
    ])

async def get_chat_md_kb(chat_id):
    chat_cfg = await _get_c_cfg(chat_id)
    def get_lbl(val, template_name):
        if val == 2: st = _("status_global")
        elif val == 1: st = _("status_on")
        else: st = _("status_off")
        return _(template_name, status=st)
        
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_lbl(chat_cfg["auto_my"], "btn_md_auto_my"), callback_data=f"md_c_tgl_auto_my_{chat_id}"),
         InlineKeyboardButton(text=get_lbl(chat_cfg["auto_other"], "btn_md_auto_other"), callback_data=f"md_c_tgl_auto_other_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["allow_cmd"], "btn_md_c_cmd_allow"), callback_data=f"md_c_tgl_allow_cmd_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["enable_tiktok"], "btn_md_tiktok"), callback_data=f"md_c_tgl_enable_tiktok_{chat_id}"),
         InlineKeyboardButton(text=get_lbl(chat_cfg["enable_reels"], "btn_md_reels"), callback_data=f"md_c_tgl_enable_reels_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["enable_shorts"], "btn_md_shorts"), callback_data=f"md_c_tgl_enable_shorts_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["enable_pinterest"], "btn_md_pinterest"), callback_data=f"md_c_tgl_enable_pinterest_{chat_id}")],
        [InlineKeyboardButton(text=get_lbl(chat_cfg["replace_link"], "btn_md_replace_link"), callback_data=f"md_c_tgl_replace_link_{chat_id}")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data=f"chat_{chat_id}")]
    ])

@router.callback_query(F.data == "md_main")
async def md_menu(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Media Downloader] User {call.from_user.id} accessed main menu")
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("menu_md_title"), await get_md_kb(), parse_mode="HTML")

@router.callback_query(F.data == "md_cookie_menu")
async def md_cookie_menu(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Media Downloader] User {call.from_user.id} accessed cookie menu")
    st = check_cookies_status()
    yt_st = _("md_cookie_loaded") if st["yt"] else _("md_cookie_missing")
    ig_st = _("md_cookie_loaded") if st["ig"] else _("md_cookie_missing")
    tt_st = _("md_cookie_loaded") if st["tt"] else _("md_cookie_missing")
    
    text = _("md_cookie_manager_text", yt=yt_st, ig=ig_st, tt=tt_st)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_md_upload_cookie"), callback_data="md_upload_cookie")],
        [InlineKeyboardButton(text=_("btn_md_verify_cookies"), callback_data="md_verify_cookies")],
        [InlineKeyboardButton(text=_("btn_md_clear_cookies"), callback_data="md_clear_cookies")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="md_main")]
    ])
    await safe_edit(call.message, state, text, kb, parse_mode="HTML")

@router.callback_query(F.data == "md_verify_cookies")
async def md_verify_cookies(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Media Downloader] User {call.from_user.id} verifying cookies legitimacy")
    stats = verify_cookies_file()

    def get_text(p_data):
        if not p_data["present"]: return _("md_cookie_missing")
        if p_data["valid"]: return _("md_cookie_valid", count=p_data["count"])
        return _("md_cookie_expired")

    yt_st = get_text(stats["yt"])
    ig_st = get_text(stats["ig"])
    tt_st = get_text(stats["tt"])

    text = _("md_cookie_verify_text", yt=yt_st, ig=ig_st, tt=tt_st)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_back"), callback_data="md_cookie_menu")]
    ])
    await safe_edit(call.message, state, text, kb, parse_mode="HTML")

@router.callback_query(F.data == "md_upload_cookie")
async def md_upload_cookie_prompt(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Media Downloader] User {call.from_user.id} initiated cookie upload")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="md_cookie_menu")]])
    await safe_edit(call.message, state, _("md_cookie_upload_prompt"), kb, parse_mode="HTML")
    await state.set_state(DownloadStates.waiting_for_cookie_file)

@router.message(DownloadStates.waiting_for_cookie_file, F.document)
async def md_handle_cookie_doc(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass

    doc = message.document
    if not doc.file_name.endswith('.txt'):
        logging.warning(f"[Media Downloader] User {message.from_user.id} uploaded invalid cookie file format: {doc.file_name}")
        msg = await message.answer(_("md_cookie_err_format"), parse_mode="HTML")
        await asyncio.sleep(3)
        try: await msg.delete()
        except: pass
        return

    logging.info(f"[Media Downloader] User {message.from_user.id} uploaded valid cookie file: {doc.file_name}")
    file = await message.bot.get_file(doc.file_id)
    temp_path = f"data/temp_{doc.file_id}.txt"
    await message.bot.download_file(file.file_path, temp_path)

    try:
        with open(temp_path, 'r', encoding='utf-8') as tf:
            new_cookies = tf.read()
        with open(COOKIES_PATH, 'a', encoding='utf-8') as mf:
            mf.write("\n" + new_cookies)
        logging.info("[Media Downloader] Cookies successfully appended to main database")
    except Exception as e:
        logging.error("[Media Downloader] Failed to append cookies: %s", e)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
            
    await state.set_state(None)
    
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    if menu_msg_id:
        st = check_cookies_status()
        yt_st = _("md_cookie_loaded") if st["yt"] else _("md_cookie_missing")
        ig_st = _("md_cookie_loaded") if st["ig"] else _("md_cookie_missing")
        tt_st = _("md_cookie_loaded") if st["tt"] else _("md_cookie_missing")
        
        text = _("md_cookie_manager_text", yt=yt_st, ig=ig_st, tt=tt_st)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=_("btn_md_upload_cookie"), callback_data="md_upload_cookie")],
            [InlineKeyboardButton(text=_("btn_md_verify_cookies"), callback_data="md_verify_cookies")],
            [InlineKeyboardButton(text=_("btn_md_clear_cookies"), callback_data="md_clear_cookies")],
            [InlineKeyboardButton(text=_("btn_back"), callback_data="md_main")]
        ])
        try: await message.bot.edit_message_text(text=text, chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=kb, parse_mode="HTML")
        except: pass

@router.callback_query(F.data == "md_clear_cookies")
async def md_clear_cookies(call: types.CallbackQuery, state: FSMContext):
    if os.path.exists(COOKIES_PATH):
        try: 
            os.remove(COOKIES_PATH)
            logging.info(f"[Media Downloader] User {call.from_user.id} cleared the cookie database")
        except Exception as e:
            logging.error(f"[Media Downloader] Error clearing cookie database: {e}")
    await call.answer(_("md_cookie_cleared_alert"), show_alert=True)
    await md_cookie_menu(call, state)

@router.callback_query(F.data.startswith("md_chat_main_"))
async def md_chat_menu(call: types.CallbackQuery, state: FSMContext):
    chat_id = int(call.data.split("_")[3])
    logging.info(f"[Media Downloader] User {call.from_user.id} accessed chat menu for {chat_id}")
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("menu_md_chat_title", chat_id=chat_id), await get_chat_md_kb(chat_id), parse_mode="HTML")

@router.callback_query(F.data.startswith("md_tgl_g_"))
async def md_global_toggles(call: types.CallbackQuery, state: FSMContext):
    setting = "_".join(call.data.split("_")[3:])
    logging.info(f"[Media Downloader] User {call.from_user.id} toggled global setting: {setting}")
    cfg = await _get_g_cfg()
    new_val = not cfg.get(setting, False)
    await _upd_g_cfg(**{setting: new_val})
    await safe_edit(call.message, state, _("menu_md_title"), await get_md_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("md_c_tgl_"))
async def md_chat_toggles(call: types.CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    chat_id = int(parts[-1])
    setting = "_".join(parts[3:-1])
    
    logging.info(f"[Media Downloader] User {call.from_user.id} toggled chat setting: {setting} for chat {chat_id}")
    chat_cfg = await _get_c_cfg(chat_id)
    curr = chat_cfg.get(setting, 2)
    nxt = 1 if curr == 2 else (0 if curr == 1 else 2)
    
    await _upd_c_cfg(chat_id, **{setting: nxt})
    await safe_edit(call.message, state, _("menu_md_chat_title", chat_id=chat_id), await get_chat_md_kb(chat_id), parse_mode="HTML")

@router.callback_query(F.data == "md_edit_cmd")
async def md_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    logging.info(f"[Media Downloader] User {call.from_user.id} requested command edit")
    cfg = await _get_g_cfg()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=_("btn_cancel"), callback_data="md_main")]])
    await safe_edit(call.message, state, _("md_enter_command", cmd=cfg["command"]), kb, parse_mode="HTML")
    await state.set_state(DownloadStates.waiting_for_command)

@router.message(DownloadStates.waiting_for_command)
async def md_save_cmd(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    cmd = message.text.strip().split()[0]
    logging.info(f"[Media Downloader] User {message.from_user.id} updated download command to: {cmd}")
    await _upd_g_cfg(command=cmd)
    
    await state.set_state(None)
    data = await state.get_data()
    menu_msg_id = data.get("menu_msg_id")
    
    msg = await message.answer(_("md_command_changed", cmd=cmd), parse_mode="HTML")
    await asyncio.sleep(2)
    try: await msg.delete()
    except: pass
    
    if menu_msg_id:
        try:
            await message.bot.edit_message_text(text=_("menu_md_title"), chat_id=message.chat.id, message_id=menu_msg_id, reply_markup=await get_md_kb(), parse_mode="HTML")
        except: pass

def _is_tiktok_url(url: str) -> bool:
    return bool(re.search(r"https?://(?:(?:www|m|vm|vt)\.)?tiktok\.com/", url, re.IGNORECASE))


def _is_pinterest_url(url: str) -> bool:
    return bool(re.search(r"https?://(?:(?:\w+\.)?pinterest\.[\w.]+/pin/|pin\.it/)", url, re.IGNORECASE))


def _platform_setting_for_url(url: str) -> tuple[str, str] | None:
    lowered = url.lower()
    if "tiktok.com/" in lowered:
        return "enable_tiktok", "TikTok"
    if "instagram.com/reel" in lowered:
        return "enable_reels", "Instagram Reels"
    if "youtube.com/shorts/" in lowered:
        return "enable_shorts", "YouTube Shorts"
    if _is_pinterest_url(url):
        return "enable_pinterest", "Pinterest"
    return None


def _is_platform_enabled(url: str, global_cfg: dict, chat_cfg: dict) -> bool:
    platform = _platform_setting_for_url(url)
    if platform is None:
        return False

    setting, _ = platform
    chat_value = chat_cfg.get(setting, 2)
    return bool(global_cfg.get(setting, True)) if chat_value == 2 else bool(chat_value)


def _fallback_title(url: str) -> str:
    if "youtube.com/shorts/" in url.lower():
        return "YouTube Shorts"
    if "instagram.com/reel" in url.lower():
        return "Instagram Reels"
    if _is_pinterest_url(url):
        return "Pinterest"
    return "TikTok"


def _prepare_caption(title: object, url: str) -> str:
    caption = re.sub(r"\s+", " ", str(title or "")).strip() or _fallback_title(url)
    if len(caption) > MAX_CAPTION_LENGTH:
        caption = f"{caption[:MAX_CAPTION_LENGTH - 1].rstrip()}…"
    return caption


def _metadata_title(info: dict, url: str) -> object:
    if "instagram.com/reel" in url.lower() or _is_pinterest_url(url):
        return info.get("description") or info.get("title")
    return info.get("title") or info.get("description")


def _check_staging(workdir):
    total=sum(p.stat().st_size for p in Path(workdir).rglob('*') if p.is_file() and not p.is_symlink())
    if total>512*1024**2 or shutil.disk_usage(workdir).free<128*1024**2:
        raise OSError('Temporary media budget exceeded')


def _download_video_sync(url: str, workdir: str) -> DownloadedMedia | None:
    logging.info(f"[Media Downloader] Starting yt-dlp extraction for URL: {url}")
    output_template = os.path.join(workdir, "video.%(ext)s")
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'max_filesize': 512*1024**2,
        'progress_hooks': [lambda info: _check_staging(workdir)],
        'socket_timeout': 30,
        'quiet': True,
        'noprogress': True,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Sec-Fetch-Mode': 'navigate',
        }
    }
    
    if os.path.exists(COOKIES_PATH):
        ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not isinstance(info, dict):
                return None

            prepared_path = Path(ydl.prepare_filename(info))
            candidates = [
                path
                for path in Path(workdir).iterdir()
                if path.is_file() and path.suffix.lower() not in {".part", ".temp", ".ytdl"}
            ]
            if prepared_path.exists():
                downloaded_path = prepared_path
            elif candidates:
                downloaded_path = max(candidates, key=lambda path: path.stat().st_size)
            else:
                return None

            if (
                downloaded_path.suffix.lower() not in VIDEO_EXTENSIONS
                or downloaded_path.suffix.lower() in AUDIO_EXTENSIONS
                or str(info.get("vcodec", "")).lower() == "none"
            ):
                logging.info("[Media Downloader] yt-dlp returned non-video media; trying gallery extraction")
                for candidate in candidates:
                    try:
                        candidate.unlink()
                    except OSError:
                        pass
                return None

            title = _prepare_caption(_metadata_title(info, url), url)
            logging.info(f"[Media Downloader] yt-dlp extraction complete. Saved to: {downloaded_path}")
            return DownloadedMedia(kind="video", paths=[str(downloaded_path)], title=title)
    except Exception as e:
        logging.error("[Media Downloader] yt-dlp failed: %s", e)
        return None


def _download_gallery_media_sync(url: str, workdir: str) -> DownloadedMedia | None:
    platform = "Pinterest" if _is_pinterest_url(url) else "TikTok"
    logging.info("[Media Downloader] Starting gallery-dl %s extraction for URL: %s", platform, url)
    command = [
        sys.executable,
        "-m",
        "gallery_dl",
        "--no-colors",
        "--no-mtime",
        "--no-part",
        "-D",
        workdir,
        "-f",
        "{num:02}.{extension}",
        "--Print",
        f"{GALLERY_TITLE_PREFIX}{{title|description}}",
    ]
    if _is_tiktok_url(url):
        command.extend([
            "-o", "extractor.tiktok.audio=false",
            "-o", "extractor.tiktok.videos=false",
            "-o", "extractor.tiktok.photos=true",
        ])
    else:
        command.extend([
            "-o", "extractor.pinterest.videos=true",
            "-o", "extractor.pinterest.stories=true",
        ])
    if os.path.exists(COOKIES_PATH):
        command.extend(["--cookies", os.path.abspath(COOKIES_PATH)])
    command.append(url)

    try:
        with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as stdout, tempfile.TemporaryFile(mode='w+',encoding='utf-8') as stderr:
            proc=subprocess.Popen(command,stdout=stdout,stderr=stderr,text=True)
            deadline=time.monotonic()+180
            try:
                while proc.poll() is None:
                    _check_staging(workdir)
                    if time.monotonic()>deadline:raise subprocess.TimeoutExpired(command,180)
                    time.sleep(.25)
            finally:
                if proc.poll() is None:proc.kill()
                proc.wait()
            stdout.seek(0);stderr.seek(0)
            result=subprocess.CompletedProcess(command,proc.returncode,stdout.read(65536),stderr.read(65536))
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.error("[Media Downloader] gallery-dl failed to start: %s", exc)
        return None

    media_paths = sorted(
        str(path)
        for path in Path(workdir).iterdir()
        if path.is_file() and path.suffix.lower() in PHOTO_EXTENSIONS | VIDEO_EXTENSIONS
    )
    if result.returncode != 0 or not media_paths:
        error_text = (result.stderr or result.stdout).strip()[-1000:]
        logging.error("[Media Downloader] gallery-dl failed (%s): %s", result.returncode, error_text)
        return None

    title = next(
        (
            line.removeprefix(GALLERY_TITLE_PREFIX)
            for line in result.stdout.splitlines()
            if line.startswith(GALLERY_TITLE_PREFIX)
        ),
        "",
    )
    kind = "video" if len(media_paths) == 1 and Path(media_paths[0]).suffix.lower() in VIDEO_EXTENSIONS else "media"
    logging.info("[Media Downloader] gallery-dl extracted %s %s file(s)", len(media_paths), platform)
    return DownloadedMedia(kind=kind, paths=media_paths, title=_prepare_caption(title, url))


def _download_media_sync(url: str, workdir: str) -> DownloadedMedia | None:
    downloaded = _download_video_sync(url, workdir)
    if downloaded:
        return downloaded
    if _is_tiktok_url(url) or _is_pinterest_url(url):
        return _download_gallery_media_sync(url, workdir)
    return None


def _caption_for_send(title: str, url: str, replace_link: bool) -> tuple[str, enums.ParseMode]:
    if not replace_link:
        return title, enums.ParseMode.DISABLED
    caption = f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'
    return caption, enums.ParseMode.HTML


async def _send_downloaded_media(
    client: Client,
    chat_id: int,
    reply_to_id: int,
    media: DownloadedMedia,
    url: str,
    replace_link: bool,
) -> None:
    reply_parameters = None if replace_link else ReplyParameters(message_id=reply_to_id, chat_id=chat_id)
    caption, parse_mode = _caption_for_send(media.title, url, replace_link)
    if media.kind == "video":
        await client.send_video(
            chat_id=chat_id,
            video=media.paths[0],
            caption=caption,
            parse_mode=parse_mode,
            reply_parameters=reply_parameters,
        )
        return

    for offset in range(0, len(media.paths), PHOTO_ALBUM_LIMIT):
        chunk = media.paths[offset:offset + PHOTO_ALBUM_LIMIT]
        if len(chunk) == 1:
            if Path(chunk[0]).suffix.lower() in VIDEO_EXTENSIONS:
                await client.send_video(
                    chat_id=chat_id,
                    video=chunk[0],
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_parameters=reply_parameters,
                )
            else:
                await client.send_photo(
                    chat_id=chat_id,
                    photo=chunk[0],
                    caption=caption,
                    parse_mode=parse_mode,
                    reply_parameters=reply_parameters,
                )
            continue

        album = [
            (InputMediaVideo if Path(path).suffix.lower() in VIDEO_EXTENSIONS else InputMediaPhoto)(
                media=path,
                caption=caption if index == 0 else "",
                parse_mode=parse_mode,
            )
            for index, path in enumerate(chunk)
        ]
        await client.send_media_group(
            chat_id=chat_id,
            media=album,
            parse_mode=enums.ParseMode.DISABLED,
            reply_parameters=reply_parameters,
        )

def register_userbot(app: Client):
    downloads=JobQueue(capacity=16)
    async def process_media_download(client, message, target_msg, url: str, replace_link: bool, is_manual=False):
        logging.info(f"[Media Downloader] Initiating download task for {url} in chat {message.chat.id}")
        status_msg = None
        is_me = (message.from_user and message.from_user.is_self)

        if is_manual and is_me:
            status_msg = await message.edit(_("md_status_downloading"), parse_mode=enums.ParseMode.HTML)

        os.makedirs("data", exist_ok=True)
        temp_dir = tempfile.mkdtemp(prefix=f"dl_{target_msg.id}_", dir="data")
        downloaded_media = None

        try:
            downloaded_media = await asyncio.to_thread(_download_media_sync, url, temp_dir)

            if downloaded_media and all(os.path.exists(path) for path in downloaded_media.paths):
                logging.info(
                    "[Media Downloader] Uploading %s (%s file(s)) to chat %s",
                    downloaded_media.kind,
                    len(downloaded_media.paths),
                    message.chat.id,
                )
                _check_staging(temp_dir)
                await _send_downloaded_media(
                    client,
                    message.chat.id,
                    target_msg.id,
                    downloaded_media,
                    url,
                    replace_link,
                )
                if replace_link:
                    try:
                        await target_msg.delete()
                    except Exception as delete_error:
                        logging.warning(
                            "[Media Downloader] Could not delete source message %s: %s",
                            target_msg.id,
                            delete_error,
                        )
                logging.info(f"[Media Downloader] Successfully sent media to chat {message.chat.id}")
            else:
                logging.warning(f"[Media Downloader] File not found after download attempt for {url}")
                raise ValueError(_("err_md_download_failed"))

        except Exception as e:

            if delivery_failure(e):

                logging.warning("[Delivery] Result retained in account journal",exc_info=True)

                return
            logging.error("[Media Downloader] Download task failed: %s", e)
            err_txt = _("md_process_error")
            if is_manual:
                if is_me and status_msg: 
                    try: await status_msg.edit(err_txt)
                    except: await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id))
                else:
                    await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id))
        finally:
            try:
                shutil.rmtree(temp_dir)
                logging.debug(f"[Media Downloader] Cleaned up temporary directory: {temp_dir}")
            except Exception as cleanup_err:
                logging.warning(f"[Media Downloader] Failed to clean up directory {temp_dir}: {cleanup_err}")
            if status_msg:
                try: await status_msg.delete()
                except: pass
            logging.info(f"[Media Downloader] Task completed for chat {message.chat.id}")

    @app.on_message(filters.regex(URL_PATTERN) & (filters.private | filters.group), group=10)
    async def auto_download_handler(client, message):
        is_me = bool(message.from_user and message.from_user.is_self)
        if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP) and not is_me:
            return

        cfg = await _get_g_cfg()
        chat_cfg = await _get_c_cfg(message.chat.id)
        
        c_my, c_oth = chat_cfg["auto_my"], chat_cfg["auto_other"]
        should_my = cfg["auto_my"] if c_my == 2 else bool(c_my)
        should_oth = cfg["auto_other"] if c_oth == 2 else bool(c_oth)
        
        if (is_me and should_my) or (not is_me and should_oth):
            match = URL_PATTERN.search(message.text or message.caption)
            if match:
                url = match.group(1)
                if not _is_platform_enabled(url, cfg, chat_cfg):
                    platform = _platform_setting_for_url(url)
                    logging.info(
                        "[Media Downloader] Ignoring disabled platform %s in chat %s",
                        platform[1] if platform else "unknown",
                        message.chat.id,
                    )
                    return
                logging.info(f"[Media Downloader] Auto-download match found for URL: {url} in chat {message.chat.id}")
                replace_link = cfg["replace_link"] if chat_cfg["replace_link"] == 2 else bool(chat_cfg["replace_link"])
                await downloads.submit((message.chat.id,message.id,url),lambda:process_media_download(client,message,message,url,replace_link,is_manual=False))

    @app.on_message(filters.text & filters.reply & (filters.private | filters.group), group=22)
    async def cmd_download_handler(client, message):
        is_me = bool(message.from_user and message.from_user.is_self)
        if message.chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP) and not is_me:
            return

        cfg = await _get_g_cfg()
        if message.text.lower().startswith(cfg["command"].lower()):
            chat_cfg = await _get_c_cfg(message.chat.id)
            c_allow = chat_cfg["allow_cmd"]
            allow_others = cfg["allow_cmd"] if c_allow == 2 else bool(c_allow)
            
            if is_me or allow_others:
                target = message.reply_to_message
                if target and (target.text or target.caption):
                    match = URL_PATTERN.search(target.text or target.caption)
                    if match:
                        url = match.group(1)
                        if not _is_platform_enabled(url, cfg, chat_cfg):
                            platform = _platform_setting_for_url(url)
                            logging.info(
                                "[Media Downloader] Ignoring disabled platform %s command in chat %s",
                                platform[1] if platform else "unknown",
                                message.chat.id,
                            )
                            return
                        logging.info(f"[Media Downloader] Command trigger match found for URL: {url} in chat {message.chat.id}")
                        replace_link = cfg["replace_link"] if chat_cfg["replace_link"] == 2 else bool(chat_cfg["replace_link"])
                        await downloads.submit((message.chat.id,target.id,url),lambda:process_media_download(client,message,target,url,replace_link,is_manual=True))
