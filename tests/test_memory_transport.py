import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from unittest.mock import MagicMock

from test_ai_twin_status import ReplyTests
from test_media_batches import MediaBatchTests
from aiohttp import web
from core import services
from core import gemini_media as media
from modules import ai_twin as ai


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'large.mp4'
        with self.path.open('wb') as file:
            file.truncate(12 * 1024 * 1024 + 7)
        self.uploads = self.starts = self.received = self.max_chunk = 0
        self.polls = 0
        self.state = 'PROCESSING'
        self.block = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        app = web.Application()
        app.router.add_post('/upload/v1beta/files', self.start)
        app.router.add_post('/upload-bytes', self.upload)
        app.router.add_get('/v1beta/files/test', self.status)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.root_patch = patch.object(media, 'API_ROOT', self.base)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addAsyncCleanup(self.runner.cleanup)
        media._files.clear()
        media._hashes.clear()

    async def start(self, request):
        self.starts += 1
        self.assertEqual(request.headers['X-Goog-Upload-Protocol'], 'resumable')
        self.assertEqual(int(request.headers['X-Goog-Upload-Header-Content-Length']), self.path.stat().st_size)
        return web.Response(headers={'X-Goog-Upload-URL': self.base + '/upload-bytes'})

    async def upload(self, request):
        self.uploads += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        try:
            async for chunk in request.content.iter_chunked(64 * 1024):
                self.received += len(chunk)
                self.max_chunk = max(self.max_chunk, len(chunk))
        except ConnectionResetError:
            return web.Response(status=499)
        return web.json_response({'file': {'name': 'files/test', 'state': self.state,
                                           'uri': 'https://example.invalid/files/test', 'mimeType': 'video/mp4'}})

    async def status(self, request):
        self.polls += 1
        return web.json_response({'name': 'files/test', 'state': 'ACTIVE',
                                 'uri': 'https://example.invalid/files/test', 'mimeType': 'video/mp4'})

    async def test_streaming_upload_polls_until_ready_and_reuses_hash_across_paths(self):
        first = await media.prepare_contents('hello', str(self.path), 'key-one', 'video/mp4')
        second_path = self.path.with_name('live.mp4')
        os.link(self.path, second_path)
        second = await media.prepare_contents('world', str(second_path), 'key-one', 'video/mp4')
        self.assertEqual(self.received, self.path.stat().st_size)
        self.assertLessEqual(self.max_chunk, 64 * 1024)
        self.assertEqual((self.uploads, self.starts, self.polls), (1, 1, 1))
        self.assertIsNone(first[1].inline_data)
        self.assertEqual(second[1].file_data.file_uri, first[1].file_data.file_uri)

    async def test_different_key_does_not_reuse_another_projects_file(self):
        self.state = 'ACTIVE'
        await media.prepare_contents('one', str(self.path), 'key-one', 'video/mp4')
        await media.prepare_contents('two', str(self.path), 'key-two', 'video/mp4')
        self.assertEqual(self.uploads, 2)

    async def test_small_media_stays_inline_and_empty_media_is_omitted(self):
        self.path.write_bytes(b'small')
        contents = await media.prepare_contents('hello', str(self.path), 'key', 'video/mp4')
        self.assertEqual(contents[1].inline_data.data, b'small')
        self.path.write_bytes(b'')
        self.assertEqual(await media.prepare_contents('hello', str(self.path), 'key', 'video/mp4'), ['hello'])
        self.assertEqual(self.starts, 0)

    async def test_cancellation_closes_upload_and_releases_slot(self):
        self.block = True
        task = asyncio.create_task(media.prepare_contents('one', str(self.path), 'key', 'video/mp4'))
        await asyncio.wait_for(self.entered.wait(), 5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.release.set()
        self.block = False
        self.state = 'ACTIVE'
        await asyncio.wait_for(media.prepare_contents('two', str(self.path), 'key', 'video/mp4'), 5)
        self.assertEqual(self.uploads, 2)

    async def test_failed_processing_is_not_cached(self):
        self.state = 'FAILED'
        with self.assertRaises(media.MediaPreparationError):
            await media.prepare_contents('one', str(self.path), 'key', 'video/mp4')
        self.assertEqual(len(media._files), 0)

    async def test_cache_eviction_is_bounded(self):
        self.state = 'ACTIVE'
        with patch.object(media, 'FILE_CACHE_SIZE', 1):
            await media.prepare_contents('one', str(self.path), 'key1', 'video/mp4')
            await media.prepare_contents('two', str(self.path), 'key2', 'video/mp4')
        self.assertEqual(len(media._files), 1)

    async def test_503mb_file_streams_inside_a_512mb_container(self):
        import resource
        self.state='ACTIVE'
        with self.path.open('wb') as source:source.truncate(503430097)
        result=await media.prepare_contents('describe',str(self.path),'key','video/mp4')
        self.assertEqual(self.received,503430097)
        self.assertIsNone(result[1].inline_data)
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        print('STREAM_503MB_MAX_RSS_KIB='+str(peak),flush=True)
        self.assertLess(peak,400*1024)


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    def records(self, count, length=100):
        return [{'message_id':i,'speaker':'CHAT_PARTNER','sender_id':42,'text':'x'*length}
                for i in range(1,count+1)]

    async def test_window_setting_and_latest_whole_messages_with_api_budget(self):
        self.assertEqual(services.AI_TWIN_HISTORY_LIMIT, 1000)
        self.assertEqual(services.CONTEXT_HISTORY_TOKEN_BUDGET, 65536)
        records=self.records(30)
        async def counter(text,*args):
            return (len(text.splitlines())-1)*100
        with patch.object(services,'CONTEXT_HISTORY_TOKEN_BUDGET',1000), \
             patch.object(services,'_get_ai_config',AsyncMock(return_value=(['key'],['model']))), \
             patch.object(services,'count_text_tokens',AsyncMock(side_effect=counter)):
            payload=await services._bounded_dialog_context(records,30)
        header,*rows=[json.loads(line) for line in payload.splitlines()]
        self.assertLessEqual(len(rows),10)
        self.assertEqual(rows[-1][0],30)
        self.assertTrue(all(row[2]=='x'*100 for row in rows))
        self.assertEqual(header['to'],30)

    async def test_counter_failure_has_a_conservative_local_bound(self):
        with patch.object(services,'CONTEXT_HISTORY_TOKEN_BUDGET',1000), \
             patch.object(services,'_get_ai_config',AsyncMock(return_value=(['key'],['model']))), \
             patch.object(services,'count_text_tokens',AsyncMock(side_effect=TimeoutError)):
            payload=await services._bounded_dialog_context(self.records(30),30)
        self.assertLessEqual(len(payload.encode('utf-8'))+16,1000)
        self.assertEqual(json.loads(payload.splitlines()[-1])[0],30)

    async def test_trimmed_quote_is_preserved_in_shared_lookup(self):
        records=self.records(30)
        records[-1]['reply_to']={'message_id':1,'speaker':'CHAT_PARTNER','sender_id':42,'quoted_text':'original quote'}
        with patch.object(services,'CONTEXT_HISTORY_TOKEN_BUDGET',1000), \
             patch.object(services,'_get_ai_config',AsyncMock(return_value=([],[]))):
            payload=await services._bounded_dialog_context(records,30)
        self.assertEqual(json.loads(payload.splitlines()[0])['q']['1'][1],'original quote')

    async def test_huge_description_does_not_remove_latest_message_text(self):
        records=self.records(1,10)
        records[0]['media']={'type':'photo','description_or_transcript':'x'*10000}
        with patch.object(services,'CONTEXT_HISTORY_TOKEN_BUDGET',1000), \
             patch.object(services,'_get_ai_config',AsyncMock(return_value=([],[]))):
            payload=await services._bounded_dialog_context(records,1)
        self.assertLess(len(payload.encode()),1000)
        self.assertEqual(json.loads(payload.splitlines()[-1])[2],'x'*10)
        self.assertEqual(len(records[0]['media']['description_or_transcript']),10000)


class RetainMediaTests(MediaBatchTests):
    async def test_old_media_excluded_by_budget_is_never_downloaded(self):
        self.messages=[self.message(1)]+[self.message(i,'text',text='x'*200) for i in range(2,51)]
        with patch.object(services,'CONTEXT_HISTORY_TOKEN_BUDGET',1000), \
             patch.object(services,'_get_ai_config',AsyncMock(return_value=([],[]))):
            result=await self.build(structured=True)
        self.assertEqual(self.downloads,[])
        self.assertEqual(json.loads(result[0].splitlines()[-1])[0],50)

    async def test_target_file_can_be_reused_and_other_files_are_cleaned(self):
        self.messages=[self.message(1),self.message(50)]
        async def named_download(client,message,**kwargs):
            path=Path(self.tmp.name)/f'context_{message.id}.jpg'
            path.write_bytes(b'photo')
            return str(path)
        kept=[]
        with patch.object(services,'download_media_checked',AsyncMock(side_effect=named_download)), \
             patch.object(services,'generate_ai_response',AsyncMock(return_value='recognized')):
            await self.build(structured=True,retain_target_media=kept)
        self.assertEqual([Path(path).name for path in kept],['context_50.jpg'])
        self.assertEqual(list(Path(self.tmp.name).iterdir()),[Path(kept[0])])


class WindowReplyTests(ReplyTests):
    async def test_reply_uses_1000_window_and_retained_file_cleanup_list(self):
        await self.run_reply()
        kwargs=ai.build_dialog_context.await_args.kwargs
        self.assertEqual(kwargs['limit'],1000)
        self.assertIsInstance(kwargs['retain_target_media'],list)


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    def error(self, quota='GenerateRequestsPerDayPerProjectPerModel-FreeTier', retry='31032s'):
        items=[{'violations':[{'quotaId':quota,'quotaDimensions':{'model':'gemini-3.8-flash'}}]}]
        if retry:items.append({'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':retry})
        return NS(code=429,details={'error':{'details':items}})

    def test_daily_quota_respects_actual_retry_delay(self):
        self.assertEqual(services._cooldown_for(self.error()),31033)

    def test_minute_quota_has_its_own_retry_delay(self):
        self.assertEqual(services._cooldown_for(self.error('GenerateTokensPerMinute','12s')),13)

    def test_daily_quota_without_retry_waits_for_pacific_reset(self):
        self.assertGreater(services._cooldown_for(self.error(retry=None)),60)
        self.assertLessEqual(services._cooldown_for(self.error(retry=None)),86400)

    def test_invalid_400_key_is_an_auth_failure(self):
        error=NS(code=400,details={'error':{'details':[{'reason':'API_KEY_INVALID'}]}})
        self.assertTrue(services._auth_failure(error))
        self.assertEqual(services._cooldown_for(error),services.AUTH_COOLDOWN)

    async def test_model_daily_quota_does_not_disable_other_models_on_key(self):
        state=NS(exhausted_models={},unban_time=0,search_exhausted_models={},search_unban_time=0)
        repo=NS(get_ai_key_state=AsyncMock(return_value=state))
        class Session:
            async def __aenter__(self):return NS(commit=AsyncMock())
            async def __aexit__(self,*args):pass
        with patch.object(services,'AsyncSessionLocal',Session),patch.object(services,'CoreRepository',return_value=repo):
            await services._mark_failure('key','gemini-3.7-flash',False,self.error())
            self.assertFalse(await services._is_available(repo,'key','gemini-3.7-flash',False))
            self.assertFalse(await services._is_available(repo,'key','gemini-3.8-flash',False))
            self.assertTrue(await services._is_available(repo,'key','gemini-3.5-flash-lite',False))
        self.assertEqual(state.unban_time,0)


class HistoricalContextTests(unittest.TestCase):
    def test_utc_anchors_are_shared_without_repeating_every_timestamp(self):
        from datetime import datetime,timezone,timedelta
        base=datetime(2026,10,19,12,tzinfo=timezone.utc)
        records=[{'message_id':i+1,'speaker':'CHAT_PARTNER','sender_id':42,'text':'text',
                  'timestamp':base+timedelta(minutes=i)} for i in range(5)]
        header=json.loads(services._compact_dialog_context(records,5).splitlines()[0])
        self.assertEqual(header['d'],{'1':'2026-10-19T12:00Z','5':'2026-10-19T12:04Z'})

    def test_old_topics_are_explicitly_historical_in_system_rules(self):
        system=ai.twin_system_instruction('OWNER PERSONA','UTC')
        self.assertIn('Do not revive old topics unprompted',system)
        self.assertIn('prioritize the target and recent relevant turns',system)
        self.assertIn('OWNER PERSONA',system)


class RequestResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_media_replies_do_not_prepare_unbounded_payloads(self):
        factory=MagicMock()
        factory.return_value.__aenter__=AsyncMock(return_value=NS())
        factory.return_value.__aexit__=AsyncMock(return_value=False)
        release=asyncio.Event()
        async def generate(**kwargs):
            await release.wait()
            return NS(text='ok',model_version='model',usage_metadata=None)
        client=NS(aio=NS(models=NS(generate_content=AsyncMock(side_effect=generate))))
        prepared=AsyncMock(return_value=['prompt'])
        with patch.object(services,'AsyncSessionLocal',factory), \
             patch.object(services,'CoreRepository',return_value=NS()), \
             patch.object(services,'_get_ai_config',AsyncMock(return_value=(['key'],['model']))), \
             patch.object(services,'_is_available',AsyncMock(return_value=True)), \
             patch.object(services,'prepare_contents',prepared), \
             patch.object(services.genai,'Client',return_value=client):
            tasks=[asyncio.create_task(services.generate_ai_response('prompt','media.mp4','persona',False)) for _ in range(10)]
            await asyncio.sleep(0.05)
            self.assertEqual(prepared.await_count,1)
            release.set()
            self.assertEqual(await asyncio.gather(*tasks),['ok']*10)

    async def test_cancelled_download_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'context.mp4'
            async def interrupted(*args,**kwargs):
                path.write_bytes(b'partial')
                Path(str(path)+'.temp').write_bytes(b'partial')
                raise asyncio.CancelledError()
            with patch.object(services,'download_media_checked',AsyncMock(side_effect=interrupted)):
                with self.assertRaises(asyncio.CancelledError):
                    await services.download_context_media(NS(),NS(),str(path))
            self.assertEqual(list(Path(folder).iterdir()),[])

    async def test_failed_download_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'context.mp4'
            async def interrupted(*args,**kwargs):
                Path(str(path)+'.temp').write_bytes(b'partial')
                raise IOError('incomplete')
            with patch.object(services,'download_media_checked',AsyncMock(side_effect=interrupted)):
                with self.assertRaises(IOError):
                    await services.download_context_media(NS(),NS(),str(path))
            self.assertEqual(list(Path(folder).iterdir()),[])


def load_tests(loader,tests,pattern):
    result=unittest.TestSuite()
    for cls in [TransportTests,BudgetTests,RetainMediaTests,WindowReplyTests,QuotaTests,HistoricalContextTests,RequestResourceTests]:
        for name in cls.__dict__:
            if name.startswith('test_'):result.addTest(cls(name))
    return result


if __name__=='__main__':unittest.main(verbosity=2)
