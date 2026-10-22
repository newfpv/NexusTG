"""Bounded downloads and recovery of incomplete Kurigram media connections."""
import asyncio
import inspect
import logging
import os
import time

CONNECT_TIMEOUT = 25.0
STOP_TIMEOUT = 5.0
MAX_DOWNLOAD_SECONDS = 900.0


class MediaDownloadError(OSError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Telegram media download failed: '+reason)


async def _discard(app, mapping, dc, session):
    if session is getattr(app, 'session', None):
        return
    if mapping.get(dc) is session:
        mapping.pop(dc, None)
    try:
        await asyncio.wait_for(session.stop(), STOP_TIMEOUT)
    except Exception:
        # A cancelled Session.start may leave neither worker fully initialized.
        connection = getattr(session, 'connection', None)
        if connection:
            try:await asyncio.wait_for(connection.close(), STOP_TIMEOUT)
            except Exception:pass
        for name in ('recv_task', 'ping_task'):
            task = getattr(session, name, None)
            if task and not task.done():task.cancel()


def install_media_guard(app):
    if getattr(app, '_nexus_media_guard', False) or not callable(getattr(app, 'get_session', None)):
        return
    original = app.get_session
    signature = inspect.signature(original)
    locks = {}
    # Installed before Client.start: one archive transfer must not occupy the
    # only download slot while a manual transcription waits for its first byte.
    app.get_file_semaphore = asyncio.Semaphore(2)

    async def get_session(*args, **kwargs):
        values = signature.bind_partial(*args, **kwargs).arguments
        if not values.get('is_media') or values.get('temporary'):
            return await original(*args, **kwargs)
        dc = values.get('dc_id') or await app.storage.dc_id()
        async with locks.setdefault(dc, asyncio.Lock()):
            cached = app.media_sessions.get(dc)
            if cached and cached.is_started.is_set():return cached
            if cached:
                await _discard(app, app.media_sessions, dc, cached)
            previous = dict(getattr(app, 'sessions', {}))
            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    session = await original(*args, **kwargs)
                    await session.is_started.wait()
                return session
            except BaseException:
                # Kurigram caches a Session before awaiting start(). Without
                # eviction, cancellation makes every later download reuse it.
                cached = app.media_sessions.get(dc)
                if cached:await _discard(app, app.media_sessions, dc, cached)
                other = getattr(app, 'sessions', {}).get(dc)
                # A new cross-DC session may have started but still be awaiting
                # authorization import. Existing healthy sessions stay intact.
                if other and other is not previous.get(dc):
                    await _discard(app, app.sessions, dc, other)
                logging.warning('[Media] Media connection initialization cancelled or failed')
                raise
    app.get_session = get_session
    original_file = getattr(app, 'get_file', None)
    if callable(original_file):
        active = {}
        async def get_file(file_id, *args, **kwargs):
            dc = file_id.dc_id
            active[dc] = active.get(dc, 0)+1
            failed = False
            source = original_file(file_id, *args, **kwargs)
            try:
                async for chunk in source:
                    yield chunk
            except (OSError, asyncio.CancelledError):
                failed = True
                raise
            finally:
                try:
                    await source.aclose()
                finally:
                    active[dc] -= 1
                    if not active[dc]:
                        active.pop(dc)
                        if failed:
                            async with locks.setdefault(dc, asyncio.Lock()):
                                session = app.media_sessions.get(dc)
                                if session and not active.get(dc):await _discard(app, app.media_sessions, dc, session)
        app.get_file = get_file
    app._nexus_media_guard = True


def _media_file_size(message):
    for name in ('voice','video_note','video','audio','photo','document','animation','sticker'):
        item = getattr(message, name, None)
        if isinstance(item, list):item = item[-1] if item else None
        size = getattr(item, 'file_size', 0) if item else 0
        if size:return int(size)
    return 0


def _remove(paths):
    for path in paths:
        try:os.remove(path)
        except FileNotFoundError:pass


async def _download(message, file_name, idle_timeout):
    last_progress = time.monotonic()
    transferred = 0
    started = last_progress
    async def progress(current, total):
        nonlocal last_progress, transferred
        if current > transferred:
            transferred = current
            last_progress = time.monotonic()
    task = asyncio.create_task(message.download(file_name=file_name, progress=progress))
    try:
        while not task.done():
            remaining = min(last_progress+idle_timeout, started+MAX_DOWNLOAD_SECONDS)-time.monotonic()
            if remaining <= 0:raise MediaDownloadError('download_timeout')
            await asyncio.wait({task}, timeout=remaining)
        return await task
    finally:
        if not task.done():
            task.cancel()
            try:await asyncio.wait_for(task, STOP_TIMEOUT*4+5)
            except (Exception, asyncio.CancelledError):pass


async def download_media_checked(client, message, file_name, timeout=90.0, retries=2):
    """Timeout means no byte progress; active large downloads have a total cap."""
    file_name = os.fspath(file_name)
    current = message
    expected = _media_file_size(message)
    paths = (file_name, file_name+'.temp')
    last_error = None
    try:
        for attempt in range(1, retries+1):
            _remove(paths)
            try:
                path = await _download(current, file_name, timeout)
                actual = os.path.getsize(path) if path and os.path.isfile(path) else 0
                if actual > 0 and (not expected or actual == expected):return path
                last_error = MediaDownloadError('incomplete_file')
                if path and os.path.isfile(path):os.remove(path)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
            logging.warning('[Media] Download attempt %s/%s failed: %s phase=%s',attempt,retries,type(last_error).__name__,getattr(last_error,'reason','telegram_transport'))
            if attempt < retries:
                try:
                    refreshed = await asyncio.wait_for(client.get_messages(message.chat.id,message.id),10)
                    if refreshed:
                        current = refreshed
                        expected = _media_file_size(refreshed) or expected
                except Exception:
                    logging.debug('[Media] Could not refresh attachment metadata')
                await asyncio.sleep(1)
        raise MediaDownloadError(getattr(last_error,'reason','telegram_transport')) from last_error
    except BaseException:
        _remove(paths)
        raise
