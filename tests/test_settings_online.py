import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from core import db, services, gemini_media
from modules import ai_twin as ai, always_online as online, api_keys_info as keys_info


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine('sqlite+aiosqlite:///' + self.tmp.name + '/test.db', pool_size=1, max_overflow=0)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as con:
            await con.run_sync(db.Base.metadata.create_all)
        self.patches = [patch.object(module, 'AsyncSessionLocal', self.sessions) for module in (ai, services, keys_info)]
        for p in self.patches:
            p.start()
        self.bot = NS(send_message=AsyncMock())
        self.bot_patch = patch.object(services.plugins, 'bot', self.bot)
        self.bot_patch.start()
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=7, user_id=7))
        async with self.sessions() as session:
            session.add(db.GlobalConfig(id=1, api_keys='secret-one,secret-two', admin_id=7,
                                        model_fallback_list='model-a,model-b', module_settings={'keep': {'value': 1}}, tz='Europe/Minsk'))
            session.add(db.AIKeyState(api_key='secret-one', exhausted_models={}, search_exhausted_models={}))
            await session.commit()
        services._INVALID_KEYS.clear()
        self.addAsyncCleanup(self.cleanup)

    async def cleanup(self):
        self.bot_patch.stop()
        for p in self.patches:
            p.stop()
        services._INVALID_KEYS.clear()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def config(self):
        async with self.sessions() as session:
            return await session.get(db.GlobalConfig, 1)

    async def test_budget_persists_and_preserves_other_settings(self):
        self.assertEqual((await ai._get_g_cfg())['history_token_budget'], 65536)
        await ai._upd_g_cfg(history_token_budget=131072)
        self.assertEqual((await ai._get_g_cfg())['history_token_budget'], 131072)
        self.assertEqual((await self.config()).module_settings['keep'], {'value': 1})

    async def test_budget_input_validation_and_cancel(self):
        await self.state.set_state(ai.AISettingsFSM.history_budget)
        message = NS(text='0', delete=AsyncMock())
        with patch.object(ai, 'safe_edit', AsyncMock()):
            await ai.save_history_budget(message, self.state)
            self.assertEqual(await self.state.get_state(), ai.AISettingsFSM.history_budget.state)
            self.assertNotIn('ai_engine', (await self.config()).module_settings)
            message.text = '131 072'
            await ai.save_history_budget(message, self.state)
            self.assertIsNone(await self.state.get_state())
            self.assertEqual((await ai._get_g_cfg())['history_token_budget'], 131072)
            await self.state.set_state(ai.AISettingsFSM.history_budget)
            await ai.global_settings_menu(NS(message=NS(message_id=5)), self.state)
            self.assertIsNone(await self.state.get_state())

    async def test_confirmed_invalid_key_removed_audited_and_notified_once(self):
        error = NS(code=400, details={'error': {'details': [{'reason': 'API_KEY_INVALID'}]}})
        await services._mark_failure('secret-one', 'model-a', False, error)
        cfg = await self.config()
        self.assertEqual(cfg.api_keys, 'secret-two')
        self.assertEqual(cfg.module_settings['keep'], {'value': 1})
        event = cfg.module_settings['api_keys_audit']['removed'][0]
        self.assertEqual(event['fingerprint'], keys_info.fingerprint('secret-one'))
        self.assertNotIn('secret-one', json.dumps(event))
        async with self.sessions() as session:
            self.assertIsNone(await session.get(db.AIKeyState, 'secret-one'))
        self.bot.send_message.assert_awaited_once()
        notice = self.bot.send_message.await_args.args
        self.assertEqual(notice[0], 7)
        self.assertNotIn('secret-one', notice[1])
        self.assertIn('API_KEY_INVALID', notice[1])
        await services._mark_failure('secret-one', 'model-b', False, error)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(len((await self.config()).module_settings['api_keys_audit']['removed']), 1)

    async def test_quota_and_permission_errors_preserve_keys(self):
        for code in (429, 403, 401, 503, 400):
            await services._mark_failure('secret-one', 'model-a', False, NS(code=code, details={}))
        self.assertEqual((await self.config()).api_keys, 'secret-one,secret-two')
        self.bot.send_message.assert_not_awaited()

    async def test_notification_failure_does_not_fail_generation_fallback(self):
        self.bot.send_message.side_effect = OSError('offline')
        await services._mark_failure('secret-one', 'model-a', True,
                                    NS(code=400, details={'details': [{'reason': 'API_KEY_INVALID'}]}))
        self.assertEqual((await self.config()).api_keys, 'secret-two')

    async def test_concurrent_invalid_verdicts_produce_one_notice(self):
        error = NS(code=400, details={'details': [{'reason': 'API_KEY_INVALID'}]})
        await asyncio.gather(*(services._mark_failure('secret-one', 'model-a', False, error) for _ in range(3)))
        self.bot.send_message.assert_awaited_once()

    async def test_info_uses_saved_cooldowns_without_api_and_masks_credentials(self):
        async with self.sessions() as session:
            state = await session.get(db.AIKeyState, 'secret-one')
            state.exhausted_models = {'model-a': time.time() + 600}
            state.search_unban_time = time.time() + 600
            await session.commit()
        text, kb = await keys_info.build_keys_info(999)
        self.assertNotIn('secret-one', text)
        self.assertNotIn('secret-two', text)
        self.assertIn(keys_info.fingerprint('secret-one'), text)
        self.assertIn('1', text)
        self.assertIn('Google AI Studio', text)
        self.assertLess(len(text), 4096)
        self.assertTrue(kb.inline_keyboard)


class BudgetIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_request_budgets_do_not_change_global_budget(self):
        records = [{'message_id': i, 'speaker': 'CHAT_PARTNER', 'sender_id': 42, 'text': 'x' * 250}
                   for i in range(1, 101)]
        with patch.object(services, '_get_ai_config', AsyncMock(return_value=([], []))):
            small, large = await asyncio.gather(
                services._bounded_dialog_context(records, 100, token_budget=4096),
                services._bounded_dialog_context(records, 100, token_budget=32768))
        self.assertLess(len(small.splitlines()), len(large.splitlines()))
        self.assertLessEqual(len(small.encode()) + 16, 4096)
        self.assertEqual(json.loads(small.splitlines()[-1])[0], 100)
        self.assertEqual(services.CONTEXT_HISTORY_TOKEN_BUDGET, 65536)


class OnlineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        online._task = online._binding = None
        online._configure_lock = asyncio.Lock()
        self.app = NS(is_initialized=True, is_connected=True, invoke=AsyncMock(return_value=True))
        online.userbot_app = self.app
        self.addAsyncCleanup(self.cleanup)

    async def cleanup(self):
        if online._binding and not online._binding.done():
            online._binding.cancel()
            try: await online._binding
            except asyncio.CancelledError: pass
        await online._configure(None, False)
        online.userbot_app = None

    async def test_start_and_stop_keep_one_worker(self):
        await asyncio.gather(*(online._configure(self.app, True) for _ in range(3)))
        await asyncio.sleep(0.01)
        workers = [task for task in asyncio.all_tasks() if task.get_name() == 'always-online' and not task.done()]
        self.assertEqual(len(workers), 1)
        call = self.app.invoke.await_args
        self.assertFalse(call.args[0].offline)
        self.assertEqual(call.kwargs['sleep_threshold'], 0)
        task = online._task
        await online._configure(self.app, False)
        self.assertTrue(task.cancelled())
        self.assertIsNone(online._task)

    async def test_flood_wait_is_respected(self):
        self.app.invoke.side_effect = online.FloodWait(120)
        with patch.object(online.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError)) as sleep:
            with self.assertRaises(asyncio.CancelledError):
                await online._heartbeat(self.app)
        sleep.assert_awaited_once_with(121.0)

    async def test_disconnected_client_does_not_send_status(self):
        self.app.is_connected = False
        with patch.object(online.asyncio, 'sleep', AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await online._heartbeat(self.app)
        self.app.invoke.assert_not_awaited()

    async def test_stopped_client_worker_exits(self):
        async def stop_after_update(_):
            self.app.is_initialized = False
        with patch.object(online.asyncio, 'sleep', AsyncMock(side_effect=stop_after_update)):
            await online._heartbeat(self.app)
        self.app.invoke.assert_awaited_once()

    async def test_enabled_setting_restores_on_new_client(self):
        with patch.object(online.plugins.db, 'get_module_cfg', AsyncMock(return_value={'enabled': True})):
            online.register_userbot(self.app)
            await online._binding
            await asyncio.sleep(0.01)
            first = online._task
            new_app = NS(is_initialized=True, is_connected=True, invoke=AsyncMock(return_value=True))
            online.register_userbot(new_app)
            await online._binding
            await asyncio.sleep(0.01)
        self.assertTrue(first.cancelled())
        new_app.invoke.assert_awaited_once()

    async def test_disabled_setting_has_no_worker(self):
        with patch.object(online.plugins.db, 'get_module_cfg', AsyncMock(return_value={})):
            online.register_userbot(self.app)
            await online._binding
        self.assertIsNone(online._task)
        self.app.invoke.assert_not_awaited()


class MediaErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_error_retains_invalid_reason_without_exposing_key(self):
        response = NS(status=400, json=AsyncMock(return_value={
            'error': {'details': [{'reason': 'API_KEY_INVALID'}], 'message': 'secret-example'}}))
        with self.assertRaises(gemini_media.MediaPreparationError) as caught:
            await gemini_media._json_response(response, 'upload')
        self.assertEqual(services._error_details(caught.exception)[0]['reason'], 'API_KEY_INVALID')
        self.assertNotIn('secret-example', str(caught.exception))
