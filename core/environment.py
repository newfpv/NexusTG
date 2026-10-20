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


def mark_ready(bot_id, userbot_id=None, bot_username=None):
    profile = os.environ.get('NEXUS_PROFILE_DIR')
    if not profile:
        return
    from pathlib import Path
    path = Path(profile) / 'worker.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'ready': True, 'bot_id': bot_id,
        'userbot_id': userbot_id, 'bot_username': bot_username, 'at': time.time()}), encoding='utf-8')
    os.replace(temporary, path)
