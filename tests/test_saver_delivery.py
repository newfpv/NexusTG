import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from test_account_controls import SaverRetentionTests
from modules import message_saver as saver


class SaverDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=SaverRetentionTests.asyncSetUp

    async def test_delete_notification_is_durable_deduplicated_before_topic_network(self):
        await self.connection.execute("INSERT INTO msg_cache (message_id,chat_id,user_id,text,file_path) VALUES (1,7,7,'keep','')")
        await self.connection.commit()
        msg=NS(id=1,chat=self.chat)
        await self.handlers['on_deleted_messages'](self.app,[msg])
        await self.handlers['on_deleted_messages'](self.app,[msg])
        rows=await (await self.connection.execute('SELECT event,payload,status FROM saver_outbox')).fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],'deleted:7:1')
        self.assertEqual(json.loads(rows[0][1])[3],'keep')
        self.assertEqual(rows[0][2],'queued')
        saver.get_or_create_topic.assert_not_awaited()

    async def test_ttl_off_really_skips_the_download_and_text_archive(self):
        self.config['save_ttl']=False
        message=NS(id=2,chat=self.chat,from_user=self.user,text=None,caption='private',photo=NS(ttl_seconds=2147483647),video=None,voice=None,video_note=None,document=None)
        with patch.object(saver,'cache_media',AsyncMock()) as download:
            await self.handlers['on_message'](self.app,message)
            await asyncio.gather(*list(saver.background_tasks),return_exceptions=True)
        download.assert_not_awaited()
        self.assertIsNone(await (await self.connection.execute('SELECT text FROM msg_cache WHERE message_id=2')).fetchone())

    async def test_enabled_ttl_still_archives_and_queues_once(self):
        message=NS(id=2,chat=self.chat,from_user=self.user,text=None,caption='keep ttl',photo=NS(ttl_seconds=2147483647),video=None,voice=None,video_note=None,document=None)
        with patch.object(saver,'cache_media',AsyncMock(return_value='')) as download:
            await self.handlers['on_message'](self.app,message)
            await asyncio.gather(*list(saver.background_tasks),return_exceptions=True)
        download.assert_awaited_once()
        row=await (await self.connection.execute('SELECT text,is_ttl FROM msg_cache WHERE message_id=2')).fetchone()
        self.assertEqual(row,('keep ttl',1))
        row=await (await self.connection.execute('SELECT event,status FROM saver_outbox')).fetchone()
        self.assertEqual(row,('ttl:7:2','queued'))

    async def test_long_caption_is_preserved_as_bounded_separate_messages(self):
        path=self.cache/'7_1_voice';path.write_bytes(b'voice')
        bot=NS(send_voice=AsyncMock(return_value=NS(message_id=1)),send_message=AsyncMock(return_value=NS(message_id=2)))
        self.assertTrue(await saver.execute_alert(bot,self.app,-100,7,1,'x'*9000,str(path),'voice'))
        self.assertNotIn('caption',bot.send_voice.await_args.kwargs)
        self.assertEqual(''.join(call.kwargs['text'] for call in bot.send_message.await_args_list),'x'*9000)
        self.assertTrue(all(len(call.kwargs['text'])<=3500 for call in bot.send_message.await_args_list))
        self.assertTrue(path.exists())

    async def test_gc_removing_cache_name_during_upload_does_not_break_open_copy(self):
        path=self.cache/'7_1_voice';path.write_bytes(b'voice')
        async def send(**kwargs):
            path.unlink()
            self.assertEqual(Path(kwargs['voice'].path).read_bytes(),b'voice')
            return NS(message_id=1)
        bot=NS(send_voice=send)
        self.assertTrue(await saver.execute_alert(bot,self.app,-100,7,1,'text',str(path),'voice'))
        self.assertEqual(list((self.cache.parent/'tmp').iterdir()),[])

    async def test_topic_lock_serializes_same_peer_without_blocking_other_peer(self):
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def create(app,bot,dump,user,obj):
            calls.append(user)
            if user==7 and len(calls)==1:entered.set();await release.wait()
            return user
        # Fixture mocks the public wrapper; call the real function retained here.
        from importlib import util
        # The lock itself is exercised with concurrent tasks, no Telegram calls.
        async def locked(user):
            async with saver.topic_lock((-100,user)):return await create(None,None,-100,user,None)
        one=asyncio.create_task(locked(7));await entered.wait()
        two=asyncio.create_task(locked(7));await asyncio.sleep(0)
        self.assertEqual(await locked(8),8)
        self.assertEqual(calls,[7,8])
        release.set();await asyncio.gather(one,two)
        self.assertFalse(saver.topic_locks)
