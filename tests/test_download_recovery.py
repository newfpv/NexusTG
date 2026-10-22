import asyncio
import tempfile
import inspect
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

from core import media_download as media
from modules import text_struct as struct


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    def app(self, original):
        return NS(get_session=original,storage=NS(dc_id=AsyncMock(return_value=2)),
                  media_sessions={},sessions={},session=NS(stop=AsyncMock()))

    async def test_cancelled_initialization_is_evicted_and_next_download_connects(self):
        from pyrogram import Client
        calls=[]
        class FakeSession:
            def __init__(self,*args,**kwargs):
                self.is_started=asyncio.Event();self.stop=AsyncMock();calls.append(self)
            async def start(self):
                if len(calls)==1:await asyncio.Event().wait()
                self.is_started.set()
        app=self.app(None)
        app.storage.test_mode=AsyncMock(return_value=False)
        app.get_dc_option=AsyncMock(return_value=NS(ip_address='127.0.0.1',port=443))
        app.ipv6=False;app.session.auth_key=b'test-key'
        # Run the installed Kurigram method that caches Session before start.
        app.get_session=types.MethodType(Client.get_session,app)
        media.install_media_guard(app)
        with patch.object(inspect.getmodule(Client.get_session),'Session',FakeSession),patch.object(media,'CONNECT_TIMEOUT',.02):
            with self.assertRaises(asyncio.TimeoutError):await app.get_session(2,is_media=True)
            self.assertEqual(app.media_sessions,{})
            self.assertIs(await app.get_session(2,is_media=True),calls[1])
        calls[0].stop.assert_awaited_once();app.session.stop.assert_not_awaited()

    async def test_concurrent_requests_do_not_get_a_half_initialized_session(self):
        release=asyncio.Event();entered=asyncio.Event();count=0
        async def original(dc_id=None,is_media=False):
            nonlocal count
            count+=1;session=NS(is_started=asyncio.Event(),stop=AsyncMock())
            app.media_sessions[dc_id]=session;entered.set();await release.wait()
            session.is_started.set();return session
        app=self.app(original);media.install_media_guard(app)
        first=asyncio.create_task(app.get_session(2,is_media=True));await entered.wait()
        second=asyncio.create_task(app.get_session(2,is_media=True));await asyncio.sleep(.01)
        self.assertFalse(first.done());self.assertFalse(second.done());self.assertEqual(count,1)
        release.set();one,two=await asyncio.gather(first,second)
        self.assertIs(one,two);self.assertEqual(count,1)

    async def test_main_and_existing_other_dc_sessions_survive_media_failure(self):
        other=NS(is_started=asyncio.Event(),stop=AsyncMock());other.is_started.set()
        async def original(dc_id=None,is_media=False):
            session=NS(is_started=asyncio.Event(),stop=AsyncMock())
            app.media_sessions[dc_id]=session
            raise OSError('network unavailable')
        app=self.app(original);app.sessions[2]=other;media.install_media_guard(app)
        with self.assertRaises(OSError):await app.get_session(2,is_media=True)
        self.assertIs(app.sessions[2],other);other.stop.assert_not_awaited();app.session.stop.assert_not_awaited()

    async def test_cancelled_stalled_transfer_retires_media_connection_and_releases_slot(self):
        app=self.app(AsyncMock());entered=asyncio.Event()
        session=NS(is_started=asyncio.Event(),stop=AsyncMock());session.is_started.set();app.media_sessions[2]=session
        async def original_file(file_id):
            async with app.get_file_semaphore:
                entered.set();await asyncio.Event().wait();yield b'never'
        app.get_file=original_file;media.install_media_guard(app)
        async def consume():
            async for chunk in app.get_file(NS(dc_id=2)):pass
        task=asyncio.create_task(consume());await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertNotIn(2,app.media_sessions);self.assertEqual(app.get_file_semaphore._value,2)
        session.stop.assert_awaited_once();app.session.stop.assert_not_awaited()

    async def test_cancelled_download_does_not_close_connection_used_by_another_transfer(self):
        app=self.app(AsyncMock());both=asyncio.Event();release=asyncio.Event();started=0
        session=NS(is_started=asyncio.Event(),stop=AsyncMock());session.is_started.set();app.media_sessions[2]=session
        async def original_file(file_id):
            nonlocal started
            started+=1
            if started==2:both.set()
            await release.wait();yield b'complete'
        app.get_file=original_file;media.install_media_guard(app)
        async def consume():return [chunk async for chunk in app.get_file(NS(dc_id=2))]
        one=asyncio.create_task(consume());two=asyncio.create_task(consume());await both.wait()
        one.cancel()
        with self.assertRaises(asyncio.CancelledError):await one
        session.stop.assert_not_awaited();release.set();self.assertEqual(await two,[b'complete'])
        self.assertIs(app.media_sessions[2],session)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'recording.mp3'
        self.client=NS(get_messages=AsyncMock(return_value=None))

    def msg(self,download,size=4):
        return NS(id=7,chat=NS(id=42),audio=NS(file_size=size),download=download)

    async def test_continuous_progress_can_exceed_the_old_absolute_deadline(self):
        async def download(file_name,progress):
            for n in range(4):
                await asyncio.sleep(.02);self.path.write_bytes(b'x'*(n+1));await progress(n+1,4)
            return str(self.path)
        result=await media.download_media_checked(self.client,self.msg(download),self.path,timeout=.04,retries=1)
        self.assertEqual(Path(result).read_bytes(),b'xxxx')

    async def test_stalled_attempt_refreshes_and_the_next_attempt_succeeds(self):
        cancelled=asyncio.Event()
        async def stuck(**kwargs):
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        async def success(file_name,progress):self.path.write_bytes(b'good');return str(self.path)
        refreshed=self.msg(success);self.client.get_messages.return_value=refreshed
        self.assertEqual(await media.download_media_checked(self.client,self.msg(stuck),self.path,timeout=.01),str(self.path))
        self.assertTrue(cancelled.is_set());self.client.get_messages.assert_awaited_once_with(42,7)

    async def test_external_cancellation_releases_download_slot_and_removes_partial_data(self):
        semaphore=asyncio.Semaphore(1);entered=asyncio.Event()
        async def download(file_name,progress):
            async with semaphore:
                Path(file_name+'.temp').write_bytes(b'partial');entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(media.download_media_checked(self.client,self.msg(download),self.path))
        await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(semaphore._value,1);self.assertEqual(list(self.path.parent.iterdir()),[])

    async def test_total_deadline_still_limits_download_with_frequent_progress(self):
        async def download(file_name,progress):
            for n in range(100):
                self.path.write_bytes(b'x'*(n+1));await progress(n+1,100);await asyncio.sleep(.005)
            return str(self.path)
        with patch.object(media,'MAX_DOWNLOAD_SECONDS',.03):
            with self.assertRaises(media.MediaDownloadError):
                await media.download_media_checked(self.client,self.msg(download,100),self.path,timeout=1,retries=1)
        self.assertEqual(list(self.path.parent.iterdir()),[])

    async def test_incomplete_file_is_not_sent_to_transcription(self):
        async def download(**kwargs):self.path.write_bytes(b'bad');return str(self.path)
        with self.assertRaises(media.MediaDownloadError) as caught:
            await media.download_media_checked(self.client,self.msg(download),self.path,retries=1)
        self.assertEqual(caught.exception.reason,'incomplete_file');self.assertFalse(self.path.exists())

    async def test_structurer_reports_download_failure_before_calling_the_model(self):
        with patch.object(struct,'download_media_checked',AsyncMock(side_effect=media.MediaDownloadError('download_timeout'))), \
             patch.object(struct,'transcribe_media',AsyncMock()) as speech:
            with self.assertRaises(struct.StructSourceError) as caught:
                await struct._read_struct_media(self.client,NS(id=7,chat=NS(id=42),audio=NS()))
            self.assertEqual(str(caught.exception),struct._('media_download_error'));speech.assert_not_awaited()
