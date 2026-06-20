import asyncio
import inspect
import logging
import mimetypes
import os
import re
import time
from datetime import datetime, timedelta
from typing import AsyncIterator

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from core.config import _
from core.db import AsyncSessionLocal, CoreRepository, YoutubeCache
from core.utils import download_media_checked, is_bot_dialog

GEMINI_TIMEOUT = 60.0
MEDIA_DOWNLOAD_TIMEOUT = 90.0
KEY_COOLDOWN = 60.0
SERVER_COOLDOWN = 180.0
AUTH_COOLDOWN = 1800.0
YOUTUBE_CACHE_TTL_DAYS = 7
MAX_CONTEXT_MEDIA = 3


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


async def _get_ai_config() -> tuple[list[str], list[str]]:
    async with AsyncSessionLocal() as session:
        cfg = await CoreRepository(session).get_global_config()
        return _split_csv(cfg.api_keys), _split_csv(cfg.model_fallback_list)


def _error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _cooldown_for(exc: Exception) -> float:
    code = _error_code(exc)
    if code in {401, 403}:
        return AUTH_COOLDOWN
    if code == 429:
        return KEY_COOLDOWN
    if code and code >= 500:
        return SERVER_COOLDOWN
    return 0.0


async def _is_available(repo: CoreRepository, api_key: str, model: str, search_enabled: bool) -> bool:
    state = await repo.get_ai_key_state(api_key)
    now = time.time()
    if search_enabled and state.search_unban_time > now:
        return False
    if not search_enabled and state.unban_time > now:
        return False
    exhausted = state.search_exhausted_models if search_enabled else state.exhausted_models
    return exhausted.get(model, 0) <= now


async def _mark_failure(api_key: str, model: str, search_enabled: bool, exc: Exception) -> None:
    cooldown = _cooldown_for(exc)
    if cooldown <= 0:
        return
    until = time.time() + cooldown
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        state = await repo.get_ai_key_state(api_key)
        if search_enabled:
            exhausted = dict(state.search_exhausted_models or {})
            exhausted[model] = until
            state.search_exhausted_models = exhausted
            if _error_code(exc) in {401, 403, 429}:
                state.search_unban_time = until
        else:
            exhausted = dict(state.exhausted_models or {})
            exhausted[model] = until
            state.exhausted_models = exhausted
            if _error_code(exc) in {401, 403, 429}:
                state.unban_time = until
        await session.commit()


def _guess_mime(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime
    ext = os.path.splitext(path)[1].lower()
    if ext in {".oga", ".ogg", ".opus"}:
        return "audio/ogg"
    if ext in {".mp3"}:
        return "audio/mpeg"
    if ext in {".mp4", ".m4v"}:
        return "video/mp4"
    return "application/octet-stream"


def _build_contents(prompt_context: str, media_path: str | None = None) -> list:
    prompt_context = (prompt_context or "").strip()
    contents: list = [prompt_context] if prompt_context else []
    if media_path and os.path.isfile(media_path) and os.path.getsize(media_path) > 0:
        with open(media_path, "rb") as media_file:
            contents.append(genai_types.Part.from_bytes(data=media_file.read(), mime_type=_guess_mime(media_path)))
    return contents


def _build_config(custom_prompt: str | None, search_enabled: bool) -> genai_types.GenerateContentConfig:
    kwargs = {"system_instruction": custom_prompt or None}
    if search_enabled:
        kwargs["tools"] = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
    return genai_types.GenerateContentConfig(**kwargs)


def _response_text(response) -> str:
    text = getattr(response, "text", None)
    if text:
        return text
    parts: list[str] = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            value = getattr(part, "text", None)
            if value:
                parts.append(value)
    return "".join(parts).strip()


async def _run_with_fallbacks(prompt_context: str, media_path: str | None, custom_prompt: str | None, search_enabled: bool) -> str:
    if not (prompt_context or "").strip() and not (media_path and os.path.isfile(media_path) and os.path.getsize(media_path) > 0):
        logging.warning("[Services] Gemini request skipped: empty prompt and no media")
        return _("status_waiting")

    api_keys, models = await _get_ai_config()
    if not api_keys:
        logging.error("[Services] Gemini API keys are not configured")
        return _("err_no_api_keys")
    if not models:
        models = ["gemini-2.5-flash"]

    last_error: Exception | None = None
    for api_key in api_keys:
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            usable_models = [model for model in models if await _is_available(repo, api_key, model, search_enabled)]
        if not usable_models:
            continue

        client = genai.Client(api_key=api_key)
        for model in usable_models:
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=model,
                        contents=_build_contents(prompt_context, media_path),
                        config=_build_config(custom_prompt, search_enabled),
                    ),
                    timeout=GEMINI_TIMEOUT,
                )
                text = _response_text(response)
                if text:
                    return text
            except (genai_errors.APIError, genai_errors.ClientError, genai_errors.ServerError, asyncio.TimeoutError) as exc:
                last_error = exc
                logging.warning("[Services] Gemini request failed on %s: %s", model, exc)
                await _mark_failure(api_key, model, search_enabled, exc)
            except Exception as exc:
                last_error = exc
                logging.exception("[Services] Unexpected Gemini request failure on %s", model)

    if last_error:
        logging.error("[Services] All Gemini fallbacks failed: %s", last_error)
    return _("status_waiting")


async def generate_ai_response(
    prompt_context: str,
    media_path: str | None = None,
    custom_prompt: str | None = None,
    search_enabled: bool = True,
) -> str:
    return await _run_with_fallbacks(prompt_context, media_path, custom_prompt, search_enabled)


async def generate_ai_response_stream(
    prompt_context: str,
    media_path: str | None = None,
    custom_prompt: str | None = None,
    search_enabled: bool = True,
) -> AsyncIterator[str]:
    api_keys, models = await _get_ai_config()
    if not api_keys:
        logging.error("[Services] Gemini API keys are not configured")
        yield _("err_no_api_keys")
        return
    if not models:
        models = ["gemini-2.5-flash"]

    for api_key in api_keys:
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            usable_models = [model for model in models if await _is_available(repo, api_key, model, search_enabled)]
        if not usable_models:
            continue

        client = genai.Client(api_key=api_key)
        for model in usable_models:
            try:
                stream = client.aio.models.generate_content_stream(
                    model=model,
                    contents=_build_contents(prompt_context, media_path),
                    config=_build_config(custom_prompt, search_enabled),
                )
                if inspect.isawaitable(stream):
                    stream = await asyncio.wait_for(stream, timeout=GEMINI_TIMEOUT)

                got_response = False
                while True:
                    try:
                        chunk = await asyncio.wait_for(stream.__anext__(), timeout=GEMINI_TIMEOUT * 2)
                    except StopAsyncIteration:
                        break

                    text = _response_text(chunk)
                    if text:
                        got_response = True
                        yield text
                if got_response:
                    return
            except (genai_errors.APIError, genai_errors.ClientError, genai_errors.ServerError, asyncio.TimeoutError) as exc:
                logging.warning("[Services] Gemini stream failed on %s: %s", model, exc)
                await _mark_failure(api_key, model, search_enabled, exc)
            except Exception:
                logging.exception("[Services] Unexpected Gemini stream failure on %s", model)
    yield _("status_waiting")


async def transcribe_media(media_path: str) -> str:
    if not media_path or not os.path.isfile(media_path) or os.path.getsize(media_path) <= 0:
        raise ValueError("Cannot transcribe missing or empty media file")
    prompt = _("prompt_transcribe")
    result = await generate_ai_response(prompt, media_path=media_path, custom_prompt="", search_enabled=False)
    return "" if result == _("status_waiting") else result


async def test_ai_credentials(progress_cb=None) -> str:
    api_keys, models = await _get_ai_config()
    if not api_keys or not models:
        return _("test_no_data")

    total_steps = len(api_keys) * len(models)
    current_step = 0
    final_report = _("test_result_title")
    cancelled = False

    for api_key in api_keys:
        if cancelled:
            break
        key_hidden = f"{api_key[:4]}***{api_key[-4:]}" if len(api_key) >= 8 else "***"
        final_report += _("test_key_status", key_hidden=key_hidden, status="")
        client = genai.Client(api_key=api_key)

        for model in models:
            current_step += 1
            if progress_cb:
                should_continue = await progress_cb(
                    _("test_progress", key_hidden=key_hidden, model=model, current=current_step, total=total_steps)
                )
                if should_continue is False:
                    cancelled = True
                    break
                await asyncio.sleep(0.1)

            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(model=model, contents=[_("ping_prompt")]),
                    timeout=30.0,
                )
                result = _("test_ok") if _response_text(response) else _("test_error")
            except asyncio.TimeoutError:
                result = _("test_error_timeout")
            except Exception as exc:
                result = f"{_('test_error')} ({str(exc)[:60]})"

            final_report += _("test_model_status", model=model, res=result)
        final_report += "\n"

    if cancelled:
        final_report += "\n" + _("test_cancelled_msg")
    return final_report


def _extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=)([A-Za-z0-9_-]{6,})",
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"/shorts/([A-Za-z0-9_-]{6,})",
        r"/embed/([A-Za-z0-9_-]{6,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return re.sub(r"\W+", "_", url)[:120]


def _download_youtube_context_sync(url: str) -> tuple[int, str]:
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is not installed") from exc

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "extract_flat": False,
    }
    if os.path.exists("data/cookies.txt"):
        ydl_opts["cookiefile"] = "data/cookies.txt"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title") or ""
    duration = int(info.get("duration") or 0)
    description = info.get("description") or ""
    subtitles = info.get("subtitles") or {}
    auto_subtitles = info.get("automatic_captions") or {}

    transcript = ""
    for source in (subtitles, auto_subtitles):
        if transcript:
            break
        for lang in ("ru", "en", "uk", "be"):
            entries = source.get(lang) or []
            for entry in entries:
                sub_url = entry.get("url")
                if not sub_url:
                    continue
                try:
                    import requests

                    response = requests.get(sub_url, timeout=20)
                    response.raise_for_status()
                    raw = response.text
                    raw = re.sub(r"<[^>]+>", " ", raw)
                    raw = re.sub(r"\s+", " ", raw)
                    transcript = raw.strip()
                    break
                except Exception as exc:
                    logging.debug("[Services] Failed to fetch subtitles: %s", exc)
            if transcript:
                break

    context_parts = [part for part in [f"Title: {title}" if title else "", f"Duration: {duration}s", transcript] if part]
    if not transcript and description:
        context_parts.append(f"Description: {description[:6000]}")
    return duration, "\n\n".join(context_parts).strip()


async def get_youtube_context(url: str) -> tuple[int, str]:
    video_id = _extract_video_id(url)
    cutoff = datetime.utcnow() - timedelta(days=YOUTUBE_CACHE_TTL_DAYS)

    async with AsyncSessionLocal() as session:
        cached = await session.get(YoutubeCache, video_id)
        if cached and cached.timestamp >= cutoff:
            return cached.duration, cached.context

    try:
        duration, context = await asyncio.wait_for(
            asyncio.to_thread(_download_youtube_context_sync, url),
            timeout=MEDIA_DOWNLOAD_TIMEOUT,
        )
    except Exception as exc:
        logging.error("[Services] YouTube context failed: %s", exc)
        return 0, _("yt_url_fallback")

    if not context:
        context = _("yt_url_fallback")

    async with AsyncSessionLocal() as session:
        existing = await session.get(YoutubeCache, video_id)
        if existing:
            existing.duration = duration
            existing.context = context
            existing.timestamp = datetime.utcnow()
        else:
            session.add(YoutubeCache(video_id=video_id, duration=duration, context=context))
        await session.commit()

    return duration, context


def _message_sender(message, fallback_name: str | None) -> str:
    if message.from_user and getattr(message.from_user, "is_self", False):
        return _("me_sender")
    if fallback_name:
        return fallback_name
    user = getattr(message, "from_user", None)
    return getattr(user, "first_name", None) or getattr(getattr(message, "chat", None), "title", None) or _("other_sender")


async def _describe_message_media(client, message, media_paths: list[str]) -> tuple[str, int, bool]:
    media_type = None
    duration = 0
    too_long = False
    ext = ".bin"

    if getattr(message, "photo", None):
        media_type, ext = "photo", ".jpg"
    elif getattr(message, "video", None):
        media_type, ext = "video", ".mp4"
        duration = getattr(message.video, "duration", 0) or 0
    elif getattr(message, "voice", None):
        media_type, ext = "voice", ".ogg"
        duration = getattr(message.voice, "duration", 0) or 0
    elif getattr(message, "video_note", None):
        media_type, ext = "video_note", ".mp4"
        duration = getattr(message.video_note, "duration", 0) or 0
    elif getattr(message, "audio", None):
        media_type, ext = "audio", ".mp3"
        duration = getattr(message.audio, "duration", 0) or 0

    if not media_type:
        return "", duration, too_long

    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        cached = await repo.get_media_memory(message.id, media_type)
        if cached:
            return cached, duration, duration > 600

    if duration > 600 and media_type in {"video", "video_note", "audio", "voice"}:
        too_long = True
        return _("ai_msg_file"), duration, too_long

    if len(media_paths) >= MAX_CONTEXT_MEDIA:
        return _("ai_msg_file"), duration, too_long

    path = None
    try:
        path = await download_media_checked(
            client,
            message,
            file_name=f"data/context_{message.id}{ext}",
            timeout=MEDIA_DOWNLOAD_TIMEOUT,
        )
        if path:
            media_paths.append(path)
        if media_type in {"voice", "video_note", "audio"}:
            description = await transcribe_media(path)
        else:
            prompt = _("ai_media_desc_prompt") if _("ai_media_desc_prompt") != "ai_media_desc_prompt" else "Describe this media briefly."
            description = await generate_ai_response(prompt, media_path=path, custom_prompt="", search_enabled=False)
        if not description or description == _("status_waiting"):
            return _("ai_msg_file"), duration, too_long
        async with AsyncSessionLocal() as session:
            await CoreRepository(session).save_media_memory(message.id, media_type, description)
        return description, duration, too_long
    except Exception as exc:
        logging.warning("[Services] Failed to describe media message %s: %s", getattr(message, "id", "?"), exc)
        return _("ai_msg_file"), duration, too_long


async def build_dialog_context(
    client,
    chat_id: int,
    limit: int = 30,
    target_msg_id: int | None = None,
    chat_name: str | None = None,
) -> tuple[str, list[str], int, bool]:
    messages = []
    history_kwargs = {"limit": limit}
    if target_msg_id is not None:
        history_kwargs["max_id"] = target_msg_id + 1
    try:
        history_iter = client.get_chat_history(chat_id, **history_kwargs)
    except TypeError:
        fallback_kwargs = {"limit": limit}
        if target_msg_id is not None:
            fallback_kwargs["offset_id"] = target_msg_id + 1
        history_iter = client.get_chat_history(chat_id, **fallback_kwargs)

    async for msg in history_iter:
        if target_msg_id is not None and msg.id > target_msg_id:
            continue
        if is_bot_dialog(msg):
            continue
        messages.append(msg)

    messages.sort(key=lambda item: item.id)
    media_paths: list[str] = []
    latest_media_duration = 0
    video_too_long = False
    lines: list[str] = []

    for msg in messages:
        try:
            async with AsyncSessionLocal() as session:
                if await CoreRepository(session).is_msg_ignored(chat_id, msg.id):
                    continue
        except Exception:
            pass

        sender = _message_sender(msg, chat_name)
        text = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()

        media_text, duration, too_long = await _describe_message_media(client, msg, media_paths)
        if duration:
            latest_media_duration = duration
        video_too_long = video_too_long or too_long
        if media_text:
            text = f"{text}\n[{media_text}]" if text else f"[{media_text}]"

        if not text:
            continue
        lines.append(f"{sender}: {text}")

    return "\n".join(lines), media_paths, latest_media_duration, video_too_long
