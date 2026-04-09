import os
import re
import random
import asyncio
import logging
from datetime import datetime, timezone

from pyrogram import Client, filters, enums
from pyrogram.enums import ChatType
from pyrogram.raw import functions
from pyrogram.types import ReplyParameters 

import database
from gemini_core import generate_ai_response, transcribe_media
from modules.youtube import fetch_youtube_data_sync
from modules.ai_utils import (
    skip_video_timers, clean_old_memory_cache, get_cached_media_data, 
    save_media_data, introduce_typo, simulate_human_typing, generate_media_description
)
from i18n import _

active_reply_tasks = {}

def register_userbot(app: Client):
    async def process_reply(client, message):
        clean_old_memory_cache()
        media_paths_to_cleanup = []
        
        try:
            chat_id = message.chat.id
            config_db = await database.get_config()
            if not config_db: return

            debug_log = bool(config_db[28]) if len(config_db) > 28 else False

            if config_db:
                sleep_start, sleep_end = config_db[2], config_db[3]
                if sleep_start and sleep_end:
                    now_str = datetime.now().strftime("%H:%M")
                    if sleep_start <= sleep_end:
                        if sleep_start <= now_str <= sleep_end: 
                            if debug_log: logging.info(_("ai_log_skip_sleep", chat_id=chat_id))
                            return
                    else:
                        if now_str >= sleep_start or now_str <= sleep_end: 
                            if debug_log: logging.info(_("ai_log_skip_sleep", chat_id=chat_id))
                            return

            is_global_ai = config_db[12] if (config_db and len(config_db) > 12) else False
            chat_cfg = await database.get_chat_settings(chat_id)
            chat_is_active = chat_cfg[0] if chat_cfg else False
            is_ignored = chat_cfg[6] if (chat_cfg and len(chat_cfg) > 6) else False
            
            if not is_global_ai and not chat_is_active: return 
            if is_global_ai and not chat_is_active and is_ignored: return
            
            typing_speed = config_db[5] if len(config_db) > 5 and config_db[5] is not None else 0.08
            glob_db_min = config_db[6] if len(config_db) > 6 and config_db[6] is not None else 1
            glob_db_max = config_db[7] if len(config_db) > 7 and config_db[7] is not None else 3
            glob_da_min = config_db[8] if len(config_db) > 8 and config_db[8] is not None else 1
            glob_da_max = config_db[9] if len(config_db) > 9 and config_db[9] is not None else 3
            
            g_reaction = config_db[14] if len(config_db) > 14 and config_db[14] else "👍"
            g_h_typing = bool(config_db[15]) if len(config_db) > 15 else True
            g_h_ignore = config_db[16] if len(config_db) > 16 else 10
            g_h_smart = bool(config_db[17]) if len(config_db) > 17 else True
            g_s_mul = config_db[18] if len(config_db) > 18 and config_db[18] is not None else 0.05
            g_tmin = config_db[19] if len(config_db) > 19 and config_db[19] is not None else 1.5
            g_tmax = config_db[20] if len(config_db) > 20 and config_db[20] is not None else 3.5
            g_pmin = config_db[21] if len(config_db) > 21 and config_db[21] is not None else 0.5
            g_pmax = config_db[22] if len(config_db) > 22 and config_db[22] is not None else 2.0

            if chat_cfg:
                c_h_typing = chat_cfg[8] if chat_cfg[8] is not None else 2
                c_h_ignore = chat_cfg[9] if chat_cfg[9] is not None else -1
                c_h_smart = chat_cfg[10] if chat_cfg[10] is not None else 2
                c_s_mul = chat_cfg[11] if len(chat_cfg) > 11 and chat_cfg[11] is not None else g_s_mul
                c_tmin = chat_cfg[12] if len(chat_cfg) > 12 and chat_cfg[12] is not None else g_tmin
                c_tmax = chat_cfg[13] if len(chat_cfg) > 13 and chat_cfg[13] is not None else g_tmax
                c_pmin = chat_cfg[14] if len(chat_cfg) > 14 and chat_cfg[14] is not None else g_pmin
                c_pmax = chat_cfg[15] if len(chat_cfg) > 15 and chat_cfg[15] is not None else g_pmax
            else:
                c_h_typing, c_h_ignore, c_h_smart = 2, -1, 2
                c_s_mul, c_tmin, c_tmax, c_pmin, c_pmax = g_s_mul, g_tmin, g_tmax, g_pmin, g_pmax
                
            use_h_typing = g_h_typing if c_h_typing == 2 else bool(c_h_typing)
            use_h_smart = g_h_smart if c_h_smart == 2 else bool(c_h_smart)
            use_h_ignore = g_h_ignore if c_h_ignore == -1 else c_h_ignore
            
            if chat_cfg and len(chat_cfg) > 7 and chat_cfg[7] is not None:
                search_enabled = bool(chat_cfg[7])
            else:
                search_enabled = bool(config_db[13]) if (config_db and len(config_db) > 13) else True
                
            global_prompt = config_db[4] if len(config_db) > 4 and config_db[4] else ""
            c_prompt = chat_cfg[1] if chat_cfg and len(chat_cfg) > 1 and chat_cfg[1] else ""
            
            final_prompt = global_prompt
            if c_prompt:
                final_prompt += _("ai_additional_rules", prompt=c_prompt)
            
            text_to_search = message.text or message.caption or ""
            yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', text_to_search)
            all_links = re.findall(r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)', text_to_search)
            non_yt_links = [l for l in all_links if not any(yt in l for yt in yt_links)]
            
            if len(non_yt_links) > 0:
                search_enabled = True
                link_rule = _("ai_link_rule")
                final_prompt = (final_prompt + link_rule) if final_prompt else link_rule

            if search_enabled:
                search_rule = f"\n\n{_('ai_prompt_rule_search')}"
                final_prompt = (final_prompt + search_rule) if final_prompt else search_rule

            chat_name = message.from_user.first_name if message.from_user else (message.chat.title or _("other_sender"))
            raw_history = []
            async for msg in client.get_chat_history(chat_id, limit=50):
                raw_history.append(msg)
                
            history_lines = []
            last_date_str = None
            live_media_for_gemini = None 
            
            for msg in reversed(raw_history):
                if await database.is_ignored_msg(chat_id, msg.id): continue

                msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                current_date_str = msg_date.strftime("%d %B %Y")
                time_str = msg_date.strftime("%H:%M")
                
                if current_date_str != last_date_str:
                    history_lines.append(f"\n----- {current_date_str} -----")
                    last_date_str = current_date_str
                    
                sender = _("me_sender") if (msg.from_user and msg.from_user.is_self) else chat_name
                text = msg.text or msg.caption or ""  
                
                forward_prefix = ""
                if getattr(msg, 'forward_origin', None):
                    origin = msg.forward_origin
                    f_name = _("someone")
                    if getattr(origin, 'sender_user', None): f_name = origin.sender_user.first_name
                    elif getattr(origin, 'sender_user_name', None): f_name = origin.sender_user_name
                    elif getattr(origin, 'chat', None): f_name = origin.chat.title or origin.chat.first_name
                    forward_prefix = _("ai_forwarded_from", name=f_name)
                
                yt_links_hist = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', text)
                yt_context_str = ""
                if yt_links_hist:
                    for y_url in yt_links_hist:
                        dur, y_ctx = fetch_youtube_data_sync(y_url)
                        if y_ctx: yt_context_str += _("ai_yt_context_inline", ctx=y_ctx)

                if not text and not msg.photo and not msg.video and not msg.voice and not msg.video_note and not msg.sticker:
                    text = _("ai_msg_file")

                if msg.voice or msg.video_note:
                    cached_audio = get_cached_media_data(msg.id, "transcript")
                    if cached_audio: text = _("ai_voice_memory", text=cached_audio)
                    else:
                        m_ext = ".ogg" if msg.voice else ".mp4"
                        dl_path = await client.download_media(msg, file_name=f"data/{msg.id}_audio{m_ext}")
                        if dl_path:
                            media_paths_to_cleanup.append(dl_path)
                            transc = await transcribe_media(dl_path)
                            if transc:
                                save_media_data(msg.id, "transcript", transc)
                                text = _("ai_voice_memory", text=transc)
                            else: text = _("ai_msg_voice")
                        else: text = _("ai_msg_voice")

                elif msg.photo or msg.video:
                    m_type_tag = _("ai_tag_photo") if msg.photo else _("ai_tag_video")
                    is_current = (msg.id == message.id)

                    if is_current:
                        text = _("ai_media_current", type=m_type_tag, id=msg.id, text=text)
                        m_ext = ".jpg" if msg.photo else ".mp4"
                        dl_path = await client.download_media(msg, file_name=f"data/{msg.id}_media{m_ext}")
                        if dl_path:
                            live_media_for_gemini = dl_path
                            media_paths_to_cleanup.append(dl_path)
                    else:
                        cached_desc = get_cached_media_data(msg.id, "description")
                        if cached_desc:
                            text = _("ai_media_memory_desc", type=m_type_tag, id=msg.id, desc=cached_desc, text=text)
                        else:
                            m_ext = ".jpg" if msg.photo else ".mp4"
                            dl_path = await client.download_media(msg, file_name=f"data/{msg.id}_media{m_ext}")
                            if dl_path:
                                media_paths_to_cleanup.append(dl_path)
                                desc = await generate_media_description(dl_path)
                                save_media_data(msg.id, "description", desc)
                                text = _("ai_media_memory_desc", type=m_type_tag, id=msg.id, desc=desc, text=text)

                if msg.sticker: text = _("ai_msg_sticker", emoji=msg.sticker.emoji if hasattr(msg.sticker, 'emoji') and msg.sticker.emoji else "")

                full_msg_text = f"[{time_str}] {sender}: {forward_prefix}{text}{yt_context_str}"
                if msg.id == message.id: full_msg_text = _("ai_current_msg_prefix", text=full_msg_text)
                history_lines.append(full_msg_text)

            history_str = _("ai_dialog_context_header", me=_("me_sender"), other=chat_name) + "\n".join(history_lines)
            
            if message.reply_to_message:
                orig = message.reply_to_message
                orig_sender = _("me_sender") if (orig.from_user and orig.from_user.is_self) else chat_name
                orig_text = orig.text or orig.caption or _("media_file_placeholder")
                if len(orig_text) > 400: orig_text = orig_text[:400] + "..."
                history_str += _("ai_reply_alert", text=text_to_search, sender=orig_sender, orig=orig_text)

            video_too_long = False
            latest_media_duration = 0
            
            if use_h_smart:
                for msg in raw_history:
                    if msg.from_user and msg.from_user.is_self: break 
                    if msg.voice:
                        latest_media_duration = getattr(msg.voice, 'duration', 5)
                        break
                    elif msg.video_note:
                        latest_media_duration = getattr(msg.video_note, 'duration', 5)
                        break
                    elif msg.video:
                        latest_media_duration = getattr(msg.video, 'duration', 5)
                        break
                    else:
                        text_tmp = msg.text or msg.caption or ""
                        yt_links_recent = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', text_tmp)
                        if yt_links_recent:
                            dur, _ignored_ctx = fetch_youtube_data_sync(yt_links_recent[0])
                            latest_media_duration = dur
                            break
                            
                if latest_media_duration > 1800:
                    video_too_long = True
                    history_str += _("ai_video_too_long_alert", mins=int(latest_media_duration/60))
                elif latest_media_duration > 0:
                    if debug_log: logging.info(_("log_timings_unread_media", dur=latest_media_duration))

            history_str += _("ai_sys_instructions", prompt=final_prompt)

            if debug_log:
                logging.info("="*50)
                logging.info(_("log_llm_req_main_chat"))
                logging.info(_("log_full_prompt", prompt=history_str))
                logging.info(_("log_attached_live_media", path=live_media_for_gemini))
                logging.info("="*50)

            ai_generate_task = asyncio.create_task(
                generate_ai_response(history_str, live_media_for_gemini, custom_prompt="", search_enabled=search_enabled)
            )

            c_db_min = chat_cfg[2] if (chat_cfg and chat_cfg[2] is not None) else glob_db_min
            c_db_max = chat_cfg[3] if (chat_cfg and chat_cfg[3] is not None) else glob_db_max
            c_da_min = chat_cfg[4] if (chat_cfg and chat_cfg[4] is not None) else glob_da_min
            c_da_max = chat_cfg[5] if (chat_cfg and chat_cfg[5] is not None) else glob_da_max

            delay_before = random.randint(c_db_min, c_db_max)
            if delay_before > 0:
                await asyncio.sleep(delay_before)

            is_question = bool(re.search(r'\?|как|почему|зачем|что|где|когда|чей|кого|кому', text_to_search.lower()))
            if not is_question and use_h_ignore > 0:
                if random.randint(1, 100) <= use_h_ignore:
                    ai_generate_task.cancel() 
                    try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
                    except: pass
                    await asyncio.sleep(1.0)
                    try:
                        await client.read_chat_history(chat_id)
                        if message.voice or message.video_note or message.video:
                            await client.invoke(functions.messages.ReadMessageContents(id=[message.id]))
                    except: pass
                    if random.random() < 0.5:
                        try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=(int(g_reaction) if g_reaction.isdigit() else g_reaction))
                        except: pass
                    return

            try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
            except: pass
            await asyncio.sleep(1.0)
            try:
                await client.read_chat_history(chat_id)
                if message.voice or message.video_note or message.video:
                    await client.invoke(functions.messages.ReadMessageContents(id=[message.id]))
            except: pass

            smart_delay = 0
            if use_h_smart:
                if video_too_long:
                    smart_delay = len(text_to_search) * c_s_mul
                elif latest_media_duration > 0:
                    smart_delay = latest_media_duration
                else:
                    smart_delay = len(text_to_search) * c_s_mul

            if smart_delay > 0:
                elapsed_wait = 0
                while elapsed_wait < smart_delay:
                    if chat_id in skip_video_timers:
                        skip_video_timers.remove(chat_id)
                        if debug_log: logging.info(_("log_skip_delay", chat_id=chat_id))
                        break
                    await asyncio.sleep(1)
                    elapsed_wait += 1

            try:
                reply = await ai_generate_task
            except asyncio.CancelledError: return
            except Exception as e: reply = None

            if not reply or reply == "⏳": return
            
            if debug_log:
                logging.info(_("log_llm_res_main", reply=reply))

            reply_upper = reply.upper().strip()
            
            if reply_upper.startswith("[LIKE]"):
                try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=(int(g_reaction) if g_reaction.isdigit() else g_reaction))
                except: pass
                return
                
            if "[LIKE]" in reply_upper:
                try: await client.send_reaction(chat_id=chat_id, message_id=message.id, emoji=(int(g_reaction) if g_reaction.isdigit() else g_reaction))
                except: pass
                reply = re.sub(r'(?i)\[LIKE\]', '', reply).strip()

            if not reply: 
                return 

            delay_after = random.randint(c_da_min, c_da_max)
            if delay_after > 0:
                await asyncio.sleep(delay_after)

            parts = []
            for p in reply.split('\n'):
                p = p.strip()
                if p:
                    while len(p) > 4000:
                        parts.append(p[:4000])
                        p = p[4000:]
                    if p: parts.append(p)

            use_reply = random.random() < 0.25 
            for i, part in enumerate(parts):
                typing_time = min(len(part) * float(typing_speed), 10.0) 
                await simulate_human_typing(client, chat_id, typing_time, use_h_typing, c_tmin, c_tmax, c_pmin, c_pmax)
                
                use_typo = random.random() < 0.05
                final_part = introduce_typo(part) if use_typo else part
                
                reply_params = ReplyParameters(message_id=message.id) if (i == 0 and use_reply) else None
                sent_msg = await client.send_message(chat_id, final_part, reply_parameters=reply_params)
                
                try: await client.send_chat_action(chat_id, enums.ChatAction.CANCEL)
                except: pass
                
                if use_typo and final_part != part:
                    await asyncio.sleep(random.uniform(3, 10.0))
                    try: await sent_msg.edit_text(part)
                    except: pass
                
                if i < len(parts) - 1:
                    await asyncio.sleep(random.uniform(0.5, 2.0)) 

        except asyncio.CancelledError: pass
        except Exception as e: logging.error(_("ai_log_chat_error", chat_id=message.chat.id, e=e))
        finally:
            for p in media_paths_to_cleanup:
                if p and os.path.exists(p):
                    try: os.remove(p)
                    except: pass
            if active_reply_tasks.get(message.chat.id) == asyncio.current_task():
                del active_reply_tasks[message.chat.id]

    @app.on_message(filters.private & ~filters.me)
    async def ai_auto_reply(client, message):
        if message.chat.type != ChatType.PRIVATE: return
        if message.from_user and message.from_user.is_bot: return
        if message.from_user and message.from_user.id == 777000: return
        chat_id = message.chat.id
        if chat_id in active_reply_tasks:
            active_reply_tasks[chat_id].cancel()
        task = asyncio.create_task(process_reply(client, message))
        active_reply_tasks[chat_id] = task
