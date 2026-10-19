import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from test_compact_settings import CompactTests
from core import auth, api_key_tests as checks
from core.gemini_media import MediaPreparationError
from modules import api_keys_info as keys


class StopTests(CompactTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        auth._active_credential_tests.clear()
        self.cfg = await self.config()
        self.config_patch = patch.object(auth.plugins.db, 'get_global_config', AsyncMock(return_value=self.cfg))
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    async def test_stop_cancels_inflight_rpc_and_keeps_only_answered_checks(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        calls = 0
        async def generate(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return NS(model_version='model-a')
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        client = NS(aio=NS(models=NS(generate_content=AsyncMock(side_effect=generate))))
        job = checks.CredentialTestRun()
        with patch.object(checks.genai, 'Client', return_value=client):
            task = asyncio.create_task(job.execute())
            await asyncio.wait_for(entered.wait(), 1)
            job.stop()
            await asyncio.wait_for(task, 0.3)
        self.assertTrue(cancelled.is_set())
        report = (await self.config()).module_settings['api_keys_audit']['last_test']
        self.assertEqual(report['checked'], 1)
        self.assertEqual(len(report['rows'][0]['results']), 1)
        self.assertEqual(calls, 2)
        self.assertIn('❌ 0', keys.test_summary(report['rows']))

    async def test_stop_while_waiting_for_request_slot_sends_nothing(self):
        started = asyncio.Event()
        async def progress(_):
            started.set()
            return True
        client = NS(aio=NS(models=NS(generate_content=AsyncMock())))
        job = checks.CredentialTestRun()
        with patch.object(checks, 'request_slots', return_value=asyncio.Semaphore(0)), patch.object(checks.genai, 'Client', return_value=client):
            task = asyncio.create_task(job.execute(progress))
            await started.wait()
            await asyncio.sleep(0)
            job.stop()
            await asyncio.wait_for(task, 0.3)
        client.aio.models.generate_content.assert_not_awaited()
        self.assertEqual((await self.config()).module_settings['api_keys_audit']['last_test']['checked'], 0)

    async def test_stop_returns_menu_without_waiting_for_late_sdk_cleanup(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def generate(**kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()  # Simulate a slow SDK cancellation.
            return NS(model_version='model-a')
        client = NS(aio=NS(models=NS(generate_content=AsyncMock(side_effect=generate))))
        call = NS(from_user=NS(id=7), message=NS(message_id=5, chat=NS(id=7)), data='test_ai_config', answer=AsyncMock())
        with patch.object(checks.genai, 'Client', return_value=client), patch.object(auth, 'safe_edit', AsyncMock()) as progress_edit, patch.object(keys, 'safe_edit', AsyncMock()) as menu_edit:
            await asyncio.wait_for(auth.cb_test_ai(call, self.state), 0.3)
            await asyncio.wait_for(entered.wait(), 1)
            job = auth._active_credential_tests[7]
            call.data = 'stop_ai_test'
            await asyncio.wait_for(auth.cb_stop_test(call, self.state), 0.3)
            self.assertNotIn(7, auth._active_credential_tests)
            self.assertFalse(job.task.done())
            menu_edit.assert_awaited_once()
            buttons = menu_edit.await_args.args[3].inline_keyboard
            self.assertNotIn('stop_ai_test', [button.callback_data for row in buttons for button in row])
            report = (await self.config()).module_settings['api_keys_audit']['last_test']
            self.assertEqual(report['checked'], 0)
            release.set()
            await asyncio.wait_for(job.task, 0.3)
            menu_edit.assert_awaited_once()
        self.assertEqual((await self.config()).module_settings['api_keys_audit']['last_test']['checked'], 0)

    async def test_timeout_is_not_counted_as_checked_but_http_429_is(self):
        error = MediaPreparationError('quota', 429, {})
        generate = AsyncMock(side_effect=[asyncio.TimeoutError(), NS(model_version='model-b'), error, NS(model_version='model-b')])
        with patch.object(checks.genai, 'Client', return_value=NS(aio=NS(models=NS(generate_content=generate)))):
            await checks.run_tests()
        report = (await self.config()).module_settings['api_keys_audit']['last_test']
        self.assertEqual(report['checked'], 3)
        summary = keys.test_summary(report['rows'])
        self.assertIn('⌛ 1', summary)
        self.assertIn('❌ 0', summary)
        self.assertIn('⏳ 1', summary)

    async def test_older_cancelled_report_cannot_replace_new_test(self):
        newer = {'at': '2026-10-20T00:00:02+00:00', 'rows': [], 'cancelled': False}
        older = {'at': '2026-10-20T00:00:01+00:00', 'rows': [], 'cancelled': True}
        await checks._save_report(newer)
        await checks._save_report(older)
        self.assertEqual((await self.config()).module_settings['api_keys_audit']['last_test']['at'], newer['at'])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(StopTests(name) for name in StopTests.__dict__ if name.startswith('test_'))
