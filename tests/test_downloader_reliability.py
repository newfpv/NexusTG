import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from core.job_queue import JobQueue
from core import downloader_worker as worker
from modules import media_downloader as md

URL = 'https://www.youtube.com/shorts/demo123'


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.queue = JobQueue()
        self.handlers = {}
        def on_message(flt, **kwargs):
            def save(function):self.handlers[function.__name__]=function;return function
            return save
        with patch.object(md, 'JobQueue', return_value=self.queue):
            md.register_userbot(NS(on_message=on_message))
        self.cfg = dict(auto_my=True,auto_other=True,allow_cmd=True,enable_shorts=False,
                        enable_tiktok=True,enable_reels=True,enable_pinterest=True,
                        replace_my=True,replace_other=False,command='.dl')
        self.status = NS(edit=AsyncMock(),delete=AsyncMock())
        self.client = NS(send_message=AsyncMock(return_value=self.status),send_video=AsyncMock(),
                         send_photo=AsyncMock(),send_media_group=AsyncMock(),get_messages=AsyncMock())
        self.calls = 0
        self.workdirs = []
        async def extract(url, directory):
            self.calls += 1
            path=Path(directory)/'video.mp4';path.write_bytes(b'video')
            return dict(kind='video',paths=[str(path)],title='Demo')
        def directory(**kwargs):
            path=self.root/str(len(self.workdirs));path.mkdir();self.workdirs.append(path);return str(path)
        self.extract = AsyncMock(side_effect=extract)
        for p in (patch.object(md,'_get_g_cfg',AsyncMock(return_value=self.cfg)),
                  patch.object(md,'_get_c_cfg',AsyncMock(return_value={})),
                  patch.object(md,'run_media_worker',self.extract),
                  patch.object(md.tempfile,'mkdtemp',side_effect=directory)):
            p.start();self.addCleanup(p.stop)

    def message(self, mid=1, own=False, text=URL, reply=None, **kwargs):
        message=NS(id=mid,chat=NS(id=7,type=md.enums.ChatType.PRIVATE),text=text,caption=None,
                   from_user=NS(is_self=own),outgoing=own,entities=None,caption_entities=None,
                   reply_to_message=reply,reply_to_message_id=reply.id if reply else None,
                   edit=AsyncMock(return_value=self.status),delete=AsyncMock(),**kwargs)
        return message

    async def handle(self, kind, message):
        await self.handlers[kind+'_download_handler'](self.client,message)
        if self.queue.worker:
            await self.queue.worker

    async def test_auto_own_replaces_only_after_success_and_other_keeps_reply_source(self):
        self.cfg['enable_shorts']=True
        own=self.message(own=True)
        async def send(**kwargs):own.delete.assert_not_awaited()
        self.client.send_video.side_effect=send
        await self.handle('auto',own)
        own.delete.assert_awaited_once()
        self.assertIsNone(self.client.send_video.await_args.kwargs['reply_parameters'])
        self.client.send_video.side_effect=None
        other=self.message(mid=2)
        await self.handle('auto',other)
        other.delete.assert_not_awaited()
        self.assertEqual(self.client.send_video.await_args.kwargs['reply_parameters'].message_id,2)
        self.assertFalse(any(path.exists() for path in self.workdirs))

    async def test_manual_uses_source_author_and_ignores_automatic_platform_switch(self):
        other=self.message(mid=10)
        command=self.message(mid=11,own=True,text='  .DL  ',reply=other)
        await self.handle('cmd',command)
        self.assertEqual(self.calls,1)
        other.delete.assert_not_awaited()
        self.assertEqual(self.client.send_video.await_args.kwargs['reply_parameters'].message_id,10)

    async def test_inline_command_does_not_auto_run_twice_or_erase_preserved_source(self):
        self.cfg['replace_my']=False
        command=self.message(own=True,text='.dl '+URL)
        await self.handle('auto',command)
        await self.handle('cmd',command)
        self.assertEqual(self.calls,1)
        command.edit.assert_not_awaited();command.delete.assert_not_awaited()
        self.assertEqual(self.client.send_video.await_args.kwargs['reply_parameters'].message_id,command.id)

    async def test_failed_auto_download_can_be_retried_immediately_by_command(self):
        self.cfg['enable_shorts']=True
        original=self.extract.side_effect
        async def extract(url,path):
            if not self.extract.await_count > 1:raise ValueError('unavailable')
            return await original(url,path)
        self.extract.side_effect=extract
        source=self.message()
        await self.handle('auto',source)
        self.assertFalse(self.queue.completed)
        await self.handle('cmd',self.message(mid=2,own=True,text='.dl',reply=source))
        self.client.send_video.assert_awaited_once()
        self.assertEqual(self.extract.await_count,2)

    async def test_failure_stays_visible_and_original_is_not_deleted(self):
        self.extract.side_effect=asyncio.TimeoutError()
        source=self.message(own=True)
        await self.handle('cmd',self.message(mid=2,own=True,text='.dl',reply=source))
        source.delete.assert_not_awaited();self.status.delete.assert_not_awaited()
        self.assertEqual(self.status.edit.await_args.args[0],md._('md_process_error'))
        self.assertFalse(self.queue.completed)

    async def test_failed_upload_keeps_source_and_allows_retry(self):
        self.client.send_video.side_effect=ValueError('rejected')
        source=self.message(own=True)
        await self.handle('cmd',self.message(mid=2,own=True,text='.dl',reply=source))
        source.delete.assert_not_awaited()
        self.assertFalse(self.queue.completed)

    async def test_missing_reply_object_is_fetched(self):
        self.client.get_messages.return_value=self.message(mid=12)
        command=self.message(mid=13,own=True,text='.dl');command.reply_to_message_id=12
        await self.handle('cmd',command)
        self.client.get_messages.assert_awaited_once_with(7,12)
        self.client.send_video.assert_awaited_once()

    async def test_text_link_supported_and_uploaded_media_does_not_loop(self):
        self.cfg['enable_shorts']=True
        source=self.message(text='Посмотри')
        source.entities=[NS(url=URL)]
        await self.handle('auto',source)
        own=self.message(mid=2,own=True,text=None,video=NS())
        own.caption='Demo';own.caption_entities=[NS(url=URL)]
        await self.handle('auto',own)
        self.assertEqual(self.calls,1)

    async def test_no_url_has_help_and_prefix_is_not_a_command(self):
        await self.handle('cmd',self.message(own=True,text='.dl'))
        self.assertIn('.dl',self.client.send_message.await_args.args[1])
        self.client.send_message.reset_mock()
        await self.handle('cmd',self.message(own=True,text='.dll'))
        self.client.send_message.assert_not_awaited()
        self.extract.assert_not_awaited()

    async def test_deduplicated_success_reports_it_instead_of_silent_drop(self):
        source=self.message()
        await self.handle('cmd',self.message(mid=2,own=True,text='.dl',reply=source))
        await self.handle('cmd',self.message(mid=3,own=True,text='.dl',reply=source))
        self.assertEqual(self.calls,1)
        self.assertEqual(self.client.send_message.await_args.args[1],md._('md_already_sent'))

    async def test_local_overrides_apply_to_source_author_independently(self):
        self.assertFalse(md._replace_source(self.message(own=True),self.cfg,{'replace_my':0}))
        self.assertTrue(md._replace_source(self.message(),self.cfg,{'replace_other':1}))
        self.assertFalse(md._replace_source(self.message(),self.cfg,{'replace_other':2}))


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    async def test_real_subprocess_timeout_is_reaped(self):
        program='import os,time,pathlib;pathlib.Path("'+(self.root/'pid').as_posix()+'").write_text(str(os.getpid()));time.sleep(30)'
        with patch.object(worker,'worker_command',return_value=[sys.executable,'-c',program]):
            with self.assertRaises(asyncio.TimeoutError):
                await worker.run_media_worker('url',self.root,timeout=1)
        pid=int((self.root/'pid').read_text())
        if os.name!='nt':
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)

    async def test_cancellation_stops_extractor_before_returning(self):
        marker=self.root/'pid'
        program=f'import os,time,pathlib;pathlib.Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)'
        with patch.object(worker,'worker_command',return_value=[sys.executable,'-c',program]):
            task=asyncio.create_task(worker.run_media_worker('url',self.root))
            for _ in range(100):
                if marker.exists():break
                await asyncio.sleep(.02)
            self.assertTrue(marker.exists());task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        if os.name!='nt':
            with self.assertRaises(ProcessLookupError):os.kill(int(marker.read_text()),0)

    async def test_result_cannot_reference_outside_files(self):
        outside=self.root.parent/(self.root.name+'-outside.mp4');outside.write_bytes(b'private')
        self.addCleanup(outside.unlink)
        (self.root/worker.RESULT_NAME).write_text(json.dumps(dict(kind='video',paths=[str(outside)],title='x')))
        with patch.object(worker,'worker_command',return_value=[sys.executable,'-c','pass']):
            with self.assertRaises(ValueError):await worker.run_media_worker('url',self.root)
        self.assertEqual(outside.read_bytes(),b'private')


if __name__=='__main__':unittest.main()
