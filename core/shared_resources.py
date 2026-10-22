"""Optional cross-process media slots; lock files contain no account data."""
import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager


@asynccontextmanager
async def media_slot():
    root = os.environ.get('NEXUS_RESOURCE_DIR')
    if not root:
        yield
        return
    import fcntl
    count = max(1, min(16, int(os.environ.get('NEXUS_GLOBAL_MEDIA_SLOTS', '4'))))
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    selected = None
    try:
        while selected is None:
            for offset in range(count):
                index = (os.getpid() + offset) % count
                fd = os.open(directory/f'media-{index}.lock', os.O_CREAT | os.O_RDWR, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                except BaseException:
                    os.close(fd)
                    raise
                else:
                    selected = fd
                    break
            if selected is None:
                await asyncio.sleep(.05)
        yield
    finally:
        if selected is not None:
            fcntl.flock(selected, fcntl.LOCK_UN)
            os.close(selected)
