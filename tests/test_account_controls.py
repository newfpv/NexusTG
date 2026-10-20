import ast
import asyncio
import json
import logging
import os
from pathlib import Path
import string
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from core import archive_storage as archive, diagnostics, managed_accounts as service, db
from core.menu import ChatDirectory
from modules import managed_bots as ui, setup_wizard as wizard, message_saver as saver


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=self.root/'spy_cache';self.cache.mkdir()

    def file(self,name,size,stamp):
        p=self.cache/name;p.write_bytes(b'x'*size);os.utime(p,(stamp,stamp));return p

    def test_heavy_media_evicted_before_old_voice_and_round_video(self):
        voice=self.file('1_1_voice.ogg',1024,1)
        circle=self.file('1_2_video_note',2048,2)
        heavy=self.file('1_3_document',2*1024**2,30)
        photo=self.file('1_4_photo',512,40)
        result=archive.prune_media(self.cache,4096,free_floor=0)
        self.assertFalse(heavy.exists());self.assertTrue(voice.exists());self.assertTrue(circle.exists());self.assertTrue(photo.exists())
        self.assertLessEqual(result['media_bytes'],4096)

    def test_oldest_in_same_priority_goes_first_and_text_is_never_touched(self):
        a=self.file('1_1_document',1000,1);b=self.file('1_2_photo',1000,2)
        text=self.root/'saver_cache.sqlite';text.write_text('important text')
        archive.prune_media(self.cache,1000,free_floor=0)
        self.assertFalse(a.exists());self.assertTrue(b.exists());self.assertEqual(text.read_text(),'important text')

    def test_voice_only_quota_still_has_a_hard_ceiling(self):
        old=self.file('1_1_voice',1000,1);new=self.file('1_2_voice',1000,2)
        archive.prune_media(self.cache,1000,free_floor=0)
        self.assertFalse(old.exists());self.assertTrue(new.exists())

    def test_forwarded_posts_are_removed_before_older_original_media(self):
        original=self.file('1_1_document',1000,1)
        forwarded=self.file('1_2_fwd_photo',1000,20)
        archive.prune_media(self.cache,1000,free_floor=0)
        self.assertTrue(original.exists());self.assertFalse(forwarded.exists())
        legacy=self.file('1_3_photo',1000,30)
        archive.prune_media(self.cache,1000,free_floor=0,forwarded=[legacy])
        self.assertTrue(original.exists());self.assertFalse(legacy.exists())

    def test_no_following_links_and_usage_separates_text(self):
        outside=self.root/'outside';outside.write_bytes(b'secret text')
        (self.cache/'link').symlink_to(outside)
        self.file('1_1_video',100,1)
        archive.prune_media(self.cache,0,free_floor=0)
        self.assertEqual(outside.read_bytes(),b'secret text')
        out=self.root/'usage.json'
        usage=archive.publish_usage(self.cache,outside,100,out)
        self.assertEqual(usage['media_bytes'],0);self.assertGreater(usage['text_db_bytes'],0)
        self.assertNotIn('secret',out.read_text())

    def test_abandoned_staging_cleanup_is_confined_to_saver_folders(self):
        parent=self.root/'tmp';parent.mkdir()
        old=parent/'saver_old';old.mkdir();(old/'media').write_bytes(b'partial')
        unrelated=parent/'other_module';unrelated.mkdir()
        outside=self.root/'outside';outside.mkdir();(outside/'text').write_text('keep')
        (parent/'saver_link').symlink_to(outside,target_is_directory=True)
        archive.cleanup_staging(self.cache)
        self.assertFalse(old.exists());self.assertTrue(unrelated.exists());self.assertEqual((outside/'text').read_text(),'keep')

    def test_legacy_path_only_resolves_matching_media_inside_own_cache(self):
        own=self.file('7_10_voice',100,1)
        value,reset=archive.repair_media_path(self.cache,'/app/data/spy_cache/7_10_voice',7,10)
        self.assertEqual(Path(value),own);self.assertTrue(reset)
        self.assertEqual(archive.repair_media_path(self.cache,'/app/data/spy_cache/7_10_voice',8,10)[0],'')


class DiagnosticTests(unittest.TestCase):
    def test_messages_tokens_keys_paths_and_traceback_contents_are_not_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'data/logs/diagnostics.jsonl'
            handler=diagnostics.SafeDiagnostics(path)
            self.addCleanup(handler.close)
            secret='123456:BOT_TOKEN AIzaPrivateKey +375291111111 user@example.com /home/private.session private chat text'
            record=logging.LogRecord('core','ERROR' if False else logging.ERROR,'/app/modules/message_saver.py',77,'FLOOD_WAIT '+secret,(),None,'handle_deleted')
            handler.emit(record)
            raw=path.read_text()
            for value in secret.split():self.assertNotIn(value,raw)
            rows=diagnostics.recent(root)
            self.assertEqual(rows[0]['error'],'FLOOD_WAIT');self.assertEqual(rows[0]['module'],'message_saver')
            self.assertIn(':77',diagnostics.format_rows(rows))
            # A raw or malformed record accidentally appended to a log is discarded.
            with path.open('a') as f:f.write(json.dumps({'level':'ERROR','at':secret,'message':secret})+'\n')
            self.assertEqual(len(diagnostics.recent(root)),1)


class DirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.directory=ChatDirectory(Path(self.tmp.name)/'dialogs.json')
        self.calls=[]
        async def dialogs(**kwargs):
            self.calls.append(kwargs)
            if not kwargs['from_archive']:
                for i in range(45):yield NS(chat=NS(id=1000+i,type=NS(name='SUPERGROUP')))
                for i in range(27):yield NS(chat=NS(id=i+1,type=NS(name='PRIVATE'),first_name=f'Person {i}',last_name=None,is_bot=False))
            else:
                for i in range(5):yield NS(chat=NS(id=101+i,type=NS(name='PRIVATE'),first_name=f'Archive {i}',last_name=None,is_bot=False))
        self.client=NS(is_connected=True,get_dialogs=dialogs)

    async def test_filtering_does_not_truncate_and_pages_include_archive(self):
        first=await self.directory.get_dialogs(self.client,page=0)
        self.assertEqual(len(first),20);self.assertTrue(self.directory.has_more)
        all_rows=await self.directory.get_dialogs(self.client,page=3)
        self.assertEqual(len(all_rows),32);self.assertFalse(self.directory.has_more)
        self.assertTrue(any(name=='Archive 4' for _,name in all_rows))
        self.assertEqual([c['limit'] for c in self.calls],[0,0])
        cached=ChatDirectory(self.directory.path)
        self.assertEqual(len(cached.dialogs),32)

    async def test_reconnect_retains_cache_and_manual_refresh_respects_backoff(self):
        await self.directory.get_dialogs(self.client)
        old=list(self.directory.dialogs)
        async def failing(**kwargs):
            raise TimeoutError('network unavailable')
            yield
        bad=NS(is_connected=True,get_dialogs=failing)
        self.assertEqual(await self.directory.get_dialogs(bad,force=True),old)
        calls=MagicMock(side_effect=failing);bad.get_dialogs=calls
        await self.directory.get_dialogs(bad,force=True)
        calls.assert_not_called()

    async def test_menu_has_ten_single_column_chats_and_arrows(self):
        tree=ast.parse(Path('/app/main.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='cb_chats_list')
        node.decorator_list=[]
        edit=AsyncMock()
        scope={'types':types,'FSMContext':FSMContext,'_':ui._,'InlineKeyboardMarkup':types.InlineKeyboardMarkup,
               'InlineKeyboardButton':types.InlineKeyboardButton,'chat_directory':self.directory,'userbot_app':self.client,'safe_edit':edit}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'menu','exec'),scope)
        state=NS(update_data=AsyncMock());call=NS(data='chats_list',message=NS(message_id=10))
        await scope['cb_chats_list'](call,state)
        kb=edit.await_args.args[3]
        chats=[row for row in kb.inline_keyboard if any((b.callback_data or '').startswith('chat_') for b in row)]
        self.assertEqual(len(chats),10);self.assertTrue(all(len(row)==1 for row in chats))
        self.assertTrue(any(b.callback_data=='chats_page_1' for row in kb.inline_keyboard for b in row))

    async def test_slow_rpc_is_kept_across_pages_without_restart(self):
        gate=asyncio.Event();calls=[]
        async def slow(**kwargs):
            calls.append(kwargs)
            await gate.wait()
            yield NS(chat=NS(id=1,type=NS(name='PRIVATE'),first_name='Person',last_name=None,is_bot=False))
        client=NS(is_connected=True,get_dialogs=slow)
        self.directory.timeout=.01
        self.assertEqual(await self.directory.get_dialogs(client),[])
        self.assertIsNotNone(self.directory.pending)
        gate.set()
        rows=await self.directory.get_dialogs(client,force=True)
        self.assertTrue(rows)
        self.assertEqual(len([c for c in calls if not c['from_archive']]),1)


class FirstOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_one_concurrent_start_claims_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=create_async_engine('sqlite+aiosqlite:///'+directory+'/db.sqlite',pool_size=1,max_overflow=0)
            try:
                async with engine.begin() as connection:await connection.run_sync(db.Base.metadata.create_all)
                sessions=async_sessionmaker(engine,expire_on_commit=False)
                with patch.object(db,'AsyncSessionLocal',sessions):
                    claims=await asyncio.gather(db.claim_owner(7),db.claim_owner(8))
                    self.assertEqual(sum(claims),1)
                    async with sessions() as session:
                        cfg=await session.get(db.GlobalConfig,1)
                        self.assertEqual(cfg.admin_id,7 if claims[0] else 8)
                    self.assertFalse(await db.claim_owner(9))
            finally:await engine.dispose()

    async def test_managed_owner_survives_reset_and_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);profile=root/'child';profile.mkdir()
            (profile/'account.json').write_text('{"enabled":true}')
            engine=create_async_engine('sqlite+aiosqlite:///'+str(root/'core.sqlite'))
            try:
                async with engine.begin() as connection:await connection.run_sync(db.Base.metadata.create_all)
                sessions=async_sessionmaker(engine,expire_on_commit=False)
                with patch.object(db,'AsyncSessionLocal',sessions),patch.dict(os.environ,{'NEXUS_PROFILE_DIR':str(profile),'NEXUS_ACCOUNTS_DIR':str(root),'NEXUS_ACCOUNT_NAME':'child','NEXUS_PROFILE_OWNER_ID':''}):
                    self.assertTrue(await db.claim_owner(7))
                    self.assertEqual(service.read_json(profile/'account.json')['owner_id'],7)
                    async with sessions() as session:await db.CoreRepository(session).full_reset()
                    self.assertFalse(await db.claim_owner(8))
                    self.assertTrue(await db.claim_owner(7))
                    from core.environment import bind_claimed_owner
                    with self.assertRaises(RuntimeError):bind_claimed_owner(8)
                    self.assertEqual(service.read_json(profile/'account.json')['owner_id'],7)
                    # A pre-existing database imported from an older version is
                    # pinned on its owner's first interaction as well.
                    (profile/'account.json').write_text('{"enabled":true}')
                    os.environ['NEXUS_PROFILE_OWNER_ID']=''
                    from core.owner_middleware import OwnerMiddleware
                    from datetime import datetime,timezone
                    message=types.Message(message_id=1,date=datetime.now(timezone.utc),chat=types.Chat(id=7,type='private'),from_user=types.User(id=7,is_bot=False,first_name='Owner'),text='/start')
                    handler=AsyncMock()
                    with patch.object(ui.plugins.db,'get_global_config',AsyncMock(return_value=NS(admin_id=7))):
                        await OwnerMiddleware()(handler,message,{})
                    handler.assert_awaited_once()
                    self.assertEqual(service.read_json(profile/'account.json')['owner_id'],7)
            finally:await engine.dispose()


class ManagedUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'manager.json').write_text('{"profile":"main"}')
        for name in ('main','second'):
            p=self.root/name;p.mkdir();(p/'data').mkdir()
            (p/'account.json').write_text(json.dumps({'enabled':True,'bot_username':name+'_bot'}))
            (p/'worker.json').write_text(json.dumps({'pid':123,'ready':True,'account_name':'Account '+name}))
        (self.root/'runtime.json').write_text('{"mode":"shared"}')
        (self.root/'status.json').write_text(json.dumps(service.public_status(self.root,{'main':{'pid':123},'second':{'pid':123}})))
        env=patch.dict(os.environ,{'NEXUS_ACCOUNTS_DIR':str(self.root),'NEXUS_ACCOUNT_NAME':'main','NEXUS_ACCOUNT_ROLE':'manager'})
        env.start();self.addCleanup(env.stop)
        p=patch.object(ui.plugins.db,'get_global_config',AsyncMock(return_value=NS(admin_id=7)));p.start();self.addCleanup(p.stop)
        self.storage=MemoryStorage();self.addAsyncCleanup(self.storage.close)
        self.state=FSMContext(self.storage,StorageKey(bot_id=111,chat_id=7,user_id=7))
        self.message=NS(chat=NS(id=7),from_user=NS(id=7),message_id=10,text='Renamed',delete=AsyncMock())
        p=patch.object(ui,'safe_edit',AsyncMock());self.edit=p.start();self.addCleanup(p.stop)

    async def test_names_rename_quota_and_no_open_button(self):
        await ui.show_stats(self.message,self.state)
        kb=self.edit.await_args.args[3]
        self.assertTrue(any('Account second' in b.text for row in kb.inline_keyboard for b in row))
        await self.state.update_data(managed_profile='second')
        await ui.receive_name(self.message,self.state)
        self.assertEqual(service.status()['bots'][1]['display_name'],'Renamed')
        self.message.text='0.5';await ui.receive_quota(self.message,self.state)
        self.assertEqual(service.storage_info('second')['limit_bytes'],archive.GIB//2)
        await ui.show_bot(self.message,self.state,'main')
        callbacks={b.callback_data for row in self.edit.await_args.args[3].inline_keyboard for b in row}
        self.assertIn('managed_quota_main',callbacks);self.assertIn('managed_logs_main',callbacks)
        self.assertFalse(any(b.url for row in self.edit.await_args.args[3].inline_keyboard for b in row))

    async def test_management_button_only_in_core_settings(self):
        self.assertFalse(hasattr(ui,'get_settings_buttons'))
        self.assertEqual((await ui.get_core_settings_buttons())[0][0].callback_data,'managed_stats_0')
        tree=ast.parse(Path('/app/main.py').read_text())
        functions={n.name:n for n in tree.body if isinstance(n,ast.AsyncFunctionDef)}
        self.assertIn('core_settings_buttons',ast.unparse(functions['generate_settings_menu_content']))
        self.assertNotIn('core_settings_buttons',ast.unparse(functions['generate_main_menu_content']))

    async def test_quota_rejects_nan_negative_and_does_not_restart_worker(self):
        await self.state.update_data(managed_profile='second')
        for value in ('NaN','-1','Infinity','1025','nope'):
            self.message.text=value;await ui.receive_quota(self.message,self.state)
            self.assertEqual(service.storage_info('second')['limit_bytes'],archive.DEFAULT_LIMIT)
        self.assertNotIn('generation',service.read_json(self.root/'second/account.json'))


class SaverRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import aiosqlite
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cache=self.root/'spy_cache';self.cache.mkdir()
        self.connection=await aiosqlite.connect(self.root/'saver.sqlite');self.addAsyncCleanup(self.connection.close)
        await self.connection.executescript('''CREATE TABLE msg_cache (message_id INTEGER,chat_id INTEGER,user_id INTEGER,user_name TEXT,text TEXT,media_type TEXT,file_path TEXT,is_ttl INTEGER DEFAULT 0,timestamp TEXT DEFAULT CURRENT_TIMESTAMP,is_deleted INTEGER DEFAULT 0,is_forwarded INTEGER,PRIMARY KEY(message_id,chat_id));
        CREATE TABLE msg_versions (id INTEGER PRIMARY KEY,message_id INTEGER,chat_id INTEGER,text TEXT,timestamp TEXT DEFAULT CURRENT_TIMESTAMP);''')
        self.app=NS()
        self.handlers={}
        for kind in ('on_message','on_deleted_messages','on_edited_message'):
            def factory(kind):
                def register(*args,**kwargs):
                    def decorate(function):self.handlers[kind]=function;return function
                    return decorate
                return register
            setattr(self.app,kind,factory(kind))
        self.config={'is_active':True,'save_deleted':True,'save_edited':True,'save_ttl':True,'dump_chat_id':'-100','blacklist':'','target_chats':'','limit_reg':20,'limit_ttl':50}
        for name,value in [('db_conn',self.connection),('db_lock',asyncio.Lock()),('cache_lock',asyncio.Lock()),('CACHE_DIR',str(self.cache)),('_get_cfg',AsyncMock(return_value=self.config)),('get_or_create_topic',AsyncMock(return_value=1)),('alert_queue',asyncio.Queue())]:
            p=patch.object(saver,name,value);p.start();self.addCleanup(p.stop)
        saver.register_userbot(self.app,NS())
        # Stop the network alert worker. The test inspects queued records instead.
        for task in list(saver.background_tasks):task.cancel()
        await asyncio.gather(*list(saver.background_tasks),return_exceptions=True)
        self.user=NS(id=7,first_name='User',is_bot=False,is_self=False)
        self.chat=NS(id=7,type=saver.ChatType.PRIVATE)

    async def test_deleted_text_stays_and_old_edited_text_is_versioned(self):
        await self.connection.execute("INSERT INTO msg_cache (message_id,chat_id,user_id,text,media_type,file_path,timestamp) VALUES (1,7,7,'old text',NULL,'','2000-01-01')")
        await self.connection.commit()
        message=NS(id=1,chat=self.chat,from_user=self.user,text='new text',caption=None)
        await self.handlers['on_edited_message'](self.app,message)
        await self.handlers['on_deleted_messages'](self.app,[message])
        row=await (await self.connection.execute('SELECT text,is_deleted FROM msg_cache')).fetchone()
        self.assertEqual(row,('new text',1))
        version=await (await self.connection.execute('SELECT text FROM msg_versions')).fetchone()
        self.assertEqual(version,('old text',))

    async def test_failed_forward_keeps_local_media(self):
        path=self.cache/'1_1_voice';path.write_bytes(b'voice')
        bot=NS(send_voice=AsyncMock(side_effect=OSError('network unavailable')))
        await saver.execute_alert(bot,self.app,-100,7,1,'text',str(path),'voice',True)
        self.assertTrue(path.exists())

    async def test_new_text_is_committed_even_when_download_fails(self):
        message=NS(id=2,chat=self.chat,from_user=self.user,text='keep this',caption=None,photo=None,video=None,voice=NS(file_size=100),video_note=None,document=None)
        with patch.object(saver,'cache_media',AsyncMock(side_effect=OSError('failed'))):
            await self.handlers['on_message'](self.app,message)
            tasks=list(saver.background_tasks)
            await asyncio.gather(*tasks,return_exceptions=True)
        row=await (await self.connection.execute('SELECT text FROM msg_cache WHERE message_id=2')).fetchone()
        self.assertEqual(row,('keep this',))

    async def test_low_priority_incoming_post_does_not_evict_original_voice(self):
        voice=self.cache/'1_1_voice';voice.write_bytes(b'x'*2000)
        message=NS(id=3,chat=self.chat,forward_date=123)
        async def download(file_name,progress):
            p=Path(file_name);p.write_bytes(b'x'*500);return str(p)
        message.download=download
        with patch.object(saver,'account_limit',return_value=2000):
            path=await saver.cache_media(message,'photo',NS(file_size=500),self.config,False)
        self.assertEqual(path,'');self.assertTrue(voice.exists())
        self.assertEqual(sum(p.stat().st_size for p in self.cache.iterdir()),2000)

    async def test_legacy_forwarding_metadata_is_recovered_without_downloading(self):
        media=self.cache/'7_10_photo';media.write_bytes(b'post')
        await self.connection.execute('INSERT INTO msg_cache (message_id,chat_id,user_id,text,file_path) VALUES (10,7,7,?,?)',('caption',str(media)))
        await self.connection.commit()
        self.app.is_initialized=self.app.is_connected=True
        self.app.get_messages=AsyncMock(return_value=[NS(id=10,empty=False,forward_date=123)])
        with patch.object(saver.asyncio,'sleep',AsyncMock()):
            await saver.classify_old_media(self.app)
        self.app.get_messages.assert_awaited_once_with(7,[10])
        row=await (await self.connection.execute('SELECT is_forwarded,text FROM msg_cache WHERE message_id=10')).fetchone()
        self.assertEqual(row,(1,'caption'));self.assertTrue(media.exists())

    async def test_legacy_path_repair_keeps_text_and_retries_previously_missed_metadata(self):
        media=self.cache/'7_10_voice';media.write_bytes(b'voice')
        await self.connection.execute('INSERT INTO msg_cache (message_id,chat_id,user_id,text,file_path,is_forwarded) VALUES (10,7,7,?,?, -1)',('keep','/app/data/spy_cache/7_10_voice'))
        await self.connection.execute('INSERT INTO msg_cache (message_id,chat_id,user_id,text,file_path) VALUES (11,7,7,?,?)',('missing file text','/app/data/spy_cache/7_11_photo'))
        await self.connection.commit()
        await saver.repair_cache_paths()
        rows=await (await self.connection.execute('SELECT text,file_path,is_forwarded FROM msg_cache ORDER BY message_id')).fetchall()
        self.assertEqual(rows[0],('keep',str(media),None))
        self.assertEqual(rows[1],('missing file text','',None));self.assertTrue(media.exists())


class LocaleAndWizardTests(unittest.TestCase):
    def test_full_english_coverage_and_placeholders(self):
        def flatten(node):
            result={}
            for key,value in node.items():
                if isinstance(value,dict):result.update(flatten(value))
                else:
                    self.assertNotIn(key,result);result[key]=value
            return result
        ru=flatten(json.loads(Path('/app/language_RU.json').read_text()))
        en=flatten(json.loads(Path('/app/language_EN.json').read_text()))
        self.assertEqual(set(ru),set(en))
        formatter=string.Formatter()
        for key in ru:
            self.assertEqual({p[1] for p in formatter.parse(ru[key])},{p[1] for p in formatter.parse(en[key])},key)
            self.assertNotRegex(en[key],'[А-Яа-яЁё]',key)
        self.assertEqual(len(wizard.STEPS),22)
        self.assertFalse({'keys','models','timezone'} & {key for key,_ in wizard.STEPS})


if __name__=='__main__':unittest.main()
