import os
import re
import asyncio
import logging
import yt_dlp
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters, enums
from pyrogram.types import ReplyParameters
from sqlalchemy.orm.attributes import flag_modified

from core.db import AsyncSessionLocal, CoreRepository
from core.utils import safe_edit
from core.config import _

router = Router()

URL_PATTERN = re.compile(r'(https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com|instagram\.com/reel(?:s)?)/[^\s]+)')
COOKIES_PATH = "data/cookies.txt"

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
            "allow_cmd": v.get("allow_cmd", 2)
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

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_md_settings_main"), callback_data="md_main")]]

async def get_chat_menu_buttons(chat_id: int):
    return [[InlineKeyboardButton(text=_("btn_md_chat_settings"), callback_data=f"md_chat_main_{chat_id}")]]

async def get_md_kb():
    cfg = await _get_g_cfg()
    st_auto_my = _("status_on") if cfg["auto_my"] else _("status_off")
    st_auto_oth = _("status_on") if cfg["auto_other"] else _("status_off")
    st_allow_cmd = _("status_on") if cfg["allow_cmd"] else _("status_off")
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_md_auto_my", status=st_auto_my), callback_data="md_tgl_g_auto_my"),
         InlineKeyboardButton(text=_("btn_md_auto_other", status=st_auto_oth), callback_data="md_tgl_g_auto_other")],
        [InlineKeyboardButton(text=_("btn_md_cmd_allow_others", status=st_allow_cmd), callback_data="md_tgl_g_allow_cmd")],
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
        [InlineKeyboardButton(text=_("btn_md_clear_cookies"), callback_data="md_clear_cookies")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="md_main")]
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
        logging.error(_("log_md_cookie_append_err", e=e))
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

def _download_video_sync(url: str, output_template: str) -> str | None:
    logging.info(f"[Media Downloader] Starting yt-dlp extraction for URL: {url}")
    ydl_opts = {
        'outtmpl': output_template,
        'format': 'best[ext=mp4]/best',
        'quiet': True,
        'no_warnings': True,
    }
    
    if os.path.exists(COOKIES_PATH):
        ydl_opts['cookiefile'] = COOKIES_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            logging.info(f"[Media Downloader] yt-dlp extraction complete. Saved to: {filename}")
            return filename
    except Exception as e:
        logging.error(_("log_md_ytdlp_err", e=e))
        return None

def register_userbot(app: Client):
    async def process_media_download(client, message, target_msg, url: str, is_manual=False):
        logging.info(f"[Media Downloader] Initiating download task for {url} in chat {message.chat.id}")
        status_msg = None
        is_me = (message.from_user and message.from_user.is_self)
        
        if is_manual and is_me:
            status_msg = await message.edit(_("md_status_downloading"), parse_mode=enums.ParseMode.HTML)
            
        temp_path = f"data/dl_{target_msg.id}.mp4"
        downloaded_path = None
        
        try:
            downloaded_path = await asyncio.to_thread(_download_video_sync, url, temp_path)
            
            if downloaded_path and os.path.exists(downloaded_path):
                logging.info(f"[Media Downloader] Uploading video to chat {message.chat.id}")
                await client.send_video(
                    chat_id=message.chat.id,
                    video=downloaded_path,
                    reply_parameters=ReplyParameters(message_id=target_msg.id)
                )
                logging.info(f"[Media Downloader] Successfully sent video to chat {message.chat.id}")
            else:
                logging.warning(f"[Media Downloader] File not found after download attempt for {url}")
                raise ValueError(_("err_md_download_failed"))
                
        except Exception as e:
            logging.error(_("log_md_error", e=str(e)))
            err_txt = _("md_process_error")
            if is_manual:
                if is_me and status_msg: 
                    try: await status_msg.edit(err_txt)
                    except: await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id))
                else: 
                    await client.send_message(message.chat.id, err_txt, reply_parameters=ReplyParameters(message_id=message.id))
        finally:
            if downloaded_path and os.path.exists(downloaded_path):
                try: 
                    os.remove(downloaded_path)
                    logging.debug(f"[Media Downloader] Cleaned up temporary file: {downloaded_path}")
                except Exception as cleanup_err: 
                    logging.warning(f"[Media Downloader] Failed to clean up file {downloaded_path}: {cleanup_err}")
            if status_msg:
                try: await status_msg.delete()
                except: pass
            logging.info(f"[Media Downloader] Task completed for chat {message.chat.id}")

    @app.on_message(filters.regex(URL_PATTERN) & filters.private, group=10)
    async def auto_download_handler(client, message):
        cfg = await _get_g_cfg()
        chat_cfg = await _get_c_cfg(message.chat.id)
        is_me = (message.from_user and message.from_user.is_self)
        
        c_my, c_oth = chat_cfg["auto_my"], chat_cfg["auto_other"]
        should_my = cfg["auto_my"] if c_my == 2 else bool(c_my)
        should_oth = cfg["auto_other"] if c_oth == 2 else bool(c_oth)
        
        if (is_me and should_my) or (not is_me and should_oth):
            match = URL_PATTERN.search(message.text or message.caption)
            if match:
                url = match.group(1)
                logging.info(f"[Media Downloader] Auto-download match found for URL: {url} in chat {message.chat.id}")
                asyncio.create_task(process_media_download(client, message, message, url, is_manual=False))

    @app.on_message(filters.text & filters.reply & filters.private, group=22)
    async def cmd_download_handler(client, message):
        cfg = await _get_g_cfg()
        if message.text.lower().startswith(cfg["command"].lower()):
            chat_cfg = await _get_c_cfg(message.chat.id)
            c_allow = chat_cfg["allow_cmd"]
            allow_others = cfg["allow_cmd"] if c_allow == 2 else bool(c_allow)
            
            if (message.from_user and message.from_user.is_self) or allow_others:
                target = message.reply_to_message
                if target and (target.text or target.caption):
                    match = URL_PATTERN.search(target.text or target.caption)
                    if match:
                        url = match.group(1)
                        logging.info(f"[Media Downloader] Command trigger match found for URL: {url} in chat {message.chat.id}")
                        asyncio.create_task(process_media_download(client, message, target, url, is_manual=True))