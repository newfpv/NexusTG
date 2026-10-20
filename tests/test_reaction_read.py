import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import test_ai_twin_status as fixtures
from modules import ai_twin as ai


class ReactionReadTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ReplyTests.asyncSetUp
    asyncTearDown = fixtures.ReplyTests.asyncTearDown
    run_reply = fixtures.ReplyTests.run_reply

    async def test_reaction_only_acknowledges_processed_message_before_reaction(self):
        ai.generate_ai_response.return_value='[LIKE]'
        async def react(**kwargs):
            self.client.read_chat_history.assert_awaited_once_with(42,max_id=7)
        self.client.send_reaction.side_effect=react
        self.assertEqual(await self.run_reply(),'reaction')
        self.client.send_message.assert_not_awaited()

    async def test_combined_reply_reads_once_before_reaction_and_text(self):
        ai.generate_ai_response.return_value='hello [LIKE]'
        async def react(**kwargs):
            self.client.read_chat_history.assert_awaited_once_with(42,max_id=7)
        self.client.send_reaction.side_effect=react
        self.assertEqual(await self.run_reply(),'sent')
        self.client.read_chat_history.assert_awaited_once_with(42,max_id=7)
        self.client.send_message.assert_awaited_once()

    async def test_read_flood_wait_defers_reaction_instead_of_bypassing_it(self):
        ai.generate_ai_response.return_value='[LIKE]'
        self.client.read_chat_history.side_effect=ai.FloodWait(600)
        self.assertEqual(await self.run_reply(),'flood_wait')
        self.client.send_reaction.assert_not_awaited()
        self.assertGreater(ai.save_checkpoint.await_args.kwargs['retry_at'],ai.time.time()+590)

    async def test_suppressed_forced_like_does_not_mark_unanswered_message_read(self):
        ai.generate_ai_response.return_value='[LIKE]'
        self.assertEqual(await self.run_reply(force=True),'no_text')
        self.client.read_chat_history.assert_not_awaited()
        self.client.send_reaction.assert_not_awaited()

    async def test_installed_library_passes_max_id_to_private_history_rpc(self):
        from pyrogram import Client
        from pyrogram.raw.types import InputPeerUser
        client=NS(resolve_peer=AsyncMock(return_value=InputPeerUser(user_id=42,access_hash=1)),invoke=AsyncMock())
        await Client.read_chat_history(client,42,max_id=7)
        query=client.invoke.await_args.args[0]
        self.assertIsInstance(query,ai.functions.messages.ReadHistory)
        self.assertEqual(query.max_id,7)
