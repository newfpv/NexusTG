import os
import time
import asyncio
import logging
import re
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from modules.youtube import fetch_youtube_data_sync
from i18n import _

load_dotenv()

raw_keys = os.getenv("API_KEYS", "")
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
MODEL_FALLBACK_LIST = [m.strip() for m in os.getenv("MODEL_FALLBACK_LIST", "").split(",") if m.strip()]

# Используем словари для хранения времени окончания бана (timestamp)
api_key_states = {k: {
    "unban_time": 0, 
    "exhausted_models": {}, 
    "search_unban_time": 0,
    "search_exhausted_models": {}
} for k in API_KEYS}
key_lock = asyncio.Lock()

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

async def _send_to_gemini(contents, search_enabled=True, is_fallback=False):
    start_time = time.time()
    timeout = 55
    config = get_model_config(search_enabled)

    while time.time() - start_time < timeout:
        selected_key, selected_model = None, None
        now = time.time()

        async with key_lock:
            for model in MODEL_FALLBACK_LIST:
                for key, state in api_key_states.items():
                    # Проверяем, не в бане ли модель (время разбана больше текущего)
                    is_model_exhausted = state["exhausted_models"].get(model, 0) > now
                    
                    if now >= state["unban_time"] and not is_model_exhausted:
                        is_search_exhausted = state["search_exhausted_models"].get(model, 0) > now
                        if search_enabled and (now < state["search_unban_time"] or is_search_exhausted):
                            continue
                        
                        selected_key, selected_model = key, model
                        state["unban_time"] = now + 999 
                        break
                if selected_key: break

        if not selected_key:
            if search_enabled and not is_fallback:
                can_fallback = False
                now_check = time.time()
                for model in MODEL_FALLBACK_LIST:
                    for key, state in api_key_states.items():
                        if now_check >= state["unban_time"] and state["exhausted_models"].get(model, 0) <= now_check:
                            can_fallback = True
                            break
                    if can_fallback: break
                
                if can_fallback:
                    logging.warning(_("log_gemini_search_exhausted_fallback"))
                    new_contents = list(contents)
                    warning = "\n\n[СИСТЕМНОЕ ПРАВИЛО]: ВНИМАНИЕ! ИНТЕРНЕТ И GOOGLE SEARCH СЕЙЧАС НЕДОСТУПНЫ ИЗ-ЗА ЛИМИТОВ. Если в запросе пользователя есть ссылка — ЧЕСТНО СКАЖИ, что сейчас не можешь её открыть. СТРОЖАЙШЕ ЗАПРЕЩЕНО выдумывать содержимое ссылки или элементы интерфейса."
                    if isinstance(new_contents[0], str):
                        new_contents[0] += warning
                    else:
                        new_contents.append(warning)
                        
                    return await _send_to_gemini(new_contents, search_enabled=False, is_fallback=True)
            
            await asyncio.sleep(1)
            continue
            
        key_mask = f"{selected_key[:5]}...{selected_key[-4:]}"

        try:
            client = genai.Client(api_key=selected_key)
            response = await client.aio.models.generate_content(
                model=selected_model,
                contents=contents,
                config=config
            )
            
            if response.candidates and response.candidates[0].grounding_metadata:
                logging.info(_("log_gemini_success_search", selected_model=selected_model, key_mask=key_mask))
            else:
                logging.info(_("log_gemini_success", selected_model=selected_model, key_mask=key_mask))
                
            async with key_lock:
                api_key_states[selected_key]["unban_time"] = time.time() + 2 
            return response.text.strip() if response.text else ""

        except Exception as e:
            # Склеиваем текст ошибки и её класс, чтобы точно поймать ServerError
            err_str = f"{str(e)} {type(e).__name__}".lower()
            async with key_lock:
                is_quota_error = any(x in err_str for x in ["429", "resource_exhausted", "500", "servererror", "internal", "overloaded"])
                
                if is_quota_error:
                    ban_duration = 7200 # Бан ровно на 2 часа (в секундах)
                    if search_enabled:
                        logging.warning(_("log_gemini_search_ban", selected_model=selected_model, key_mask=key_mask))
                        api_key_states[selected_key]["search_exhausted_models"][selected_model] = time.time() + ban_duration
                        api_key_states[selected_key]["unban_time"] = 0 
                        
                        # Если забанены все модели для поиска — баним ключ на 8 часов
                        active_search_bans = sum(1 for t in api_key_states[selected_key]["search_exhausted_models"].values() if t > time.time())
                        if active_search_bans >= len(MODEL_FALLBACK_LIST):
                            logging.warning(_("log_gemini_search_key_ban", key_mask=key_mask))
                            api_key_states[selected_key]["search_unban_time"] = time.time() + 28800
                            api_key_states[selected_key]["search_exhausted_models"].clear()
                    else:
                        logging.warning(_("log_gemini_model_ban", selected_model=selected_model, key_mask=key_mask))
                        api_key_states[selected_key]["exhausted_models"][selected_model] = time.time() + ban_duration
                        api_key_states[selected_key]["unban_time"] = time.time() # Освобождаем ключ для других моделей
                elif "400" in err_str or "invalid" in err_str:
                    logging.error(_("log_gemini_400", selected_model=selected_model, key_mask=key_mask))
                    api_key_states[selected_key]["unban_time"] = time.time() + 5
                else:
                    logging.error(_("log_gemini_unknown_error", selected_model=selected_model, key_mask=key_mask, error_type=type(e).__name__))
                    api_key_states[selected_key]["unban_time"] = time.time() + 10
    return "⏳"

async def transcribe_media(media_path: str) -> str:
    logging.info(_("log_transcribe_start", media_path=media_path))
    if not media_path or not os.path.exists(media_path): 
        return ""
    try:
        with open(media_path, "rb") as f:
            media_bytes = f.read()
            mime_type = "audio/ogg" if media_path.endswith((".ogg", ".oga", ".mp3", ".wav")) else "video/mp4"
            
            part = genai_types.Part.from_bytes(data=media_bytes, mime_type=mime_type)
            prompt = _("prompt_transcribe")
            
            text = await _send_to_gemini([prompt, part], search_enabled=False)
            return text if text != "⏳" else ""
    except Exception as e:
        logging.error(_("log_transcribe_error", e=e))
        return ""

async def generate_ai_response(prompt_context: str, media_path: str = None, custom_prompt: str = None, search_enabled: bool = True) -> str:
    logging.info(_("log_generate_start"))
    
    contents = [_("context_assembly", custom_prompt=custom_prompt, prompt_context=prompt_context)]
    
    if media_path and os.path.exists(media_path):
        if not media_path.endswith((".ogg", ".oga", ".mp4", ".mov", ".avi", ".mp3", ".wav")):
            try:
                logging.info(_("log_attach_image", media_path=media_path))
                with open(media_path, "rb") as f:
                    media_bytes = f.read()
                    contents.append(genai_types.Part.from_bytes(data=media_bytes, mime_type="image/jpeg"))
            except Exception as e:
                logging.error(_("log_attach_image_error", e=e))

    logging.info(_("log_gemini_send"))
    reply = await _send_to_gemini(contents, search_enabled=search_enabled)
    return reply
