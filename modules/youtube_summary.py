import re
import html
import time
import asyncio
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions
from sqlalchemy import delete

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from core.db import AsyncSessionLocal, YoutubeCache
from core.utils import safe_edit, safe_delete, get_cancel_kb, CoreAPI, md_to_html
from core.config import _
from core.services import get_youtube_context, generate_ai_response_stream

router = Router()
MODULE_NAME = "youtube_summary"

class YtSummaryFSM(StatesGroup):
    wait_command = State()
    wait_prompt = State()

async def _get_cfg() -> dict:
    cfg = await CoreAPI.get_module_cfg(MODULE_NAME)
    return {
        "is_active": cfg.get("is_active", True),
        "command": cfg.get("command", ".sum"),
        "prompt": cfg.get("prompt", _("ys_default_prompt")),
        "debug_mode": cfg.get("debug_mode", False)
    }

async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg(MODULE_NAME, **kwargs)

async def get_settings_buttons() -> list:
    return [[InlineKeyboardButton(text=_("ys_btn_main"), callback_data="ys_main_menu")]]

async def build_ys_kb() -> InlineKeyboardMarkup:
    cfg = await _get_cfg()
    status = _("status_on") if cfg["is_active"] else _("status_off")
    debug_status = "ON" if cfg["debug_mode"] else "OFF"
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("ys_btn_status", status=status), callback_data="ys_toggle")],
        [InlineKeyboardButton(text=_("ys_btn_debug", status=debug_status), callback_data="ys_toggle_debug")],
        [InlineKeyboardButton(text=_("ys_btn_cmd", cmd=cfg["command"]), callback_data="ys_edit_cmd")],
        [InlineKeyboardButton(text=_("ys_btn_prompt"), callback_data="ys_edit_prompt")],
        [InlineKeyboardButton(text=_("ys_btn_clear_cache"), callback_data="ys_clear_cache")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])

@router.callback_query(F.data == "ys_main_menu")
async def ys_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("ys_menu_title"), await build_ys_kb(), parse_mode="HTML")

@router.callback_query(F.data == "ys_toggle")
async def ys_toggle(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await _upd_cfg(is_active=not cfg["is_active"])
    await ys_menu(call, state)

@router.callback_query(F.data == "ys_toggle_debug")
async def ys_toggle_debug(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await _upd_cfg(debug_mode=not cfg.get("debug_mode", False))
    await ys_menu(call, state)

@router.callback_query(F.data == "ys_clear_cache")
async def ys_clear_cache(call: types.CallbackQuery, state: FSMContext):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(YoutubeCache))
        await session.commit()
    await call.answer(_("ys_cache_cleared"), show_alert=True)

@router.callback_query(F.data == "ys_edit_cmd")
async def ys_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await safe_edit(call.message, state, _("ys_ask_cmd", cmd=cfg["command"]), get_cancel_kb("ys_main_menu"), parse_mode="HTML")
    await state.set_state(YtSummaryFSM.wait_command)

@router.callback_query(F.data == "ys_edit_prompt")
async def ys_edit_prompt(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg()
    await safe_edit(call.message, state, _("ys_ask_prompt", prompt=html.escape(cfg["prompt"])), get_cancel_kb("ys_main_menu"), parse_mode="HTML")
    await state.set_state(YtSummaryFSM.wait_prompt)

@router.message(YtSummaryFSM.wait_command)
async def ys_save_cmd(message: types.Message, state: FSMContext):
    await safe_delete(message)
    cmd = message.text.strip().split()[0]
    await _upd_cfg(command=cmd)
    await _return_to_menu(message, state)

@router.message(YtSummaryFSM.wait_prompt)
async def ys_save_prompt(message: types.Message, state: FSMContext):
    await safe_delete(message)
    await _upd_cfg(prompt=message.text.strip())
    await _return_to_menu(message, state)

async def _return_to_menu(message: types.Message, state: FSMContext):
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        mock_call = types.CallbackQuery(
            id="", from_user=message.from_user, chat_instance="", 
            message=types.Message(message_id=data["menu_msg_id"], chat=message.chat, date=message.date)
        )
        await ys_menu(mock_call, state)

def _clean_formatting(text: str) -> str:
    html_text = md_to_html(text)
    
    lines = []
    for line in html_text.split('\n'):
        clean_line = line.strip()
        if clean_line.startswith('&gt;') or clean_line.startswith('>'):
            content = clean_line.replace('&gt;', '', 1).replace('>', '', 1).strip()
            lines.append(f"<blockquote>{content}</blockquote>")
        else:
            lines.append(line)
    
    res = '\n'.join(lines)
    replacements = {
        "\n* ": "\n• ",
        " * ": " • ",
        "**": "",
    }
    for old, new in replacements.items():
        res = res.replace(old, new)
    return res

def register_userbot(app: Client):
    @app.on_message(filters.me & filters.text, group=31)
    async def process_yt_summary(client: Client, message):
        cfg = await _get_cfg()
        if not cfg["is_active"] or not message.text.startswith(cfg["command"]):
            return

        target_text = message.text
        if message.reply_to_message:
            target_text += " " + (message.reply_to_message.text or message.reply_to_message.caption or "")

        yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', target_text)
        if not yt_links:
            await message.edit(_("ys_error_no_link"))
            return

        target_url = yt_links[0]
        no_preview = LinkPreviewOptions(is_disabled=True)
        active_msg = await message.edit(_("ys_processing"), link_preview_options=no_preview)

        try:
            dur, ctx = await get_youtube_context(target_url)
            
            if cfg.get("debug_mode"):
                print(f"\n--- YT DEBUG START ---\nURL: {target_url}\nCONTEXT SIZE: {len(ctx) if ctx else 0}\nCONTENT:\n{ctx}\n--- YT DEBUG END ---\n")

            if not ctx or ctx == _("yt_url_fallback"):
                return await active_msg.edit(_("ys_error_no_context"), link_preview_options=no_preview)

            stream_gen = generate_ai_response_stream(
                prompt_context=ctx,
                custom_prompt=cfg["prompt"],
                search_enabled=False 
            )

            full_reply = ""
            last_ui_update = time.time()
            part_count = 1
            
            async for chunk in stream_gen:
                full_reply += chunk
                
                if time.time() - last_ui_update > 1.4:
                    formatted = _clean_formatting(full_reply)
                    
                    if len(formatted) > 3800:
                        split_idx = full_reply.rfind("\n", 0, 3200)
                        if split_idx == -1: split_idx = 3200
                        
                        to_send = full_reply[:split_idx]
                        full_reply = full_reply[split_idx:].lstrip()
                        
                        header = f"📺 <b>Summary (Part {part_count}):</b>\n\n"
                        await active_msg.edit(f"{header}{_clean_formatting(to_send)}", parse_mode=enums.ParseMode.HTML)
                        
                        part_count += 1
                        active_msg = await client.send_message(message.chat.id, "<i>...</i>", parse_mode=enums.ParseMode.HTML)
                    
                    try:
                        header = f"📺 <b>Summary (Part {part_count}):</b>\n\n"
                        await active_msg.edit(f"{header}{_clean_formatting(full_reply)} <i>...</i>", parse_mode=enums.ParseMode.HTML)
                        last_ui_update = time.time()
                    except Exception:
                        pass

            header = f"📺 <b>Summary (Part {part_count}):</b>\n\n"
            await active_msg.edit(f"{header}{_clean_formatting(full_reply)}", parse_mode=enums.ParseMode.HTML)

        except Exception as e:
            logging.error(f"YT Summary Error: {e}")
            await active_msg.edit(_("ys_error_ai", e=str(e)[:200]))