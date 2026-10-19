"""Small explicit API checks with a compact, credential-free saved report."""
import asyncio
import hashlib
from datetime import datetime, timezone

import aiohttp
from google import genai
from google.genai import types
from core.config import _
from core.db import AsyncSessionLocal, CoreRepository, AIKeyState
from core.gemini_media import _json_response, request_slots


async def probe_key(key):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get('https://generativelanguage.googleapis.com/v1beta/models',
                               params={'pageSize': 1}, headers={'x-goog-api-key': key}) as response:
            await _json_response(response, 'key check')


async def _save_report(report):
    async with AsyncSessionLocal() as session:
        cfg = await CoreRepository(session).get_global_config()
        settings = dict(cfg.module_settings or {})
        audit = dict(settings.get('api_keys_audit', {}))
        audit['last_test'] = report
        settings['api_keys_audit'] = audit
        cfg.module_settings = settings
        await session.commit()


async def run_tests(progress_cb=None, mode='models'):
    from core import services
    from modules.api_keys_info import test_summary
    keys, models = await services._get_ai_config()
    keys = list(dict.fromkeys(keys))
    models = list(dict.fromkeys(models))
    checks = ['API'] if mode == 'keys' else models
    report = {'at': datetime.now(timezone.utc).isoformat(), 'mode': mode, 'rows': [], 'cancelled': False}
    total = len(keys) * len(checks)
    step = 0
    for key in keys:
        row = {'fingerprint': hashlib.sha256(key.encode()).hexdigest()[:10], 'results': []}
        client = genai.Client(api_key=key) if mode != 'keys' else None
        for model in checks:
            progress = _("api_test_progress", current=step, total=total,
                         bar='▰' * (step * 8 // max(total, 1)) + '▱' * (8 - step * 8 // max(total, 1)),
                         key=row['fingerprint'], model=model.removeprefix('gemini-'), summary=test_summary(report['rows'] + [row]))
            if progress_cb and await progress_cb(progress) is False:
                report['cancelled'] = True
                break
            status = 'ok'
            code = 200
            try:
                async with request_slots():
                    if mode == 'keys':
                        await probe_key(key)
                    else:
                        async with asyncio.timeout(30):
                            response = await client.aio.models.generate_content(model=model, contents=['OK'],
                                config=types.GenerateContentConfig(max_output_tokens=1))
                        canonical = getattr(response, 'model_version', None)
                        if isinstance(canonical, str):
                            services._MODEL_CANONICAL[model] = canonical
                        async with AsyncSessionLocal() as session:
                            state = await session.get(AIKeyState, key)
                            if state:
                                state.unban_time = 0
                                state.exhausted_models = {k: v for k, v in (state.exhausted_models or {}).items()
                                                          if k not in {model, canonical}}
                                await session.commit()
            except asyncio.TimeoutError:
                status, code = 'timeout', None
            except Exception as exc:
                code = services._error_code(exc)
                if any(item.get('reason') == 'API_KEY_INVALID' for item in services._error_details(exc)):
                    status = 'invalid'
                elif code == 429:
                    status = 'quota'
                elif code in {401, 403}:
                    status = 'forbidden'
                else:
                    status = 'error'
                if mode != 'keys' or status == 'invalid':
                    await services._mark_failure(key, model, False, exc)
            row['results'].append({'model': model[:120], 'status': status, 'code': code})
            step += 1
            if status == 'invalid':
                break
        if row['results']:
            report['rows'].append(row)
        if report['cancelled']:
            break
    await _save_report(report)
    return _("api_test_finished", summary=test_summary(report['rows']))
