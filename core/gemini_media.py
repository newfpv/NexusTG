"""Bounded-memory Gemini media transport, independent of SDK upload signatures."""
import asyncio
import hashlib
import json
import logging
import os
import time
import weakref
from collections import OrderedDict
from contextlib import asynccontextmanager

import aiohttp
from google.genai import types
from core.shared_resources import media_slot

INLINE_LIMIT = 4 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
UPLOAD_TIMEOUT = 600.0
PROCESSING_TIMEOUT = 300.0
FILE_CACHE_TTL = 3600.0
FILE_CACHE_SIZE = 128
_files = OrderedDict()
_hashes = OrderedDict()
_loop_slots = weakref.WeakKeyDictionary()
API_ROOT = 'https://generativelanguage.googleapis.com'


class MediaPreparationError(Exception):
    def __init__(self, message, code=None, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


def request_slots():
    loop = asyncio.get_running_loop()
    return _loop_slots.setdefault(loop, (asyncio.Semaphore(1), asyncio.Semaphore(2), asyncio.Semaphore(2)))[1]


@asynccontextmanager
async def bounded_media_request(path):
    if path:
        request_slots()
        async with _loop_slots[asyncio.get_running_loop()][2]:
            async with media_slot():
                yield
    else:
        yield


def _upload_slots():
    request_slots()
    return _loop_slots[asyncio.get_running_loop()][0]


def _sha256_file(path):
    stat = os.stat(path)
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if identity in _hashes:
        _hashes.move_to_end(identity)
        return _hashes[identity]
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        while chunk := source.read(UPLOAD_CHUNK_SIZE):
            digest.update(chunk)
    result = digest.hexdigest()
    _hashes[identity] = result
    while len(_hashes) > FILE_CACHE_SIZE:
        _hashes.popitem(last=False)
    return result


async def _json_response(response, action):
    if response.status >= 400:
        # Upload URLs contain private session tokens; never include them or keys.
        try:
            details = await response.json()
        except (ValueError, aiohttp.ContentTypeError):
            details = None
        raise MediaPreparationError(f'Gemini media {action} failed (HTTP {response.status})', response.status, details)
    return await response.json()


async def _upload(api_key, path, mime_type):
    size = os.path.getsize(path)
    headers = {'x-goog-api-key': api_key, 'X-Goog-Upload-Protocol': 'resumable',
               'X-Goog-Upload-Command': 'start', 'X-Goog-Upload-Header-Content-Length': str(size),
               'X-Goog-Upload-Header-Content-Type': mime_type}
    timeout = aiohttp.ClientTimeout(total=UPLOAD_TIMEOUT, connect=15, sock_read=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(API_ROOT + '/upload/v1beta/files', headers=headers,
                                json={'file': {'display_name': 'NexusTG media'}}) as response:
            if response.status >= 400:
                await _json_response(response, 'start')
            upload_url = response.headers.get('X-Goog-Upload-URL')
        if not upload_url:
            raise MediaPreparationError('Gemini media upload URL is missing')
        with open(path, 'rb') as source:
            async def chunks():
                while chunk := source.read(UPLOAD_CHUNK_SIZE):
                    yield chunk
                    await asyncio.sleep(0)
            async with session.post(upload_url, data=chunks(), headers={
                    'Content-Length': str(size), 'Content-Type': mime_type,
                    'X-Goog-Upload-Offset': '0', 'X-Goog-Upload-Command': 'upload, finalize'}) as response:
                result = await _json_response(response, 'upload')
        file = result.get('file', {})
        deadline = time.monotonic() + PROCESSING_TIMEOUT
        while file.get('state') != 'ACTIVE':
            if file.get('state') == 'FAILED':
                raise MediaPreparationError('Gemini media processing failed')
            if time.monotonic() >= deadline:
                raise MediaPreparationError('Gemini media processing timed out')
            name = file.get('name', '')
            if not name.startswith('files/'):
                raise MediaPreparationError('Gemini media name is missing')
            await asyncio.sleep(2)
            async with session.get(API_ROOT + '/v1beta/' + name,
                                   headers={'x-goog-api-key': api_key}) as response:
                file = await _json_response(response, 'status')
        if not file.get('uri'):
            raise MediaPreparationError('Gemini media URI is missing')
        return file


async def prepare_contents(prompt, path, api_key, mime_type):
    contents = [(prompt or '').strip()] if (prompt or '').strip() else []
    if not path or not os.path.isfile(path) or os.path.getsize(path) <= 0:
        return contents
    if os.path.getsize(path) <= INLINE_LIMIT:
        with open(path, 'rb') as source:
            data = source.read(INLINE_LIMIT + 1)
        if len(data) > INLINE_LIMIT:
            raise MediaPreparationError('Media changed during preparation')
        contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        return contents
    # Hashing, uploading and waiting are serialized. The model timeout starts later.
    async with _upload_slots():
        digest = _sha256_file(path)
        key = (hashlib.sha256(api_key.encode()).hexdigest(), digest)
        cached = _files.get(key)
        if cached and cached[0] > time.monotonic():
            file = cached[1]
            _files.move_to_end(key)
        else:
            logging.info('[Services] Streaming media upload: %s bytes', os.path.getsize(path))
            try:
                file = await _upload(api_key, path, mime_type)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                raise MediaPreparationError(f'Gemini media transport failed: {type(exc).__name__}') from None
            _files[key] = (time.monotonic() + FILE_CACHE_TTL, file)
            while len(_files) > FILE_CACHE_SIZE:
                _files.popitem(last=False)
    contents.append(types.Part.from_uri(file_uri=file['uri'], mime_type=file.get('mimeType') or mime_type))
    return contents


async def count_text_tokens(text, api_key, model):
    timeout = aiohttp.ClientTimeout(total=15, connect=5)
    request = {'contents': [{'role': 'user', 'parts': [{'text': text}]}]}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(API_ROOT + '/v1beta/models/' + model.removeprefix('models/') + ':countTokens',
                                headers={'x-goog-api-key': api_key}, json=request) as response:
            data = await _json_response(response, 'token count')
    count = data.get('totalTokens')
    if not isinstance(count, int) or count < 0:
        raise MediaPreparationError('Gemini token count is missing')
    return count
