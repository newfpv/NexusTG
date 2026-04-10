import os
import time
import asyncio
import logging
from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

from modules.youtube import fetch_youtube_data_sync
from i18n import _

load_dotenv()

raw_keys = os.getenv("API_KEYS", "")
API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
MODEL_FALLBACK_LIST = [m.strip() for m in os.getenv("MODEL_FALLBACK_LIST", "").split(",") if m.strip()]

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

async def _send_to_gemini(contents, search_enabled=True):
    """Sends a request to Gemini with fallback mechanisms and timeouts, without deadlocking."""
    max_empty_retries = 3
    empty_attempts = 0

    while empty_attempts < max_empty_retries:
        for model_name in MODEL_FALLBACK_LIST:
            for api_key in API_KEYS:
                
                async with key_lock:
                    state = api_key_states[api_key]
                    current_time = time.time()
                    
                    if current_time < state["unban_time"]:
                        continue
                    if model_name in state["exhausted_models"] and current_time < state["exhausted_models"][model_name]:
                        continue
                    
                    key_mask = f"{api_key[:7]}...{api_key[-4:]}"
                    
                    actual_search = search_enabled
                    if search_enabled:
                        if current_time < state["search_unban_time"] or \
                           (model_name in state["search_exhausted_models"] and current_time < state["search_exhausted_models"][model_name]):
                            actual_search = False

                try:
                    client = genai.Client(api_key=api_key)
                    config = get_model_config(search_enabled=actual_search)
                    
                    response = await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=model_name,
                            contents=contents,
                            config=config
                        ),
                        timeout=40.0
                    )
                    
                    if not response.text or not response.text.strip():
                        empty_attempts += 1
                        logging.warning(_("log_gemini_empty_retry", attempt=empty_attempts, max_retries=max_empty_retries))
                        break

                    if search_enabled and not actual_search:
                        logging.warning(_("log_gemini_search_exhausted_fallback"))
                    
                    if getattr(response.candidates[0], "grounding_metadata", None) and getattr(response.candidates[0].grounding_metadata, "search_entry_point", None):
                        logging.info(_("log_gemini_success_search", selected_model=model_name, key_mask=key_mask))
                    else:
                        logging.info(_("log_gemini_success", selected_model=model_name, key_mask=key_mask))
                    
                    return response.text

                except asyncio.TimeoutError:
                    logging.warning(_("log_gemini_timeout", model=model_name))
                    continue
                    
                except Exception as e:
                    error_str = str(e).lower()
                    
                    async with key_lock:
                        if "429" in error_str:
                            if "search" in error_str or "grounding" in error_str:
                                state["search_exhausted_models"][model_name] = time.time() + 10800
                                logging.warning(_("log_gemini_search_ban", selected_model=model_name, key_mask=key_mask))
                                if len(state["search_exhausted_models"]) == len(MODEL_FALLBACK_LIST):
                                    state["search_unban_time"] = time.time() + 28800
                                    logging.warning(_("log_gemini_search_key_ban", key_mask=key_mask))
                            else:
                                state["exhausted_models"][model_name] = time.time() + 7200
                                logging.warning(_("log_gemini_limit", selected_model=model_name, key_mask=key_mask))
                        
                        elif "500" in error_str or "503" in error_str:
                            state["exhausted_models"][model_name] = time.time() + 7200
                            logging.warning(_("log_gemini_model_ban", selected_model=model_name, key_mask=key_mask))
                            
                        elif "400" in error_str:
                            state["unban_time"] = time.time() + 5
                            logging.error(_("log_gemini_400", selected_model=model_name, key_mask=key_mask))
                        else:
                            logging.error(_("log_gemini_unknown_error", selected_model=model_name, key_mask=key_mask, error_type=type(e).__name__))
                    continue

    return "⏳"

async def transcribe_media(media_path: str) -> str:
    if not os.path.exists(media_path): return ""
    logging.info(_("log_transcribe_start", media_path=media_path))
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
    return await _send_to_gemini(contents, search_enabled=search_enabled)
