import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from test_ai_twin_status import ReplyTests
from core import services
from modules import ai_twin as ai


class MediaBatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = {}
        self.downloads = []
        self.recognized = []
        self.active_files = 0
        self.max_active_files = 0
        self.ignored = set()
        self.messages = []
        self.block_on = None
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        repo = NS(get_media_memory=AsyncMock(side_effect=self.get_cache),
                  save_media_memory=AsyncMock(side_effect=self.save_cache),
                  is_msg_ignored=AsyncMock(side_effect=lambda cid, mid: mid in self.ignored))
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=NS())
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        self.client = NS(get_chat_history=self.history)
        self.patches = [patch.object(services, 'AsyncSessionLocal', factory),
                        patch.object(services, 'CoreRepository', return_value=repo),
                        patch.object(services, 'download_media_checked', AsyncMock(side_effect=self.download)),
                        patch.object(services, 'generate_ai_response', AsyncMock(side_effect=self.recognize)),
                        patch.object(services, 'transcribe_media', AsyncMock(side_effect=self.transcribe))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def message(self, mid, kind='photo', **extra):
        attrs = dict(id=mid, chat=NS(id=42, type=ai.ChatType.PRIVATE),
                     from_user=NS(is_self=False, is_bot=False, first_name='Test'),
                     text=f'text-{mid}' if kind == 'text' else None, caption=None, outgoing=False)
        attrs.update({key: NS(duration=10) if key == kind else None
                      for key in ('photo', 'video', 'voice', 'video_note', 'audio')})
        attrs.update(extra)
        return NS(**attrs)

    async def history(self, *args, **kwargs):
        for msg in reversed(self.messages):
            yield msg

    async def get_cache(self, mid, kind):
        return self.cache.get((mid, kind))

    async def save_cache(self, mid, kind, content):
        self.cache[(mid, kind)] = content

    async def download(self, client, msg, **kwargs):
        path = Path(self.tmp.name) / str(msg.id)
        path.write_bytes(b'fake-media')
        self.downloads.append(msg.id)
        self.max_active_files = max(self.max_active_files, len(list(Path(self.tmp.name).iterdir())))
        return str(path)

    async def recognize(self, prompt, media_path=None, **kwargs):
        mid = int(Path(media_path).name)
        if mid == self.block_on:
            self.blocked.set()
            await self.release.wait()
        self.recognized.append(mid)
        return f'content-{mid}'

    async def transcribe(self, path):
        return await self.recognize('', media_path=path)

    async def build(self, **kwargs):
        return await services.build_dialog_context(self.client, 42, limit=50, target_msg_id=50, **kwargs)

    async def test_thirty_five_images_all_reach_context_in_order(self):
        self.messages = [self.message(i) for i in range(1, 36)]
        result = await self.build()
        self.assertEqual(self.recognized, list(range(1, 36)))
        self.assertEqual(len(self.cache), 35)
        lines = result[0].splitlines()
        self.assertEqual(len(lines), 35)
        for mid, line in enumerate(lines, 1):
            self.assertIn(f'[content-{mid}]', line)
        self.assertLessEqual(self.max_active_files, 3)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.assertEqual(result[1], [])

    async def test_completed_cache_is_reused_on_next_message(self):
        self.messages = [self.message(i) for i in range(1, 8)]
        await self.build()
        self.messages.append(self.message(8))
        await self.build()
        self.assertEqual(self.downloads, list(range(1, 9)))
        self.assertEqual(self.recognized, list(range(1, 9)))

    async def test_new_message_cancels_and_resumes_from_completed_file(self):
        self.messages = [self.message(i) for i in range(1, 8)]
        self.block_on = 5
        task = asyncio.create_task(self.build())
        await asyncio.wait_for(self.blocked.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(set(mid for mid, _ in self.cache), {1, 2, 3, 4, 6})
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.messages.append(self.message(8, 'text'))
        self.release.set()
        result = await self.build()
        self.assertCountEqual(self.recognized, list(range(1, 8)))
        self.assertIn('text-8', result[0])
        self.assertCountEqual(self.downloads, [1, 2, 3, 4, 5, 5, 6, 7])

    async def test_batch_deadline_cleans_files_and_keeps_completed_cache(self):
        self.messages = [self.message(i) for i in range(1, 8)]
        self.block_on = 5
        with patch.object(services, 'CONTEXT_MEDIA_BATCH_TIMEOUT', 0.05):
            with self.assertRaises(asyncio.TimeoutError):
                await self.build()
        self.assertEqual(set(mid for mid, _ in self.cache), {1, 2, 3, 4, 6})
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_timeout_is_per_batch_rather_than_entire_album(self):
        self.messages = [self.message(i) for i in range(1, 10)]
        real_wait = asyncio.wait_for
        deadlines = []

        async def capture(awaitable, timeout):
            deadlines.append(timeout)
            return await real_wait(awaitable, timeout)

        with patch.object(services.asyncio, 'wait_for', side_effect=capture):
            await self.build()
        self.assertEqual(deadlines, [services.CONTEXT_HISTORY_TIMEOUT] + [services.CONTEXT_MEDIA_BATCH_TIMEOUT] * 3)

    async def test_ignored_bot_and_newer_media_are_not_downloaded(self):
        self.messages = [self.message(1), self.message(2),
                         self.message(3, from_user=NS(is_self=False, is_bot=True)), self.message(51)]
        self.ignored = {2}
        await self.build()
        self.assertEqual(self.downloads, [1])

    async def test_mixed_media_and_text_keep_playback_marks(self):
        self.messages = [self.message(1), self.message(2, 'text'), self.message(3, 'voice'),
                         self.message(4, 'video_note'), self.message(5, 'audio'), self.message(6, 'photo')]
        played = []
        result = await self.build(played_media_ids=played)
        self.assertEqual(played, [3, 4])
        self.assertEqual(self.downloads, [1, 3, 4, 5, 6])
        self.assertEqual(len(result[0].splitlines()), 6)

    async def test_text_only_history_uses_no_media_calls(self):
        self.messages = [self.message(1, 'text'), self.message(2, 'text')]
        result = await self.build()
        self.assertEqual(self.downloads, [])
        self.assertIn('text-1', result[0])
        self.assertIn('text-2', result[0])

    async def test_long_video_keeps_existing_skip_rule(self):
        self.messages = [self.message(1, 'video', video=NS(duration=601)), self.message(2)]
        result = await self.build()
        self.assertEqual(self.downloads, [2])
        self.assertTrue(result[3])


class BatchReplyTests(ReplyTests):
    async def test_reply_waits_until_whole_context_is_ready(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def context(*args, **kwargs):
            entered.set()
            await release.wait()
            return 'all images', [], 0, False

        ai.build_dialog_context.side_effect = context
        task = asyncio.create_task(self.run_reply())
        await asyncio.wait_for(entered.wait(), timeout=2)
        self.client.send_message.assert_not_awaited()
        ai.generate_ai_response.assert_not_awaited()
        release.set()
        self.assertEqual(await task, 'sent')
        self.assertIn('all images', ai.generate_ai_response.await_args.args[0])

    async def test_context_timeout_never_sends_partial_answer(self):
        ai.build_dialog_context.side_effect = asyncio.TimeoutError
        self.assertEqual(await self.run_reply(), 'context_timeout')
        self.client.send_message.assert_not_awaited()
        ai.generate_ai_response.assert_not_awaited()


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(loader.loadTestsFromTestCase(MediaBatchTests))
    for name in BatchReplyTests.__dict__:
        if name.startswith('test_'):
            suite.addTest(BatchReplyTests(name))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
