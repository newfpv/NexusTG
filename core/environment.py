"""Load only the selected account's environment file."""
import os
import json
import time
from dotenv import load_dotenv

PINNED = ('NEXUS_ENV_FILE', 'NEXUS_CODE_ROOT', 'NEXUS_ACCOUNT_NAME', 'NEXUS_PROFILE_DIR', 'NEXUS_RESOURCE_DIR',
          'NEXUS_ACCOUNTS_DIR', 'NEXUS_ACCOUNT_ROLE', 'NEXUS_PROFILE_OWNER_ID')

def load_environment():
    selected = {name: os.environ[name] for name in PINNED if name in os.environ}
    load_dotenv(dotenv_path=os.environ.get('NEXUS_ENV_FILE', '.env'), override=True)
    # A profile file cannot redirect subsequent loads to a different account.
    os.environ.update(selected)


def bind_claimed_owner(owner_id):
    """Persist the first managed owner independently of resettable settings."""
    selected=os.environ.get('NEXUS_PROFILE_DIR')
    if not selected:return
    from pathlib import Path
    from core.managed_accounts import atomic_json,read_json,registry_lock
    profile=Path(selected).resolve()
    root=Path(os.environ['NEXUS_ACCOUNTS_DIR']).resolve()
    if profile.parent!=root or profile.name!=os.environ.get('NEXUS_ACCOUNT_NAME') or not (profile/'account.json').is_file():
        raise RuntimeError('Invalid selected account profile')
    with registry_lock(root):
        path=profile/'account.json';metadata=read_json(path)
        if metadata.get('owner_id') not in (None,owner_id):
            raise RuntimeError('Profile owner does not match its database')
        metadata['owner_id']=owner_id
        atomic_json(path,metadata)
    os.environ['NEXUS_PROFILE_OWNER_ID']=str(owner_id)


def mark_ready(bot_id, userbot_id=None, bot_username=None, account_name=None):
    profile = os.environ.get('NEXUS_PROFILE_DIR')
    if not profile:
        return
    from pathlib import Path
    path = Path(profile) / 'worker.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'ready': True, 'bot_id': bot_id,
        'userbot_id': userbot_id, 'bot_username': bot_username, 'account_name': account_name, 'at': time.time()}), encoding='utf-8')
    os.replace(temporary, path)
