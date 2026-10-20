import asyncio
import base64
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from pyrogram.raw.functions.messages import SendMessage, SetTyping, GetHistory
from pyrogram.raw.types import InputPeerUser, SendMessageTypingAction
from pyrogram.errors import FloodWait, PeerFlood
from core.telegram_guard import delivery_context, AccountGuard, TelegramPaused, TelegramBusy, BotDeliveryGuard
from core.session_lock import authorization_fingerprint, claim_session, release_session
from core.job_queue import JobQueue
from modules.always_online import heartbeat_range


class Clock:
    value=1000.0
    def now(self):return self.value
    async def sleep(self,seconds):
        self.value+=seconds
        await asyncio.sleep(0)


class GuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'limits.json'
        self.clock=Clock()
        self.guard=AccountGuard(self.path,'one',self.clock.now,self.clock.sleep)
        self.peer=InputPeerUser(user_id=7,access_hash=1)

    def query(self,text='hello'):
        return SendMessage(peer=self.peer,message=text,random_id=123)

    async def test_flood_retries_same_random_id_and_persists_account_pause(self):
        call=AsyncMock(side_effect=[FloodWait(120),True])
        query=self.query()
        self.assertTrue(await self.guard.invoke(call,query))
        self.assertEqual(call.await_count,2)
        self.assertIs(call.await_args_list[0].args[0],call.await_args_list[1].args[0])
        self.assertGreaterEqual(self.clock.value,1121)
        stored=json.loads(self.path.read_text())
        self.assertEqual(stored['until'],1121)
        self.assertNotIn('hello',self.path.read_text())
        self.assertEqual(list(self.guard.outbox.glob('*.json')),[])
        self.assertEqual(call.await_args.kwargs['retries'],1)
        self.assertEqual(call.await_args.kwargs['sleep_threshold'],0)

    async def test_other_module_and_restarted_guard_respect_deadline(self):
        self.guard.defer(180)
        restored=AccountGuard(self.path,'one',self.clock.now,self.clock.sleep)
        at=[]
        async def call(query,**kwargs):at.append(self.clock.value);return True
        await restored.invoke(call,self.query())
        self.assertGreaterEqual(at[0],1181)
        # A different authorization cannot inherit a previous login's pause.
        self.assertEqual(AccountGuard(self.path,'two').until,0)

    async def test_network_ambiguity_does_not_resend(self):
        call=AsyncMock(side_effect=OSError('unknown delivery outcome'))
        with self.assertRaises(OSError):await self.guard.invoke(call,self.query())
        self.assertEqual(call.await_count,1)
        jobs=list(self.guard.outbox.glob('*.json'));self.assertEqual(len(jobs),1)
        self.assertEqual(json.loads(jobs[0].read_text())['status'],'inflight')
        app=NS(invoke=AsyncMock())
        await self.guard.recover(app)
        app.invoke.assert_not_awaited()

    async def test_cancelled_cooldown_answer_is_recoverable_without_resending_inflight(self):
        waiting=asyncio.Event()
        async def sleep(seconds):waiting.set();await asyncio.Event().wait()
        self.guard.sleep=sleep
        self.guard.defer(300)
        token=delivery_context.set('voice-result')
        task=asyncio.create_task(self.guard.invoke(AsyncMock(),self.query('keep result')))
        delivery_context.reset(token)
        await waiting.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        jobs=list(self.guard.outbox.glob('*.json'))
        self.assertEqual(json.loads(jobs[0].read_text())['status'],'queued')
        app=NS(invoke=AsyncMock(return_value=True))
        await self.guard.recover(app)
        self.assertEqual(app.invoke.await_args.args[0].message,'keep result')
        self.assertEqual(list(self.guard.outbox.glob('*.json')),[])
        self.assertEqual(self.guard.pending,0)

    async def test_peer_flood_pauses_sibling_writes_until_owner_acknowledges(self):
        with self.assertRaises(PeerFlood):await self.guard.invoke(AsyncMock(side_effect=PeerFlood()),self.query())
        next_call=AsyncMock()
        with self.assertRaises(TelegramPaused):await self.guard.invoke(next_call,self.query('next'))
        next_call.assert_not_awaited()
        self.guard.defer(10);until=self.guard.until;self.guard.resume()
        self.assertEqual(self.guard.until,until)
        self.assertFalse(self.guard.paused)

    async def test_same_dialog_order_and_account_pacing(self):
        values=[]
        async def call(query,**kwargs):values.append((query.message,self.clock.now()));await asyncio.sleep(0);return True
        await asyncio.gather(*(self.guard.invoke(call,self.query(str(i))) for i in range(8)))
        self.assertEqual([v[0] for v in values],[str(i) for i in range(8)])
        self.assertTrue(all(b[1]-a[1]>=1.09 for a,b in zip(values,values[1:])))

    async def test_typing_duplicates_and_typing_during_flood_are_dropped(self):
        query=SetTyping(peer=self.peer,action=SendMessageTypingAction())
        call=AsyncMock(return_value=True)
        await self.guard.invoke(call,query);await self.guard.invoke(call,query)
        self.assertEqual(call.await_count,1)
        self.guard.defer(180)
        await self.guard.invoke(call,query)
        self.assertEqual(call.await_count,1)

    async def test_admission_cap_does_not_send_or_create_journal(self):
        self.guard.pending=128;call=AsyncMock()
        with self.assertRaises(TelegramBusy):await self.guard.invoke(call,self.query())
        call.assert_not_awaited()
        self.assertFalse(self.guard.outbox.exists())


class ResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_shopping_auto_events_update_one_list_and_reject_unlisted_senders(self):
        from modules import shop_list as shop
        handlers={}
        def on_message(flt,**kwargs):
            handlers['filter']=flt
            def decorate(fn):handlers['handler']=fn;return fn
            return decorate
        app=NS(on_message=on_message)
        shop.register_userbot(app)
        cfg=dict(active=True,command='.shop',prompt='',auto_chats='7',allowed_senders='8',allow_others=True,delete_orig=False)
        message=lambda mid,text:NS(id=mid,chat=NS(id=7,type=shop.enums.ChatType.PRIVATE),from_user=NS(id=7,is_self=False,is_bot=False),text=text,caption=None,reply_to_message=None,message_thread_id=None,checklist=None,via_bot=None)
        stored=NS(id=100,chat=NS(id=7),text=None,caption=None,checklist=NS(title=shop._('checklist_title'),tasks=[NS(text='milk')]),delete=AsyncMock())
        client=NS(send_checklist=AsyncMock(return_value=stored),edit_message_checklist=AsyncMock(return_value=stored))
        with patch.object(shop,'_get_cfg',AsyncMock(return_value=cfg)),patch.object(shop,'generate_ai_response',AsyncMock(side_effect=['["milk"]','["milk","bread"]'])),patch.object(shop,'simulate_typing',AsyncMock()):
            self.assertFalse(await handlers['filter'](client,message(1,'milk')))
            cfg['allowed_senders']='7'
            self.assertTrue(await handlers['filter'](client,message(1,'milk')))
            await handlers['handler'](client,message(1,'milk'))
            await handlers['handler'](client,message(2,'bread'))
        self.assertEqual(client.send_checklist.await_count,1)
        self.assertEqual(client.edit_message_checklist.await_count,1)
        stored.delete.assert_not_awaited()

    async def test_batch_cleanup_resumes_only_unacknowledged_originals(self):
        from core.durable_cleanup import delete_batch,recover
        with tempfile.TemporaryDirectory() as temp:
            messages=[NS(chat=NS(id=7),id=i,delete=AsyncMock(return_value=True)) for i in (1,2,3)]
            messages[1].delete.side_effect=OSError('disconnected')
            with self.assertRaises(OSError):await delete_batch(messages,temp)
            pending=json.loads(next(Path(temp).glob('*.json')).read_text())
            self.assertEqual(pending,[[7,2],[7,3]])
            app=NS(is_initialized=True,delete_messages=AsyncMock(return_value=1))
            await recover(app,temp)
            self.assertEqual([call.args for call in app.delete_messages.await_args_list],[(7,[2]),(7,[3])])
            self.assertEqual(list(Path(temp).iterdir()),[])

    async def test_model_stream_finishes_while_preview_delivery_is_blocked(self):
        from modules.ai_command import _consume_stream
        import time
        preview_started=asyncio.Event();generated=asyncio.Event();release=asyncio.Event()
        async def edit(*args,**kwargs):
            preview_started.set()
            await release.wait()
        status=NS(edit=edit,chat=NS(id=7),id=1)
        async def chunks():
            yield 'first '
            await preview_started.wait()
            yield 'last'
            generated.set()
        task=asyncio.create_task(_consume_stream(chunks(),status,'',None,time.time(),30))
        await asyncio.wait_for(generated.wait(),timeout=4)
        self.assertFalse(task.done())
        release.set()
        self.assertEqual(await task,'first last')

    async def test_queue_deduplicates_active_and_completed_events(self):
        queue=JobQueue(capacity=2)
        started=asyncio.Event();release=asyncio.Event();calls=[]
        async def work():calls.append(1);started.set();await release.wait()
        self.assertTrue(await queue.submit('a',work));await started.wait()
        self.assertFalse(await queue.submit('a',work))
        release.set();await queue.worker
        self.assertFalse(await queue.submit('a',work));self.assertEqual(calls,[1])

    async def test_bot_retry_after_does_not_block_callback_ack(self):
        from aiogram.methods import SendMessage as BotSend, AnswerCallbackQuery
        from aiogram.exceptions import TelegramRetryAfter
        with tempfile.TemporaryDirectory() as temp:
            guard=BotDeliveryGuard(Path(temp)/'bot.json')
            gate=asyncio.Event();entered=asyncio.Event()
            async def wait(*args,**kwargs):entered.set();await gate.wait()
            guard.guard.wait=wait
            method=BotSend(chat_id=1,text='private')
            call=AsyncMock(return_value=True)
            task=asyncio.create_task(guard(call,NS(),method));await entered.wait()
            ack=AnswerCallbackQuery(callback_query_id='x')
            self.assertTrue(await guard(call,NS(),ack))
            self.assertIs(call.await_args.args[1],ack)
            gate.set();await task

    async def test_bot_429_retries_only_after_shared_deadline(self):
        from aiogram.methods import SendMessage as BotSend
        from aiogram.exceptions import TelegramRetryAfter
        with tempfile.TemporaryDirectory() as temp:
            guard=BotDeliveryGuard(Path(temp)/'bot.json')
            clock=Clock();guard.guard.now=clock.now;guard.guard.sleep=clock.sleep
            method=BotSend(chat_id=1,text='private')
            call=AsyncMock(side_effect=[TelegramRetryAfter(method=method,message='wait',retry_after=120),True])
            with patch('core.telegram_guard.time.time',clock.now):
                self.assertTrue(await guard(call,NS(),method))
            self.assertGreaterEqual(clock.value,1121)
            self.assertEqual(call.await_count,2)
            self.assertEqual(json.loads((Path(temp)/'bot.json').read_text())['until'],1121)

    async def test_slow_archive_upload_does_not_hold_another_chats_menu(self):
        from aiogram.methods import SendDocument,EditMessageText
        with tempfile.TemporaryDirectory() as temp:
            guard=BotDeliveryGuard(Path(temp)/'bot.json')
            started=asyncio.Event();release=asyncio.Event()
            async def request(bot,method):
                if type(method).__name__=='SendDocument':started.set();await release.wait()
                return True
            upload=asyncio.create_task(guard(request,NS(),SendDocument(chat_id=-100,document='file-id')))
            await started.wait()
            self.assertTrue(await asyncio.wait_for(guard(request,NS(),EditMessageText(chat_id=1,message_id=2,text='menu')),timeout=1))
            release.set();await upload


class PureTests(unittest.TestCase):
    def test_presence_jitter_never_exceeds_recommendation_or_observed_expiry(self):
        self.assertEqual(heartbeat_range(210,300),(60,90))
        low,high=heartbeat_range(60,50)
        self.assertLessEqual(high,40)
        self.assertGreater(low,0);self.assertLess(low,high)

    def test_same_auth_key_has_same_fingerprint_even_if_session_metadata_differs(self):
        def session(api_id):return base64.urlsafe_b64encode(struct.pack('>BI?256sQ?',2,api_id,False,b'x'*256,123,False)).decode().rstrip('=')
        self.assertEqual(authorization_fingerprint(session(1)),authorization_fingerprint(session(2)))
        with self.assertRaises(ValueError):authorization_fingerprint('bad')

    def test_duplicate_authorization_lock_is_released_after_stop(self):
        if __import__('os').name=='nt':self.skipTest('Linux worker locking')
        session=base64.urlsafe_b64encode(struct.pack('>BI?256sQ?',2,1,False,b'x'*256,123,False)).decode().rstrip('=')
        with tempfile.TemporaryDirectory() as temp, patch.dict('os.environ',{'NEXUS_RESOURCE_DIR':temp}):
            one,two=NS(),NS();claim_session(one,session)
            with self.assertRaises(RuntimeError):claim_session(two,session)
            release_session(one);claim_session(two,session);release_session(two)
