"""Resume deletion of batch originals only after their replacement was accepted."""
import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path


def save(path,todo):
    path.parent.mkdir(parents=True,exist_ok=True)
    os.chmod(path.parent,0o700)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(todo),encoding='utf-8')
    os.chmod(temp,0o600)
    os.replace(temp,path)


async def delete_batch(messages,root='data/batch_cleanup'):
    todo=[(message.chat.id,message.id) for message in messages]
    if not todo:return
    key=hashlib.sha256(json.dumps(todo).encode()).hexdigest()
    path=Path(root)/(key+'.json')
    save(path,todo)
    for message in messages:
        result=await message.delete()
        if result is None or result is False:return
        todo=todo[1:]
        if todo:save(path,todo)
        else:path.unlink(missing_ok=True)


async def recover(app,root='data/batch_cleanup'):
    while not getattr(app,'is_initialized',False):await asyncio.sleep(1)
    for path in Path(root).glob('*.json'):
        try:
            todo=json.loads(path.read_text(encoding='utf-8'))
            while todo:
                chat,mid=todo[0]
                if type(chat) is not int or type(mid) is not int:raise ValueError('Invalid cleanup record')
                result=await app.delete_messages(chat,[mid])
                if result is None or result is False:break
                todo=todo[1:]
                if todo:save(path,todo)
                else:path.unlink(missing_ok=True)
        except asyncio.CancelledError:raise
        except Exception:
            logging.warning('[Struct] Confirmed replacement cleanup paused',exc_info=True)
