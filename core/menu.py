"""Bounded dialog refresh; opening settings never requires a Telegram profile RPC."""
import asyncio
import json
import logging
import time
from pathlib import Path


class ChatDirectory:
    def __init__(self, path="data/menu_dialogs.json", ttl=60.0, timeout=6.0):
        self.path = Path(path)
        self.ttl = ttl
        self.timeout = timeout
        self.dialogs = []
        self.names = {}
        self.updated_at = 0.0
        self.retry_at = 0.0
        self.task = None
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.dialogs = [(int(row[0]), str(row[1])) for row in saved[:30]]
            self.names.update(self.dialogs)
        except (OSError, ValueError, TypeError, IndexError):
            pass

    def remember(self, chat):
        if getattr(chat, "is_bot", False) or chat.id == 777000:
            return None
        kind = getattr(chat.type, "name", "")
        if kind != "PRIVATE":
            return None
        name = " ".join(filter(None, (chat.first_name, chat.last_name)))
        name = name or str(chat.id)
        self.names[chat.id] = name
        return chat.id, name

    async def _fetch(self, client):
        result = []
        async for dialog in client.get_dialogs(limit=30):
            row = self.remember(dialog.chat)
            if row:
                result.append(row)
        return result

    def _save(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    async def _refresh(self, client):
        try:
            rows = await asyncio.wait_for(self._fetch(client), timeout=self.timeout)
        except Exception as exc:
            self.retry_at = time.monotonic() + 15.0
            logging.warning("[Menu] Dialog refresh failed: %s", type(exc).__name__)
            return False
        self.dialogs = rows
        self.updated_at = time.monotonic()
        try:
            await asyncio.to_thread(self._save, rows)
        except OSError as exc:
            logging.warning("[Menu] Cannot persist dialog cache: %s", type(exc).__name__)
        return True

    async def get_dialogs(self, client, force=False):
        if client is None or not client.is_connected:
            return list(self.dialogs)
        now = time.monotonic()
        if not force and (now - self.updated_at < self.ttl or now < self.retry_at):
            return list(self.dialogs)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._refresh(client))
        # Keep the last known list usable during a reconnect or a refresh.
        if force or not self.dialogs:
            await asyncio.shield(self.task)
        return list(self.dialogs)


chat_directory = ChatDirectory()
