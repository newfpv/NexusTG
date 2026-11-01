"""Bounded downloader subprocess; cancellation stops extractors and ffmpeg too."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys

DOWNLOAD_TIMEOUT = 240
RESULT_NAME = 'download-result.json'


def worker_command(url, workdir):
    return [sys.executable, '-m', 'core.downloader_worker', url, str(workdir)]


async def run_media_worker(url, workdir, timeout=DOWNLOAD_TIMEOUT):
    root = Path(workdir).resolve()
    with (root / 'download-worker.log').open('wb') as log:
        process = await asyncio.create_subprocess_exec(
            *worker_command(url, root), stdout=log, stderr=log,
            start_new_session=os.name != 'nt',
        )
        try:
            await asyncio.wait_for(process.wait(), timeout)
        finally:
            if process.returncode is None:
                try:
                    if os.name != 'nt':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        if process.returncode:
            raise RuntimeError(f'Downloader worker exited with code {process.returncode}')
    result_path = root / RESULT_NAME
    if not result_path.exists():
        return None
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if not result:
        return None
    if result.get('kind') not in {'video', 'media'} or not result.get('paths'):
        raise ValueError('Invalid downloader result')
    for name in result['paths']:
        path = Path(name)
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
            raise ValueError('Downloader result is outside its temporary directory')
    return result


if __name__ == '__main__':
    from dataclasses import asdict
    from modules.media_downloader import _download_media_sync
    url, directory = sys.argv[1:]
    result = _download_media_sync(url, directory)
    (Path(directory) / RESULT_NAME).write_text(
        json.dumps(asdict(result) if result else None, ensure_ascii=False), encoding='utf-8',
    )
