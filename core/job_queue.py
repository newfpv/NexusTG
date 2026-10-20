"""Bounded per-account work queues; event deduplication survives repeated updates."""
import asyncio
import logging
import time
from collections import OrderedDict
from functools import wraps


class JobQueue:
    def __init__(self, capacity=32, completed_ttl=600):
        self.queue = asyncio.Queue(maxsize=capacity)
        self.pending = set()
        self.completed = OrderedDict()
        self.worker = None
        self.ttl = completed_ttl

    async def submit(self, key, factory):
        now = time.monotonic()
        while self.completed and next(iter(self.completed.values())) < now-self.ttl:
            self.completed.popitem(last=False)
        if key in self.pending or key in self.completed:
            return False
        # Backpressure instead of unbounded create_task/to_thread downloads.
        self.pending.add(key)
        try:
            await self.queue.put((key,factory))
        except BaseException:
            self.pending.discard(key)
            raise
        if not self.worker or self.worker.done():
            self.worker = asyncio.create_task(self.run(),name='module-work-queue')
        return True

    async def run(self):
        while not self.queue.empty():
            key,factory=await self.queue.get()
            try:
                await factory()
                self.completed[key]=time.monotonic()
                while len(self.completed)>1024:self.completed.popitem(last=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.warning('[Work queue] Job failed; original retained',exc_info=True)
            finally:
                self.pending.discard(key)
                self.queue.task_done()


def serial_events(function):
    """Serialize a module's replies per dialog and ignore duplicate events.

    Callback queries use their own menu middleware instead. This decorator is
    for userbot message handlers with (client,message) arguments.
    """
    entries={}
    recent=OrderedDict()
    @wraps(function)
    async def wrapped(client,message,*args,**kwargs):
        chat=message.chat.id
        key=(chat,message.id)
        if key in recent:return
        recent[key]=None
        while len(recent)>2048:recent.popitem(last=False)
        entry=entries.setdefault(chat,[asyncio.Lock(),0])
        entry[1]+=1
        try:
            async with entry[0]:
                return await function(client,message,*args,**kwargs)
        except asyncio.CancelledError:
            recent.pop(key,None)
            raise
        finally:
            entry[1]-=1
            if not entry[1]:entries.pop(chat,None)
    return wrapped
