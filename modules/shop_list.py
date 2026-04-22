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

def _parse_auto_chat_entries(raw_value: str) -> list[tuple[str, str | None]]:
    entries = []
    for part in (raw_value or "").split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            chat_id, topic_id = token.split(":", 1)
            entries.append((chat_id, topic_id))
        else:
            entries.append((token, None))
    return entries

def _matches_auto_chat(raw_value: str, chat_id: int, topic_id: int | None) -> bool:
    current_chat_id = str(chat_id)
    current_topic_id = str(topic_id) if topic_id is not None else None

    for entry_chat_id, entry_topic_id in _parse_auto_chat_entries(raw_value):
        if entry_chat_id != current_chat_id:
            continue
        if entry_topic_id is None:
            return True
        if current_topic_id == entry_topic_id:
            return True
    return False

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
    async def shop_filter(flt, cli, m):
        try:
            text = m.text or m.caption or ""
            if not text: 
                return False
                
            cfg = await _get_cfg()
            cmd = str(cfg.get("command", ".shop")).strip()
            
            is_cmd = text.startswith(cmd)
            
            is_reply_list = False
            if m.reply_to_message:
                t_text = m.reply_to_message.text or m.reply_to_message.caption or ""
                if _("checklist_title") in t_text:
                    is_reply_list = True

            is_auto = False
            if cfg.get("auto_chats"):
                is_auto = _matches_auto_chat(
                    cfg.get("auto_chats", ""),
                    m.chat.id,
                    getattr(m, "message_thread_id", None)
                )

            if is_cmd or is_reply_list or is_auto:
                if not cfg.get("active", True):
                    logging.warning(f"[Shop] Module is OFF")
                    return False
                    
                is_me = bool(m.from_user and m.from_user.is_self)
                if not is_me and not cfg.get("allow_others", True):
                    logging.warning(f"[Shop] Blocked: allow_others is OFF")
                    return False
                    
                return True
                
            return False
        except Exception as e:
            logging.error(f"[Shop] Filter crash: {e}")
            return False

    @app.on_message(filters.create(shop_filter), group=24)
    async def handle_shop(client, message):
        typing_task = None
        try:
            logging.info(f"[Shop] Triggered in chat {message.chat.id}")
            cfg = await _get_cfg()
            cmd = str(cfg.get("command", ".shop")).strip()
            sys_prompt = cfg.get("prompt", "")

            raw_text = message.text or message.caption or ""
            is_manual = False
            
            if raw_text.startswith(cmd):
                is_manual = True
                raw_text = raw_text[len(cmd):].strip()

            old_list = ""
            should_delete_old = None

            if message.reply_to_message:
                target = message.reply_to_message
                is_real_checklist = getattr(target, "checklist", None) is not None
                target_text = target.text or target.caption or ""
                check_title = _("checklist_title")

                title_match = False
                if is_real_checklist and getattr(target.checklist, "title", "") == check_title:
                    title_match = True
                elif check_title in target_text:
                    title_match = True

                if title_match:
                    should_delete_old = target
                    if is_real_checklist:
                        old_list = "\n".join([t.text for t in getattr(target.checklist, "tasks", [])])
                    else:
                        items = [l.replace("- [ ] ", "").replace("- [x] ", "").strip() for l in target_text.split("\n") if l.strip().startswith("- [")]
                        old_list = "\n".join(items)

            if is_manual and message.reply_to_message and not raw_text:
                raw_text = message.reply_to_message.text or message.reply_to_message.caption or ""

            if not raw_text and not old_list:
                logging.warning("[Shop] Empty text, skipping")
                return

            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))

            query_body = _("shop_ai_query_template", existing=old_list, new=raw_text) if old_list else _("shop_ai_query_new", text=raw_text)
            full_query = f"{sys_prompt}\n\n{query_body}"
            
            logging.info("[Shop] Sending to AI...")
            res = await generate_ai_response(full_query, search_enabled=False)
            
            if not res or res == "⏳" or res == _("status_waiting"):
                logging.error("[Shop] AI timeout or wait status")
                return

            json_text = res.strip()
            if "```json" in json_text:
                json_text = json_text.split("```json")[1].split("```")[0]
            elif "```" in json_text:
                json_text = json_text.split("```")[1].split("```")[0]

            start_idx = json_text.find('[')
            end_idx = json_text.rfind(']')

            if start_idx == -1 or end_idx == -1:
                logging.error(f"[Shop] JSON not found in AI reply: {res}")
                return

            json_str = json_text[start_idx:end_idx+1]
            json_str = re.sub(r',\s*\]', ']', json_str)

            try:
                tasks_list = json.loads(json_str)
            except json.JSONDecodeError as e:
                logging.error(f"[Shop] JSON Parse error: {e}")
                return

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
                except Exception as list_err:
                    logging.warning(f"[Shop] send_checklist failed: {list_err}")
                    fmt = _("markdown_title") + "\n".join([f"- [ ] {t}" for t in chunk])
                    await client.send_message(message.chat.id, fmt, message_thread_id=reply_id)

            if should_delete_old:
                try: await should_delete_old.delete()
                except: pass

            if cfg.get("delete_orig", True):
                try: await message.delete()
                except: pass

        except Exception as e:
            logging.error(f"[Shop] Critical error: {e}")
        finally:
            if typing_task and not typing_task.done():
                typing_task.cancel()
