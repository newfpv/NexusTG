import ast
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from test_settings_online import SettingsTests
from core import api_key_tests as checks, services, auth
from core.gemini_media import MediaPreparationError
from modules import api_keys_info as keys, always_online as online, custom_saved as saved, fact_checker as fact


class CompactTests(SettingsTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.check_patch = patch.object(checks, 'AsyncSessionLocal', self.sessions)
        self.check_patch.start()
        self.addCleanup(self.check_patch.stop)

    async def test_masked_panel_and_explicit_owner_reveal(self):
        hidden, kb = await keys.build_keys_info()
        self.assertLess(len(hidden), 600)
        self.assertNotIn('secret-one', hidden)
        data = {b.callback_data for row in kb.inline_keyboard for b in row}
        self.assertIn('api_keys_show_0', data)
        self.assertIn('api_keys_test', data)
        self.assertIn('test_ai_config', data)
        self.assertIn('api_keys_edit', data)
        reveal, _ = await keys.build_keys_info(reveal=True)
        self.assertIn('secret-one', reveal)
        call = NS(data='api_keys_show_0', from_user=NS(id=9), message=NS(chat=NS(id=9), message_id=1), answer=AsyncMock())
        with patch.object(keys, 'safe_edit', AsyncMock()) as edit:
            await keys.keys_info(call, self.state)
            edit.assert_not_awaited()
            call.answer.assert_awaited_once()

    async def test_result_groups_and_pagination_fit_telegram(self):
        rows = [{'fingerprint': str(i), 'results': [{'model': 'gemini-' + 'long-model-' * 10, 'status': status}
                for status in ['ok', 'quota', 'error', 'timeout', 'forbidden', 'invalid']]} for i in range(7)]
        async with self.sessions() as session:
            cfg = await services.CoreRepository(session).get_global_config()
            cfg.module_settings = {'api_keys_audit': {'last_test': {'rows': rows}}}
            await session.commit()
        result, kb = await keys.build_keys_info(results=True)
        self.assertLess(len(result), 3000)
        self.assertTrue(any('api_keys_results_1' == b.callback_data for row in kb.inline_keyboard for b in row))
        self.assertNotIn('secret-one', result)

    async def test_quick_check_removes_only_invalid_and_saves_safe_results(self):
        error = MediaPreparationError('invalid', 400, {'error': {'details': [{'reason': 'API_KEY_INVALID'}]}})
        with patch.object(checks, 'probe_key', AsyncMock(side_effect=[error, None])) as probe, \
             patch.object(checks.genai, 'Client') as generator:
            await checks.run_tests(mode='keys')
        self.assertEqual(probe.await_count, 2)
        generator.assert_not_called()
        cfg = await self.config()
        self.assertEqual(cfg.api_keys, 'secret-two')
        report = cfg.module_settings['api_keys_audit']['last_test']
        self.assertNotIn('secret-one', json.dumps(report))
        self.assertEqual(report['rows'][0]['results'][0]['status'], 'invalid')
        self.bot.send_message.assert_awaited_once()

    async def test_model_checks_use_tiny_output_and_readable_status(self):
        error = MediaPreparationError('quota', 429, {'error': {'details': []}})
        generate = AsyncMock(side_effect=[NS(model_version='model-a'), error, NS(model_version='model-a'), NS(model_version='model-b')])
        client = NS(aio=NS(models=NS(generate_content=generate)))
        with patch.object(checks.genai, 'Client', return_value=client):
            await checks.run_tests(mode='models')
        self.assertEqual(generate.await_count, 4)
        self.assertEqual(generate.await_args.kwargs['config'].max_output_tokens, 1)
        cfg = await self.config()
        rows = cfg.module_settings['api_keys_audit']['last_test']['rows']
        self.assertIn('⏳ 1', keys.test_summary(rows))
        self.assertEqual(cfg.api_keys, 'secret-one,secret-two')

    async def test_cancel_retains_partial_report(self):
        with patch.object(checks, 'probe_key', AsyncMock()) as probe:
            await checks.run_tests(mode='keys', progress_cb=AsyncMock(side_effect=[True, False]))
        self.assertEqual(probe.await_count, 1)
        report = (await self.config()).module_settings['api_keys_audit']['last_test']
        self.assertTrue(report['cancelled'])
        self.assertEqual(len(report['rows']), 1)
        result, _ = await keys.build_keys_info(results=True)
        self.assertIn(keys._('api_test_cancelled'), result)

    async def test_api_menu_replaces_separate_test_and_limits_buttons(self):
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        tree = ast.parse(Path('/app/main.py').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'generate_settings_menu_content')
        scope = {'InlineKeyboardMarkup': InlineKeyboardMarkup, 'InlineKeyboardButton': InlineKeyboardButton, '_': keys._, 'plugins': NS(core_settings_buttons=[])}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'menu', 'exec'), scope)
        _, kb = await scope['generate_settings_menu_content']()
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn('set_edit_keys', callbacks)
        self.assertNotIn('test_ai_config', callbacks)
        self.assertNotIn('sys_limits_menu', callbacks)


class ModuleUITests(unittest.IsolatedAsyncioTestCase):
    async def test_online_is_one_toggle_and_fact_checker_has_no_handlers(self):
        with patch.object(online.plugins.db, 'get_module_cfg', AsyncMock(return_value={'enabled': True})):
            rows = await online.get_settings_buttons()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0].callback_data, 'online_set_off')
        app = NS(on_message=MagicMock())
        fact.register_userbot(app)
        app.on_message.assert_not_called()
        self.assertEqual(await fact.get_settings_buttons(), [])


class SavedTransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine('sqlite+aiosqlite:///' + self.tmp.name + '/test.db')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.cfg = dict(is_active=True, transfer_saved=True, target_chat_id='-100', target_topic_id='55', trigger_command='.save')
        self.config_patch = patch.object(saved, '_get_cfg', AsyncMock(side_effect=lambda: dict(self.cfg)))
        self.db_patch = patch.object(saved, 'AsyncSessionLocal', self.sessions)
        self.config_patch.start();self.db_patch.start()
        saved._pending_transfers.clear();saved._transfer_lock = asyncio.Lock()
        self.message = NS(id=10, chat=NS(id=7), media_group_id=None, service=None, empty=False)
        self.client = NS(me=NS(id=7), forward_messages=AsyncMock(return_value=[NS(id=100, empty=False)]),
                         delete_messages=AsyncMock(return_value=1), get_media_group=AsyncMock())
        self.addAsyncCleanup(self.cleanup)

    async def cleanup(self):
        self.config_patch.stop();self.db_patch.stop()
        await self.engine.dispose();self.tmp.cleanup()

    async def test_forward_committed_before_delete_and_repeat_is_idempotent(self):
        async def delete(*args, **kwargs):
            async with self.sessions() as session:
                row = (await session.execute(text('select completed from custom_saved_transfers'))).first()
                self.assertEqual(row[0], 0)
            return 1
        self.client.delete_messages.side_effect = delete
        await saved.transfer_saved_message(self.client, self.message)
        await saved.transfer_saved_message(self.client, self.message)
        self.client.forward_messages.assert_awaited_once_with(chat_id=-100, from_chat_id=7, message_ids=[10], message_thread_id=55)
        self.client.delete_messages.assert_awaited_once()

    async def test_forward_failure_never_deletes_original(self):
        self.client.forward_messages.side_effect = OSError('offline')
        await saved.transfer_saved_message(self.client, self.message)
        self.client.delete_messages.assert_not_awaited()

    async def test_delete_failure_retries_without_forwarding_twice(self):
        self.client.delete_messages.side_effect = [OSError('offline'), 1]
        await saved.transfer_saved_message(self.client, self.message)
        saved._pending_transfers.clear()  # Mimic process-local state being lost.
        await saved.transfer_saved_message(self.client, self.message)
        self.client.forward_messages.assert_awaited_once()
        self.assertEqual(self.client.delete_messages.await_count, 2)

    async def test_disabled_wrong_source_and_self_destination_do_nothing(self):
        self.cfg['transfer_saved'] = False
        await saved.transfer_saved_message(self.client, self.message)
        self.cfg['transfer_saved'] = True
        self.cfg['target_chat_id'] = '7'
        await saved.transfer_saved_message(self.client, self.message)
        self.message.chat.id = 9
        await saved.transfer_saved_message(self.client, self.message)
        self.client.forward_messages.assert_not_awaited()
        self.client.delete_messages.assert_not_awaited()

    async def test_whole_album_is_forwarded_and_deleted_once(self):
        self.message.media_group_id = 'album'
        second = NS(id=11, chat=NS(id=7), media_group_id='album', service=None, empty=False)
        self.client.get_media_group.return_value = [self.message, second]
        self.client.forward_messages.return_value = [NS(id=100), NS(id=101)]
        self.client.delete_messages.return_value = 2
        await asyncio.gather(saved.transfer_saved_message(self.client, self.message), saved.transfer_saved_message(self.client, second))
        self.client.forward_messages.assert_awaited_once_with(chat_id=-100, from_chat_id=7, message_ids=[10, 11], message_thread_id=55)
        self.client.delete_messages.assert_awaited_once_with(7, [10, 11])

    async def test_partial_album_forward_does_not_delete_sources(self):
        self.message.media_group_id = 'album'
        second = NS(id=11, chat=NS(id=7), media_group_id='album')
        self.client.get_media_group.return_value = [self.message, second]
        await saved.transfer_saved_message(self.client, self.message)
        self.client.delete_messages.assert_not_awaited()

    async def test_messages_before_enabling_are_never_moved(self):
        from datetime import datetime, timezone
        self.cfg['transfer_since'] = int(time.time())
        self.message.date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await saved.transfer_saved_message(self.client, self.message)
        self.client.forward_messages.assert_not_awaited()
        self.client.delete_messages.assert_not_awaited()


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in [CompactTests, ModuleUITests, SavedTransferTests]:
        for name in cls.__dict__:
            if name.startswith('test_'):suite.addTest(cls(name))
    return suite
