import asyncio
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch
from test_managed_bots import ManagedTests
from test_media_batches import MediaBatchTests
from core.ai_pool import KeyPool
from core import managed_accounts as service
from modules import managed_bots as ui, ai_twin as ai
from core import services


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_service_dispatches_three_at_once_and_uses_every_configured_key(self):
        keys=[f'key-{i}' for i in range(9)];used=[];gate=asyncio.Event();started=asyncio.Event()
        factory=MagicMock();factory.return_value.__aenter__=AsyncMock(return_value=NS());factory.return_value.__aexit__=AsyncMock(return_value=False)
        def client(api_key):
            async def generate(**kwargs):
                used.append(api_key)
                if len(used)==3:started.set()
                await gate.wait()
                return NS(text='ok',usage_metadata=None,model_version='model')
            return NS(aio=NS(models=NS(generate_content=generate)))
        with patch.object(services,'AsyncSessionLocal',factory),patch.object(services,'CoreRepository',return_value=NS()),patch.object(services,'_get_ai_config',AsyncMock(return_value=(keys,['model']))),patch.object(services,'_is_available',AsyncMock(return_value=True)),patch.object(services,'prepare_contents',AsyncMock(return_value=['prompt'])),patch.object(services.genai,'Client',side_effect=client):
            tasks=[asyncio.create_task(services.generate_ai_response('prompt','test.jpg',search_enabled=False)) for _ in keys]
            try:
                await asyncio.wait_for(started.wait(),1)
                self.assertEqual(len(set(used)),3)
                gate.set()
                self.assertEqual(await asyncio.gather(*tasks),['ok']*9)
            finally:
                gate.set()
                await asyncio.gather(*tasks,return_exceptions=True)
        self.assertCountEqual(used,keys)

    async def test_all_keys_rotate_and_busy_key_does_not_block_free_key(self):
        pool=KeyPool();seen=[]
        for _ in range(7):
            async with pool.lease(['a','b','c']) as key:seen.append(key)
        self.assertEqual(seen,['a','b','c','a','b','c','a'])
        async with pool.lease(['a','b','c']) as first:
            async with pool.lease(['a','b','c']) as second:
                self.assertNotEqual(first,second)

    async def test_waiter_cancellation_and_request_failure_release_key(self):
        pool=KeyPool()
        async def waiting():
            async with pool.lease(['a']):return True
        async with pool.lease(['a']):
            task=asyncio.create_task(waiting());await asyncio.sleep(0)
            self.assertFalse(task.done());task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        with self.assertRaises(ValueError):
            async with pool.lease(['a']):raise ValueError()
        self.assertTrue(await asyncio.wait_for(waiting(),1))


class ParallelTests(MediaBatchTests):
    async def test_three_files_actually_overlap_and_context_stays_in_order(self):
        self.messages=[self.message(i) for i in range(1,7)]
        gates={i:asyncio.Event() for i in range(1,4)};entered=set();all_entered=asyncio.Event()
        async def recognize(prompt,media_path=None,**kwargs):
            mid=int(Path(media_path).name)
            if mid<=3:
                entered.add(mid)
                if len(entered)==3:all_entered.set()
                await gates[mid].wait()
            return f'content-{mid}'
        events=[]
        with patch('core.services.generate_ai_response',AsyncMock(side_effect=recognize)):
            task=asyncio.create_task(self.build(on_progress=lambda phase,**d:events.append(d)))
            await asyncio.wait_for(all_entered.wait(),1)
            self.assertTrue(any(e.get('recognizing')==3 for e in events))
            for mid in (3,2,1):gates[mid].set();await asyncio.sleep(0)
            result=await task
        for mid,line in enumerate(result[0].splitlines(),1):self.assertIn(f'[content-{mid}]',line)
        self.assertLessEqual(self.max_active_files,3)
        self.assertEqual(list(Path(self.tmp.name).iterdir()),[])


class DeleteTests(ManagedTests):
    async def test_confirm_then_stop_before_removal_other_profile_preserved(self):
        child=self.profile('second',99,222)
        (child/'data/private').write_text('archive')
        other=self.profile('other',88,333)
        before=(other/'account.json').read_bytes()
        self.call.data='managed_delete_second'
        await ui.callbacks(self.call,self.state)
        self.assertNotIn('delete_requested',json.loads((child/'account.json').read_text()))
        nonce=(await self.state.get_data())['managed_delete_confirmation']['nonce']
        self.call.data='managed_remove_'+nonce
        await ui.callbacks(self.call,self.state)
        self.assertFalse(json.loads((child/'account.json').read_text())['enabled'])
        service.purge_deleted(self.root,{'second'})
        self.assertTrue(child.exists())
        service.purge_deleted(self.root,set())
        self.assertFalse(child.exists());self.assertEqual((other/'account.json').read_bytes(),before)
        self.assertTrue((self.root/'main').exists())

    async def test_primary_forged_stale_and_cancelled_confirmations_cannot_delete(self):
        child=self.profile('second',99,222)
        for data in ('managed_delete_main','managed_remove_forged'):
            self.call.data=data;await ui.callbacks(self.call,self.state)
        self.assertTrue(child.exists())
        with self.assertRaises(PermissionError):service.request_delete('main','111')
        with self.assertRaises(ValueError):service.request_delete('second','333')
        self.call.data='managed_delete_second';await ui.callbacks(self.call,self.state)
        nonce=(await self.state.get_data())['managed_delete_confirmation']['nonce']
        self.call.data='managed_bot_second';await ui.callbacks(self.call,self.state)
        self.call.data='managed_remove_'+nonce;await ui.callbacks(self.call,self.state)
        self.assertNotIn('delete_requested',json.loads((child/'account.json').read_text()))

    async def test_symlinks_never_delete_targets_and_pending_profile_cannot_restart(self):
        child=self.profile('second',99,222);outside=self.root/'outside';outside.mkdir()
        (outside/'keep').write_text('private')
        (child/'data/link').symlink_to(outside,target_is_directory=True)
        service.request_delete('second','222')
        with self.assertRaises(ValueError):service.change_account('second','enable')
        service.purge_deleted(self.root,set())
        self.assertEqual((outside/'keep').read_text(),'private')
        self.assertFalse(child.exists())


class ParallelStatusTests(unittest.TestCase):
    def test_counts_queue_age_and_no_private_ids(self):
        ai.set_reply_status(42,'context',NS(id=987654321))
        ai.update_context_progress(42,987654321,'recognize',total=100,done=23,cached=5,skipped=1,active=3,downloading=1,recognizing=2)
        text=ai.reply_status_text(42)
        self.assertIn('23/100',text);self.assertIn('74',text)
        self.assertNotIn('987654321',text)
        self.assertLessEqual(len(text.splitlines()),5)


def load_tests(loader,tests,pattern):
    suite=unittest.TestSuite()
    for cls in (PoolTests,ParallelTests,DeleteTests,ParallelStatusTests):
        for name in cls.__dict__:
            if name.startswith('test_'):suite.addTest(cls(name))
    return suite
