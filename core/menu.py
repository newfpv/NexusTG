"""Incremental private-chat directory, without truncating before filtering."""
import asyncio
import json
import logging
import time
from pathlib import Path

PAGE_SIZE = 10


class ChatDirectory:
    def __init__(self, path='data/menu_dialogs.json', ttl=60.0, timeout=6.0):
        self.path = Path(path)
        self.ttl, self.timeout = ttl, timeout
        self.dialogs, self.names = [], {}
        self.updated_at = self.retry_at = 0.0
        self.task = None
        self.has_more = True
        self.iterator = self.client = None
        self.pending = None
        self.clear_on_response = False
        self.lock = asyncio.Lock()
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            rows = saved.get('rows', []) if isinstance(saved, dict) else saved
            self.dialogs = [(int(row[0]), str(row[1])) for row in rows]
            self.names.update(self.dialogs)
        except (OSError, ValueError, TypeError, IndexError, AttributeError):
            pass

    def remember(self, chat):
        if getattr(chat, 'is_bot', False) or chat.id == 777000:
            return None
        if getattr(chat.type, 'name', '') != 'PRIVATE':
            return None
        name = ' '.join(filter(None, (getattr(chat, 'first_name', None), getattr(chat, 'last_name', None)))) or str(chat.id)
        self.names[chat.id] = name
        return chat.id, name

    def _save(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'rows': rows}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    async def _all_dialogs(self, client):
        for archived in (False, True):
            async for dialog in client.get_dialogs(limit=0, from_archive=archived):
                yield dialog

    async def _refresh(self, client, target, reset=False):
        async with self.lock:
            if self.pending is not None and self.client is client:
                # A slow RPC continues once in the background. Do not restart
                # the same first page on each timeout or refresh tap.
                reset=False
            starting = reset or self.client is not client or self.iterator is None
            clear = reset and (self.client is client) or self.client is not client
            if starting:
                self.clear_on_response=clear
                if self.pending is not None:
                    self.pending.cancel()
                    await asyncio.gather(self.pending,return_exceptions=True)
                    self.pending=None
                if self.iterator is not None:
                    try:await self.iterator.aclose()
                    except (AttributeError,RuntimeError):pass
                self.client = client
                self.iterator = self._all_dialogs(client).__aiter__()
                self.has_more = True
            first = starting
            try:
                async with asyncio.timeout(self.timeout):
                    while self.has_more and (len(self.dialogs) < target or first or self.pending is not None):
                        try:
                            if self.pending is None:
                                self.pending=asyncio.create_task(anext(self.iterator))
                            dialog = await asyncio.shield(self.pending)
                            self.pending=None
                        except StopAsyncIteration:
                            self.pending=None
                            if self.clear_on_response:
                                self.dialogs = []
                                self.clear_on_response=False
                            self.has_more = False
                            break
                        if self.clear_on_response:
                            self.dialogs = []
                            self.clear_on_response=False
                        first = False
                        row = self.remember(dialog.chat)
                        if row:
                            existing = next((i for i, item in enumerate(self.dialogs) if item[0] == row[0]), None)
                            if existing is None:
                                self.dialogs.append(row)
                            else:
                                self.dialogs[existing] = row
            except Exception as exc:
                if isinstance(exc,TimeoutError) and self.pending is not None and not self.pending.done():
                    # Keep partial rows, the iterator, and its one pending read.
                    try:await asyncio.to_thread(self._save,list(self.dialogs))
                    except OSError:pass
                    return False
                self.iterator = None
                self.pending=None
                wait=getattr(exc,'value',60)
                self.retry_at = time.monotonic() + max(60.0,float(wait) if isinstance(wait,(int,float)) else 60.0)
                logging.warning('[Menu] Dialog refresh failed: %s', type(exc).__name__)
                return False
            self.updated_at = time.monotonic()
            try:
                await asyncio.to_thread(self._save, list(self.dialogs))
            except OSError as exc:
                logging.warning('[Menu] Cannot persist dialog cache: %s', type(exc).__name__)
            return True

    async def get_dialogs(self, client, force=False, page=0):
        if client is None or not client.is_connected:
            return list(self.dialogs)
        page = max(0, int(page))
        target = (page + 2) * PAGE_SIZE
        now = time.monotonic()
        stale = now - self.updated_at >= self.ttl
        need_more = self.has_more and len(self.dialogs) < target
        reset = force or self.client is not client or (stale and not need_more)
        if now < self.retry_at:
            return list(self.dialogs)
        if not (reset or need_more):
            return list(self.dialogs)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._refresh(client, target, reset=reset))
        await asyncio.shield(self.task)
        return list(self.dialogs)


chat_directory = ChatDirectory()
