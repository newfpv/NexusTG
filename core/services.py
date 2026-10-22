import asyncio
import inspect
import hashlib
import json
import logging
import mimetypes
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from core.gemini_media import (prepare_contents, request_slots, count_text_tokens,
                               MediaPreparationError, INLINE_LIMIT, bounded_media_request)
from core.ai_pool import key_lease
from core.config import _
from core.db import AsyncSessionLocal, CoreRepository, YoutubeCache, AIKeyState
from core.utils import download_media_checked, is_bot_dialog, plugins

GEMINI_TIMEOUT = 60.0
MEDIA_DOWNLOAD_TIMEOUT = 300.0
KEY_COOLDOWN = 60.0
SERVER_COOLDOWN = 180.0
AUTH_COOLDOWN = 1800.0
YOUTUBE_CACHE_TTL_DAYS = 7
CONTEXT_MEDIA_BATCH_SIZE = 3
CONTEXT_MEDIA_BATCH_TIMEOUT = 1200.0
CONTEXT_HISTORY_TIMEOUT = 30.0
AI_TWIN_HISTORY_LIMIT = 1000
CONTEXT_HISTORY_TOKEN_BUDGET = 65536
HISTORY_TOKEN_BUDGET_MIN = 4096
HISTORY_TOKEN_BUDGET_MAX = 262144
_INVALID_KEYS = set()
_MODEL_CANONICAL = {}


def history_token_budget(value=None):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return CONTEXT_HISTORY_TOKEN_BUDGET
    return max(HISTORY_TOKEN_BUDGET_MIN, min(HISTORY_TOKEN_BUDGET_MAX, value))


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


def _error_details(exc):
    data = getattr(exc, "details", None)
    if not isinstance(data, dict):
        return []
    data = data.get("error", data)
    items = data.get("details", []) if isinstance(data, dict) else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _auth_failure(exc):
    return (_error_code(exc) in {401, 403} or any(
        item.get("reason") == "API_KEY_INVALID" for item in _error_details(exc)))


def _cooldown_for(exc: Exception) -> float:
    code = _error_code(exc)
    if _auth_failure(exc):
        return AUTH_COOLDOWN
    if code == 429:
        details = _error_details(exc)
        for item in details:
            if item.get("@type", "").endswith("RetryInfo"):
                try:
                    return max(1.0, min(86400.0, float(item["retryDelay"].removesuffix("s")) + 1))
                except (KeyError, TypeError, ValueError):
                    pass
        if any("PerDay" in violation.get("quotaId", "")
               for item in details for violation in item.get("violations", [])):
            pacific = datetime.now(ZoneInfo("America/Los_Angeles"))
            reset = datetime.combine(pacific.date() + timedelta(days=1), datetime.min.time(), tzinfo=pacific.tzinfo)
            return max(1.0, reset.timestamp() - pacific.timestamp())
        return KEY_COOLDOWN
    if code and code >= 500:
        return SERVER_COOLDOWN
    return 0.0


async def _is_available(repo: CoreRepository, api_key: str, model: str, search_enabled: bool) -> bool:
    if api_key in _INVALID_KEYS:
        return False
    state = await repo.get_ai_key_state(api_key)
    now = time.time()
    if search_enabled and state.search_unban_time > now:
        return False
    if not search_enabled and state.unban_time > now:
        return False
    exhausted = state.search_exhausted_models if search_enabled else state.exhausted_models
    return max(exhausted.get(model, 0), exhausted.get(_MODEL_CANONICAL.get(model, model), 0)) <= now


async def _mark_failure(api_key: str, model: str, search_enabled: bool, exc: Exception) -> None:
    # Delete only an explicit invalid-key verdict, never quota/permission errors.
    if any(item.get("reason") == "API_KEY_INVALID" for item in _error_details(exc)):
        removed = False
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            cfg = await repo.get_global_config()
            keys = _split_csv(cfg.api_keys)
            if api_key in keys:
                removed = True
                fingerprint = hashlib.sha256(api_key.encode()).hexdigest()[:10]
                position = keys.index(api_key) + 1
                cfg.api_keys = ",".join(key for key in keys if key != api_key)
                remaining = len(_split_csv(cfg.api_keys))
                settings = dict(cfg.module_settings or {})
                audit = dict(settings.get("api_keys_audit", {}))
                events = list(audit.get("removed", []))
                events.append({"fingerprint": fingerprint, "position": position,
                               "reason": "API_KEY_INVALID", "at": datetime.now(timezone.utc).isoformat()})
                audit["removed"] = events[-20:]
                settings["api_keys_audit"] = audit
                cfg.module_settings = settings
            admin_id = cfg.admin_id
            state = await session.get(AIKeyState, api_key)
            if state is not None:
                await session.delete(state)
            await session.commit()
        _INVALID_KEYS.add(api_key)
        if removed:
            logging.warning("[Services] Removed a confirmed invalid Gemini API key (%s)", fingerprint)
            if plugins.bot and admin_id:
                try:
                    await asyncio.wait_for(plugins.bot.send_message(admin_id, _("api_key_removed_notice",
                        position=position, fingerprint=fingerprint, remaining=remaining)), timeout=10)
                except Exception as notification_error:
                    logging.warning("[Services] Key removal notice failed: %s", type(notification_error).__name__)
        return
    cooldown = _cooldown_for(exc)
    if cooldown <= 0:
        return
    until = time.time() + cooldown
    canonical = next((violation.get("quotaDimensions", {}).get("model")
                      for item in _error_details(exc) for violation in item.get("violations", [])
                      if violation.get("quotaDimensions", {}).get("model")), model)
    _MODEL_CANONICAL[model] = canonical
    async with AsyncSessionLocal() as session:
        repo = CoreRepository(session)
        state = await repo.get_ai_key_state(api_key)
        if search_enabled:
            exhausted = dict(state.search_exhausted_models or {})
            exhausted[model] = until
            exhausted[canonical] = until
            state.search_exhausted_models = exhausted
            if _auth_failure(exc):
                state.search_unban_time = until
        else:
            exhausted = dict(state.exhausted_models or {})
            exhausted[model] = until
            exhausted[canonical] = until
            state.exhausted_models = exhausted
            if _auth_failure(exc):
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
        if os.path.getsize(media_path) > INLINE_LIMIT:
            raise ValueError("Large media requires async streaming preparation")
        with open(media_path, "rb") as media_file:
            contents.append(genai_types.Part.from_bytes(data=media_file.read(INLINE_LIMIT + 1), mime_type=_guess_mime(media_path)))
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

    async with bounded_media_request(media_path):
        api_keys, models = await _get_ai_config()
        if not api_keys:
            logging.error("[Services] Gemini API keys are not configured")
            return _("err_no_api_keys")
        if not models:
            models = ["gemini-2.5-flash"]

        if media_path and not custom_prompt:
            # Save scarce full-model daily requests for actual conversation replies.
            models = sorted(models, key=lambda name: "-lite" not in name)

        last_error: Exception | None = None
        pending_keys = list(dict.fromkeys(api_keys))
        while pending_keys:
            async with key_lease(pending_keys) as api_key:
                pending_keys.remove(api_key)
                async with AsyncSessionLocal() as session:
                    repo = CoreRepository(session)
                    usable_models = [model for model in models if await _is_available(repo, api_key, model, search_enabled)]
                if not usable_models:
                    continue

                client = genai.Client(api_key=api_key)
                try:
                    contents = await prepare_contents(prompt_context, media_path, api_key,
                                                      _guess_mime(media_path) if media_path else "")
                except MediaPreparationError as exc:
                    logging.warning("[Services] Media preparation failed: %s", exc)
                    await _mark_failure(api_key, usable_models[0], search_enabled, exc)
                    continue
                for model in usable_models:
                    async with AsyncSessionLocal() as session:
                        if not await _is_available(CoreRepository(session), api_key, model, search_enabled):
                            continue
                    try:
                        async with request_slots():
                            response = await asyncio.wait_for(
                                client.aio.models.generate_content(
                                    model=model,
                                    contents=contents,
                                    config=_build_config(custom_prompt, search_enabled),
                                ),
                                timeout=GEMINI_TIMEOUT * (2 if media_path else 1),
                            )
                        usage = getattr(response, "usage_metadata", None)
                        if isinstance(getattr(response, "model_version", None), str):
                            _MODEL_CANONICAL[model] = response.model_version
                        logging.info("[Services] Gemini success: requested=%s resolved=%s input_tokens=%s output_tokens=%s",
                                     model, getattr(response, "model_version", model),
                                     getattr(usage, "prompt_token_count", None), getattr(usage, "candidates_token_count", None))
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
    async with bounded_media_request(media_path):
        api_keys, models = await _get_ai_config()
        if not api_keys:
            logging.error("[Services] Gemini API keys are not configured")
            yield _("err_no_api_keys")
            return
        if not models:
            models = ["gemini-2.5-flash"]

        pending_keys = list(dict.fromkeys(api_keys))
        while pending_keys:
            async with key_lease(pending_keys) as api_key:
                pending_keys.remove(api_key)
                async with AsyncSessionLocal() as session:
                    repo = CoreRepository(session)
                    usable_models = [model for model in models if await _is_available(repo, api_key, model, search_enabled)]
                if not usable_models:
                    continue

                client = genai.Client(api_key=api_key)
                try:
                    contents = await prepare_contents(prompt_context, media_path, api_key,
                                                      _guess_mime(media_path) if media_path else "")
                except MediaPreparationError as exc:
                    logging.warning("[Services] Media preparation failed: %s", exc)
                    await _mark_failure(api_key, usable_models[0], search_enabled, exc)
                    continue
                for model in usable_models:
                    async with AsyncSessionLocal() as session:
                        if not await _is_available(CoreRepository(session), api_key, model, search_enabled):
                            continue
                    try:
                        async with request_slots():
                            stream = client.aio.models.generate_content_stream(
                                model=model,
                                contents=contents,
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


async def test_ai_credentials(progress_cb=None, mode="models") -> str:
    from core.api_key_tests import run_tests
    return await run_tests(progress_cb=progress_cb, mode=mode)


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


def _is_owner_message(message, owner_id=None) -> bool:
    user = getattr(message, "from_user", None)
    return bool(getattr(message, "outgoing", False)
                or getattr(user, "is_self", False)
                or (owner_id is not None and getattr(user, "id", None) == owner_id))


def _message_sender(message, fallback_name: str | None) -> str:
    if _is_owner_message(message):
        return _("me_sender")
    user = getattr(message, "from_user", None)
    return (getattr(user, "first_name", None) or fallback_name
            or getattr(getattr(message, "chat", None), "title", None) or _("other_sender"))


def _speaker_identity(message, chat_id, owner_id):
    user = getattr(message, "from_user", None)
    sender_id = getattr(user, "id", None)
    if _is_owner_message(message, owner_id):
        speaker = "ACCOUNT_OWNER"
    elif sender_id is not None and sender_id == chat_id:
        speaker = "CHAT_PARTNER"
    elif sender_id is not None:
        speaker = "OTHER"
    else:
        speaker = "UNKNOWN"
    return {
        "speaker": speaker,
        "sender_id": sender_id if sender_id is not None else (owner_id if speaker == "ACCOUNT_OWNER" else None),
        "sender_name": getattr(user, "first_name", None),
    }


def _dialog_message_record(message, chat_id, owner_id, media_text="", reply_message=None):
    record = {"record_type": "message", "message_id": message.id,
              **_speaker_identity(message, chat_id, owner_id),
              "text": getattr(message, "text", None) or getattr(message, "caption", None) or ""}
    date = getattr(message, "date", None)
    if isinstance(date, datetime):
        record["timestamp"] = (date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date).astimezone(timezone.utc)
    for kind in ("photo", "video", "voice", "video_note", "audio", "document", "animation", "sticker"):
        if getattr(message, kind, None):
            record["media"] = {"type": kind, "description_or_transcript": media_text or None}
            break
    reply_id = getattr(message, "reply_to_message_id", None)
    if reply_message is not None:
        reply_id = reply_message.id
        record["reply_to"] = {"message_id": reply_id,
                              **_speaker_identity(reply_message, chat_id, owner_id),
                              "quoted_text": (getattr(reply_message, "text", None)
                                              or getattr(reply_message, "caption", None) or "")[:400]}
    elif reply_id:
        record["reply_to"] = {"message_id": reply_id}
    origin = getattr(message, "forward_from", None) or getattr(message, "forward_from_chat", None)
    hidden_name = getattr(message, "forward_sender_name", None)
    if origin is not None or hidden_name:
        record["forwarded_from"] = {
            "id": getattr(origin, "id", None),
            "name": (getattr(origin, "first_name", None) or getattr(origin, "title", None) or hidden_name),
        }
    return record


def _compact_dialog_context(records, target_id, owner_id=None, chat_id=None, chat_name=None):
    people = {}
    if owner_id is not None:
        people["o"] = [owner_id]
    if chat_id is not None:
        people["p"] = [chat_id, chat_name] if chat_name else [chat_id]
    others = {}

    def register(identity):
        role = identity.get("speaker")
        uid, name = identity.get("sender_id"), identity.get("sender_name")
        if role == "UNKNOWN":
            return "u"
        if role in {"ACCOUNT_OWNER", "CHAT_PARTNER"}:
            tag = "o" if role == "ACCOUNT_OWNER" else "p"
        else:
            if uid is None:
                return "u"
            tag = others.setdefault(uid, f"x{len(others) + 1}")
        old = people.get(tag, [])
        uid = uid if uid is not None else (old[0] if old else None)
        name = name or (old[1] if len(old) > 1 else None)
        if uid is not None or name:
            people[tag] = [uid, name] if name else [uid]
        return tag

    for record in records:
        register(record)
        if "reply_to" in record and "speaker" in record["reply_to"]:
            register(record["reply_to"])
    known_ids = {record["message_id"] for record in records}
    quotes = {}
    rows = []
    for record in records:
        row = [record["message_id"], register(record), record["text"]]
        details = {}
        media = record.get("media")
        if media:
            details["m"] = [media["type"]]
            if media.get("description_or_transcript"):
                details["m"].append(media["description_or_transcript"])
        reply = record.get("reply_to")
        if reply:
            rid = reply["message_id"]
            details["r"] = rid
            if rid not in known_ids and "speaker" in reply:
                entry = quotes.setdefault(str(rid), [register(reply)])
                if entry[0] == "u":
                    entry[0] = register(reply)
                if len(entry) == 1 and reply.get("quoted_text"):
                    entry.append(reply["quoted_text"])
        origin = record.get("forwarded_from")
        if origin:
            uid = origin.get("id")
            if uid is None:
                details["f"] = [None, origin.get("name")]
            else:
                tag = next((tag for tag, info in people.items() if info[0] == uid), None)
                details["f"] = tag or register({"speaker": "OTHER", "sender_id": uid,
                                                "sender_name": origin.get("name")})
        if details:
            row.append(details)
        rows.append(row)
    header = {"to": target_id, "s": people}
    dated = [record for record in records if record.get("timestamp") is not None]
    anchors = {}
    previous = None
    for record in dated:
        stamp = record["timestamp"]
        if (previous is None or stamp.date() != previous.date()
                or (stamp - previous).total_seconds() >= 8 * 3600 or record is dated[-1]):
            anchors[str(record["message_id"])] = stamp.strftime("%Y-%m-%dT%H:%MZ")
        previous = stamp
    if anchors:
        header["d"] = anchors
    if quotes:
        header["q"] = quotes
    payload = [header, *rows]
    return "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in payload)


async def _bounded_dialog_context(records, target_id, owner_id=None, chat_id=None, chat_name=None, token_budget=None):
    """Keep the newest whole messages; count the serialized history, not raw media."""
    def serialize(items):
        return _compact_dialog_context(items, target_id, owner_id, chat_id, chat_name)

    payload = serialize(records)
    budget = history_token_budget(token_budget)
    if len(payload.encode("utf-8")) + 16 <= budget:
        return payload
    kept = records
    try:
        keys, models = await _get_ai_config()
        if not keys:
            raise ValueError("Token counter has no configured key")
        model = (models or ["gemini-2.5-flash"])[0]
        for _attempt in range(5):
            count = await count_text_tokens(payload, keys[0], model)
            if count <= budget:
                logging.info("[Services] Context history: %s/%s messages, %s/%s tokens",
                             len(kept), len(records), count, budget)
                return payload
            if len(kept) <= 1:
                break
            size = max(1, min(len(kept) - 1, int(len(kept) * budget / count * 0.9)))
            kept = kept[-size:]
            payload = serialize(kept)
    except Exception as exc:
        # A counter outage must not grow context without a limit or block a reply.
        logging.warning("[Services] Context counter unavailable (%s); using conservative UTF-8 bound",
                        type(exc).__name__)
    low, high = 1, len(kept)
    while low < high:
        size = (low + high + 1) // 2
        candidate = serialize(kept[-size:])
        if len(candidate.encode("utf-8")) + 16 <= budget:
            low = size
        else:
            high = size - 1
    kept = kept[-low:]
    payload = serialize(kept)
    if len(payload.encode("utf-8")) + 16 > budget and kept and "media" in kept[-1]:
        # Preserve the actual message; a huge generated attachment description
        # remains in the cache and the original target can still be attached.
        latest = dict(kept[-1])
        latest["media"] = {**latest["media"], "description_or_transcript": "[Media description exceeds context budget]"}
        payload = serialize([latest])
    logging.info("[Services] Context history bounded locally: %s/%s messages", len(kept), len(records))
    return payload


async def download_context_media(client, message, file_name, timeout=MEDIA_DOWNLOAD_TIMEOUT, on_progress=None):
    try:
        kwargs={'on_progress':on_progress} if on_progress else {}
        return await download_media_checked(client, message, file_name=file_name, timeout=timeout, **kwargs)
    except BaseException:
        for candidate in (file_name, file_name + ".temp"):
            try:
                if os.path.isfile(candidate):
                    os.remove(candidate)
            except OSError:
                pass
        raise


async def _describe_message_media(client, message, media_paths: list[str], on_progress=None) -> tuple[str, int, bool]:
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

    path = None
    try:
        if on_progress:
            on_progress('download', kind=media_type)
        path = await download_context_media(
            client,
            message,
            file_name=f"data/context_{message.id}{ext}",
            timeout=MEDIA_DOWNLOAD_TIMEOUT,
            on_progress=(lambda current,total:on_progress('download',kind=media_type,bytes_current=current,bytes_total=total)) if on_progress else None,
        )
        if path:
            media_paths.append(path)
        if on_progress:
            on_progress('recognize', kind=media_type)
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
    played_media_ids: list[int] | None = None,
    structured: bool = False,
    target_message=None,
    retain_target_media: list[str] | None = None,
    token_budget: int | None = None,
    on_progress=None,
) -> tuple[str, list[str], int, bool]:
    def report(phase, **details):
        if on_progress:
            try:
                on_progress(phase, **details)
            except Exception:
                logging.warning('[Services] Progress observer failed', exc_info=False)

    report('history')
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

    async def collect_history():
        async for msg in history_iter:
            if target_msg_id is not None and msg.id > target_msg_id:
                continue
            if is_bot_dialog(msg):
                continue
            messages.append(msg)

    await asyncio.wait_for(collect_history(), timeout=CONTEXT_HISTORY_TIMEOUT)

    if (target_message is not None and getattr(target_message, "id", None) == target_msg_id
            and getattr(getattr(target_message, "chat", None), "id", None) == chat_id
            and not is_bot_dialog(target_message)):
        # An incoming update can include reply metadata that get_chat_history lacks.
        messages = [msg for msg in messages if msg.id != target_message.id]
        messages.append(target_message)
    messages.sort(key=lambda item: item.id)
    if limit > 0:
        messages = messages[-limit:]
    report('prepare', messages=len(messages))
    media_paths: list[str] = []
    latest_media_duration = 0
    video_too_long = False
    lines: list[str] = []
    structured_records = []

    context_messages = []
    ignored_ids = set()
    for msg in messages:
        try:
            async with AsyncSessionLocal() as session:
                if await CoreRepository(session).is_msg_ignored(chat_id, msg.id):
                    ignored_ids.add(msg.id)
                    continue
        except Exception:
            pass
        context_messages.append(msg)

    media_results = {}
    messages_by_id = {msg.id: msg for msg in context_messages}
    owner_id = getattr(getattr(client, "me", None), "id", None)
    if owner_id is None:
        owner_id = next((getattr(getattr(msg, "from_user", None), "id", None)
                         for msg in context_messages if _is_owner_message(msg)
                         and getattr(getattr(msg, "from_user", None), "id", None) is not None), None)

    def context_record(msg):
        reply = getattr(msg, "reply_to_message", None)
        if reply is None:
            reply = messages_by_id.get(getattr(msg, "reply_to_message_id", None))
        if reply is not None and reply.id in ignored_ids:
            reply = None
        return _dialog_message_record(msg, chat_id, owner_id, media_results.get(msg.id, ("", 0, False))[0], reply)

    async def prune_history():
        nonlocal context_messages
        records = [context_record(msg) for msg in context_messages]
        records = [record for record in records if record["text"] or "media" in record]
        bounded = await _bounded_dialog_context(records, target_msg_id, owner_id, chat_id, chat_name, token_budget)
        selected = {json.loads(line)[0] for line in bounded.splitlines()[1:]}
        context_messages = [msg for msg in context_messages if msg.id in selected]
        for mid in list(media_results):
            if mid not in selected:
                del media_results[mid]

    if structured:
        # Read cached descriptions before choosing the window; discarded old
        # attachments must not be downloaded just to be thrown away afterward.
        await prune_history()
        cached_count = 0
        async with AsyncSessionLocal() as session:
            repo = CoreRepository(session)
            for msg in reversed(context_messages[:]):
                if msg.id not in {item.id for item in context_messages}:
                    continue
                kind = next((kind for kind in ("photo", "video", "voice", "video_note", "audio")
                             if getattr(msg, kind, None)), None)
                if kind:
                    cached = await repo.get_media_memory(msg.id, kind)
                    if cached:
                        duration = getattr(getattr(msg, kind), "duration", 0) or 0
                        media_results[msg.id] = (cached, duration, duration > 600)
                        cached_count += 1
                        if cached_count % 15 == 0:
                            await prune_history()
        await prune_history()
    media_messages = [msg for msg in context_messages
                      if any(getattr(msg, kind, None) for kind in ("photo", "video", "voice", "video_note", "audio"))]
    if structured:
        media_messages.reverse()  # Recognize the most recent attachments first.
    total_batches = (len(media_messages) + CONTEXT_MEDIA_BATCH_SIZE - 1) // CONTEXT_MEDIA_BATCH_SIZE
    cached_ids = set(media_results)

    def media_progress(phase, **details):
        selected = {msg.id for msg in context_messages}
        selected_media = [msg for msg in media_messages if msg.id in selected]
        done = [msg.id for msg in selected_media if msg.id in media_results]
        skipped = sum(media_results[mid][0] in {'', _('ai_msg_file'), _('status_waiting'), _('err_no_api_keys')}
                      or media_results[mid][2] for mid in done)
        report(phase, total=len(selected_media), done=len(done), cached=len(cached_ids & set(done)),
               skipped=skipped, **details)

    media_progress('media')
    for offset in range(0, len(media_messages), CONTEXT_MEDIA_BATCH_SIZE):
        allowed = {msg.id for msg in context_messages}
        batch = [msg for msg in media_messages[offset:offset + CONTEXT_MEDIA_BATCH_SIZE] if msg.id in allowed]
        if not batch:
            continue
        batch_paths = []
        batch_number = offset // CONTEXT_MEDIA_BATCH_SIZE + 1
        batch_deadline = time.time() + CONTEXT_MEDIA_BATCH_TIMEOUT
        logging.info("[Services] Chat %s: media batch %s/%s (%s attachments)",
                     chat_id, batch_number, total_batches, len(batch))

        async def describe_batch(batch_messages, paths):
            active = {}

            def report_active(phase='media'):
                media_progress(phase, batch=batch_number, batches=total_batches,
                               deadline=batch_deadline, active=len(active),
                               downloading=sum(value['phase'] == 'download' for value in active.values()),
                               recognizing=sum(value['phase'] == 'recognize' for value in active.values()),
                               bytes_current=sum(value.get('bytes_current',0) for value in active.values() if value['phase']=='download'),
                               bytes_total=sum(value.get('bytes_total',0) for value in active.values() if value['phase']=='download'))

            async def describe_one(media_message):
                if media_message.id in media_results:
                    return
                def item_progress(phase, **details):
                    active[media_message.id] = {**active.get(media_message.id,{}),**details,'phase':phase}
                    report_active(phase)
                try:
                    if on_progress:
                        media_results[media_message.id] = await _describe_message_media(
                            client, media_message, paths, on_progress=item_progress)
                    else:
                        media_results[media_message.id] = await _describe_message_media(client, media_message, paths)
                finally:
                    active.pop(media_message.id, None)
                    report_active()

            tasks = [asyncio.create_task(describe_one(message)) for message in batch_messages]
            try:
                await asyncio.gather(*tasks)
            finally:
                # Never clean files until every cancelled download/model task has stopped.
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await asyncio.wait_for(describe_batch(batch, batch_paths), timeout=CONTEXT_MEDIA_BATCH_TIMEOUT)
        finally:
            # Results are cached after each file. Cancellation can safely resume from that cache.
            for path in batch_paths:
                try:
                    target = next((msg for msg in batch if msg.id == target_msg_id), None)
                    keep = (retain_target_media is not None and target is not None
                            and (getattr(target, "photo", None) or getattr(target, "video", None))
                            and os.path.basename(path).startswith(f"context_{target_msg_id}.")
                            and not media_results.get(target_msg_id, ("", 0, False))[2])
                    if keep and path and os.path.isfile(path):
                        retain_target_media.append(path)
                    elif path and os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    logging.warning("[Services] Could not remove context media %s", path, exc_info=True)
        if structured and batch_number % 5 == 0:
            await prune_history()
    media_progress('pack')
    for msg in context_messages:
        sender = _message_sender(msg, chat_name)
        text = (getattr(msg, "text", None) or getattr(msg, "caption", None) or "").strip()

        media_text, duration, too_long = media_results.get(msg.id, ("", 0, False))
        # Telegram tracks playback separately from reading the chat history.
        if (played_media_ids is not None
                and (getattr(msg, "voice", None) or getattr(msg, "video_note", None))
                and not _is_owner_message(msg, owner_id)
                and media_text and media_text not in {_("ai_msg_file"), _("status_waiting"), _("err_no_api_keys")}):
            played_media_ids.append(msg.id)
        if duration:
            latest_media_duration = duration
        video_too_long = video_too_long or too_long
        if media_text:
            text = f"{text}\n[{media_text}]" if text else f"[{media_text}]"

        if structured:
            reply = getattr(msg, "reply_to_message", None)
            if reply is None:
                reply = messages_by_id.get(getattr(msg, "reply_to_message_id", None))
            if reply is not None and reply.id in ignored_ids:
                reply = None
            record = _dialog_message_record(msg, chat_id, owner_id, media_text, reply)
            if record["text"] or "media" in record:
                # JSON escaping keeps multiline text and embedded speaker labels in one message.
                structured_records.append(record)
        elif text:
            lines.append(f"{sender}: {text}")

    history = (await _bounded_dialog_context(structured_records, target_msg_id, owner_id, chat_id, chat_name, token_budget)
               if structured else "\n".join(lines))
    report('ready')
    return history, media_paths, latest_media_duration, video_too_long
