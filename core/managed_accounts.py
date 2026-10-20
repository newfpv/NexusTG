"""Private profile operations and an explicit, non-sensitive status projection."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shlex
import time
import shutil

NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')
TOKEN = re.compile(r'^\d+:[A-Za-z0-9_-]{20,}$')


def read_json(path, default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):return {} if default is None else default


def atomic_json(path, value):
    path=Path(path);temporary=path.with_name(path.name+'.tmp')
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as target:
        json.dump(value,target,ensure_ascii=False);target.flush();os.fsync(target.fileno())
    os.replace(temporary,path)


def manager_name(root):
    name=read_json(Path(root)/'manager.json').get('profile')
    return name if isinstance(name,str) and NAME.fullmatch(name) else None


def is_manager():
    root=os.environ.get('NEXUS_ACCOUNTS_DIR')
    name=os.environ.get('NEXUS_ACCOUNT_NAME')
    return bool(root and name and os.environ.get('NEXUS_ACCOUNT_ROLE')=='manager' and manager_name(root)==name)


def require_root():
    if not is_manager():raise PermissionError('Account management is available in the primary bot only')
    root=Path(os.environ['NEXUS_ACCOUNTS_DIR']).resolve()
    if not root.is_dir():raise ValueError('Account runtime unavailable')
    return root


def profile_dirs(root):
    return [p for p in sorted(Path(root).iterdir()) if p.is_dir() and not p.is_symlink() and NAME.fullmatch(p.name) and (p/'account.json').is_file()]


@contextmanager
def registry_lock(root):
    import fcntl
    with open(Path(root)/'.manager.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def token_identity(path):
    try:
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('TG_BOT_TOKEN='):
                values=shlex.split(line.split('=',1)[1],comments=True)
                return values[0].split(':',1)[0] if values else None
    except (OSError,ValueError):pass
    return None


def create_account(token, bot_id, username, owner_id=None, language='RU', display_name=None):
    root=require_root()
    if not TOKEN.fullmatch(token) or str(bot_id)!=token.split(':',1)[0]:raise ValueError('Invalid bot token')
    if owner_id is not None and (not isinstance(owner_id,int) or isinstance(owner_id,bool) or not 0<owner_id<2**63):raise ValueError('Invalid owner')
    if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username or ''):raise ValueError('Invalid bot username')
    name='bot_'+str(bot_id)
    with registry_lock(root):
        if any(token_identity(p/'.env')==str(bot_id) for p in profile_dirs(root)):raise FileExistsError('Bot already registered')
        target=root/name
        if target.exists():raise FileExistsError('Profile already exists')
        target.mkdir(mode=0o700);(target/'data').mkdir(mode=0o700)
        try:
            descriptor=os.open(target/'.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w') as file:file.write(f'TG_BOT_TOKEN={token}\nLANG_FILE=language_{language if language in {"RU","EN"} else "RU"}.json\n')
            metadata={'enabled':True,'generation':0,'bot_username':username,'created_at':time.time()}
            if owner_id is not None:metadata['owner_id']=owner_id  # Retain explicit CLI/backward-compatible binding.
            if display_name:metadata['bot_name']=clean_name(display_name)
            atomic_json(target/'account.json',metadata)
        except BaseException:
            # Only this unpublished, newly-created empty profile may be rolled back.
            (target/'.env').unlink(missing_ok=True)
            (target/'account.json.tmp').unlink(missing_ok=True)
            (target/'data').rmdir();target.rmdir()
            raise
    return name


def request_promotion():
    root=require_root()
    if read_json(root/'runtime.json').get('mode')=='single':
        atomic_json(root/'promote.request',{'at':time.time()})


def change_account(name, action):
    root=require_root()
    if not NAME.fullmatch(name or '') or name==manager_name(root):raise PermissionError('The primary bot is managed on the server')
    path=root/name
    if path.is_symlink() or path not in profile_dirs(root):raise ValueError('Unknown profile')
    if action not in {'enable','disable','restart'}:raise ValueError('Unknown operation')
    with registry_lock(root):
        cfg=read_json(path/'account.json')
        if action=='restart':cfg['generation']=int(cfg.get('generation',0))+1
        else:cfg['enabled']=action=='enable'
        atomic_json(path/'account.json',cfg)


def public_status(root, workers, restarts=None):
    """Never include owner IDs, keys, sessions, prompts, phones, paths or chat data."""
    result=[]
    for path in profile_dirs(root):
        cfg=read_json(path/'account.json');worker=workers.get(path.name,{})
        record=read_json(path/'worker.json')
        running=bool(worker.get('pid'))
        ready=bool(running and record.get('pid')==worker.get('pid') and record.get('ready'))
        username=cfg.get('bot_username') or record.get('bot_username')
        if not re.fullmatch(r'[A-Za-z0-9_]{5,32}',username or ''):username=None
        display=clean_name(cfg.get('display_name') or record.get('account_name') or cfg.get('account_name') or cfg.get('bot_name') or '')
        result.append({'name':path.name,'username':username,'display_name':display,'enabled':bool(cfg.get('enabled',True)),
                       'running':running,'ready':ready,'primary':path.name==manager_name(root)})
    return {'at':time.time(),'bots':result}


def status():
    root=require_root();snapshot=read_json(root/'status.json')
    if read_json(root/'runtime.json').get('mode')=='single':
        return public_status(root,{os.environ['NEXUS_ACCOUNT_NAME']:{'pid':os.getpid()}})
    if time.time()-snapshot.get('at',0)>12:return {'at':0,'bots':public_status(root,{})['bots']}
    # Re-project even a modified status file through the explicit public schema.
    # Rebuild the public schema from authoritative metadata; renames apply immediately.
    workers={row['name']:{'pid':read_json(root/row['name']/'worker.json').get('pid')} for row in snapshot.get('bots',[])
             if isinstance(row,dict) and NAME.fullmatch(str(row.get('name',''))) and row.get('running')}
    return public_status(root,workers)


def clean_name(value):
    if not isinstance(value,str):return ''
    import unicodedata
    return ' '.join(''.join(ch for ch in value if not unicodedata.category(ch).startswith('C')).split())[:64]


def profile(name):
    root=require_root()
    if not NAME.fullmatch(name or ''):raise ValueError('Unknown profile')
    path=root/name
    if path not in profile_dirs(root):raise ValueError('Unknown profile')
    return path


def rename_account(name, value):
    path=profile(name);value=clean_name(value)
    if not value:raise ValueError('Empty name')
    with registry_lock(path.parent):
        cfg=read_json(path/'account.json');cfg['display_name']=value;atomic_json(path/'account.json',cfg)


def set_archive_limit(name, limit):
    from core.archive_storage import MAX_LIMIT
    if isinstance(limit,bool) or not isinstance(limit,int) or not 0<=limit<=MAX_LIMIT:raise ValueError('Invalid quota')
    path=profile(name)
    with registry_lock(path.parent):
        cfg=read_json(path/'account.json');cfg['archive_limit_bytes']=limit;atomic_json(path/'account.json',cfg)


def storage_info(name):
    from core.archive_storage import account_limit
    path=profile(name);usage=read_json(path/'data/archive_usage.json')
    def number(key):
        value=usage.get(key,0)
        return value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else 0
    return {'media_bytes':number('media_bytes'),'text_db_bytes':number('text_db_bytes'),
            'files':number('files'),'limit_bytes':account_limit(path),'at':usage.get('at',0)}


def storage_totals():
    root=require_root();result={'media_bytes':0,'text_db_bytes':0,'files':0}
    for path in profile_dirs(root):
        usage=storage_info(path.name)
        for key in result:result[key]+=usage[key]
    disk=shutil.disk_usage(root)
    return {**result,'disk_free':disk.free,'disk_used':disk.used,'disk_total':disk.total}
