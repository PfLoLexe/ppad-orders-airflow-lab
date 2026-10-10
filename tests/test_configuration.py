"""Запуск с новым набором рядом с эталоном и сохранение секретов."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class ConfigurationTests(unittest.TestCase):
    def test_explicit_manifest_and_dataset_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'scripts').mkdir()
            shutil.copy(ROOT/'scripts/configure.py',root/'scripts/configure.py')
            for name in ('default','large'):
                folder=root/'data/snapshots'/name
                folder.mkdir(parents=True)
                (folder/'manifest.json').write_text(json.dumps({'dataset_id':name}))
            env=dict(os.environ,COMPOSE_PROJECT_NAME='orders-lab-large')
            def run(*args):
                return subprocess.run([sys.executable,str(root/'scripts/configure.py'),*args],env=env,capture_output=True,text=True)
            self.assertNotEqual(run().returncode,0)
            selected='data/snapshots/large/manifest.json'
            result=run('--manifest',selected)
            self.assertEqual(result.returncode,0,result.stderr)
            original=(root/'.env').read_bytes()
            values=dict(line.split('=',1) for line in original.decode().splitlines())
            self.assertEqual(values['DATASET_MANIFEST'],selected)
            self.assertEqual(values['COMPOSE_PROJECT_NAME'],'orders-lab-large')
            self.assertEqual(run('--manifest',selected).returncode,0)
            self.assertEqual((root/'.env').read_bytes(),original)
            self.assertNotEqual(run('--manifest','data/snapshots/default/manifest.json').returncode,0)
            self.assertEqual((root/'.env').read_bytes(),original)
            auth=json.loads((root/'private/passwords.json').read_text())
            self.assertEqual(auth['admin'],values['AIRFLOW_PASSWORD'])
            self.assertNotIn(values['AIRFLOW_PASSWORD'],result.stdout+result.stderr)

if __name__=='__main__':unittest.main()
