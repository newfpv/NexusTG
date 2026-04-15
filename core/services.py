import os
import re
import time
import asyncio
import logging
import requests
import yt_dlp
from google import genai
from google.genai import types as genai_types
from typing import AsyncIterable
from datetime import timezone

from core.config import _
from core.db import AsyncSessionLocal, CoreRepository, YoutubeCache

GEMINI_TIMEOUT = 25.0
MAX_RETRIES_PER_KEY = 2
CIRCUIT_BREAKER_THRESHOLD = 5

circuit_failures = {}
circuit_last_failure = {}

def get_model_config(search_enabled=True):
    tools = [{"google_search": {}}] if search_enabled else None
    return genai_types.GenerateContentConfig(
        safety_settings=[
            genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
            genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
            genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
            genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
        ],
        tools=tools
    )

async def _call_gemini_with_timeout(client, model_name, contents, config):
    try:
        return await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model_name,
                contents=contents,
                config=config
            ),
            timeout=GEMINI_TIMEOUT
        )
    except asyncio.TimeoutError:
        logging.warning(_("log_gemini_timeout", model_name=model_name))
        return None

def _check_circuit_breaker(api_key: str) -> bool:
    now = time.time()
    failures = circuit_failures.get(api_key, 0)
    last_fail = circuit_last_failure.get(api_key, 0)
    
    if failures >= CIRCUIT_BREAKER_THRESHOLD:
        if now - last_fail < 300:
            return False
        circuit_failures[api_key] = 0
    
    return True

def _record_failure(api_key: str):
    circuit_failures[api_key] = circuit_failures.get(api_key, 0) + 1
    circuit_last_failure[api_key] = time.time()

async def generate_ai_response(prompt_context: str, media_path: str = None, custom_prompt: str = None, search_enabled: bool = True) -> str:
    logging.info(_("log_generate_start"))
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        config = await repo.get_global_config()
        
    api_keys = [k.strip() for k in (config.api_keys or "").split(",") if k.strip()]
    model_fallback_list = [m.strip() for m in (config.model_fallback_list or "gemini-2.5-flash-lite").split(",") if m.strip()]
    
    if not api_keys:
        return _("err_no_api_keys")

    contents = [_("context_assembly", custom_prompt=custom_prompt or "", prompt_context=prompt_context)]
    
    if media_path and os.path.exists(media_path):
        try:
            ext = media_path.lower()
            mime_type = "image/jpeg"
            if ext.endswith((".ogg", ".oga")): mime_type = "audio/ogg"
            elif ext.endswith(".mp3"): mime_type = "audio/mp3"
            elif ext.endswith(".wav"): mime_type = "audio/wav"
            elif ext.endswith((".mp4", ".mov", ".avi")): mime_type = "video/mp4"

            with open(media_path, "rb") as f:
                contents.append(genai_types.Part.from_bytes(data=f.read(), mime_type=mime_type))
        except Exception as e:
            logging.error(_("log_media_attach_error", e=e))

    for model_name in model_fallback_list:
        for api_key in api_keys:
            if not _check_circuit_breaker(api_key):
                continue
                
            async with AsyncSessionLocal() as session:
                repo = CoreRepository(session)
                state = await repo.get_ai_key_state(api_key)
                current_time = time.time()
                
                if current_time < state.unban_time: continue
                if model_name in (state.exhausted_models or {}) and current_time < state.exhausted_models[model_name]:
                    continue
                
                actual_search = search_enabled
                if search_enabled:
                    if current_time < state.search_unban_time or (model_name in (state.search_exhausted_models or {}) and current_time < state.search_exhausted_models[model_name]):
                        actual_search = False

                try:
                    client = genai.Client(api_key=api_key)
                    response = await _call_gemini_with_timeout(client, model_name, contents, get_model_config(search_enabled=actual_search))
                    
                    if response is None:
                        _record_failure(api_key)
                        continue
                        
                    if response.text and response.text.strip():
                        logging.info(_("log_ai_generated", model=model_name, search="on" if actual_search else "off"))
                        circuit_failures[api_key] = 0
                        return response.text
                    else:
                        continue
                        
                except Exception as e:
                    err_str = str(e).lower()
                    new_exh = dict(state.exhausted_models or {})
                    new_s_exh = dict(state.search_exhausted_models or {})
                    key_hidden = f"{api_key[:4]}***{api_key[-4:]}"
                    
                    if any(x in err_str for x in ["500", "503", "502"]):
                        _record_failure(api_key)
                        for k in api_keys:
                            k_state = await repo.get_ai_key_state(k)
                            k_exh = dict(k_state.exhausted_models or {})
                            k_exh[model_name] = current_time + 180
                            await repo.update_ai_key_state(k, exhausted_models=k_exh)
                        logging.warning(_("log_model_banned_global", model=model_name))
                        break 
                        
                    elif "429" in err_str:
                        _record_failure(api_key)
                        if "search" in err_str or "grounding" in err_str:
                            new_s_exh[model_name] = current_time + 60
                            await repo.update_ai_key_state(api_key, search_exhausted_models=new_s_exh)
                            logging.warning(_("log_model_banned_search", model=model_name, key=key_hidden))
                        else:
                            new_exh[model_name] = current_time + 60
                            await repo.update_ai_key_state(api_key, exhausted_models=new_exh)
                            logging.warning(_("log_model_banned_local", model=model_name, key=key_hidden))
                            
                    elif "400" in err_str:
                        await repo.update_ai_key_state(api_key, unban_time=current_time + 10)
                        
                    continue
                    
    return _("status_waiting")

async def generate_ai_response_stream(prompt_context: str, media_path: str = None, custom_prompt: str = None, search_enabled: bool = True) -> AsyncIterable[str]:
    logging.info(_("log_generate_start"))
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        config = await repo.get_global_config()
        
    api_keys = [k.strip() for k in (config.api_keys or "").split(",") if k.strip()]
    model_fallback_list = [m.strip() for m in (config.model_fallback_list or "gemini-2.5-flash-lite").split(",") if m.strip()]
    
    if not api_keys:
        yield _("err_no_api_keys")
        return

    contents = [_("context_assembly", custom_prompt=custom_prompt or "", prompt_context=prompt_context)]
    
    if media_path and os.path.exists(media_path):
        try:
            mime_type = "image/jpeg"
            if media_path.lower().endswith((".mp4", ".mov")): mime_type = "video/mp4"
            elif media_path.lower().endswith((".ogg", ".mp3")): mime_type = "audio/mpeg"
            with open(media_path, "rb") as f:
                contents.append(genai_types.Part.from_bytes(data=f.read(), mime_type=mime_type))
        except Exception: pass

    for model_name in model_fallback_list:
        for api_key in api_keys:
            if not _check_circuit_breaker(api_key):
                continue
                
            async with AsyncSessionLocal() as session:
                repo = CoreRepository(session)
                state = await repo.get_ai_key_state(api_key)
                current_time = time.time()

                if current_time < state.unban_time: continue
                if model_name in (state.exhausted_models or {}) and current_time < state.exhausted_models[model_name]:
                    continue
                
                actual_search = search_enabled
                if search_enabled:
                    if current_time < state.search_unban_time or (model_name in (state.search_exhausted_models or {}) and current_time < state.search_exhausted_models[model_name]):
                        actual_search = False

                try:
                    client = genai.Client(api_key=api_key)
                    got_response = False
                    
                    async def _stream_with_timeout():
                        async for chunk in client.aio.models.generate_content_stream(
                            model=model_name,
                            contents=contents,
                            config=get_model_config(search_enabled=actual_search)
                        ):
                            yield chunk
                    
                    try:
                        async for chunk in asyncio.wait_for(_stream_with_timeout(), timeout=GEMINI_TIMEOUT * 2):
                            if chunk.text and chunk.text.strip():
                                if not got_response:
                                    logging.info(_("log_ai_generated", model=model_name, search="on" if actual_search else "off"))
                                    got_response = True
                                    circuit_failures[api_key] = 0
                                yield chunk.text
                        if got_response: return
                    except asyncio.TimeoutError:
                        _record_failure(api_key)
                        if got_response: return
                        continue
                        
                except Exception as e:
                    err_str = str(e).lower()
                    new_exh = dict(state.exhausted_models or {})
                    new_s_exh = dict(state.search_exhausted_models or {})
                    key_hidden = f"{api_key[:4]}***{api_key[-4:]}"
                    
                    if any(x in err_str for x in ["500", "503", "502"]):
                        _record_failure(api_key)
                        for k in api_keys:
                            k_state = await repo.get_ai_key_state(k)
                            k_exh = dict(k_state.exhausted_models or {})
                            k_exh[model_name] = current_time + 180
                            await repo.update_ai_key_state(k, exhausted_models=k_exh)
                        logging.warning(_("log_model_banned_global", model=model_name))
                        break 
                        
                    elif "429" in err_str:
                        _record_failure(api_key)
                        if "search" in err_str or "grounding" in err_str:
                            new_s_exh[model_name] = current_time + 60
                            await repo.update_ai_key_state(api_key, search_exhausted_models=new_s_exh)
                            logging.warning(_("log_model_banned_search", model=model_name, key=key_hidden))
                        else:
                            new_exh[model_name] = current_time + 60
                            await repo.update_ai_key_state(api_key, exhausted_models=new_exh)
                            logging.warning(_("log_model_banned_local", model=model_name, key=key_hidden))
                            
                    elif "400" in err_str:
                        await repo.update_ai_key_state(api_key, unban_time=current_time + 10)
                        
                    continue

async def test_ai_credentials(progress_cb=None) -> str:
    logging.info(_("log_test_start"))
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        config = await repo.get_global_config()
        
    if not config.api_keys or not config.model_fallback_list:
        return _("test_no_data")

    api_keys = [k.strip() for k in config.api_keys.split(",") if k.strip()]
    models = [m.strip() for m in config.model_fallback_list.split(",") if m.strip()]
    
    total_steps = len(api_keys) * len(models)
    current_step = 0
    final_report = _("test_result_title")
    is_cancelled = False
    
    for key in api_keys:
        if is_cancelled: break
        key_hidden = f"{key[:4]}***{key[-4:]}"
        final_report += _("test_key_status", key_hidden=key_hidden, status="")
        
        for model in models:
            current_step += 1
            if progress_cb:
                should_continue = await progress_cb(_("test_progress", key_hidden=key_hidden, model=model, current=current_step, total=total_steps))
                if should_continue is False:
                    is_cancelled = True
                    break
                await asyncio.sleep(0.1)

            try:
                client = genai.Client(api_key=key)
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(model=model, contents=[_("ping_prompt")]),
                    timeout=15.0
                )
                res_text = _("test_ok")
            except asyncio.TimeoutError:
                res_text = _("test_error") + " (timeout)"
            except Exception as e:
                res_text = f"{_('test_error')} ({str(e)[:15]}...)"
            
            final_report += _("test_model_status", model=model, res=res_text)
        final_report += "\n"
        
    if is_cancelled: final_report += "\n" + _("test_cancelled_msg")
    logging.info(_("log_test_complete"))
    return final_report

async def transcribe_media(media_path: str) -> str:
    if not os.path.exists(media_path): return ""
    try:
        text = await generate_ai_response(
            prompt_context=_("prompt_transcribe"), 
            media_path=media_path, 
            search_enabled=False
        )
        return text if text != _("status_waiting") else ""
    except Exception as e:
        logging.error(_("log_transcribe_error", e=e))
        return ""

COOKIES_PATH = "data/cookies.txt"

def extract_youtube_id(url: str) -> str | None:
    patterns = [r"(?:v=|\/)([0-9A-Za-z_-]{11}).*", r"youtu\.be\/([0-9A-Za-z_-]{11})", r"shorts\/([0-9A-Za-z_-]{11})"]
    for p in patterns:
        match = re.search(p, url)
        if match: return match.group(1)
    return None

def _fetch_yt_sync(url: str, video_id: str) -> tuple[int, str]:
    ydl_opts = {
        'quiet': True, 'skip_download': True, 'writesubtitles': True,
        'writeautomaticsub': True, 'subtitleslangs': ['ru', 'en'],
        'subtitlesformat': 'json3/vtt/best', 'ignore_no_formats_error': True
    }
    if os.path.exists(COOKIES_PATH): ydl_opts['cookiefile'] = COOKIES_PATH
    duration, context = 0, ""
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            context += _("yt_title_desc", title=info.get('title', ''), desc=info.get('description', ''))
            duration = info.get('duration', 0)
            subs = info.get('requested_subtitles', {})
            for lang in ['ru', 'en']:
                if lang in subs and subs[lang].get('url'):
                    resp = requests.get(subs[lang]['url'], timeout=10)
                    if resp.status_code == 200:
                        context += _("yt_subs_text", text=resp.text[:50000])
                        break
    except Exception as e:
        logging.error(_("log_yt_parse_error", e=e))
    return duration, context

async def get_youtube_context(url: str) -> tuple[int, str]:
    video_id = extract_youtube_id(url)
    if not video_id: return 0, _("yt_url_fallback")
    async with AsyncSessionLocal() as session:
        cached = await session.get(YoutubeCache, video_id)
        if cached: return cached.duration, cached.context
        duration, context = await asyncio.to_thread(_fetch_yt_sync, url, video_id)
        session.add(YoutubeCache(video_id=video_id, duration=duration, context=context))
        await session.commit()
        return duration, context

async def generate_media_description(media_path: str) -> str:
    try:
        res = await generate_ai_response(_("ai_media_desc_prompt"), media_path, search_enabled=False)
        if not res or res == _("status_waiting"): return _("ai_media_desc_unavailable")
        return res
    except Exception: return _("ai_media_desc_failed")

async def enrich_text_with_links(text: str) -> tuple[str, bool]:
    search_needed = False
    enriched_text = text
    yt_links = re.findall(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be|youtube\.com/shorts)/[^\s]+)', text)
    all_links = re.findall(r'(?:https?://)?(?:www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b(?:[-a-zA-Z0-9()@:%_\+.~#?&//=]*)', text)
    non_yt_links = [l for l in all_links if not any(yt in l for yt in yt_links)]
    if non_yt_links: search_needed = True
    yt_context_str = ""
    if yt_links:
        for y_url in yt_links:
            try:
                dur, y_ctx = await get_youtube_context(y_url)
                if y_ctx: yt_context_str += _("ai_yt_context_inline", ctx=y_ctx)
            except: pass
    if yt_context_str: enriched_text = f"{text}{yt_context_str}"
    return enriched_text, search_needed

async def build_dialog_context(client, chat_id: int, limit: int, target_msg_id: int = None, chat_name: str = None) -> tuple[str, list, int, bool]:
    history_lines = []
    media_paths_to_cleanup = []
    last_date_str = None
    latest_media_duration = 0
    video_too_long = False

    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        messages = [msg async for msg in client.get_chat_history(chat_id, limit=limit)]
        media_tasks = []

        async def fetch_audio(msg):
            m_ext = ".ogg" if msg.voice else ".mp4"
            dl_path = await client.download_media(msg, file_name=f"data/{msg.id}_audio{m_ext}")
            if dl_path:
                media_paths_to_cleanup.append(dl_path)
                transc = await transcribe_media(dl_path)
                if transc: await repo.save_media_memory(msg.id, "transcript", transc)

        async def fetch_video(msg):
            m_ext = ".jpg" if msg.photo else ".mp4"
            dl_path = await client.download_media(msg, file_name=f"data/{msg.id}_media{m_ext}")
            if dl_path:
                media_paths_to_cleanup.append(dl_path)
                desc = await generate_media_description(dl_path)
                if desc: await repo.save_media_memory(msg.id, "description", desc)

        for msg in messages:
            is_ignored_msg = False
            try: is_ignored_msg = await repo.is_msg_ignored(chat_id, msg.id)
            except Exception: pass
            if is_ignored_msg: continue

            if msg.voice or msg.video_note:
                if latest_media_duration == 0 and not (msg.from_user and msg.from_user.is_self):
                    latest_media_duration = getattr(msg.voice, 'duration', getattr(msg.video_note, 'duration', 5))
                    if latest_media_duration > 1800: video_too_long = True
                if not await repo.get_media_memory(msg.id, "transcript"): media_tasks.append(fetch_audio(msg))
            elif msg.photo or msg.video:
                if msg.video and latest_media_duration == 0 and not (msg.from_user and msg.from_user.is_self):
                    latest_media_duration = getattr(msg.video, 'duration', 5)
                    if latest_media_duration > 1800: video_too_long = True
                if not (target_msg_id and msg.id == target_msg_id):
                    if not await repo.get_media_memory(msg.id, "description"): media_tasks.append(fetch_video(msg))

        if media_tasks: await asyncio.gather(*media_tasks, return_exceptions=True)

        for msg in messages:
            is_ignored_msg = False
            try: is_ignored_msg = await repo.is_msg_ignored(chat_id, msg.id)
            except Exception: pass
            if is_ignored_msg: continue

            msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
            current_date_str = msg_date.strftime("%d %B %Y")
            time_str = msg_date.strftime("%H:%M")
            if current_date_str != last_date_str:
                history_lines.insert(0, _("ai_date_divider", date=current_date_str))
                last_date_str = current_date_str

            sender = _("me_sender") if (msg.from_user and msg.from_user.is_self) else (chat_name or _("other_sender"))
            text = msg.text or msg.caption or ""
            forward_prefix = ""
            if getattr(msg, 'forward_origin', None):
                origin = msg.forward_origin
                f_name = _("someone")
                if getattr(origin, 'sender_user', None) and origin.sender_user: f_name = origin.sender_user.first_name
                elif getattr(origin, 'sender_user_name', None): f_name = origin.sender_user_name
                elif getattr(origin, 'chat', None) and origin.chat: f_name = origin.chat.title or origin.chat.first_name
                forward_prefix = _("ai_forwarded_from", name=f_name) + " "

            if msg.voice or msg.video_note:
                cached_audio = await repo.get_media_memory(msg.id, "transcript")
                text = _("ai_voice_memory", text=cached_audio) if cached_audio else _("ai_msg_voice")
            elif msg.photo or msg.video:
                m_tag = _("ai_tag_photo") if msg.photo else _("ai_tag_video")
                if target_msg_id and msg.id == target_msg_id: text = _("ai_media_current", type=m_tag, id=msg.id, text=text)
                else:
                    cached_desc = await repo.get_media_memory(msg.id, "description")
                    text = _("ai_media_memory_desc", type=m_tag, id=msg.id, desc=cached_desc, text=text) if cached_desc else text
            elif msg.sticker: text = _("ai_msg_sticker", emoji=msg.sticker.emoji if hasattr(msg.sticker, 'emoji') else "")

            full_msg_text = f"[{time_str}] {sender}: {forward_prefix}{text}"
            if target_msg_id and msg.id == target_msg_id: full_msg_text = _("ai_current_msg_prefix", text=full_msg_text)
            history_lines.insert(0, full_msg_text)

    return "\n".join(history_lines), media_paths_to_cleanup, latest_media_duration, video_too_long