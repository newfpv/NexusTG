"""Linux supervisor: preload dependencies once, isolate each account in a process."""
import argparse
import gc
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
from core.managed_accounts import atomic_json, manager_name, public_status, read_json

NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
DEPENDENCIES = ('aiogram', 'pyrogram', 'sqlalchemy', 'google.genai', 'aiohttp',
                'dotenv', 'yt_dlp', 'gallery_dl', 'qrcode')


def preload_dependencies():
    gc.disable()
    for name in DEPENDENCIES:
        importlib.import_module(name)
    native_tasks = Path('/proc/self/task')
    if native_tasks.exists() and len(list(native_tasks.iterdir())) != 1:
        gc.enable()
        return False
    gc.collect()
    gc.freeze()
    return hasattr(os, 'fork')


def read_profiles(root):
    profiles = {}
    seen_tokens = set()
    for directory in sorted(Path(root).iterdir()):
        if not directory.is_dir() or directory.is_symlink() or not NAME.fullmatch(directory.name):
            continue
        config_path = directory / 'account.json'
        env_path = directory / '.env'
        if not config_path.is_file() or not env_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
            if not config.get('enabled', True):
                continue
            from dotenv import dotenv_values
            token = (dotenv_values(env_path).get('TG_BOT_TOKEN') or '').strip()
            if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
                raise ValueError('invalid controller token format')
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            bot_identity = token.split(':',1)[0]
            if token_hash in seen_tokens or bot_identity in seen_tokens:
                raise ValueError('controller token already used by another account')
            seen_tokens.add(token_hash)
            seen_tokens.add(bot_identity)
            # Only fingerprints are retained in the parent supervisor.
            version = (int(config.get('generation', 0)), env_path.stat().st_mtime_ns)
            profiles[directory.name] = (directory, version)
        except (ValueError, OSError, TypeError):
            print(f'[supervisor] Invalid or duplicate profile: {directory.name}', flush=True)
    return profiles


def run_worker(profile, code_root):
    profile, code_root = Path(profile).resolve(), Path(code_root).resolve()
    gc.enable()
    os.chdir(profile)
    for key in ('TG_BOT_TOKEN', 'API_ID', 'API_HASH', 'LANG_FILE'):
        os.environ.pop(key, None)
    root=profile.parent
    metadata=read_json(profile/'account.json')
    os.environ.update(NEXUS_ENV_FILE=str(profile / '.env'), NEXUS_PROFILE_DIR=str(profile),
                      NEXUS_CODE_ROOT=str(code_root), NEXUS_ACCOUNT_NAME=profile.name,
                      NEXUS_ACCOUNTS_DIR=str(root), NEXUS_ACCOUNT_ROLE='manager' if manager_name(root)==profile.name else 'worker',
                      NEXUS_PROFILE_OWNER_ID=str(metadata.get('owner_id') or ''))
    for name in ('core', 'modules'):
        link = profile / name
        if not link.exists():
            link.symlink_to(code_root / name, target_is_directory=True)
        if not link.is_symlink() or link.resolve() != code_root / name:
            raise ValueError('Profile code path must point to the shared installation')
    (profile / 'data' / 'tmp').mkdir(parents=True, exist_ok=True)
    (profile / 'data' / 'home').mkdir(parents=True, exist_ok=True)
    os.environ.update(TMPDIR=str(profile / 'data/tmp'), HOME=str(profile / 'data/home'))
    import tempfile
    tempfile.tempdir = str(profile / 'data/tmp')
    sys.path.insert(0, str(code_root))
    import asyncio
    import main as application
    from core.diagnostics import install
    install()
    async def start():
        await application.bot.me()  # Validate this controller before declaring it ready.
        if read_json(root/'runtime.json').get('mode')!='single' or manager_name(root)!=profile.name:
            await application.main()
            return False
        async def promotion():
            while True:
                await asyncio.sleep(1)
                if (root/'promote.request').exists() and len(read_profiles(root))>1:
                    return
        task=asyncio.create_task(application.main());watcher=asyncio.create_task(promotion())
        done,_=await asyncio.wait([task,watcher],return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            watcher.cancel();await asyncio.gather(watcher,return_exceptions=True)
            await task
            return False
        try:
            if hasattr(application,'dp'):
                async with asyncio.timeout(3):await application.dp.stop_polling()
        except (RuntimeError,TimeoutError):pass
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        if hasattr(application,'stop_userbot'):
            try:
                async with asyncio.timeout(5):await application.stop_userbot()
            except Exception:pass
        if hasattr(application.bot,'session'):await application.bot.session.close()
        database=sys.modules.get('core.db')
        if database is not None:
            async with asyncio.timeout(5):await database.engine.dispose()
        return True
    try:
        return asyncio.run(start())
    except Exception:
        import logging
        logging.exception('[Runtime] Worker startup failed')
        raise


class Supervisor:
    def __init__(self, root, code_root, shared=True):
        self.root, self.code_root = Path(root).resolve(), Path(code_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.shared = shared
        self.workers = {}
        self.retries = {}
        self.selector = selectors.DefaultSelector()
        self.stopping = False

    def stop(self, *_):
        self.stopping = True

    def _signal_worker(self, worker, sig):
        try:os.killpg(worker['pid'], sig)
        except ProcessLookupError:pass

    def _start(self, name, profile, version):
        (profile / 'worker.json').unlink(missing_ok=True)
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        if self.shared:
            sys.stdout.flush();sys.stderr.flush()
            pid = os.fork()
            if pid == 0:
                try:
                    self.selector.close()
                    for worker in self.workers.values():
                        os.close(worker['fd'])
                    os.close(read_fd)
                    os.dup2(write_fd, 1);os.dup2(write_fd, 2);os.close(write_fd)
                    os.setsid()
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
                    signal.signal(signal.SIGINT, signal.SIG_DFL)
                    run_worker(profile, self.code_root)
                except BaseException:
                    import traceback;traceback.print_exc()
                finally:
                    sys.stdout.flush();sys.stderr.flush();os._exit(1)
        else:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', str(profile),
                '--code-root', str(self.code_root)], stdout=write_fd, stderr=write_fd, start_new_session=True)
            pid = process.pid
        os.close(write_fd)
        self.workers[name] = {'pid': pid, 'fd': read_fd, 'version': version, 'buffer': b'',
                              'started': time.monotonic(), 'stop_at': None}
        self.selector.register(read_fd, selectors.EVENT_READ, name)
        print(f'[supervisor] Started {name}, pid={pid}', flush=True)

    def _drain(self, name, worker):
        try:data = os.read(worker['fd'], 8192)
        except BlockingIOError:return
        if not data:return
        buffer = worker['buffer'] + data
        while b'\n' in buffer:
            line, buffer = buffer.split(b'\n', 1)
            print(f'[{name}] ' + line.decode('utf-8', errors='replace'), flush=True)
        # Log lines must not grow a per-account unbounded buffer.
        if len(buffer) > 65536:
            print(f'[{name}] ' + buffer[:65536].decode('utf-8', errors='replace'), flush=True)
            buffer = b''
        worker['buffer'] = buffer

    def _reap(self, desired):
        for name, worker in list(self.workers.items()):
            try:pid, status = os.waitpid(worker['pid'], os.WNOHANG)
            except ChildProcessError:pid, status = worker['pid'], 0
            if not pid:continue
            self._drain(name, worker)
            self.selector.unregister(worker['fd']);os.close(worker['fd'])
            self.workers.pop(name)
            if name in desired and worker['stop_at'] is None:
                previous = self.retries.get(name, (0, 0))[0]
                failures = previous + 1 if time.monotonic() - worker['started'] < 60 else 1
                self.retries[name] = (failures, time.monotonic() + min(60, 2 ** min(failures, 6)))
                print(f'[supervisor] {name} exited; retry scheduled', flush=True)
            else:
                self.retries.pop(name, None)

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        next_scan = 0
        desired = {}
        try:
            while not self.stopping:
                now = time.monotonic()
                if now >= next_scan:
                    desired = read_profiles(self.root)
                    next_scan = now + 2
                    for name, worker in list(self.workers.items()):
                        if name not in desired or desired[name][1] != worker['version']:
                            if worker['stop_at'] is None:
                                worker['stop_at'] = now
                                self._signal_worker(worker, signal.SIGTERM)
                    for name, (profile, version) in desired.items():
                        if name not in self.workers and now >= self.retries.get(name, (0, 0))[1]:
                            self._start(name, profile, version)
                    atomic_json(self.root/'status.json',public_status(self.root,self.workers))
                for _, worker in list(self.workers.items()):
                    if worker['stop_at'] is not None and now - worker['stop_at'] > 10:
                        self._signal_worker(worker, signal.SIGKILL)
                for key, _ in self.selector.select(timeout=.2):
                    worker = self.workers.get(key.data)
                    if worker:self._drain(key.data, worker)
                self._reap(desired)
        finally:
            for worker in self.workers.values():self._signal_worker(worker, signal.SIGTERM)
            deadline = time.monotonic() + 10
            while self.workers and time.monotonic() < deadline:
                for key, _ in self.selector.select(timeout=.1):
                    worker = self.workers.get(key.data)
                    if worker:self._drain(key.data, worker)
                self._reap({})
            for worker in self.workers.values():self._signal_worker(worker, signal.SIGKILL)
            self.selector.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=os.environ.get('NEXUS_ACCOUNTS_DIR', '/accounts'))
    parser.add_argument('--code-root', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        return run_worker(args.worker, args.code_root)
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    profiles = read_profiles(root)
    if profiles and manager_name(root) is None:
        primary='main' if 'main' in profiles else next(iter(profiles))
        atomic_json(root/'manager.json',{'profile':primary})
    (root/'promote.request').unlink(missing_ok=True)
    if len(profiles) == 1:
        name, (profile, _) = next(iter(profiles.items()))
        (root / 'runtime.json').write_text(json.dumps({'mode':'single','pid':os.getpid()}))
        print(f'[supervisor] Single account: {name}; no extra supervisor process', flush=True)
        if run_worker(profile, args.code_root):
            # Replace the direct worker only after its UI confirmed the addition.
            # exec closes its clients; the new supervisor then starts both profiles.
            os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve()),'--root',str(root),'--code-root',args.code_root])
        return
    shared = preload_dependencies()
    resource_root = root / '.resources'
    resource_root.mkdir(exist_ok=True)
    os.environ['NEXUS_RESOURCE_DIR'] = str(resource_root)
    (root / 'runtime.json').write_text(json.dumps({'mode':'shared' if shared else 'fallback','pid':os.getpid()}))
    print('[supervisor] Dependency preloading ready; mode=' + ('shared' if shared else 'isolated fallback'), flush=True)
    Supervisor(args.root, args.code_root, shared).run()

if __name__ == '__main__':main()
