"""Offline single-to-shared transition, pinned roles and independent owner checks."""
from pathlib import Path
import json,os,subprocess,sys,tempfile,time
sys.path.insert(0,'/app')
from core import managed_accounts as service

def wait(predicate,seconds=75):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.2)
    raise AssertionError('Timed out waiting for managed runtime')

def record(profile):return service.read_json(profile/'probe.json')

with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary)/'accounts';root.mkdir();code=Path(temporary)/'code';code.mkdir()
    for name in ['core','modules']:(code/name).mkdir()
    (code/'main.py').write_text('''import asyncio,json,os,sys
from pathlib import Path
from core.environment import load_environment
load_environment()
class Bot:
    async def me(self):return None
bot=Bot()
async def main():
    p=Path.cwd()
    value={'pid':os.getpid(),'role':os.environ['NEXUS_ACCOUNT_ROLE'],'owner':os.environ['NEXUS_PROFILE_OWNER_ID'],'token':os.environ['TG_BOT_TOKEN']}
    (p/'probe.json').write_text(json.dumps(value))
    (p/'worker.json').write_text(json.dumps({'pid':os.getpid(),'ready':True,'bot_username':'primary_bot' if p.name=='main' else 'child_bot','userbot_id':987654321}))
    while True:await asyncio.sleep(1)
''')
    primary=root/'main';primary.mkdir();(primary/'data').mkdir()
    (primary/'data/private').write_text('private-main-archive')
    (primary/'.env').write_text('TG_BOT_TOKEN=111:private-main\n')
    service.atomic_json(primary/'account.json',{'enabled':True,'generation':0,'owner_id':7})
    process=subprocess.Popen([sys.executable,'/app/account_runtime.py','--root',str(root),'--code-root',str(code)],cwd='/app')
    try:
        initial=wait(lambda:record(primary))
        assert initial['pid']==process.pid and initial['role']=='manager' and initial['owner']=='7'
        os.environ.update(NEXUS_ACCOUNT_ROLE='manager',NEXUS_ACCOUNT_NAME='main',NEXUS_ACCOUNTS_DIR=str(root))
        name=service.create_account('22222:'+'private-child-'*3,22222,'child_bot',99)
        child=root/name
        with (child/'.env').open('a') as file:file.write('NEXUS_ACCOUNT_ROLE=manager\nNEXUS_PROFILE_OWNER_ID=7\nNEXUS_ACCOUNTS_DIR=/elsewhere\n')
        service.request_promotion()
        wait(lambda:service.read_json(root/'runtime.json').get('mode')=='shared')
        c=wait(lambda:record(child))
        m=wait(lambda:record(primary).get('pid') not in {None,initial['pid']} and record(primary))
        assert c['role']=='worker' and c['owner']=='99' and c['token'].startswith('22222:')
        assert m['role']=='manager' and m['owner']=='7'
        assert not (child/'data/private').exists() and (primary/'data/private').read_text()=='private-main-archive'
        snapshot=wait(lambda:service.read_json(root/'status.json').get('bots'))
        text=json.dumps(snapshot)
        for private in ['private-main','private-child','owner_id','987654321','session','api_keys']:assert private not in text
        service.change_account(name,'disable');wait(lambda:not Path(f'/proc/{c["pid"]}').exists())
        assert record(primary)['pid']==m['pid']
        service.change_account(name,'enable');wait(lambda:record(child).get('pid') not in {None,c['pid']})
        assert record(primary)['pid']==m['pid']
        print('PASS: direct primary promotes to shared runtime; added bot has its own owner, token and data; role spoof rejected; status excludes private fields; worker controls do not restart primary.',flush=True)
    finally:
        process.terminate();process.wait(timeout=20)

    # A failure before polling must still be available in the safe manager log.
    failure=root/'failure';failure.mkdir();(failure/'data').mkdir()
    (failure/'.env').write_text('TG_BOT_TOKEN=333:private-failure-token\n')
    service.atomic_json(failure/'account.json',{'enabled':True})
    (code/'main.py').write_text('''import os
from core.environment import load_environment
load_environment()
class Bot:
    async def me(self):raise ConnectionError(os.environ['TG_BOT_TOKEN'])
bot=Bot()
''')
    failed=subprocess.run([sys.executable,'/app/account_runtime.py','--worker',str(failure),'--code-root',str(code)],cwd='/app',capture_output=True,text=True)
    assert failed.returncode!=0
    safe=(failure/'data/logs/diagnostics.jsonl').read_text()
    assert 'ConnectionError' in safe and 'private-failure-token' not in safe and '333:' not in safe
    print('PASS: startup failures before polling are recorded without token/exception contents.',flush=True)
