import os
import re
import html
import asyncio
import logging
from pyrogram import Client, filters

from gemini_core import generate_ai_response, transcribe_media
from utils import simulate_typing
from modules.youtube import fetch_youtube_data_sync
from i18n import _

def register_userbot(app: Client):
    
    # Кастомный фильтр для триггера ИИ
    async def is_ai_target(_, __, message):
        # 1. Если это ручная команда .ai (строго только твои сообщения)
        if message.from_user and message.from_user.is_self:
            if message.text and re.match(r"^\.ai(?:\s+|$)", message.text):
                return True
        
        # 2. Если это ответ (твой или чужой) на предыдущее сообщение от ИИ
        if message.reply_to_message and message.reply_to_message.text:
            # Ищем маркер ответа. Если в i18n он другой, поменяй строку ниже!
            if "🤖 Ответ Gemini:" in message.reply_to_message.text:
                # Исключаем ручную команду, чтобы фильтры не конфликтовали
                if message.text and not message.text.startswith(".ai"):
                    return True
        return False

    ai_trigger = filters.create(is_ai_target)

    @app.on_message(ai_trigger)
    async def handle_ai_request(client, message):
        try:
            # Определяем, был ли это ручной вызов .ai или автоматический реплай
            is_manual = bool(message.from_user and message.from_user.is_self and message.text and message.text.startswith(".ai"))
            
            query = ""
            if is_manual:
                match = re.match(r"^\.ai(?:\s+(.*))?", message.text or message.caption or "", flags=re.DOTALL)
                if match and match.group(1):
                    query = match.group(1).strip()
            else:
                # Если авто-реплай, весь текст сообщения это и есть запрос
                query = message.text or message.caption or ""

            # Плашка статуса "Думаю..."
            status_msg = None
            if is_manual:
                # Свою команду .ai можно просто заменить
                status_msg = await message.edit(_("cmd_ai_thinking"))
            else:
                # На обычный реплай (даже свой) отвечаем новым сообщением, чтобы не стереть запрос из истории
                status_msg = await message.reply(_("cmd_ai_thinking"))
            
            typing_task = asyncio.create_task(simulate_typing(client, message.chat.id, 10))
            
            # Определяем целевое сообщение для поиска медиа и ссылок
            target_msg = message.reply_to_message if (is_manual and message.reply_to_message) else message
            
            media_path = None
            transcript = ""
            yt_context = ""

            media_ext = ""
            if target_msg.photo: media_ext = ".jpg"
            elif target_msg.voice: media_ext = ".ogg"
            elif target_msg.video or target_msg.video_note: media_ext = ".mp4"
            elif target_msg.audio: media_ext = ".mp3"
            elif target_msg.document: 
                ext = target_msg.document.file_name.split('.')[-1].lower() if target_msg.document.file_name and '.' in target_msg.document.file_name else "file"
                media_ext = f".{ext}"
            
            if media_ext:
                logging.info(_("cmd_ai_downloading", media_ext=media_ext))
                media_path = await target_msg.download(file_name=f"data/ai_auto_{message.id}{media_ext}")
                if media_path and media_path.lower().endswith((".ogg", ".oga", ".mp4", ".mov", ".avi", ".mp3", ".wav", ".m4a")):
                    transcript = await transcribe_media(media_path)

            text_to_search = target_msg.text or target_msg.caption or ""
            if text_to_search:
                yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s]+)', text_to_search)
                if yt_links:
                    logging.info(_("cmd_ai_yt_found"))
                    _dur, yt_context = await asyncio.to_thread(fetch_youtube_data_sync, yt_links[0])

            # Собираем контекст из последних сообщений (он сам захватит предыдущий ответ Gemini)
            hist = []
            async for m in client.get_chat_history(message.chat.id, limit=6):
                if m.id == message.id: continue
                sender = _("me_sender") if (m.from_user and m.from_user.is_self) else _("other_sender")
                hist.append(f"{sender}: {m.text or m.caption or _('media_file_placeholder')}")
            hist.reverse()
            hist_str = "\n".join(hist)
            
            full_query = _("cmd_ai_context_dialogue", hist_str=hist_str)
            
            if is_manual and message.reply_to_message:
                orig_sender = _("me_sender") if (target_msg.from_user and target_msg.from_user.is_self) else _("other_sender")
                full_query += _("cmd_ai_context_reply", orig_sender=orig_sender, text_to_search=text_to_search)
            
            if transcript:
                full_query += _("cmd_ai_context_transcript", transcript=transcript)
            elif media_path and not transcript:
                full_query += _("cmd_ai_context_media_sys")
                
            if yt_context:
                full_query += _("cmd_ai_context_yt", yt_context=yt_context)
            
            if not query:
                query = _("cmd_ai_default_query")
                
            full_query += _("cmd_ai_task_query", query=query)
            
            reply = await generate_ai_response(
                full_query, 
                media_path=media_path,
                custom_prompt=_("cmd_ai_custom_prompt")
            )
            
            typing_task.cancel()
            
            parts = []
            while len(reply) > 4000:
                parts.append(reply[:4000])
                reply = reply[4000:]
            if reply:
                parts.append(reply)
            
            # Отправляем ответ
            for i, part in enumerate(parts):
                if i == 0:
                    safe_query = html.escape(query if query != _("cmd_ai_default_query") else _("cmd_ai_safe_query_fallback"))
                    text = _("cmd_ai_reply_format", safe_query=safe_query, part=part)
                else:
                    text = part
                
                if i == 0:
                    # Редактируем первую часть прямо в плашке "Думаю..."
                    await status_msg.edit(text)
                else:
                    # Если кусков несколько, шлем их реплаем друг на друга
                    await client.send_message(message.chat.id, text, reply_to_message_id=status_msg.id)

        except Exception as e:
            logging.error(_("cmd_ai_log_error", e=e))
            if 'status_msg' in locals() and status_msg:
                await status_msg.edit(_("cmd_ai_error_msg", e=e))
        finally:
            if 'media_path' in locals() and media_path and os.path.exists(media_path):
                try: os.remove(media_path)
                except: pass