"""Dependency-free account management CLI; bot setup continues in Telegram."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parent
ROOT = BASE / 'accounts'
NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,39}$')


def env_value(raw, name):
    for line in raw.splitlines():
        line = line.strip().removeprefix('export ')
        if line.startswith(name + '='):
            values = shlex.split(line.split('=',1)[1], comments=True)
            return values[0] if values else ''
    return ''


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:file.write(content)


def write_config(directory, config):
    path = directory / 'account.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def account_dirs():
    return [p for p in sorted(ROOT.iterdir()) if p.is_dir() and not p.is_symlink() and NAME.fullmatch(p.name)
            and (p/'account.json').is_file()] if ROOT.is_dir() else []


def enabled_count():
    return sum(json.loads((p/'account.json').read_text()).get('enabled', True) for p in account_dirs())


def compose_command():
    command = ['docker', 'compose', '-f', str(BASE/'compose.accounts.yml')]
    mounts = []
    for directory in account_dirs():
        config = json.loads((directory/'account.json').read_text())
        if config.get('host_data_dir'):
            source = Path(config['host_data_dir']).resolve()
            if not source.is_dir():raise ValueError('An imported account data directory is unavailable.')
            mounts.append({'type':'bind','source':str(source).replace('$','$$'),'target':f'/accounts/{directory.name}/data'})
    if mounts:
        path = BASE/'.accounts.mounts.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'services':{'accounts':{'volumes':mounts}}}),encoding='utf-8')
        os.replace(temporary,path)
        command += ['-f',str(path)]
    return command


def start(force=False, build=False):
    command = compose_command() + ['up', '-d']
    if build:command.append('--build')
    if force:command.append('--force-recreate')
    subprocess.run(command, cwd=BASE, check=True)


def runtime_mode():
    try:return json.loads((ROOT/'runtime.json').read_text())['mode']
    except (FileNotFoundError, ValueError, KeyError):return None


def add_account(name, raw_env, start_bot=True, enabled=True):
    if not NAME.fullmatch(name):raise ValueError('Use a–z, digits, _ or -; start with a letter or digit.')
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = ROOT/name
    if target.exists():raise ValueError('This account name already exists; no files were changed.')
    token = env_value(raw_env, 'TG_BOT_TOKEN')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):raise ValueError('Invalid TG_BOT_TOKEN format.')
    bot_id = token.split(':',1)[0]
    for directory in account_dirs():
        other = env_value((directory/'.env').read_text(encoding='utf-8'), 'TG_BOT_TOKEN')
        if other.split(':',1)[0] == bot_id:raise ValueError('This Telegram bot is already assigned to another profile.')
    target.mkdir(mode=0o700)
    (target/'data').mkdir(mode=0o700)
    write_private(target/'.env', raw_env.rstrip()+'\n')
    write_config(target, {'enabled': enabled, 'generation': 0})
    if not (ROOT/'manager.json').exists():
        primary='main' if any(p.name=='main' for p in account_dirs()) else account_dirs()[0].name
        (ROOT/'manager.json').write_text(json.dumps({'profile':primary}),encoding='utf-8')
    print(f'Created {name}. Open this Telegram bot and send /start; the setup wizard will guide you.')
    if start_bot:
        start(force=runtime_mode()=='single' and enabled_count()>1)
    return target


def change_account(name, action, start_bot=True):
    if not NAME.fullmatch(name):raise ValueError('Invalid account name.')
    directory = ROOT/name
    if directory not in account_dirs():raise ValueError('Account not found.')
    config = json.loads((directory/'account.json').read_text())
    if action=='restart':config['generation']=int(config.get('generation',0))+1
    else:config['enabled']=action=='enable'
    write_config(directory, config)
    if start_bot:
        count = enabled_count()
        if count==0:subprocess.run(compose_command()+['stop'],cwd=BASE,check=True)
        else:start(force=runtime_mode()=='single' or count==1)
    print(f'{name}: {action}')


def adopt_account(name, env_file, data_dir, legacy_container):
    source = Path(data_dir).resolve()
    if not source.is_dir() or not (source/'core_database.db').is_file():
        raise ValueError('Existing core_database.db was not found; no account was changed.')
    directory = add_account(name,Path(env_file).read_text(encoding='utf-8-sig'),False,enabled=False)
    config = json.loads((directory/'account.json').read_text())
    config['host_data_dir'] = str(source)
    write_config(directory,config)
    # Prepare the image while the old bot is still running.
    subprocess.run(compose_command()+['build'],cwd=BASE,check=True)
    was_running = subprocess.check_output(['docker','inspect',legacy_container,'--format','{{.State.Running}}'],text=True).strip()=='true'
    if was_running:subprocess.run(['docker','stop',legacy_container],check=True)
    try:
        config['enabled']=True;write_config(directory,config)
        start(force=runtime_mode()=='single' and enabled_count()>1)
        deadline=time.monotonic()+90
        expected_id=int(env_value((directory/'.env').read_text(),'TG_BOT_TOKEN').split(':',1)[0])
        while time.monotonic()<deadline:
            try:status=json.loads((directory/'worker.json').read_text())
            except (FileNotFoundError,ValueError):status={}
            if status.get('ready') and status.get('bot_id')==expected_id:
                print(f'{name}: imported; original data stays in {source}')
                return
            time.sleep(.5)
        raise RuntimeError('The imported account did not become ready.')
    except BaseException:
        config['enabled']=False;write_config(directory,config)
        if enabled_count():start(force=True)
        else:subprocess.run(compose_command()+['stop'],cwd=BASE,check=False)
        if was_running:subprocess.run(['docker','start',legacy_container],check=True)
        raise


def main():
    parser=argparse.ArgumentParser(description='NexusTG accounts: one installation, independent profiles.')
    sub=parser.add_subparsers(dest='command',required=True)
    add=sub.add_parser('add',help='Add an independent account; finish setup in Telegram')
    add.add_argument('name',nargs='?',default='main');add.add_argument('--env-file');add.add_argument('--no-start',action='store_true')
    adopt=sub.add_parser('adopt',help='Import an existing bot without copying or moving its data')
    adopt.add_argument('name');adopt.add_argument('--env-file',default=str(BASE/'.env'));adopt.add_argument('--data-dir',default=str(BASE/'data'));adopt.add_argument('--legacy-container',required=True)
    sub.add_parser('start');sub.add_parser('list');sub.add_parser('stop');sub.add_parser('update')
    for command in ['enable','disable','restart']:
        item=sub.add_parser(command);item.add_argument('name');item.add_argument('--no-start',action='store_true')
    logs=sub.add_parser('logs');logs.add_argument('name',nargs='?');logs.add_argument('-f','--follow',action='store_true')
    args=parser.parse_args()
    try:
        if args.command=='add':
            if args.env_file:raw=Path(args.env_file).read_text(encoding='utf-8-sig')
            else:
                token=getpass.getpass('Token from @BotFather (hidden): ').strip()
                language=input('Language RU/EN [RU]: ').strip().upper() or 'RU'
                if language not in {'RU','EN'}:raise ValueError('Choose RU or EN.')
                raw=f'TG_BOT_TOKEN={token}\nLANG_FILE=language_{language}.json\n'
            add_account(args.name,raw,not args.no_start)
        elif args.command=='adopt':adopt_account(args.name,args.env_file,args.data_dir,args.legacy_container)
        elif args.command in {'enable','disable','restart'}:change_account(args.name,args.command,not args.no_start)
        elif args.command in {'start','update'}:start(build=args.command=='update')
        elif args.command=='stop':subprocess.run(compose_command()+['stop'],cwd=BASE,check=True)
        elif args.command=='list':
            for directory in account_dirs():
                cfg=json.loads((directory/'account.json').read_text())
                try:status=json.loads((directory/'worker.json').read_text())
                except (FileNotFoundError,ValueError):status={}
                print(f'{directory.name}: '+('enabled' if cfg.get('enabled',True) else 'disabled')+
                      (f' · last worker PID {status["pid"]}' if 'pid' in status else ' · not started'))
        elif args.command=='logs':
            command=compose_command()+['logs','--no-log-prefix','--tail','100']+(['-f'] if args.follow else [])
            if args.name and not NAME.fullmatch(args.name):raise ValueError('Invalid account name.')
            process=subprocess.Popen(command,cwd=BASE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            try:
                for line in process.stdout:
                    if not args.name or runtime_mode()=='single' or f'[{args.name}]' in line:print(line,end='',flush=True)
            except KeyboardInterrupt:process.terminate()
            finally:process.wait()
    except (ValueError,OSError,RuntimeError,subprocess.CalledProcessError) as exc:
        print(str(exc),file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
