import asyncio
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from test_ai_twin_status import ReplyTests
from core import services
from modules import ai_twin as ai


class PlaybackReplyTests(ReplyTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.client.invoke = AsyncMock()

        async def context(*args, **kwargs):
            kwargs['played_media_ids'].extend([5, 6])
            return 'voice transcript', [], 10, False

        ai.build_dialog_context.side_effect = context

    async def test_batch_playback_before_reply(self):
        async def send(*args, **kwargs):
            self.client.invoke.assert_awaited_once()
            self.assertEqual(self.client.invoke.await_args.args[0].id, [5, 6])
            return NS(edit_text=AsyncMock())

        self.client.send_message.side_effect = send
        self.assertEqual(await self.run_reply(), 'sent')
        self.assertIsInstance(self.client.invoke.await_args.args[0], ai.functions.messages.ReadMessageContents)

    async def test_reaction_marks_playback(self):
        ai.generate_ai_response.return_value = '[LIKE]'
        self.assertEqual(await self.run_reply(), 'reaction')
        self.client.invoke.assert_awaited_once()

    async def test_generation_failure_still_marks_processed_media(self):
        ai.generate_ai_response.return_value = None
        self.assertEqual(await self.run_reply(), 'api_unavailable')
        self.client.invoke.assert_awaited_once()

    async def test_empty_batch_does_not_call_telegram(self):
        ai.build_dialog_context.side_effect = None
        self.assertEqual(await self.run_reply(), 'sent')
        self.client.invoke.assert_not_awaited()

    async def test_playback_error_does_not_block_reply(self):
        self.client.invoke.side_effect = RuntimeError('simulated Telegram failure')
        self.assertEqual(await self.run_reply(), 'sent')
        self.client.send_message.assert_awaited_once()

    async def test_playback_flood_wait_is_persisted(self):
        self.client.invoke.side_effect = ai.FloodWait(600)
        self.assertEqual(await self.run_reply(), 'flood_wait')
        self.client.send_message.assert_not_awaited()
        self.assertGreater(ai.save_checkpoint.await_args.kwargs['retry_at'], ai.time.time() + 590)

    async def test_disabled_does_not_mark_playback(self):
        self.g['is_active'] = False
        self.assertEqual(await self.run_reply(), 'disabled')
        self.client.invoke.assert_not_awaited()

    async def test_context_timeout_does_not_mark_playback(self):
        ai.build_dialog_context.side_effect = asyncio.TimeoutError
        self.assertEqual(await self.run_reply(), 'context_timeout')
        self.client.invoke.assert_not_awaited()


class ContextPlaybackTests(unittest.IsolatedAsyncioTestCase):
    def message(self, msg_id, kind='voice', outgoing=False, is_self=False):
        return NS(id=msg_id, chat=NS(id=42, type=ai.ChatType.PRIVATE),
                  from_user=NS(is_self=is_self, is_bot=False, first_name='Test'),
                  voice=NS(duration=10) if kind == 'voice' else None,
                  video_note=NS(duration=10) if kind == 'video_note' else None,
                  outgoing=outgoing, text='text' if kind == 'text' else None, caption=None)

    async def build(self, messages, descriptions=None, ignored=(), collector=True):
        async def history(*args, **kwargs):
            for msg in reversed(messages):
                yield msg

        async def describe(client, msg, paths):
            if descriptions is not None and msg.id in descriptions:
                return descriptions[msg.id], 10, False
            return ('transcript' if msg.voice or msg.video_note else ''), 10, False

        repo = NS(is_msg_ignored=AsyncMock(side_effect=lambda chat_id, msg_id: msg_id in ignored))
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=NS())
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        played = []
        kwargs = dict(target_msg_id=10)
        if collector:
            kwargs['played_media_ids'] = played
        with patch.object(services, 'AsyncSessionLocal', session_factory), \
                patch.object(services, 'CoreRepository', return_value=repo), \
                patch.object(services, '_describe_message_media', side_effect=describe):
            result = await services.build_dialog_context(NS(get_chat_history=history), 42, **kwargs)
        return played, result

    async def test_collects_all_consumed_incoming_voice_and_round_video(self):
        played, _ = await self.build([self.message(1), self.message(2, 'video_note'), self.message(3, 'text')])
        self.assertEqual(played, [1, 2])

    async def test_excludes_outgoing_and_self(self):
        played, _ = await self.build([self.message(1, outgoing=True), self.message(2, is_self=True)])
        self.assertEqual(played, [])

    async def test_skipped_failed_and_unavailable_media_not_marked(self):
        descriptions = {1: services._('ai_msg_file'), 2: '', 3: services._('status_waiting'),
                        4: services._('err_no_api_keys')}
        played, _ = await self.build([self.message(i) for i in range(1, 5)], descriptions)
        self.assertEqual(played, [])

    async def test_ignored_and_newer_messages_not_marked(self):
        played, _ = await self.build([self.message(1), self.message(2), self.message(11)], ignored={2})
        self.assertEqual(played, [1])

    async def test_existing_context_callers_keep_return_shape(self):
        played, result = await self.build([self.message(1)], collector=False)
        self.assertEqual(played, [])
        self.assertEqual(len(result), 4)
        self.assertIn('transcript', result[0])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for name in PlaybackReplyTests.__dict__:
        if name.startswith('test_'):
            suite.addTest(PlaybackReplyTests(name))
    suite.addTests(loader.loadTestsFromTestCase(ContextPlaybackTests))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
