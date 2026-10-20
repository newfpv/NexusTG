"""Account-local media quota. Text databases are measured, never pruned."""
import json
import os
from pathlib import Path
import stat
import time
import re
import shutil

GIB = 1024 ** 3
DEFAULT_LIMIT = 2 * GIB
MAX_LIMIT = 1024 * GIB


def valid_limit(value):
    if isinstance(value, bool):
        return DEFAULT_LIMIT
    try:
        return max(0, min(MAX_LIMIT, int(value)))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_LIMIT


def account_limit(profile=None):
    profile = Path(profile or os.environ.get('NEXUS_PROFILE_DIR') or '.')
    try:
        config = json.loads((profile / 'account.json').read_text(encoding='utf-8'))
        return valid_limit(config.get('archive_limit_bytes', DEFAULT_LIMIT))
    except (OSError, ValueError, AttributeError):
        return DEFAULT_LIMIT


def media_files(cache, forwarded=()):
    """Do not follow symlinks, including symlinked directories."""
    cache = Path(cache)
    if cache.is_symlink() or not cache.is_dir():
        return []
    result = []
    forwarded={str(Path(path).absolute()) for path in forwarded}
    for directory, folders, files in os.walk(cache, followlinks=False):
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            protected = bool(re.search(r'_(?:voice|video_note)(?:\.[a-zA-Z0-9]+)?$', name))
            shared = '_fwd_' in name or str(path.absolute()) in forwarded
            tier = 0 if shared else 3 if protected else 1 if info.st_size > 1024 ** 2 else 2
            result.append((tier, info.st_mtime_ns, str(path), info.st_size))
    return result


def prune_media(cache, limit, reserve=0, free_floor=64 * 1024 ** 2, forwarded=()):
    """Shared posts first, then heavy files; original voice/round videos last."""
    cache = Path(cache)
    limit = valid_limit(limit)
    rows = media_files(cache, forwarded)
    total = sum(row[3] for row in rows)
    budget = max(0, limit - max(0, reserve))
    # Avoid exhausting the filesystem even if the configured quota is large.
    try:
        info = os.statvfs(cache)
        free = info.f_bavail * info.f_frsize
    except (OSError, AttributeError):
        free = free_floor + reserve
    removed = []
    for _, _, filename, size in sorted(rows):
        if total <= budget and free >= free_floor + reserve:
            break
        path = Path(filename)
        try:
            if path.is_symlink():
                continue
            path.unlink()
        except OSError:
            continue
        total -= size
        free += size
        removed.append(filename)
    return {'media_bytes': total, 'files': len(rows) - len(removed), 'removed': removed,
            'limit_bytes': limit, 'at': time.time()}


def publish_usage(cache, database, limit, destination='data/archive_usage.json'):
    rows = media_files(cache)
    path = Path(database)
    db_bytes = 0
    for suffix in ('', '-wal', '-shm', '-journal'):
        candidate = Path(str(path) + suffix)
        try:
            if not candidate.is_symlink():
                db_bytes += candidate.stat().st_size
        except OSError:
            pass
    data = {'media_bytes': sum(row[3] for row in rows), 'files': len(rows),
            'text_db_bytes': db_bytes, 'limit_bytes': valid_limit(limit), 'at': time.time()}
    from core.managed_accounts import atomic_json
    atomic_json(destination, data)
    return data


def cleanup_staging(cache):
    """Only abandoned saver-owned download folders, before this worker starts."""
    parent=Path(cache).parent/'tmp'
    if not parent.is_dir() or parent.is_symlink():return
    intended=parent.resolve()
    for path in parent.glob('saver_*'):
        if path.is_dir() and not path.is_symlink() and path.resolve().parent==intended:
            shutil.rmtree(path)


def repair_media_path(cache, stored, chat_id, message_id):
    """Resolve a legacy path only to this account's matching cached filename."""
    cache=Path(cache)
    if cache.is_symlink():return '',False
    name=str(stored or '').replace('\\','/').rsplit('/',1)[-1]
    if not name.startswith(f'{chat_id}_{message_id}_'):return '',False
    target=cache/name
    if not target.is_file() or target.is_symlink() or target.resolve().parent!=cache.resolve():return '',False
    previous=Path(stored).resolve()
    return str(target),previous!=target.resolve()
