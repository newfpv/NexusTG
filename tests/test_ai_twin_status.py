import asyncio
import copy
import json
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from pathlib import Path
import atexit
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import config as _test_config
_test_database = tempfile.TemporaryDirectory(prefix='nexustg-tests-', ignore_cleanup_errors=True)
atexit.register(_test_database.cleanup)
_test_config.DB_PATH = 'sqlite+aiosqlite:///' + str(Path(_test_database.name) / 'test.sqlite3')
from modules import ai_twin as ai


class ReplyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ai.active_reply_tasks.clear()
        ai.reply_statuses.clear()
        self.g = dict(is_active=True, search_enabled=False, prompt='', typing_speed=0,
                      sleep_start=None, sleep_end=None, db_min=0, db_max=0,
                      da_min=0, da_max=0, reaction='👍', h_typing=False,
                      h_ignore=0, h_smart=False, s_mul=0, tmin=0, tmax=0,
                      pmin=0, pmax=0, ai_debug=False)
        self.c = dict(is_active=False, is_ignored=False, prompt=None,
                      db_min=None, db_max=None, da_min=None, da_max=None,
                      h_typing=2, h_ignore=-1, h_smart=2, s_mul=None,
                      tmin=None, tmax=None, pmin=None, pmax=None, search_enabled=False)
        self.msg = NS(chat=NS(id=42, title=None, type=ai.ChatType.PRIVATE), id=7,
                      from_user=NS(id=42, first_name='Test', is_bot=False, is_self=False),
                      text='hello', caption=None, photo=None, video=None,
                      reply_to_message=None, outgoing=False, service=None, empty=False)
        self.client = NS(send_message=AsyncMock(return_value=NS(edit_text=AsyncMock())),
                         read_chat_history=AsyncMock(), send_reaction=AsyncMock(),
                         send_chat_action=AsyncMock(), is_connected=True)
        self.patches = [patch.object(ai, '_get_g_cfg', AsyncMock(side_effect=lambda: copy.deepcopy(self.g))),
                        patch.object(ai, '_get_c_cfg', AsyncMock(side_effect=lambda cid: copy.deepcopy(self.c))),
                        patch.object(ai, 'build_dialog_context', AsyncMock(return_value=('history', [], 0, False))),
                        patch.object(ai, 'generate_ai_response', AsyncMock(return_value='reply')),
                        patch.object(ai, 'simulate_human_typing', AsyncMock()),
                        patch.object(ai, 'save_checkpoint', AsyncMock()),
                        patch.object(ai, 'get_checkpoint', AsyncMock(return_value=None))]
        for p in self.patches:
            p.start()
        ai.set_reply_status(42, 'received', self.msg)

    async def asyncTearDown(self):
        for task in list(ai.active_reply_tasks.values()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for p in reversed(self.patches):
            p.stop()

    async def run_reply(self, force=False):
        await ai.process_reply(self.client, self.msg, force=force)
        return ai.reply_statuses[42]['stage']

    async def test_disabled(self):
        self.g['is_active'] = False
        self.assertEqual(await self.run_reply(), 'disabled')
        self.client.send_message.assert_not_awaited()

    async def test_excluded(self):
        self.c['is_ignored'] = True
        self.assertEqual(await self.run_reply(), 'excluded')

    async def test_sleep(self):
        self.g.update(sleep_start='00:00', sleep_end='23:59')
        self.assertEqual(await self.run_reply(), 'sleep')

    async def test_random_ignore(self):
        self.g['h_ignore'] = 100
        self.assertEqual(await self.run_reply(), 'ignored')
        self.client.send_message.assert_not_awaited()

    async def test_force_bypasses_disabled_sleep_delays_and_ignore(self):
        self.g.update(is_active=False, sleep_start='00:00', sleep_end='23:59',
                      db_min=100, db_max=100, da_min=100, da_max=100,
                      h_ignore=100, h_smart=True, s_mul=100, h_typing=True)
        self.c['is_ignored'] = True
        stage = await asyncio.wait_for(self.run_reply(force=True), 3)
        self.assertEqual(stage, 'sent')
        self.client.send_message.assert_awaited_once()
        ai.simulate_human_typing.assert_not_awaited()

    async def test_api_limit(self):
        ai.generate_ai_response.return_value = ai._('status_waiting')
        self.assertEqual(await self.run_reply(), 'api_unavailable')
        self.client.send_message.assert_not_awaited()

    async def test_no_key_error_not_sent_to_contact(self):
        ai.generate_ai_response.return_value = ai._('err_no_api_keys')
        self.assertEqual(await self.run_reply(), 'api_unavailable')
        self.client.send_message.assert_not_awaited()

    async def test_reaction(self):
        ai.generate_ai_response.return_value = '[LIKE]'
        self.assertEqual(await self.run_reply(), 'reaction')
        self.client.send_message.assert_not_awaited()

    async def test_force_only_reaction_is_not_reported_as_sent(self):
        ai.generate_ai_response.return_value = '[LIKE]'
        self.assertEqual(await self.run_reply(force=True), 'no_text')
        self.client.send_message.assert_not_awaited()
        self.client.send_reaction.assert_not_awaited()

    async def test_send_failure(self):
        self.client.send_message.side_effect = RuntimeError('test send failure')
        self.assertEqual(await self.run_reply(), 'error')

    async def test_context_timeout(self):
        ai.build_dialog_context.side_effect = asyncio.TimeoutError
        self.assertEqual(await self.run_reply(), 'context_timeout')

    async def test_telegram_flood_wait_persisted(self):
        ai.build_dialog_context.side_effect = ai.FloodWait(600)
        self.assertEqual(await self.run_reply(), 'flood_wait')
        self.assertGreater(ai.save_checkpoint.await_args.kwargs['retry_at'], ai.time.time() + 590)
        self.client.send_message.assert_not_awaited()

    async def test_force_cannot_bypass_telegram_limit(self):
        ai.get_checkpoint.return_value = dict(retry_at=ai.time.time() + 600)
        self.assertFalse(await ai.schedule_reply(self.client, self.msg, force=True))

    async def test_forced_double_click(self):
        event = asyncio.Event()
        async def blocking(*args, **kwargs):
            await event.wait()
        with patch.object(ai, 'process_reply', blocking):
            self.assertTrue(await ai.schedule_reply(self.client, self.msg, force=True))
            original = ai.active_reply_tasks[42]
            self.assertFalse(await ai.schedule_reply(self.client, self.msg, force=True))
            self.assertIs(ai.active_reply_tasks[42], original)

    async def test_replace_waiting_task(self):
        event = asyncio.Event()
        async def blocking(*args, **kwargs):
            await event.wait()
        with patch.object(ai, 'process_reply', blocking):
            await ai.schedule_reply(self.client, self.msg)
            original = ai.active_reply_tasks[42]
            self.assertTrue(await ai.schedule_reply(self.client, self.msg, force=True))
            self.assertTrue(original.cancelled())

    async def test_force_during_send_is_rejected(self):
        event = asyncio.Event()
        async def blocking(*args, **kwargs):
            await event.wait()
        with patch.object(ai, 'process_reply', blocking):
            await ai.schedule_reply(self.client, self.msg)
            ai.set_reply_status(42, 'sending')
            self.assertFalse(await ai.schedule_reply(self.client, self.msg, force=True))

    async def test_recovery_does_not_cancel_live_task(self):
        event = asyncio.Event()
        async def blocking(*args, **kwargs):
            await event.wait()
        with patch.object(ai, 'process_reply', blocking):
            await ai.schedule_reply(self.client, self.msg)
            original = ai.active_reply_tasks[42]
            self.assertFalse(await ai.schedule_reply(self.client, self.msg, recovery=True))
            self.assertIs(ai.active_reply_tasks[42], original)

    async def test_recovery_does_not_repeat_just_sent_reply(self):
        ai.set_reply_status(42, 'sent')
        self.assertFalse(await ai.schedule_reply(self.client, self.msg, recovery=True))
        self.assertFalse(await ai.schedule_reply(self.client, self.msg, force=True))

    async def test_cancelled_status(self):
        ai.generate_ai_response.side_effect = asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            await self.run_reply()
        self.assertEqual(ai.reply_statuses[42]['stage'], 'cancelled')

    async def test_force_does_not_reply_after_outgoing_message(self):
        async def history(*args, **kwargs):
            yield NS(outgoing=True)
            yield self.msg
        self.client.get_chat_history = history
        ai.userbot_client = self.client
        call = NS(from_user=NS(id=1), data='ai_force_reply_42',
                  answer=AsyncMock(), message=NS(answer=AsyncMock()))
        with patch.object(ai.plugins.db, 'get_global_config', AsyncMock(return_value=NS(admin_id=1))):
            await ai.force_reply_cb(call, NS())
        call.message.answer.assert_awaited_once_with(ai._('ai_force_no_messages'))
        self.assertNotIn(42, ai.active_reply_tasks)

    async def test_force_permission(self):
        call = NS(from_user=NS(id=2), answer=AsyncMock())
        with patch.object(ai.plugins.db, 'get_global_config', AsyncMock(return_value=NS(admin_id=1))):
            await ai.force_reply_cb(call, NS())
        call.answer.assert_awaited_once_with(ai._('auth_not_authorized'), show_alert=True)

    async def test_status_timer(self):
        ai.set_reply_status(42, 'delay_before', seconds=60)
        self.assertIn('7', ai.reply_status_text(42))
        self.assertIn('60', ai.reply_status_text(42))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        from datetime import datetime, timezone
        self.now = 2000000000
        self.message = NS(id=100, chat=NS(type=ai.ChatType.PRIVATE), outgoing=False,
                          from_user=NS(id=42, is_self=False, is_bot=False),
                          date=datetime.fromtimestamp(self.now - 120, tz=timezone.utc),
                          service=None, empty=False)

    def check(self, checkpoint=None):
        from core.ai_recovery import should_recover
        return should_recover(self.message, checkpoint, self.now)

    def test_missed_update(self):
        self.assertTrue(self.check())

    def test_no_repeat_for_terminal_states(self):
        for stage in ['sent', 'reaction', 'ignored', 'disabled', 'excluded', 'no_text']:
            self.assertFalse(self.check(dict(message_id=100, stage=stage, updated_at=0, attempts=1)))

    def test_retry_transient_failure_after_backoff(self):
        checkpoint = dict(message_id=100, stage='api_unavailable', updated_at=self.now-61, attempts=1)
        self.assertTrue(self.check(checkpoint))
        checkpoint['updated_at'] = self.now - 10
        self.assertFalse(self.check(checkpoint))
        checkpoint.update(updated_at=self.now-61, attempts=2)
        self.assertFalse(self.check(checkpoint))

    def test_new_message_bypasses_previous_backoff(self):
        self.assertTrue(self.check(dict(message_id=99, stage='api_unavailable', updated_at=self.now, attempts=8)))

    def test_new_message_does_not_bypass_telegram_wait(self):
        self.assertFalse(self.check(dict(message_id=99, stage='flood_wait', updated_at=0,
                                        attempts=1, retry_at=self.now + 600)))

    def test_already_answered_history_is_not_replayed(self):
        self.message.outgoing = True
        self.assertFalse(self.check())

    def test_old_history_is_not_replayed(self):
        from datetime import timedelta
        self.message.date -= timedelta(days=2)
        self.assertFalse(self.check())


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_attempts_and_stale_writer(self):
        from core import ai_recovery as recovery
        await recovery.save_checkpoint(987654321, 10, 'received', new_attempt=True)
        await recovery.save_checkpoint(987654321, 10, 'api_unavailable')
        first = await recovery.get_checkpoint(987654321)
        self.assertEqual(first['attempts'], 1)
        await recovery.save_checkpoint(987654321, 10, 'received', new_attempt=True)
        second = await recovery.get_checkpoint(987654321)
        self.assertEqual(second['attempts'], 2)
        await recovery.save_checkpoint(987654321, 11, 'sent', new_attempt=True)
        await recovery.save_checkpoint(987654321, 10, 'cancelled')
        final = await recovery.get_checkpoint(987654321)
        self.assertEqual((final['message_id'], final['stage'], final['attempts']), (11, 'sent', 1))

    async def test_recover_missed_message_starts_normal_reply(self):
        from core import ai_recovery as recovery
        from datetime import datetime, timezone
        msg = NS(id=50, date=datetime.now(timezone.utc), outgoing=False,
                 chat=NS(id=555, type=ai.ChatType.PRIVATE),
                 from_user=NS(id=555, is_self=False, is_bot=False), service=None, empty=False)
        async def history(*args, **kwargs):
            yield msg
        client = NS(get_chat_history=history)
        with patch.object(ai, '_get_c_cfg', AsyncMock(return_value=dict(is_active=True, is_ignored=False))), \
                patch.object(ai, '_get_g_cfg', AsyncMock(return_value=dict(is_active=False))), \
                patch.object(ai, 'schedule_reply', AsyncMock(return_value=True)):
            await recovery.recover_chat(client, 555)
            ai.schedule_reply.assert_awaited_once_with(client, msg, recovery=True)

    async def test_no_recovery_when_auto_reply_is_disabled(self):
        from core import ai_recovery as recovery
        with patch.object(ai, '_get_c_cfg', AsyncMock(return_value=dict(is_active=False, is_ignored=False))), \
                patch.object(ai, '_get_g_cfg', AsyncMock(return_value=dict(is_active=False))), \
                patch.object(ai, 'schedule_reply', AsyncMock()):
            await recovery.recover_chat(NS(), 556)
            ai.schedule_reply.assert_not_awaited()


if __name__ == '__main__':
    unittest.main(verbosity=2)
