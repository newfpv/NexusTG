"""Per-account MTProto admission, pacing and durable server-directed cooldowns.

Only explicit FloodWait rejections are retried here, with the same raw query
(and random_id). Ambiguous transport failures are never blindly resent.
The controller Bot API has a separate guard and never shares account limits.
"""
import asyncio
import hashlib
import json
import logging
import os
import time
import base64
import io
import uuid
from collections import OrderedDict
from pathlib import Path
from contextvars import ContextVar

from pyrogram.errors import FloodWait, FloodPremiumWait, SlowmodeWait, RPCError


class TelegramPaused(RuntimeError):
    pass


class TelegramBusy(RuntimeError):
    pass


RESTRICTED = {"PeerFlood", "UserRestricted", "UserDeactivated", "UserDeactivatedBan",
              "AuthKeyDuplicated", "AuthKeyUnregistered", "SessionRevoked", "SessionExpired"}
delivery_context=ContextVar('telegram_durable_result',default=None)


def retain_result(key,text):
    """Keep the complete result even if only its first block reached Telegram."""
    folder=Path('data/pending_results');folder.mkdir(parents=True,exist_ok=True)
    os.chmod(folder,0o700)
    path=folder/(hashlib.sha256(key.encode()).hexdigest()+'.json')
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps({'result':text,'at':time.time()},ensure_ascii=False),encoding='utf-8')
    os.chmod(temp,0o600);os.replace(temp,path)
    return path


def delivery_failure(exc):
    return isinstance(exc, (TelegramPaused, TelegramBusy, FloodWait, FloodPremiumWait, SlowmodeWait, OSError, TimeoutError)) or type(exc).__name__ in RESTRICTED


def raw_method(query):
    while hasattr(query, "query"):
        query = query.query
    return query


def is_write(query):
    name = type(query).__name__
    return not (name.startswith(("Get", "Check", "Resolve", "Search")) or
                type(query).__module__.startswith("pyrogram.raw.functions.upload"))


def peer_key(query):
    peer = getattr(query, "peer", None) or getattr(query, "channel", None)
    if peer is None:
        return "account"
    return (type(peer).__name__, getattr(peer, "user_id", None),
            getattr(peer, "chat_id", None), getattr(peer, "channel_id", None))


class AccountGuard:
    def __init__(self, path="data/telegram_limits.json", binding="", now=time.time, sleep=asyncio.sleep):
        self.path = Path(path)
        self.now, self.sleep = now, sleep
        self.binding = binding
        self.until = 0.0
        self.paused = ""
        self.next_write = 0.0
        self.pending = 0
        self.reads = asyncio.Semaphore(4)
        self.writer = asyncio.Lock()
        self.peers = {}
        self.last_actions = OrderedDict()
        self.last_edits = OrderedDict()
        self.notify = None
        self.outbox = self.path.parent / "telegram_outbox"
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("binding") == binding:
                self.until = max(0.0, float(data.get("until", 0)))
                self.paused = data.get("paused", "") if data.get("paused") in RESTRICTED else ""
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(dict(binding=self.binding, until=self.until, paused=self.paused)), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)

    def defer(self, seconds):
        self.until = max(self.until, self.now() + max(0, float(seconds)) + 1)
        self.save()

    async def wait(self, writing=False):
        while True:
            if self.paused and writing:
                raise TelegramPaused(self.paused)
            delay = self.until - self.now()
            if delay <= 0:
                return
            await self.sleep(min(delay, 60))

    def resume(self):
        self.paused = ""
        self.save()  # A human acknowledgement never clears an active FloodWait.

    def journal(self, job, query, status):
        if job is None:
            return
        self.outbox.mkdir(parents=True, exist_ok=True)
        os.chmod(self.outbox,0o700)
        path = self.outbox / (job + ".json")
        raw=raw_method(query)
        supersedes=None
        if type(raw).__name__=='EditMessage':
            supersedes=repr((peer_key(raw),getattr(raw,'id',None)))
        if not path.exists():
            files=list(self.outbox.glob('*.json'))
            if len(files)>2048 or sum(p.stat().st_size for p in files)>32*1024**2:
                raise TelegramBusy('Pending delivery journal requires attention')
            if supersedes:
                for old in files:
                    try:
                        data=json.loads(old.read_text(encoding='utf-8'))
                        if data.get('status')=='queued' and data.get('supersedes')==supersedes:
                            old.unlink(missing_ok=True)
                    except (OSError,ValueError):pass
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(dict(binding=self.binding, status=status,
            recoverable=bool(delivery_context.get()),supersedes=supersedes,
            query=base64.b64encode(query.write()).decode(), at=self.now())), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, path)

    async def _attempt(self, call, query, args, kwargs, writing, job=None):
        while True:
            await self.wait(writing)
            try:
                self.journal(job, query, "inflight")
                # Kurigram otherwise sleeps/retries internally, hiding FloodWait
                # from sibling modules and resending non-idempotent requests.
                return await call(query, *args, **dict(kwargs, sleep_threshold=0, retries=1))
            except (FloodWait, FloodPremiumWait, SlowmodeWait) as exc:
                self.journal(job, query, "queued")
                self.defer(exc.value)
                logging.warning("[Telegram guard] FloodWait; account requests deferred", exc_info=True)
            except Exception as exc:
                name = type(exc).__name__
                if name in RESTRICTED:
                    changed = self.paused != name
                    self.paused = name
                    self.save()
                    logging.error("[Telegram guard] Account sending paused", exc_info=True)
                    if changed and self.notify:
                        asyncio.create_task(self.notify(name), name="telegram-restriction-notice")
                elif isinstance(exc, RPCError) and job:
                    # An explicit rejection cannot have delivered the request.
                    # Invalid HTML, unknown peers etc. must not accumulate as
                    # ambiguous deliveries or be replayed at the next startup.
                    (self.outbox / (job + ".json")).unlink(missing_ok=True)
                raise

    async def invoke(self, call, query, *args, **kwargs):
        raw = raw_method(query)
        writing = is_write(raw)
        action = type(raw).__name__ == "SetTyping"
        key = peer_key(raw)
        if action:
            signature = (key, type(getattr(raw, "action", None)).__name__)
            if self.until > self.now() or self.paused or self.last_actions.get(signature, 0) > self.now() - 3.5:
                return True
        if self.pending >= 128:
            if action:
                return True
            raise TelegramBusy("Account request backlog is full")
        self.pending += 1
        entry = None
        job = None
        try:
            if not writing:
                async with self.reads:
                    return await self._attempt(call, query, args, kwargs, False)
            entry = self.peers.setdefault(key, [asyncio.Lock(), 0, 0.0])
            entry[1] += 1
            # Preserve generated answers/progress during a cooldown or restart.
            # Presence/typing has no durable user content and is never replayed.
            if not action and type(raw).__name__ != "UpdateStatus":
                job = uuid.uuid4().hex
                self.journal(job, query, "queued")
            async with entry[0]:
                async with self.writer:
                    await self.wait(True)
                    edit = type(raw).__name__.startswith("EditMessage")
                    edit_key = (key, getattr(raw, "id", None))
                    deadline = max(self.next_write, entry[2], self.last_edits.get(edit_key, 0) + 2.5 if edit else 0)
                    if deadline > self.now():
                        await self.sleep(deadline - self.now())
                    try:
                        result = await self._attempt(call, query, args, kwargs, True, job)
                    finally:
                        self.next_write = self.now() + 0.65
                        entry[2] = self.now() + 1.1
                    if action:
                        self.last_actions[signature] = self.now()
                        while len(self.last_actions) > 512:
                            self.last_actions.popitem(last=False)
                    if edit:
                        self.last_edits[edit_key] = self.now()
                        while len(self.last_edits) > 512:
                            self.last_edits.popitem(last=False)
                    if job:
                        (self.outbox / (job + ".json")).unlink(missing_ok=True)
                    return result
        finally:
            self.pending -= 1
            if entry:
                entry[1] -= 1
                # Keep a bounded recent peer timing cache; never remove a lock
                # with waiters (including callers cancelled before acquisition).
                if len(self.peers) > 512:
                    for old, value in list(self.peers.items()):
                        if value[1] == 0 and value[2] < self.now():
                            self.peers.pop(old, None)

    async def recover(self, app):
        """Only requests known not to have been sent are replayed automatically.

        Inflight/ambiguous records remain private for diagnosis; replaying them
        after a restart could duplicate an operation. No pickle/untrusted eval.
        """
        from pyrogram.raw.core import TLObject
        files=[]
        for path in self.outbox.glob('*.json'):
            try:files.append((path.stat().st_mtime,path))
            except OSError:pass
        for _,path in sorted(files):
            data=None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("binding") != self.binding or data.get("status") != "queued":
                    continue
                query = TLObject.read(io.BytesIO(base64.b64decode(data["query"])))
                name=type(raw_method(query)).__name__
                if name not in {"DeleteMessages", "DeleteChannelMessages"} and not (
                        name in {"SendMessage","EditMessage"} and data.get('recoverable')):
                    # Creation/forward/upload flows need their high-level
                    # receipts and cannot be completed by replaying raw RPCs.
                    data['status']='review'
                    path.write_text(json.dumps(data),encoding='utf-8')
                    continue
                await app.invoke(query)
                path.unlink(missing_ok=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.warning("[Telegram guard] Pending delivery requires attention", exc_info=True)
                # Avoid hammering an expired peer/file reference on each boot.
                if isinstance(data,dict):
                    data["status"] = "review"
                    path.write_text(json.dumps(data), encoding="utf-8")


def install_guard(app, session_string=""):
    existing = getattr(app, "telegram_guard", None)
    if existing:
        return existing
    from core.session_lock import authorization_fingerprint
    guard = AccountGuard(binding=authorization_fingerprint(session_string))
    original = app.invoke

    async def invoke(query, *args, **kwargs):
        return await guard.invoke(original, query, *args, **kwargs)

    app.invoke = invoke
    app.telegram_guard = guard
    return guard


class BotDeliveryGuard:
    """Separate Bot API cooldown. Callback acknowledgement stays responsive."""
    def __init__(self, path="data/bot_limits.json"):
        self.guard = AccountGuard(path)
        self.lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(3)
        self.peers = {}
        self.next = 0.0

    async def __call__(self, make_request, bot, method):
        from aiogram.exceptions import TelegramRetryAfter
        name = type(method).__name__
        if name in {"GetUpdates", "AnswerCallbackQuery", "GetMe"}:
            return await make_request(bot, method)
        key=getattr(method,'chat_id',None) or 'account'
        entry=self.peers.setdefault(key,[asyncio.Lock(),0,0.0])
        entry[1]+=1
        try:
            async with entry[0], self.slots:
                while True:
                    async with self.lock:
                        await self.guard.wait()
                        delay = max(self.next,entry[2]) - time.time()
                        if delay > 0:
                            await asyncio.sleep(delay)
                        self.next=time.time()+0.15
                        # Bot API guidance: one message/second per chat and
                        # twenty/minute per group; topics share the group limit.
                        group=str(key).startswith('-')
                        entry[2]=time.time()+(3.2 if group else 1.1)
                    try:
                        return await make_request(bot, method)
                    except TelegramRetryAfter as exc:
                        self.guard.defer(exc.retry_after)
        finally:
            entry[1]-=1
            if len(self.peers)>512:
                for old,value in list(self.peers.items()):
                    if not value[1] and value[2]<time.time():self.peers.pop(old,None)
