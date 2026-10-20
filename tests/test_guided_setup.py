import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import EditMessageText
from aiogram.types import InlineKeyboardMarkup as Markup, InlineKeyboardButton as Button
from modules import setup_wizard as wizard


class WizardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        wizard.active=False;wizard.current_step=0
        self.record={}
        self.cfg=NS(admin_id=7,session_string='private',api_keys='secret-a,secret-b',model_fallback_list='model',tz='Europe/Minsk',global_prompt='<hello>',typing_speed=.1,sleep_start=None,sleep_end=None,global_ai_active=False,google_search=False,module_settings={})
        self.state=NS(update_data=AsyncMock(),set_state=AsyncMock())
        self.message=NS(chat=NS(id=7),message_id=10)
        async def get(_):return dict(self.record)
        async def update(_,**changes):self.record.update(changes)
        for name,value in [('get_global_config',AsyncMock(return_value=self.cfg)),('get_module_cfg',get),('update_module_cfg',update)]:
            p=patch.object(wizard.plugins.db,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(wizard,'safe_edit',AsyncMock());self.edit=p.start();self.addCleanup(p.stop)
        self.addCleanup(setattr,wizard,'active',False)

    async def test_all_cards_fit_and_skipping_does_not_enable_features(self):
        for i,(key,callback) in enumerate(wizard.STEPS):
            await wizard.show_step(self.message,self.state,i)
            text=self.edit.await_args.args[2]
            self.assertLess(len(text),3000)
            self.assertNotIn('wizard_desc_',text)
            self.assertNotIn('secret-a',text)
            self.assertEqual(self.record['step'],i)
            self.assertFalse(self.cfg.global_ai_active)
            self.assertEqual(self.cfg.module_settings,{})
        await wizard.show_step(self.message,self.state,len(wizard.STEPS))
        self.assertTrue(self.record['completed']);self.assertFalse(wizard.active)

    async def test_bookmark_survives_restart_and_pause(self):
        await wizard.show_step(self.message,self.state,14)
        wizard.active=False;wizard.current_step=0
        await wizard.on_startup()
        self.assertTrue(wizard.active);self.assertEqual(wizard.current_step,14)
        call=NS(from_user=NS(id=7),message=self.message,data='wizard_pause',answer=AsyncMock())
        with patch.object(wizard.plugins,'generate_menu_cb',AsyncMock(return_value=('menu',None))):
            await wizard.navigate(call,self.state)
        self.assertFalse(wizard.active)
        await wizard.begin(self.message,self.state)
        self.assertEqual(wizard.current_step,14)

    async def test_native_direct_editor_keeps_navigation_only_on_owner_menu(self):
        storage=MemoryStorage();key=StorageKey(bot_id=11,chat_id=7,user_id=7)
        await storage.set_data(key,{'menu_msg_id':10})
        middleware=wizard.WizardNavigationMiddleware(storage)
        wizard.active=True;wizard.current_step=4
        for chat_id,message_id,expected in [(7,10,2),(7,99,1),(8,10,1)]:
            method=EditMessageText(chat_id=chat_id,message_id=message_id,text='saved',reply_markup=Markup(inline_keyboard=[[Button(text='Back',callback_data='aicmd_main')]]))
            await middleware(AsyncMock(),NS(id=11),method)
            self.assertEqual(len(method.reply_markup.inline_keyboard),expected)
        await storage.close()

    async def test_unauthorized_click_cannot_activate_wizard(self):
        call=NS(from_user=NS(id=8),message=self.message,data='wizard_start',answer=AsyncMock())
        await wizard.navigate(call,self.state)
        self.assertFalse(wizard.active);self.edit.assert_not_awaited()

    async def test_leaving_test_cancels_it_and_resets_input_state(self):
        from core.auth import _active_credential_tests
        job=NS(stop=lambda:None)
        from unittest.mock import Mock
        job.stop=Mock();_active_credential_tests[7]=job
        await wizard.show_step(self.message,self.state,4)
        job.stop.assert_called_once();self.assertNotIn(7,_active_credential_tests)
        self.state.set_state.assert_awaited_with(None)

    async def test_translations_and_navigation_are_complete(self):
        for language in ['RU','EN']:
            data=json.loads(Path(f'/app/language_{language}.json').read_text())
            for key,_ in wizard.STEPS:
                for prefix in ['wizard_title_','wizard_desc_','wizard_action_']:
                    self.assertIn(prefix+key,data)
        wizard.active=True
        kb=Markup(inline_keyboard=[[Button(text='Next',callback_data='wizard_step_3')]])
        self.assertIs(wizard.attach_navigation(kb),kb)
        self.record['step']='bad';await wizard.on_startup()
        self.assertEqual(wizard.current_step,0)

    async def test_first_authorization_opens_wizard_and_completed_setup_does_not(self):
        from core import auth
        client=NS(sign_in=AsyncMock(),get_me=AsyncMock(return_value=NS(phone_number='12345')),export_session_string=AsyncMock(return_value='fake-session'),disconnect=AsyncMock())
        for completed in [False,True]:
            self.record={'completed':completed}
            with patch.object(auth,'_delete_qr_message',AsyncMock(),create=True),patch.object(auth,'safe_edit',AsyncMock()),patch.object(auth.plugins.db,'save_session',AsyncMock()),patch.object(auth.plugins,'start_userbot_cb',AsyncMock()),patch.object(wizard,'begin',AsyncMock()) as begin:
                if hasattr(auth,'_finish_authorized_client'):
                    await auth._finish_authorized_client(client,'12345',self.message,self.state)
                else:
                    auth.auth_clients[7]=client
                    self.state.get_data=AsyncMock(return_value={'phone':'12345','hash':'fake'})
                    call=NS(from_user=NS(id=7),message=self.message)
                    await auth.process_auth_code(call,self.state,'12345')
                self.assertEqual(begin.await_count,0 if completed else 1)

    async def test_initial_timezone_proceeds_to_login_before_optional_cookies(self):
        from core import auth
        message=NS(text='Europe/Minsk',delete=AsyncMock())
        with patch.object(auth.plugins.db,'update_global_config',AsyncMock()) as update,patch.object(auth,'finish_setup',AsyncMock()) as finish:
            await auth.setup_timezone(message,self.state)
            update.assert_awaited_once_with(tz='Europe/Minsk')
            finish.assert_awaited_once_with(message,self.state)

if __name__=='__main__':unittest.main()
