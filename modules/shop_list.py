import os
import json
import asyncio
import re
import logging
from aiogram import Router, F, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from pyrogram import Client, filters
from pyrogram.types import InputChecklist, InputChecklistTask

from core.services import generate_ai_response
from core.utils import safe_edit, simulate_typing, CoreAPI, get_cancel_kb, get_back_kb
from core.config import _

router = Router()

class ShopStates(StatesGroup):
    waiting_for_chats = State()
    waiting_for_command = State()

async def _get_cfg():
    s = await CoreAPI.get_module_cfg("shop")
    return {
        "active": s.get("active", True),
        "allow_others": s.get("allow_others", True),
        "command": s.get("command", ".shop"),
        "delete_orig": s.get("delete_orig", True),
        "auto_chats": s.get("auto_chats", ""),
        "prompt": s.get("prompt", _("default_ai_prompt"))
    }

async def _upd_cfg(**kwargs):
    await CoreAPI.update_module_cfg("shop", **kwargs)

async def get_settings_buttons():
    return [[InlineKeyboardButton(text=_("btn_shop_settings"), callback_data="shop_main")]]

async def get_shop_kb():
    cfg = await _get_cfg()
    st_act = _("status_on") if cfg["active"] else _("status_off")
    st_del = _("status_on") if cfg["delete_orig"] else _("status_off")
    st_oth = _("status_on") if cfg["allow_others"] else _("status_off")
    chats_lbl = (cfg["auto_chats"][:12] + "...") if len(cfg["auto_chats"]) > 12 else (cfg["auto_chats"] or _("status_empty"))
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("btn_shop_status", status=st_act), callback_data="shop_tgl_active")],
        [InlineKeyboardButton(text=_("btn_shop_cmd", cmd=cfg["command"]), callback_data="shop_edit_cmd"),
         InlineKeyboardButton(text=_("btn_shop_del_orig", status=st_del), callback_data="shop_tgl_delete_orig")],
        [InlineKeyboardButton(text=_("btn_shop_others", status=st_oth), callback_data="shop_tgl_allow_others")],
        [InlineKeyboardButton(text=_("btn_shop_auto_chats", status=chats_lbl), callback_data="shop_edit_chats")],
        [InlineKeyboardButton(text=_("btn_back"), callback_data="main_menu")]
    ])

@router.callback_query(F.data == "shop_main")
async def shop_menu(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(menu_msg_id=call.message.message_id)
    await safe_edit(call.message, state, _("menu_shop_title"), await get_shop_kb(), parse_mode="HTML")

@router.callback_query(F.data.startswith("shop_tgl_"))
async def shop_toggles(call: types.CallbackQuery, state: FSMContext):
    setting = call.data.replace("shop_tgl_", "")
    cfg = await _get_cfg()
    await _upd_cfg(**{setting: not cfg[setting]})
    await shop_menu(call, state)

@router.callback_query(F.data == "shop_edit_cmd")
async def shop_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    await safe_edit(call.message, state, _("shop_enter_cmd"), get_cancel_kb("shop_main"), parse_mode="HTML")
    await state.set_state(ShopStates.waiting_for_command)

@router.message(ShopStates.waiting_for_command)
async def shop_save_cmd(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    cmd = message.text.strip().split()[0]
    await _upd_cfg(command=cmd)
    
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        try:
            await message.bot.edit_message_text(_("menu_shop_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_shop_kb(), parse_mode="HTML")
        except: pass

@router.callback_query(F.data == "shop_edit_chats")
async def shop_edit_chats(call: types.CallbackQuery, state: FSMContext):
    cfg = await _get_cfg() 
    current_chats = cfg.get("auto_chats", "") 
    chats_str = current_chats if current_chats else _("status_empty")
    
    await safe_edit(call.message, state, _("shop_enter_chats", chats=chats_str), get_cancel_kb("shop_main"), parse_mode="HTML")
    await state.set_state(ShopStates.waiting_for_chats)

@router.message(ShopStates.waiting_for_chats)
async def shop_save_chats(message: types.Message, state: FSMContext):
    try: await message.delete()
    except: pass
    
    txt = message.text.strip()
    
    if txt.lower() in [_("cmd_reset_val").lower(), "reset"]:
        await _upd_cfg(auto_chats="")
    else:
        clean_txt = txt.replace(" ", "")
        await _upd_cfg(auto_chats=clean_txt)
        
    await state.set_state(None)
    data = await state.get_data()
    if data.get("menu_msg_id"):
        try:
            await message.bot.edit_message_text(_("menu_shop_title"), message.chat.id, data["menu_msg_id"], reply_markup=await get_shop_kb(), parse_mode="HTML")
        except: pass

def register_userbot(app: Client):
    async def shop_filter(*args):
        m = args[-1]
        if getattr(m, "checklist", None): return False
        text_to_check = m.text or m.caption or ""
        
        cfg = await _get_cfg()
        if not cfg["active"]: return False
        
        is_me = (m.from_user and m.from_user.is_self)
        if not is_me and not cfg["allow_others"]: return False

        if text_to_check and re.match(rf"^{re.escape(cfg['command'])}(?:\s+|$)", text_to_check):
            return True
        
        if m.reply_to_message:
            target = m.reply_to_message
            is_real_checklist = getattr(target, "checklist", None) is not None
            target_text = target.text or target.caption or ""
            has_md_items = "- [ ]" in target_text or "- [x]" in target_text
            
            check_title = _("checklist_title")
            title_match = False
            if is_real_checklist and target.checklist.title == check_title:
                title_match = True
            elif check_title in target_text and (is_real_checklist or has_md_items):
                title_match = True
                
            if title_match: return True
            
        if cfg["auto_chats"]:
            targets = [x.strip() for x in cfg["auto_chats"].split(",") if x.strip()]
            curr_chat = str(m.chat.id)
            thread_id = getattr(m, "message_thread_id", None)
            curr_topic = str(thread_id) if thread_id else None
            for t in targets:
                if ":" in t:
                    tc, tt = t.split(":", 1)
                    if tc == curr_chat and tt == curr_topic: return True
                elif t == curr_chat: return True
        return False

    @app.on_message(filters.create(shop_filter))
    async def process_shop(client, message):
        cfg = await _get_cfg()
        cmd = cfg["command"]
        sys_prompt = cfg.get("prompt", "")
        
        raw_text = (message.text or message.caption or "")
        is_manual = raw_text.startswith(cmd)
        if is_manual: raw_text = raw_text.replace(cmd, "", 1).strip()
        
        old_list = ""
        should_delete_old = None
        
        if message.reply_to_message:
            target = message.reply_to_message
            is_real_checklist = getattr(target, "checklist", None) is not None
            target_text = target.text or target.caption or ""
            check_title = _("checklist_title")
            
            title_match = False
            if is_real_checklist and target.checklist.title == check_title:
                title_match = True
            elif check_title in target_text:
                title_match = True

            if title_match:
                should_delete_old = target
                if is_real_checklist:
                    old_list = "\n".join([t.text for t in target.checklist.tasks])
                else:
                    items = [l.replace("- [ ] ", "").replace("- [x] ", "").strip() for l in target_text.split("\n") if l.strip().startswith("- [")]
                    old_list = "\n".join(items)

        if is_manual and message.reply_to_message and not raw_text:
            raw_text = message.reply_to_message.text or message.reply_to_message.caption or ""

        if not raw_text and not old_list: return
        typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
        
        try:
            if old_list:
                query_body = _("shop_ai_query_template", existing=old_list, new=raw_text)
            else:
                query_body = _("shop_ai_query_new", text=raw_text)
                    
            full_query = f"{sys_prompt}\n\n{query_body}"
            
            res = await generate_ai_response(full_query, search_enabled=False)
            match = re.search(r"\[.*\]", res, re.DOTALL)
            if not match: return
            
            tasks_list = json.loads(match.group(0))
            clean_tasks = [str(t).split(":", 1)[-1].strip() if ":" in str(t) else str(t) for t in tasks_list]
            reply_id = getattr(message, "message_thread_id", None)

            for i in range(0, len(clean_tasks), 30):
                chunk = clean_tasks[i:i+30]
                tasks_objs = [InputChecklistTask(id=idx+1, text=txt) for idx, txt in enumerate(chunk)]
                checklist = InputChecklist(
                    title=_("checklist_title"), 
                    tasks=tasks_objs, 
                    others_can_mark_tasks_as_done=True, 
                    others_can_add_tasks=True
                )
                try:
                    await client.send_checklist(chat_id=message.chat.id, checklist=checklist, message_thread_id=reply_id)
                except Exception:
                    fmt = _("markdown_title") + "\n".join([f"- [ ] {t}" for t in chunk])
                    await client.send_message(message.chat.id, fmt, message_thread_id=reply_id)

            if should_delete_old:
                try: await should_delete_old.delete()
                except: pass
            if cfg["delete_orig"]:
                try: await message.delete()
                except: pass
        except Exception as e: 
            logging.error(_("log_shop_error", e=e))
        finally:
            typing_task.cancel()