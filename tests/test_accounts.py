import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import accounts
from core import environment


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name,value in [('BASE',self.root),('ROOT',self.root/'accounts')]:
            p=patch.object(accounts,name,value);p.start();self.addCleanup(p.stop)

    def test_unique_accounts_private_files_and_no_overwrite(self):
        first=accounts.add_account('main','TG_BOT_TOKEN=123:fake\n',False)
        self.assertEqual(stat.S_IMODE((first/'.env').stat().st_mode),0o600)
        for name,raw in [('main','TG_BOT_TOKEN=456:fake'),('second','TG_BOT_TOKEN=123:different'),('../bad','TG_BOT_TOKEN=789:fake')]:
            with self.assertRaises(ValueError):accounts.add_account(name,raw,False)
        self.assertIn('123:fake',(first/'.env').read_text())

    def test_only_changed_profile_generation_changes(self):
        a=accounts.add_account('a','TG_BOT_TOKEN=123:fake',False)
        b=accounts.add_account('b','TG_BOT_TOKEN=456:fake',False)
        before=(b/'account.json').read_bytes()
        accounts.change_account('a','restart',False)
        self.assertEqual(json.loads((a/'account.json').read_text())['generation'],1)
        self.assertEqual(before,(b/'account.json').read_bytes())

    def test_external_data_uses_original_bind_mount(self):
        profile=accounts.add_account('main','TG_BOT_TOKEN=123:fake',False)
        data=self.root/'original-data';data.mkdir();(data/'original').write_text('private')
        accounts.write_config(profile,{'host_data_dir':str(data),'enabled':True})
        command=accounts.compose_command()
        generated=json.loads((self.root/'.accounts.mounts.json').read_text())
        self.assertIn(str(self.root/'.accounts.mounts.json'),command)
        volume=generated['services']['accounts']['volumes'][0]
        self.assertEqual(volume['source'],str(data));self.assertEqual(volume['target'],'/accounts/main/data')
        self.assertEqual((data/'original').read_text(),'private')

    def test_explicit_environment_cannot_redirect_to_other_profile(self):
        own=self.root/'.env';other=self.root/'other.env'
        own.write_text(f'TG_BOT_TOKEN=123:own\nNEXUS_ENV_FILE={other}\n')
        other.write_text('TG_BOT_TOKEN=456:other\n')
        with patch.dict(os.environ,{'NEXUS_ENV_FILE':str(own),'TG_BOT_TOKEN':'inherited'}):
            environment.load_environment();environment.load_environment()
            self.assertEqual(os.environ['TG_BOT_TOKEN'],'123:own')
            self.assertEqual(os.environ['NEXUS_ENV_FILE'],str(own))

if __name__=='__main__':unittest.main()
