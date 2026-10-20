import asyncio
import time
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock,patch
from modules import always_online as online


class PresenceInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_optional_probe_does_not_delay_successful_online_for_five_minutes(self):
        def reply(query,**kwargs):
            name=type(query).__name__
            if name=='GetConfig':return NS(online_update_period_ms=210000,offline_idle_timeout_ms=30000,date=int(time.time()),expires=int(time.time())+3600)
            if name=='GetUsers':raise ValueError('optional inspection unavailable')
            return True
        app=NS(is_initialized=True,is_connected=True,invoke=AsyncMock(side_effect=reply))
        snapshots=[]
        with patch.object(online,'userbot_app',app),patch.object(online,'_save_presence',side_effect=lambda value:snapshots.append(dict(value))),patch.object(online.random,'uniform',return_value=75),patch.object(online.asyncio,'sleep',AsyncMock(side_effect=asyncio.CancelledError)) as sleep:
            with self.assertRaises(asyncio.CancelledError):await online._heartbeat(app)
        sleep.assert_awaited_once_with(75)
        self.assertEqual(sum(type(call.args[0]).__name__=='UpdateStatus' for call in app.invoke.await_args_list),1)
        self.assertTrue(any(snapshot.get('successful_updates')==1 for snapshot in snapshots))
