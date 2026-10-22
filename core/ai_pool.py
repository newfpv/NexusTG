"""Fair per-account API-key leases. No keys or requests cross account processes."""
import asyncio
import weakref
from contextlib import asynccontextmanager

_pools = weakref.WeakKeyDictionary()


class KeyPool:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.busy = set()
        self.last = None

    @asynccontextmanager
    async def lease(self, keys):
        keys = list(dict.fromkeys(keys))
        if not keys:
            raise ValueError('Empty key pool')
        async with self.condition:
            await self.condition.wait_for(lambda: any(key not in self.busy for key in keys))
            start = (keys.index(self.last)+1) % len(keys) if self.last in keys else 0
            ordered = keys[start:] + keys[:start]
            selected = next(key for key in ordered if key not in self.busy)
            self.busy.add(selected)
            self.last = selected
        try:
            yield selected
        finally:
            async with self.condition:
                self.busy.discard(selected)
                self.condition.notify_all()


def key_lease(keys):
    loop = asyncio.get_running_loop()
    return _pools.setdefault(loop, KeyPool()).lease(keys)
