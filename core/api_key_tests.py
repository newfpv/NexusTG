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


def checked_count(report):
    return sum(item.get('answered', item.get('status') != 'timeout')
               for row in report.get('rows', []) for item in row.get('results', []))


async def _save_report(report):
    import copy
    report = copy.deepcopy(report)
    report['rows'] = [row for row in report.get('rows', []) if row.get('results')]
    report['checked'] = checked_count(report)
    async with AsyncSessionLocal() as session:
        cfg = await CoreRepository(session).get_global_config()
        settings = dict(cfg.module_settings or {})
        audit = dict(settings.get('api_keys_audit', {}))
        # A stopped older worker must never overwrite a newer test's results.
        if audit.get('last_test', {}).get('at', '') > report.get('at', ''):
            return
        audit['last_test'] = report
        settings['api_keys_audit'] = audit
        cfg.module_settings = settings
        await session.commit()


class CredentialTestRun:
    def __init__(self, mode='models'):
        self.mode = mode
        self.report = {'at': datetime.now(timezone.utc).isoformat(), 'mode': mode, 'rows': [], 'cancelled': False}
        self.request_task = None
        self.task = None

    def stop(self):
        self.report['cancelled'] = True
        if self.request_task and not self.request_task.done():
            self.request_task.cancel()

    async def _request(self, key, model, client):
        async with request_slots():
            if self.mode == 'keys':
                await probe_key(key)
                return None
            async with asyncio.timeout(30):
                return await client.aio.models.generate_content(model=model, contents=['OK'],
                    config=types.GenerateContentConfig(max_output_tokens=1))

    async def execute(self, progress_cb=None):
        from core import services
        from modules.api_keys_info import test_summary
        keys, models = await services._get_ai_config()
        keys = list(dict.fromkeys(keys))
        checks = ['API'] if self.mode == 'keys' else list(dict.fromkeys(models))
        total = len(keys) * len(checks)
        try:
            for key in keys:
                if self.report['cancelled']:
                    break
                row = {'fingerprint': hashlib.sha256(key.encode()).hexdigest()[:10], 'results': []}
                self.report['rows'].append(row)
                client = genai.Client(api_key=key) if self.mode != 'keys' else None
                for model in checks:
                    if self.report['cancelled']:
                        break
                    checked = checked_count(self.report)
                    progress = _("api_test_progress", current=checked, total=total,
                        bar='▰' * (checked * 8 // max(total, 1)) + '▱' * (8 - checked * 8 // max(total, 1)),
                        key=row['fingerprint'], model=model.removeprefix('gemini-'), summary=test_summary(self.report['rows']))
                    if progress_cb and await progress_cb(progress) is False:
                        self.stop()
                    if self.report['cancelled']:
                        break
                    status, code, failure, response = 'ok', 200, None, None
                    self.request_task = asyncio.create_task(self._request(key, model, client), name='api-key-probe')
                    try:
                        response = await self.request_task
                    except asyncio.CancelledError:
                        if self.report['cancelled']:
                            break
                        raise
                    except asyncio.TimeoutError:
                        status, code = 'timeout', None
                    except Exception as exc:
                        failure = exc
                        code = services._error_code(exc)
                        if any(item.get('reason') == 'API_KEY_INVALID' for item in services._error_details(exc)):
                            status = 'invalid'
                        elif code == 429:
                            status = 'quota'
                        elif code in {401, 403}:
                            status = 'forbidden'
                        elif code is None:
                            status = 'timeout'
                        else:
                            status = 'error'
                    finally:
                        self.request_task = None
                    # A late result from a cancelled HTTP operation is not a check.
                    if self.report['cancelled']:
                        break
                    row['results'].append({'model': model[:120], 'status': status, 'code': code, 'answered': code is not None})
                    if failure and (self.mode != 'keys' or status == 'invalid'):
                        await services._mark_failure(key, model, False, failure)
                    elif status == 'ok' and self.mode != 'keys':
                        canonical = getattr(response, 'model_version', None)
                        if isinstance(canonical, str):
                            services._MODEL_CANONICAL[model] = canonical
                        async with AsyncSessionLocal() as session:
                            state = await session.get(AIKeyState, key)
                            if state:
                                state.unban_time = 0
                                state.exhausted_models = {k: v for k, v in (state.exhausted_models or {}).items() if k not in {model, canonical}}
                                await session.commit()
                    if status == 'invalid':
                        break
        except asyncio.CancelledError:
            self.stop()
            raise
        finally:
            await _save_report(self.report)
        return _("api_test_finished", summary=test_summary(self.report['rows']))


async def run_tests(progress_cb=None, mode='models'):
    return await CredentialTestRun(mode).execute(progress_cb)
