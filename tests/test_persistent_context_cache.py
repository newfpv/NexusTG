from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from core import db, services
from pyrogram.enums import ChatType


class PersistentCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'database.sqlite'
        self.engine=create_async_engine('sqlite+aiosqlite:///'+str(self.path))
        self.addAsyncCleanup(self.dispose)
        async with self.engine.begin() as connection:
            await connection.run_sync(db.Base.metadata.create_all)
        self.factory=async_sessionmaker(self.engine,expire_on_commit=False)

    async def dispose(self):await self.engine.dispose()

    async def save(self,mid,content='cached',chat=7,kind='voice'):
        async with self.factory() as session:
            await db.CoreRepository(session).save_media_memory(mid,kind,content,chat_id=chat)

    async def get(self,mid,chat=7,kind='voice'):
        async with self.factory() as session:
            return await db.CoreRepository(session).get_media_memory(mid,kind,chat_id=chat)

    async def advance(self,floor,chat=7):
        async with self.factory() as session:await db.CoreRepository(session).advance_media_window(chat,floor)

    async def test_old_transcript_survives_reopen_and_is_used_without_downloading(self):
        async with self.factory() as session:
            session.add(db.DialogMediaMemory(chat_id=7,msg_id=20,media_type='voice',content='old real transcript',timestamp=datetime(2000,1,1)))
            await session.commit()
        await self.engine.dispose()
        self.engine=create_async_engine('sqlite+aiosqlite:///'+str(self.path))
        self.factory=async_sessionmaker(self.engine,expire_on_commit=False)
        message=NS(id=20,chat=NS(id=7),voice=NS(duration=12))
        with patch.object(services,'AsyncSessionLocal',self.factory),patch.object(services,'download_context_media',AsyncMock()) as download:
            result=await services._describe_message_media(NS(),message,[])
        self.assertEqual(result[0],'old real transcript');download.assert_not_awaited()

    async def test_same_ids_in_other_chats_do_not_mix_and_are_not_pruned(self):
        await self.save(100,'first');await self.save(100,'second',chat=8)
        self.assertEqual(await self.get(100),'first');self.assertEqual(await self.get(100,chat=8),'second')
        await self.advance(101)
        self.assertIsNone(await self.get(100));self.assertEqual(await self.get(100,chat=8),'second')

    async def test_window_only_moves_forward_and_late_old_recognition_cannot_restore_eviction(self):
        await self.save(100);await self.save(200)
        await self.advance(150);await self.advance(50)
        await self.save(100,'late result')
        self.assertIsNone(await self.get(100));self.assertEqual(await self.get(200),'cached')

    async def test_a_thousand_text_messages_advance_media_boundary_too(self):
        await self.save(199);await self.save(200)
        async def history(*args,**kwargs):
            for mid in range(1199,199,-1):
                yield NS(id=mid,chat=NS(id=7,type=ChatType.PRIVATE),from_user=NS(id=7,is_self=False,is_bot=False,first_name='Partner'),text='text',caption=None,outgoing=False)
        with patch.object(services,'AsyncSessionLocal',self.factory),patch.object(db.CoreRepository,'is_msg_ignored',AsyncMock(return_value=False)):
            await services.build_dialog_context(NS(get_chat_history=history),7,limit=1000,target_msg_id=1199)
        self.assertIsNone(await self.get(199));self.assertEqual(await self.get(200),'cached')

    async def test_short_manual_history_does_not_reduce_long_term_window(self):
        await self.save(1);await self.save(200)
        async def history(*args,**kwargs):
            yield NS(id=201,chat=NS(id=7,type=ChatType.PRIVATE),from_user=NS(id=7,is_self=False,is_bot=False,first_name='Partner'),text='text',caption=None,outgoing=False)
        with patch.object(services,'AsyncSessionLocal',self.factory):
            await services.build_dialog_context(NS(get_chat_history=history),7,limit=30)
        self.assertEqual(await self.get(1),'cached');self.assertEqual(await self.get(200),'cached')

    async def test_cache_is_bounded_even_when_only_manual_small_windows_are_used(self):
        async with self.factory() as session:
            await session.execute(insert(db.DialogMediaMemory),[
                dict(chat_id=7,msg_id=i,media_type='voice',content='x',timestamp=datetime(2000,1,1)) for i in range(1,1001)])
            await session.commit()
        await self.save(1001)
        self.assertIsNone(await self.get(1));self.assertEqual(await self.get(1001),'cached')
        async with self.factory() as session:
            rows=(await session.execute(select(db.DialogMediaMemory))).scalars().all()
        self.assertEqual(len(rows),1000)

    async def test_legacy_migration_keeps_unambiguous_cache_without_guessing(self):
        path=Path(self.temp.name)/'saver.sqlite'
        with sqlite3.connect(path) as archive:
            archive.execute('CREATE TABLE msg_cache (message_id INTEGER,chat_id INTEGER,media_type TEXT)')
            archive.executemany('INSERT INTO msg_cache VALUES (?,?,?)',[(1,7,'voice'),(2,7,'voice'),(2,8,'voice'),(3,7,'photo'),(4,9,'voice')])
        async with self.factory() as session:
            session.add_all([db.MediaMemoryCache(msg_id=i,media_type='voice',content='legacy'+str(i),timestamp=datetime(2000,1,1)) for i in range(1,5)])
            await session.commit()
        await self.advance(5,chat=9)
        with patch.object(db,'AsyncSessionLocal',self.factory):
            await db.migrate_legacy_media_memory(path);await db.migrate_legacy_media_memory(path)
        self.assertEqual(await self.get(1),'legacy1');self.assertIsNone(await self.get(2));self.assertIsNone(await self.get(3))
        self.assertIsNone(await self.get(4,chat=9))
        async with self.factory() as session:
            legacy=(await session.execute(select(db.MediaMemoryCache.msg_id))).scalars().all()
        self.assertEqual(sorted(legacy),[2,3])


if __name__=='__main__':unittest.main()
