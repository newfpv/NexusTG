import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch

from aiogram.methods import SendDocument,SendMessage,EditMessageText
from core.telegram_guard import BotDeliveryGuard
from core.menu_middleware import menu_request,latest_requests
from modules import managed_bots as ui


class MenuCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_can_edit_during_upload_to_the_same_chat_and_other_background_work(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=BotDeliveryGuard(Path(folder)/'limits.json')
            upload_entered=asyncio.Event();text_entered=asyncio.Event();release=asyncio.Event()
            async def request(bot,method):
                if isinstance(method,SendDocument):upload_entered.set();await release.wait()
                if isinstance(method,SendMessage):text_entered.set();await release.wait()
                return True
            tasks=[asyncio.create_task(guard(request,None,SendDocument(chat_id=1,document='file-id')))]
            await upload_entered.wait()
            tasks.append(asyncio.create_task(guard(request,None,SendDocument(chat_id=-100,document='file-id'))))
            tasks.append(asyncio.create_task(guard(request,None,SendMessage(chat_id=2,text='background'))))
            await text_entered.wait()
            guard.peers[1][2]=0
            key=(1,4);latest_requests[key]='menu';token=menu_request.set({'key':key,'id':'menu'})
            try:
                result=await asyncio.wait_for(guard(request,None,EditMessageText(chat_id=1,message_id=4,text='menu')),1)
                self.assertTrue(result);self.assertFalse(tasks[0].done())
            finally:
                menu_request.reset(token);latest_requests.pop(key,None);release.set();await asyncio.gather(*tasks)

    async def test_group_cooldown_does_not_hold_global_scheduler_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            guard=BotDeliveryGuard(Path(folder)/'limits.json')
            guard.peers[-100]=[asyncio.Lock(),0,time.time()+60]
            waiting=asyncio.create_task(guard(AsyncMock(return_value=True),None,SendMessage(chat_id=-100,text='group')))
            await asyncio.sleep(.02)
            try:
                self.assertTrue(await asyncio.wait_for(guard(AsyncMock(return_value=True),None,EditMessageText(chat_id=1,message_id=4,text='menu')),1))
            finally:
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):await waiting

    async def test_my_bots_acknowledges_navigation_before_loading_statistics(self):
        call=NS(data='managed_stats_0',from_user=NS(id=7),message=NS(chat=NS(id=7),message_id=4),answer=AsyncMock())
        state=NS(update_data=AsyncMock(),set_state=AsyncMock())
        async def show(*args):call.answer.assert_awaited_once_with()
        with patch.object(ui,'allowed',AsyncMock(return_value=True)),patch.object(ui,'show_stats',AsyncMock(side_effect=show)) as render:
            await ui.callbacks(call,state)
        render.assert_awaited_once()

    async def test_slow_disk_statistics_do_not_stop_bot_list_rendering(self):
        async def offload(func,*args):
            if func is ui.service.status:return {'bots':[],'at':time.time()}
            await asyncio.Event().wait()
        with patch.object(ui,'_storage_job',None),patch.object(ui,'STORAGE_SUMMARY_TIMEOUT',.01), \
             patch.object(ui.asyncio,'to_thread',offload),patch.object(ui,'safe_edit',AsyncMock()) as render:
            try:
                await ui.show_stats(NS(),NS())
                render.assert_awaited_once()
                self.assertIn(ui._('managed_storage_delayed'),render.await_args.args[2])
            finally:
                ui._storage_job.cancel()
                with self.assertRaises(asyncio.CancelledError):await ui._storage_job
