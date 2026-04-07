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

from gemini_core import generate_ai_response
from utils import safe_edit, simulate_typing

# ==========================================
# ИЗОЛИРОВАННАЯ БАЗА/КОНФИГ МОДУЛЯ
# ==========================================
CONFIG_FILE = "data/shop_config.json"

DEFAULT_PROMPT = """You are a smart shopping list sorter.
Your task: extract items from the user's text (even if it's prose, not a list), combine them with the current list (if provided), and sort them logically so similar items are grouped together.
RETURN ONLY A VALID JSON ARRAY OF STRINGS.
RULES:
1. STRICTLY FORBIDDEN to write category or department names.
2. The string must contain ONLY the name of the item itself (and quantity if specified) and a suitable emoji.
3. Extract items from any text. If it says "buy bread, 2 liters of milk and cheese", make 3 separate items out of it. Ignore filler words.
4. Keep the output language the same as the user's input language (usually Russian).

Example of a CORRECT answer:
[
  "🍅 Помидоры",
  "🥛 Молоко (2 литра)",
  "🍗 Куриное филе",
  "🌯 Шаурма сырная",
  "🧼 Мыло"
]"""

# Исправлено форматирование Markdown
DEFAULT_TEXTS = {
    "menu_main": "🛒 *Shopping List Settings*\n\nBot uses native Kurigram checklists. All texts can be modified in `shop_config.json`.",
    "menu_title": "🛒 *Shopping List Settings*",
    "prompt_reset_alert": "Prompt has been reset! It now understands prose and text.",
    "enter_command": "Enter new command:",
    "command_changed": "✅ Command changed to `{}`!",
    "enter_chats": "Enter chat/topic IDs separated by comma.\nFormat: `chat_id:topic_id`",
    "chats_updated": "✅ Auto-chats updated!",
    "empty_list": "❌ Empty list.",
    "error_send": "❌ Error sending checklist: {}",
    "checklist_title": "🛒 Shopping List",
    "markdown_title": "🛒 *Shopping List:*\n\n",
    "btn_settings_main": "🛒 Shopping List Settings",
    "btn_status": "Status: {}",
    "btn_command": "Command: {}",
    "btn_del_orig": "Delete original: {}",
    "btn_allow_others": "Allow others: {}",
    "btn_chats": "Auto-chats: {}",
    "btn_reset_prompt": "📝 RESET PROMPT",
    "btn_cancel": "🔙 Cancel",
    "btn_back": "🔙 Back",
    "status_on": "ON ✅",
    "status_off": "OFF ❌"
}

def load_config():
    defaults = {
        "is_active": True,
        "allow_others": True,
        "command": ".shop",
        "delete_orig": True,
        "auto_chats": "",
        "custom_prompt": DEFAULT_PROMPT,
        "texts": DEFAULT_TEXTS
    }
    
    if not os.path.exists("data"):
        os.makedirs("data")
    if not os.path.exists(CONFIG_FILE):
        save_config(defaults)
        return defaults
        
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
            
        for k, v in defaults.items():
            if k not in user_cfg:
                user_cfg[k] = v
                
        if "texts" not in user_cfg or not isinstance(user_cfg["texts"], dict):
            user_cfg["texts"] = DEFAULT_TEXTS
        else:
            for tk, tv in DEFAULT_TEXTS.items():
                if tk not in user_cfg["texts"]:
                    user_cfg["texts"][tk] = tv
                    
        return user_cfg
    except:
        return defaults

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

# ==========================================
# НАСТРОЙКИ AIOGRAM (UI)
# ==========================================
router = Router()

class ShopStates(StatesGroup):
    waiting_for_chats = State()
    waiting_for_prompt = State()
    waiting_for_command = State()

async def get_settings_buttons():
    cfg = load_config()
    return [[InlineKeyboardButton(text=cfg["texts"]["btn_settings_main"], callback_data="shop_main_menu")]]

def get_shop_keyboard(cfg):
    t = cfg["texts"]
    status = t["status_on"] if cfg.get("is_active") else t["status_off"]
    del_orig = t["status_on"] if cfg.get("delete_orig") else t["status_off"]
    allow_oth = t["status_on"] if cfg.get("allow_others", True) else t["status_off"]
    cmd = cfg.get("command", ".shop")
    chats = cfg.get("auto_chats", "None")
    if len(chats) > 15: chats = chats[:15] + "..."
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["btn_status"].format(status), callback_data="shop_toggle")],
        [
            InlineKeyboardButton(text=t["btn_command"].format(cmd), callback_data="shop_edit_cmd"),
            InlineKeyboardButton(text=t["btn_del_orig"].format(del_orig), callback_data="shop_toggle_del")
        ],
        [InlineKeyboardButton(text=t["btn_allow_others"].format(allow_oth), callback_data="shop_toggle_others")],
        [InlineKeyboardButton(text=t["btn_chats"].format(chats), callback_data="shop_edit_chats")],
        [InlineKeyboardButton(text=t["btn_reset_prompt"], callback_data="shop_reset_prompt")],
        [InlineKeyboardButton(text=t["btn_back"], callback_data="global_settings")]
    ])

@router.callback_query(F.data == "shop_main_menu")
async def shop_menu_handler(call: types.CallbackQuery, state: FSMContext):
    cfg = load_config()
    await safe_edit(call.message, state, cfg["texts"]["menu_main"], get_shop_keyboard(cfg))

@router.callback_query(F.data.in_({"shop_toggle", "shop_toggle_del", "shop_toggle_others"}))
async def shop_toggles_handler(call: types.CallbackQuery, state: FSMContext):
    cfg = load_config()
    if call.data == "shop_toggle":
        cfg["is_active"] = not cfg.get("is_active")
    elif call.data == "shop_toggle_del":
        cfg["delete_orig"] = not cfg.get("delete_orig")
    elif call.data == "shop_toggle_others":
        cfg["allow_others"] = not cfg.get("allow_others", True)
    save_config(cfg)
    await safe_edit(call.message, state, cfg["texts"]["menu_title"], get_shop_keyboard(cfg))

@router.callback_query(F.data == "shop_reset_prompt")
async def shop_reset_prompt(call: types.CallbackQuery, state: FSMContext):
    cfg = load_config()
    cfg["custom_prompt"] = DEFAULT_PROMPT
    save_config(cfg)
    await call.answer(cfg["texts"]["prompt_reset_alert"], show_alert=True)
    await safe_edit(call.message, state, cfg["texts"]["menu_title"], get_shop_keyboard(cfg))

@router.callback_query(F.data == "shop_edit_cmd")
async def shop_edit_cmd(call: types.CallbackQuery, state: FSMContext):
    cfg = load_config()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=cfg["texts"]["btn_cancel"], callback_data="shop_main_menu")]])
    await safe_edit(call.message, state, cfg["texts"]["enter_command"], kb)
    await state.set_state(ShopStates.waiting_for_command)

@router.message(ShopStates.waiting_for_command)
async def shop_save_cmd(message: types.Message, state: FSMContext):
    cfg = load_config()
    cfg["command"] = message.text.strip().split()[0]
    save_config(cfg)
    await message.delete()
    await state.clear()
    msg = await message.answer(cfg["texts"]["command_changed"].format(cfg["command"]))
    await asyncio.sleep(2)
    await msg.delete()
    await safe_edit(message, state, cfg["texts"]["menu_title"], get_shop_keyboard(cfg))

@router.callback_query(F.data == "shop_edit_chats")
async def shop_edit_chats(call: types.CallbackQuery, state: FSMContext):
    cfg = load_config()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=cfg["texts"]["btn_cancel"], callback_data="shop_main_menu")]])
    await safe_edit(call.message, state, cfg["texts"]["enter_chats"], kb)
    await state.set_state(ShopStates.waiting_for_chats)

@router.message(ShopStates.waiting_for_chats)
async def shop_save_chats(message: types.Message, state: FSMContext):
    cfg = load_config()
    text = message.text.strip()
    cfg["auto_chats"] = "" if text.lower() == "сброс" or text.lower() == "reset" else text
    save_config(cfg)
    await message.delete()
    await state.clear()
    msg = await message.answer(cfg["texts"]["chats_updated"])
    await asyncio.sleep(2)
    await msg.delete()
    await safe_edit(message, state, cfg["texts"]["menu_title"], get_shop_keyboard(cfg))

# ==========================================
# ЛОГИКА ЮЗЕРБОТА (KURIGRAM)
# ==========================================
def register_userbot(app: Client):
    
    async def shop_filter(_, __, message):
        cfg = load_config()
        if not cfg.get("is_active"): return False
        
        cmd = cfg.get("command", ".shop")
        allow_others = cfg.get("allow_others", True)
        is_me = (message.from_user and message.from_user.is_self)
        
        if message.text and re.match(rf"^{re.escape(cmd)}(?:\s+|$)", message.text):
            if is_me or allow_others:
                return True
                
        if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_self:
            t_title = cfg.get("texts", {}).get("checklist_title", "Shopping List")
            is_our_list = False
            
            if getattr(message.reply_to_message, "checklist", None):
                is_our_list = True
            elif message.reply_to_message.text and t_title in message.reply_to_message.text:
                is_our_list = True
                
            if is_our_list:
                if is_me or allow_others:
                    return True
                
        chats_raw = cfg.get("auto_chats", "")
        if chats_raw:
            allowed_targets = [x.strip() for x in chats_raw.split(",") if x.strip()]
            current_chat = str(message.chat.id)
            
            thread_id = getattr(message, "message_thread_id", None)
            if not thread_id and getattr(message, "reply_to_message_id", None):
                thread_id = message.reply_to_message_id
            current_topic = str(thread_id) if thread_id else None
            
            for target in allowed_targets:
                if ":" in target:
                    t_chat, t_topic = target.split(":", 1)
                    if t_chat == current_chat and t_topic == current_topic:
                        return True
                else:
                    if target == current_chat:
                        return True
        return False

    @app.on_message(filters.create(shop_filter))
    async def process_shop_list(client: Client, message):
        cfg = load_config()
        t = cfg["texts"]
        cmd = cfg.get("command", ".shop")
        
        is_manual = message.text and message.text.startswith(cmd)
        
        raw_text = ""
        old_list_text = ""
        should_delete_old_msg = None
        
        current_msg_text = ""
        if message.text or message.caption:
            current_msg_text = (message.text or message.caption)
            if is_manual:
                current_msg_text = current_msg_text.replace(cmd, "", 1).strip()
        
        is_reply_to_our_list = False
        if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_self:
            if getattr(message.reply_to_message, "checklist", None) or (message.reply_to_message.text and t["checklist_title"] in message.reply_to_message.text):
                is_reply_to_our_list = True

        if is_reply_to_our_list:
            target_msg = message.reply_to_message
            should_delete_old_msg = target_msg
            
            if getattr(target_msg, "checklist", None):
                old_list_text = "\n".join([task.text for task in target_msg.checklist.tasks])
            else:
                lines = target_msg.text.split('\n')
                items = [line.replace('- [ ] ', '').replace('- [x] ', '').strip() for line in lines if line.strip().startswith('- [')]
                old_list_text = "\n".join(items)
                
            raw_text = current_msg_text 
        else:
            if is_manual and message.reply_to_message and not current_msg_text:
                raw_text = message.reply_to_message.text or message.reply_to_message.caption or ""
            else:
                raw_text = current_msg_text

        if not raw_text and not old_list_text:
            if is_manual:
                if message.from_user and message.from_user.is_self:
                    await message.edit(t["empty_list"])
                else:
                    await client.send_message(message.chat.id, t["empty_list"], reply_to_message_id=message.id)
            return

        typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 15))
        
        try:
            prompt = cfg.get("custom_prompt", DEFAULT_PROMPT)
            if old_list_text:
                full_query = f"{prompt}\n\n[EXISTING LIST]:\n{old_list_text}\n\n[ADD NEW ITEMS/TEXT]:\n{raw_text}"
            else:
                full_query = f"{prompt}\n\n[USER TEXT]:\n{raw_text}"
            
            ai_response = await generate_ai_response(full_query, search_enabled=False)
            
            json_match = re.search(r"\[.*\]", ai_response, re.DOTALL)
            tasks_list = []
            if json_match:
                try:
                    tasks_list = json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    pass
            
            if not tasks_list:
                raise ValueError("AI did not return a valid JSON array.")

            clean_tasks = []
            for item in tasks_list:
                text_item = str(item)
                if ":" in text_item:
                    text_item = text_item.split(":", 1)[-1].strip()
                if text_item:
                    clean_tasks.append(text_item)

            tasks_list = clean_tasks

            reply_id = getattr(message, "message_thread_id", None)
            if not reply_id:
                fallback_topic = None
                chats_raw = cfg.get("auto_chats", "")
                if chats_raw:
                    for target in [x.strip() for x in chats_raw.split(",") if x.strip()]:
                        if ":" in target and target.split(":")[0] == str(message.chat.id):
                            fallback_topic = int(target.split(":")[1])
                            break
                reply_id = fallback_topic

            CHUNK_SIZE = 30 
            
            for i in range(0, len(tasks_list), CHUNK_SIZE):
                chunk = tasks_list[i:i + CHUNK_SIZE]
                checklist_tasks = [InputChecklistTask(id=idx+1, text=task_text) for idx, task_text in enumerate(chunk)]
                
                checklist = InputChecklist(
                    title=t["checklist_title"], 
                    tasks=checklist_tasks,
                    others_can_mark_tasks_as_done=True,
                    others_can_add_tasks=True
                )
                
                try:
                    if hasattr(client, "send_checklist"):
                        try:
                            await client.send_checklist(
                                chat_id=message.chat.id,
                                message_thread_id=reply_id,
                                checklist=checklist
                            )
                        except TypeError as e:
                            if "message_thread_id" in str(e):
                                await client.send_checklist(
                                    chat_id=message.chat.id,
                                    reply_to_message_id=reply_id,
                                    checklist=checklist
                                )
                            else:
                                raise e
                    else:
                        raise AttributeError("send_checklist method not found.")
                
                except Exception as ex:
                    logging.warning(f"Failed to send native checklist: {ex}. Falling back to markdown.")
                    formatted_list = t["markdown_title"]
                    for task_text in chunk:
                        formatted_list += f"- [ ] {task_text}\n"
                    
                    await client.send_message(
                        chat_id=message.chat.id,
                        text=formatted_list,
                        message_thread_id=reply_id
                    )

            if should_delete_old_msg:
                try: await should_delete_old_msg.delete()
                except: pass

            if cfg.get("delete_orig", True):
                try: await message.delete()
                except: pass

        except Exception as e:
            logging.error(f"Shop Module Error: {e}")
            if is_manual:
                err_msg = t["error_send"].format(e)
                if message.from_user and message.from_user.is_self:
                    await message.edit(err_msg)
                else:
                    await client.send_message(message.chat.id, err_msg, reply_to_message_id=message.id)
        finally:
            typing_task.cancel()
