import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

from pyrogram.errors import MessageIdInvalid,EntityBoundsInvalid
from modules import text_struct as struct


class StructDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.saved=Path(self.temp.name)/'result.json'
        def retain(key,text):self.saved.write_text(text,encoding='utf-8');return self.saved
        p=patch.object(struct,'retain_result',side_effect=retain);p.start();self.addCleanup(p.stop)
        self.message=NS(id=7,chat=NS(id=42),media=None,message_thread_id=None,
            edit_text=AsyncMock(return_value=True),delete=AsyncMock())
        self.client=NS(send_message=AsyncMock(return_value=NS(id=8)))

    async def test_source_error_does_not_append_a_second_generic_error(self):
        self.assertFalse(await struct._apply_edit(self.message,'',None,client=self.client,error_text='DOWNLOAD_FAILED'))
        self.assertEqual(self.message.edit_text.await_args.args[0],'DOWNLOAD_FAILED')
        self.message.delete.assert_not_awaited();self.client.send_message.assert_not_awaited()

    async def test_missing_target_sends_result_without_replying_to_missing_message(self):
        self.message.edit_text.side_effect=MessageIdInvalid()
        self.assertTrue(await struct._apply_edit(self.message,'old','**corrected**',client=self.client))
        self.client.send_message.assert_awaited_once()
        args=self.client.send_message.await_args.kwargs
        self.assertEqual(args['text'],'**corrected**')
        self.assertNotIn('reply_parameters',args)
        self.message.edit_text.assert_awaited_once()
        self.message.delete.assert_not_awaited()
        self.assertFalse(self.saved.exists())

    async def test_ambiguous_edit_does_not_send_duplicate_and_keeps_result(self):
        self.message.edit_text.side_effect=OSError('connection dropped')
        with self.assertRaises(OSError):
            await struct._apply_edit(self.message,'old','complete result',client=self.client)
        self.client.send_message.assert_not_awaited()
        self.assertEqual(self.saved.read_text(),'complete result')

    async def test_long_unicode_result_is_split_without_losing_any_text(self):
        answer='😀'*5000+'end'
        self.assertTrue(await struct._apply_edit(self.message,'old',answer,client=self.client))
        parts=[self.message.edit_text.await_args.args[0]]+[call.kwargs['text'] for call in self.client.send_message.await_args_list]
        self.assertEqual(''.join(parts),answer)
        self.assertTrue(all(len(part.encode('utf-16-le'))//2<=3600 for part in parts))

    async def test_html_rejection_falls_back_to_plain_edit_without_extra_message(self):
        self.message.edit_text.side_effect=[EntityBoundsInvalid(),True]
        self.assertTrue(await struct._apply_edit(self.message,'old','**corrected**',client=self.client))
        self.client.send_message.assert_not_awaited()
        self.assertEqual(self.message.edit_text.await_args.kwargs['parse_mode'],struct.enums.ParseMode.DISABLED)

    async def test_partial_delivery_failure_retains_full_result_and_never_deletes_source(self):
        self.message.edit_text.side_effect=MessageIdInvalid()
        self.client.send_message.side_effect=[NS(id=8),OSError('interrupted')]
        answer='x'*8000
        with self.assertRaises(OSError):await struct._apply_edit(self.message,'old',answer,client=self.client)
        self.assertEqual(self.saved.read_text(),answer)
        self.message.delete.assert_not_awaited()

    async def test_generation_timeout_cancels_wait_and_leaves_no_permanent_dialog_lock(self):
        from core.job_queue import serial_events
        cancelled=asyncio.Event()
        calls=0
        async def generate(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==1:
                try:await asyncio.Event().wait()
                finally:cancelled.set()
            return 'second result'
        @serial_events
        async def command(client,message):return await struct._generate_struct_response('source')
        with patch.object(struct,'generate_ai_response',generate),patch.object(struct,'STRUCT_GENERATION_TIMEOUT',.01):
            with self.assertRaises(asyncio.TimeoutError):await command(None,NS(id=1,chat=NS(id=42)))
            self.assertTrue(cancelled.is_set())
            self.assertEqual(await command(None,NS(id=2,chat=NS(id=42))),'second result')
