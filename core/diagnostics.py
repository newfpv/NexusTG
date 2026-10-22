"""A bounded diagnostic log with no free-form log messages or exception text."""
from collections import deque
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re

LEVELS = {'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
ERRORS = ('FLOOD_WAIT', 'PEER_FLOOD', 'USER_RESTRICTED', 'USER_DEACTIVATED',
          'AUTH_KEY_DUPLICATED', 'AUTH_KEY_UNREGISTERED', 'SESSION_REVOKED',
          'SESSION_EXPIRED', 'PHONE_NUMBER_BANNED', 'API_KEY_INVALID',
          'PEER_ID_INVALID', 'CHAT_WRITE_FORBIDDEN', 'CHAT_ADMIN_REQUIRED',
          'MESSAGE_TOO_LONG', 'MESSAGE_NOT_MODIFIED', 'FILE_REFERENCE_EXPIRED',
          'TimeoutError', 'ConnectionError', 'OSError', 'OperationalError',
          'TelegramNetworkError', 'TelegramRetryAfter', 'TelegramBadRequest',
          'FloodWait', 'CancelledError', 'ValueError', 'TypeError', 'KeyError',
          'TelegramUnauthorizedError', 'Unauthorized', 'AuthKeyUnregistered', 'SessionRevoked','MediaDownloadError')
EVENTS = ('startup', 'connected', 'polling', 'timeout', 'retry', 'cooldown',
          'cleanup', 'quota', 'download', 'upload', 'callback', 'failed', 'error')
IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,79}$')


def project(record):
    """Whitelist fields: never export the original message, args or traceback."""
    if not isinstance(record, dict) or record.get('level') not in LEVELS:
        return None
    safe = {'at': str(record.get('at', ''))[:24], 'level': record['level']}
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', safe['at']):
        return None
    for key in ('module', 'function'):
        value = record.get(key, '')
        safe[key] = value if isinstance(value, str) and IDENTIFIER.fullmatch(value) else 'unknown'
    safe['line'] = max(0, min(100000, int(record.get('line', 0))))
    safe['event'] = record.get('event') if record.get('event') in EVENTS else 'runtime'
    safe['error'] = record.get('error') if record.get('error') in ERRORS else ''
    code = record.get('http')
    if code in (400, 401, 403, 404, 408, 413, 429, 500, 502, 503, 504):
        safe['http'] = code
    return safe


class SafeDiagnostics(logging.Handler):
    def __init__(self, path):
        super().__init__(logging.INFO)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.target = RotatingFileHandler(path, maxBytes=128 * 1024, backupCount=2, encoding='utf-8')
        os.chmod(path, 0o600)

    def emit(self, record):
        try:
            # Messages are inspected solely to select constants. Nothing is copied.
            message = record.getMessage()
            if record.levelno < logging.WARNING and not any(x in message.lower() for x in ('started', 'startup', 'connected', 'polling')):
                return
            lower = message.lower()
            event = next((name for name in EVENTS if name in lower), 'runtime')
            error = next((name for name in ERRORS if re.search(r'\b' + name + r'\b', message, re.IGNORECASE)), '')
            if not error:
                phrases={'caption is too long':'MESSAGE_TOO_LONG','message is too long':'MESSAGE_TOO_LONG',
                         'too many requests':'FLOOD_WAIT','database is locked':'OperationalError',
                         'not enough rights':'CHAT_ADMIN_REQUIRED','timed out':'TimeoutError'}
                error=next((code for phrase,code in phrases.items() if phrase in lower),'')
            if record.exc_info and record.exc_info[0].__name__ in ERRORS:
                error = record.exc_info[0].__name__
            http = re.search(r'\b(?:HTTP(?: status)?[ :=/]*|status(?:_code)?[ :=]*)(400|401|403|404|408|413|429|500|502|503|504)\b', message, re.I)
            row = project({'at': datetime.fromtimestamp(record.created, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                           'level': record.levelname, 'module': record.module, 'function': record.funcName,
                           'line': record.lineno, 'event': event, 'error': error,
                           'http': int(http[1]) if http else None})
            if row:
                output = logging.LogRecord('diagnostics', logging.INFO, '', 0,
                                           json.dumps(row, ensure_ascii=True), (), None)
                self.target.emit(output)
        except Exception:
            # Diagnostics must not stop a worker or recursively log secrets.
            pass

    def close(self):
        self.target.close()
        super().close()


def install():
    logger = logging.getLogger()
    if not any(isinstance(handler, SafeDiagnostics) for handler in logger.handlers):
        logger.addHandler(SafeDiagnostics('data/logs/diagnostics.jsonl'))


def recent(profile, limit=60):
    """Re-project files on read as well; do not expose raw Docker logs."""
    path = Path(profile) / 'data/logs/diagnostics.jsonl'
    result = deque(maxlen=max(1, min(200, limit)))
    for file in (Path(str(path) + '.2'), Path(str(path) + '.1'), path):
        if file.is_symlink():
            continue
        try:
            # Read a bounded tail, even if a local file was unexpectedly enlarged.
            with file.open('rb') as source:
                source.seek(0, 2)
                start = max(0, source.tell() - 128 * 1024)
                source.seek(start)
                if start:
                    source.readline()
                lines = source.read(128 * 1024).decode('utf-8', errors='replace').splitlines()
            for line in lines:
                try:
                    row = project(json.loads(line))
                    if row:
                        result.append(row)
                except (ValueError, TypeError, OverflowError):
                    continue
        except OSError:
            continue
    return list(result)


def format_rows(rows):
    return '\n'.join(f"{r['at']} {r['level']} {r['module']}.{r['function']}:{r['line']} {r['event']} {r['error']}" +
                     (f" HTTP {r['http']}" if r.get('http') else '') for r in rows)
