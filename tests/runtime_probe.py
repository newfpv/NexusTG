"""Offline process lifecycle and shared media-slot integration checks."""
import asyncio,json,os,signal,subprocess,sys,tempfile,time
from pathlib import Path
sys.path.insert(0,'/app')
import account_runtime as runtime

def wait(predicate,seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=predicate()
        if value:return value
        time.sleep(.1)
    raise AssertionError('Timed out waiting for worker lifecycle')

def report(directory):
    try:return json.loads((directory/'probe.json').read_text())
    except (FileNotFoundError,ValueError):return {}

def add(root,name,identity):
    directory=root/name;directory.mkdir();(directory/'data').mkdir()
    (directory/'.env').write_text(f'TG_BOT_TOKEN={identity}:private-{name}\n')
    (directory/'account.json').write_text(json.dumps({'enabled':True,'generation':0}))
    (directory/'data'/'deleted.txt').write_text('archive-'+name)
    return directory

def supervisor_probe(base):
    root=base/'profiles';root.mkdir();code=base/'code';code.mkdir()
    for name in ['core','modules']:(code/name).mkdir()
    (code/'main.py').write_text('''import asyncio,json,os,sys
from pathlib import Path
from dotenv import dotenv_values
os.environ.update(dotenv_values(os.environ['NEXUS_ENV_FILE']))
class Bot:
    async def me(self):return None
bot=Bot()
async def main():
    own=Path.cwd()
    value={'pid':os.getpid(),'name':os.environ['NEXUS_ACCOUNT_NAME'],'token':os.environ['TG_BOT_TOKEN'],
           'archive':(own/'data/deleted.txt').read_text(),'home':os.environ['HOME'],'tmp':os.environ['TMPDIR'],
           'preloaded':all(k in sys.modules for k in ['aiogram','yt_dlp','gallery_dl','google.genai'])}
    (own/'probe.json').write_text(json.dumps(value))
    while True:await asyncio.sleep(1)
''')
    alpha=add(root,'alpha',100);beta=add(root,'beta',200)
    assert runtime.preload_dependencies(), 'Fork unexpectedly unsafe in isolated test'
    pid=os.fork()
    if not pid:
        try:runtime.Supervisor(root,code,shared=True).run()
        finally:os._exit(0)
    try:
        a=wait(lambda:report(alpha));b=wait(lambda:report(beta))
        for directory,item in [(alpha,a),(beta,b)]:
            assert item['preloaded'] and item['name']==directory.name
            assert item['archive']=='archive-'+directory.name
            assert item['token'].endswith('private-'+directory.name)
            assert item['home']==str(directory/'data/home') and item['tmp']==str(directory/'data/tmp')
        os.kill(a['pid'],signal.SIGKILL)
        wait(lambda:report(alpha).get('pid') not in {None,a['pid']})
        assert report(beta)['pid']==b['pid'],'Other account restarted after crash'
        gamma=add(root,'gamma',300);g=wait(lambda:report(gamma))
        (beta/'account.json').write_text(json.dumps({'enabled':False,'generation':0}))
        wait(lambda:not Path(f'/proc/{b["pid"]}').exists())
        old=report(alpha)['pid']
        (gamma/'.env').write_text('TG_BOT_TOKEN=301:changed\n')
        wait(lambda:report(gamma).get('pid') not in {None,g['pid']})
        assert report(gamma)['token']=='301:changed'
        assert report(alpha)['pid']==old
        print('PASS: preloaded dependencies, per-account paths/env/archive, crash isolation, hot add/disable/env restart',flush=True)
    finally:
        os.kill(pid,signal.SIGTERM);os.waitpid(pid,0)
    # One account runs directly in the entrypoint process.
    solo=base/'solo';solo.mkdir();directory=add(solo,'single',400)
    process=subprocess.Popen([sys.executable,'/app/account_runtime.py','--root',str(solo),'--code-root',str(code)])
    try:
        value=wait(lambda:report(directory))
        assert value['pid']==process.pid
        assert json.loads((solo/'runtime.json').read_text())['mode']=='single'
        print('PASS: single account has no supervisor process',flush=True)
    finally:process.terminate();process.wait(timeout=15)

def media_probe(base):
    os.environ['NEXUS_RESOURCE_DIR']=str(base/'locks')
    os.environ['NEXUS_GLOBAL_MEDIA_SLOTS']='2'
    script='''import asyncio,sys,time
from pathlib import Path
from core.shared_resources import media_slot
async def main():
    async with media_slot():
        p=Path(sys.argv[1]);p.write_text(str(time.monotonic()))
        await asyncio.sleep(.4)
        p.with_suffix('.end').write_text(str(time.monotonic()))
asyncio.run(main())
'''
    jobs=[subprocess.Popen([sys.executable,'-c',script,str(base/f'slot{i}.start')],cwd='/app') for i in range(5)]
    for job in jobs:assert job.wait(timeout=15)==0
    events=[]
    for i in range(5):
        events.extend([(float((base/f'slot{i}.start').read_text()),1),(float((base/f'slot{i}.end').read_text()),-1)])
    count=peak=0
    for _,delta in sorted(events):count+=delta;peak=max(peak,count)
    assert peak==2,peak
    async def cancelled():
        from core.shared_resources import media_slot
        entered=asyncio.Event()
        async def holder():
            async with media_slot():entered.set();await asyncio.Event().wait()
        one=asyncio.create_task(holder());await entered.wait();one.cancel()
        try:await one
        except asyncio.CancelledError:pass
        async with asyncio.timeout(1):
            async with media_slot():
                async with media_slot():pass
    asyncio.run(cancelled())
    print('PASS: cross-process heavy media peak=2; cancelled tasks release slots',flush=True)

if __name__=='__main__':
    with tempfile.TemporaryDirectory() as directory:
        base=Path(directory);supervisor_probe(base);media_probe(base)
