import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from test_media_batches import MediaBatchTests
from modules import ai_twin as ai


class ContextProgressTests(MediaBatchTests):
    async def test_counts_include_cached_files_and_phases_stop_before_answer(self):
        self.messages = [self.message(1), self.message(2), self.message(3, 'text')]
        self.cache[(1, 'photo')] = 'cached description'
        events=[]
        await self.build(structured=True,on_progress=lambda phase,**details:events.append((phase,details)))
        self.assertEqual([mid for mid in self.downloads], [2])
        self.assertEqual(events[0][0], 'history')
        self.assertEqual(events[-1][0], 'ready')
        self.assertIn('download', [phase for phase,_ in events])
        self.assertIn('recognize', [phase for phase,_ in events])
        final=next(details for phase,details in reversed(events) if phase=='pack')
        self.assertEqual((final['done'], final['total'], final['cached'], final['skipped']), (2,2,1,0))

    async def test_failed_recognition_is_skipped_not_reported_as_success(self):
        self.messages=[self.message(1)]
        events=[]
        with patch('core.services.generate_ai_response',AsyncMock(return_value=None)):
            await self.build(on_progress=lambda phase,**details:events.append((phase,details)))
        final=next(details for phase,details in reversed(events) if phase=='pack')
        self.assertEqual((final['done'],final['skipped']), (1,1))

    async def test_progress_observer_failure_cannot_cancel_response(self):
        self.messages=[self.message(1)]
        def broken(*args,**kwargs):raise ValueError('observer failure')
        result=await self.build(on_progress=broken)
        self.assertIn('content-1',result[0])

    async def test_cancelled_recognition_does_not_claim_completed_media(self):
        self.messages=[self.message(1)]
        self.block_on=1
        events=[]
        task=asyncio.create_task(self.build(on_progress=lambda phase,**details:events.append((phase,details))))
        await asyncio.wait_for(self.blocked.wait(),1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertNotIn('ready',[p for p,_ in events])
        self.assertTrue(all(d.get('done',0)==0 for _,d in events))


class ProgressStatusTests(unittest.TestCase):
    def setUp(self):ai.reply_statuses.clear()

    def test_old_message_progress_cannot_overwrite_new_reply(self):
        ai.set_reply_status(42,'received',self.message(2))
        ai.set_reply_status(42,'context')
        ai.update_context_progress(42,1,'recognize',total=20,done=5)
        self.assertEqual(ai.reply_statuses[42]['progress'],{})

    @staticmethod
    def message(mid):
        from types import SimpleNamespace
        return SimpleNamespace(id=mid)

    def test_stalled_phase_displays_age_and_timeout_without_claiming_eta(self):
        ai.set_reply_status(42,'context',self.message(2))
        ai.update_context_progress(42,2,'recognize',kind='photo',done=12,total=29,cached=5,skipped=1,deadline=time.time()+600)
        ai.reply_statuses[42]['progress']['changed_at']=time.time()-120
        rendered=ai.reply_status_text(42)
        self.assertIn('12/29',rendered)
        self.assertIn('120',rendered)
        self.assertIn(ai._('ai_progress_eta_unknown'),rendered)
        self.assertNotIn('AI_KEY',rendered)

    def test_terminal_stage_clears_old_progress_and_limits(self):
        ai.set_reply_status(42,'context',self.message(2))
        ai.update_context_progress(42,2,'download',total=29,done=12,cached=1,skipped=0)
        ai.set_reply_status(42,'sent')
        ai.update_context_progress(42,2,'recognize',total=29)
        self.assertEqual(ai.reply_statuses[42]['progress'],{})
        self.assertNotIn('12/29',ai.reply_status_text(42))
