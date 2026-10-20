import json,os,stat,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from core import managed_accounts as service,db,environment
from modules import managed_bots as ui

TOKEN='12345:'+'private-token-'*3

class ManagedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'manager.json').write_text(json.dumps({'profile':'main'}))
        self.profile('main',7,111)
        p=patch.dict(os.environ,{'NEXUS_ACCOUNT_ROLE':'manager','NEXUS_ACCOUNT_NAME':'main','NEXUS_ACCOUNTS_DIR':str(self.root),'NEXUS_PROFILE_OWNER_ID':'7'})
        p.start();self.addCleanup(p.stop)
        p=patch.object(ui.plugins.db,'get_global_config',AsyncMock(return_value=NS(admin_id=7)));p.start();self.addCleanup(p.stop)
        self.storage=MemoryStorage();self.addAsyncCleanup(self.storage.close)
        self.state=FSMContext(storage=self.storage,key=StorageKey(bot_id=111,chat_id=7,user_id=7))
        self.message=NS(chat=NS(id=7),from_user=NS(id=7),message_id=10,text=TOKEN,document=None,delete=AsyncMock())
        self.call=NS(from_user=NS(id=7),message=self.message,data='managed_add',answer=AsyncMock())
        p=patch.object(ui,'safe_edit',AsyncMock());self.edit=p.start();self.addCleanup(p.stop)

    def profile(self,name,owner,bot):
        p=self.root/name;p.mkdir();(p/'data').mkdir()
        (p/'.env').write_text(f'TG_BOT_TOKEN={bot}:secret\n')
        (p/'account.json').write_text(json.dumps({'enabled':True,'generation':0,'owner_id':owner,'phone':'secret-phone','api_keys':'secret-ai-key','global_prompt':'secret-prompt'}))
        return p

    async def test_management_only_primary_and_only_its_owner(self):
        self.assertTrue(await ui.get_core_settings_buttons())
        self.assertTrue(await ui.allowed(7,7))
        self.assertFalse(await ui.allowed(8,8));self.assertFalse(await ui.allowed(7,8))
        with patch.dict(os.environ,{'NEXUS_ACCOUNT_ROLE':'worker','NEXUS_ACCOUNT_NAME':'second'}):
            self.assertFalse(await ui.get_core_settings_buttons())
            self.call.data='managed_disable_main'
            await ui.callbacks(self.call,self.state)
            self.edit.assert_not_awaited()
            with self.assertRaises(PermissionError):service.create_account(TOKEN,12345,'other_bot',8)

    async def test_add_leaves_owner_for_first_start_and_copies_no_parent_data(self):
        await ui.callbacks(self.call,self.state)
        with patch.object(ui,'validate_token',AsyncMock(return_value=NS(id=12345,username='other_bot'))):
            await ui.receive_token(self.message,self.state)
        self.message.delete.assert_awaited_once()
        p=self.root/'bot_12345';cfg=json.loads((p/'account.json').read_text())
        self.assertNotIn('owner_id',cfg)
        self.assertEqual(list((p/'data').iterdir()),[])
        self.assertEqual(stat.S_IMODE((p/'.env').stat().st_mode),0o600)
        self.assertIsNone((await self.state.get_data())['managed_token'])
        for call in self.edit.await_args_list:
            self.assertNotIn(TOKEN,call.args[2]);self.assertNotIn('secret-ai-key',call.args[2])

    async def test_explicit_owner_still_supported_for_existing_cli_workflows(self):
        service.create_account(TOKEN,12345,'other_bot',99)
        cfg=json.loads((self.root/'bot_12345/account.json').read_text())
        self.assertEqual(cfg['owner_id'],99)

    async def test_stats_are_a_public_projection_not_private_config(self):
        p=self.profile('second',999,222)
        (p/'worker.json').write_text(json.dumps({'pid':55,'ready':True,'bot_username':'public_bot','userbot_id':999,'session':'secret-session'}))
        result=service.public_status(self.root,{'second':{'pid':55}})
        value=json.dumps(result)
        for secret in ['owner_id','999','phone','secret','api_keys','session','global_prompt']:
            self.assertNotIn(secret,value)
        (self.root/'status.json').write_text(json.dumps(result));(self.root/'runtime.json').write_text('"invalid"')
        # Read a real public snapshot with normal runtime metadata.
        (self.root/'runtime.json').write_text('{"mode":"shared"}')
        await ui.show_stats(self.message,self.state)
        text=self.edit.await_args.args[2]
        buttons=self.edit.await_args.args[3]
        self.assertTrue(any('@public_bot' in b.text for row in buttons.inline_keyboard for b in row))
        self.assertNotIn('second',text);self.assertNotIn('999',text)

    async def test_duplicate_token_never_creates_another_profile(self):
        service.create_account(TOKEN,12345,'other_bot',7)
        with self.assertRaises(FileExistsError):service.create_account(TOKEN,12345,'other_bot',99)
        self.assertEqual(len(service.profile_dirs(self.root)),2)

    async def test_stop_restart_preserve_data_and_primary_is_protected(self):
        p=self.profile('second',99,222);(p/'data/private').write_text('private-archive')
        service.change_account('second','disable');service.change_account('second','restart');service.change_account('second','enable')
        self.assertEqual((p/'data/private').read_text(),'private-archive')
        cfg=json.loads((p/'account.json').read_text());self.assertTrue(cfg['enabled']);self.assertEqual(cfg['generation'],1)
        with self.assertRaises(PermissionError):service.change_account('main','disable')
        with self.assertRaises((ValueError,PermissionError)):service.change_account('../main','disable')

    async def test_child_env_cannot_elevate_its_role(self):
        p=self.root/'own.env';p.write_text('NEXUS_ACCOUNT_ROLE=manager\nNEXUS_PROFILE_OWNER_ID=7\nNEXUS_ACCOUNTS_DIR=/elsewhere\n')
        with patch.dict(os.environ,{'NEXUS_ENV_FILE':str(p),'NEXUS_ACCOUNT_ROLE':'worker','NEXUS_PROFILE_OWNER_ID':'99'}):
            environment.load_environment()
            self.assertEqual(os.environ['NEXUS_ACCOUNT_ROLE'],'worker')
            self.assertEqual(os.environ['NEXUS_PROFILE_OWNER_ID'],'99')
            self.assertEqual(os.environ['NEXUS_ACCOUNTS_DIR'],str(self.root))
            self.assertFalse(service.is_manager())

    async def test_new_database_binds_owner_before_start_and_stays_empty(self):
        engine=create_async_engine('sqlite+aiosqlite:///'+str(self.root/'new.db'))
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as conn:await conn.run_sync(db.Base.metadata.create_all)
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        with patch.object(db,'AsyncSessionLocal',sessions),patch.dict(os.environ,{'NEXUS_PROFILE_OWNER_ID':'99'}):
            await db.bind_profile_owner()
            async with sessions() as session:
                cfg=await session.get(db.GlobalConfig,1)
                self.assertEqual(cfg.admin_id,99);self.assertIsNone(cfg.api_keys);self.assertIsNone(cfg.session_string);self.assertIsNone(cfg.global_prompt)
            with patch.dict(os.environ,{'NEXUS_PROFILE_OWNER_ID':'7'}):
                with self.assertRaises(RuntimeError):await db.bind_profile_owner()

    async def test_single_mode_requests_supervisor_after_confirmation(self):
        (self.root/'runtime.json').write_text('{"mode":"single"}')
        service.request_promotion();self.assertTrue((self.root/'promote.request').is_file())


class OwnerGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_userbot_uses_its_own_database_api_credentials(self):
        import ast,inspect,logging
        from unittest.mock import MagicMock
        from pyrogram import filters,StopPropagation
        tree=ast.parse(Path('/app/main.py').read_text())
        node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='start_userbot')
        client=NS(on_message=MagicMock(return_value=lambda f:f),start=AsyncMock())
        constructor=MagicMock(return_value=client)
        plugins=NS(db=NS(get_global_config=AsyncMock(return_value=NS(api_id=999,api_hash='own-hash'))),userbot_handlers=[])
        scope={'asyncio':__import__('asyncio'),'install_guard':lambda *a:NS(recover=AsyncMock()),'Client':constructor,'plugins':plugins,'API_ID':111,'API_HASH':'parent-hash','userbot_app':None,'filters':filters,'StopPropagation':StopPropagation,'logging':logging,'inspect':inspect}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'account_start','exec'),scope)
        import base64,struct
        from core.session_lock import release_session
        encoded=base64.urlsafe_b64encode(struct.pack('>BI?256sQ?',2,999,False,b'x'*256,123,False)).decode().rstrip('=')
        with tempfile.TemporaryDirectory() as locks,patch.dict(os.environ,{'NEXUS_RESOURCE_DIR':locks}):
            try:await scope['start_userbot'](encoded)
            finally:release_session(client)
        self.assertEqual(constructor.call_args.kwargs['api_id'],999)
        self.assertEqual(constructor.call_args.kwargs['api_hash'],'own-hash')

    async def test_foreign_user_cannot_reach_controller_callbacks(self):
        from aiogram import types
        from datetime import datetime,timezone
        from core.owner_middleware import OwnerMiddleware
        user=types.User(id=8,is_bot=False,first_name='test')
        message=types.Message(message_id=1,date=datetime.now(timezone.utc),chat=types.Chat(id=8,type='private'))
        call=types.CallbackQuery(id='test',from_user=user,chat_instance='test',message=message,data='full_reset')
        handler=AsyncMock()
        with patch.dict(os.environ,{'NEXUS_PROFILE_OWNER_ID':'7'}),patch.object(ui.plugins.db,'get_global_config',AsyncMock(return_value=NS(admin_id=7))),patch.object(types.CallbackQuery,'answer',AsyncMock()) as answer:
            await OwnerMiddleware()(handler,call,{})
            handler.assert_not_awaited();answer.assert_awaited_once()

if __name__=='__main__':unittest.main()
