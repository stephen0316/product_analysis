"""Exercise deployment transitions with a fake Docker CLI, without touching services."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NEW_IMAGE = "ghcr.io/example/evaluation@sha256:" + "a" * 64
OLD_IMAGE = "ghcr.io/example/evaluation@sha256:" + "b" * 64
FAKE_DOCKER = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ['PRODUCT_EVAL_DEPLOY_DIR'])
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\n')
mode = os.environ.get('FAKE_MODE', '')
state_file = root / 'state'
state = state_file.read_text() if state_file.exists() else 'old'
if args[0] == 'pull':
    sys.exit(1 if mode == 'pull_failure' else 0)
if args[0] == 'compose':
    command = args[args.index('-f') + 2:]
    if command[0] == 'ps':
        if not (mode == 'no_container' and state == 'old'):
            print('new-container' if state == 'new' else 'old-container')
    elif command[0] == 'run':
        sys.stdout.buffer.write(b'fake-volume-backup')
    elif command[0] == 'stop':
        state_file.write_text('stopped')
    elif command[0] == 'up':
        current = (root / 'image.env').read_text()
        state_file.write_text('new' if os.environ['NEW_IMAGE'] in current else 'old')
    sys.exit(0)
if args[0] == 'inspect':
    if '.Config.Image' in args[2]:
        print(os.environ['OLD_IMAGE'])
    else:
        print('unhealthy' if state == 'new' and mode == 'health_failure' else 'healthy')
    sys.exit(0)
if args[0] == 'run':
    if mode == 'backup_failure':
        sys.exit(1)
    sys.stdout.buffer.write(b'fake-backup')
    sys.exit(0)
sys.exit('Unexpected docker command')
'''


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name, body in [('docker', FAKE_DOCKER), ('flock', '#!/bin/sh\nexit 0\n')]:
            executable = self.bin / name
            executable.write_text(body)
            executable.chmod(0o755)
        self.secret = 'GEMINI_API_KEY=private-test-key\n'
        (self.root / '.env').write_text(self.secret)
        (self.root / 'image.env').write_text(f'PRODUCT_EVAL_IMAGE={OLD_IMAGE}\n')
        (self.root / 'docker-compose.yml').write_text('# previous compose\n')
        self.env = {**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}',
                    'PRODUCT_EVAL_DEPLOY_DIR': str(self.root),
                    'OLD_IMAGE': OLD_IMAGE, 'NEW_IMAGE': NEW_IMAGE}

    def tearDown(self):
        self.temp.cleanup()

    def deploy(self, mode='', image=NEW_IMAGE):
        return subprocess.run(['bash', str(ROOT / 'scripts/deploy.sh'), image],
                              env={**self.env, 'FAKE_MODE': mode}, text=True,
                              capture_output=True, timeout=15)

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_success_backs_up_before_switching_and_keeps_secrets(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / '.env').read_text(), self.secret)
        self.assertEqual((self.root / 'deployed-image.txt').read_text().strip(), NEW_IMAGE)
        self.assertIn(OLD_IMAGE, (self.root / 'previous-image.env').read_text())
        self.assertEqual(len(list((self.root / 'backups').glob('*.tar.gz'))), 1)
        calls = self.calls()
        backup = next(i for i, args in enumerate(calls) if args[0] == 'run')
        switch = next(i for i, args in enumerate(calls) if args[0] == 'compose' and 'up' in args)
        self.assertLess(backup, switch)
        self.assertNotIn('private-test-key', result.stdout + result.stderr)

    def test_pull_failure_does_not_stop_the_application(self):
        result = self.deploy('pull_failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([args[0] for args in self.calls()], ['pull'])
        self.assertIn(OLD_IMAGE, (self.root / 'image.env').read_text())

    def test_bad_health_restores_previous_image_and_compose_but_still_fails(self):
        result = self.deploy('health_failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(OLD_IMAGE, (self.root / 'image.env').read_text())
        self.assertEqual((self.root / 'docker-compose.yml').read_text(), '# previous compose\n')
        self.assertEqual((self.root / 'state').read_text(), 'old')
        self.assertFalse((self.root / 'deployed-image.txt').exists())
        self.assertEqual(len(list((self.root / 'backups').glob('*.tar.gz'))), 1)

    def test_backup_failure_restarts_old_version_without_deploying_new_version(self):
        result = self.deploy('backup_failure')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.root / 'state').read_text(), 'old')
        self.assertIn(OLD_IMAGE, (self.root / 'image.env').read_text())
        self.assertFalse(list((self.root / 'backups').iterdir()))

    def test_first_deployment_has_no_old_data_to_back_up(self):
        (self.root / 'docker-compose.yml').unlink()
        (self.root / 'image.env').unlink()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(args[0] == 'run' for args in self.calls()))

    def test_existing_volume_is_backed_up_when_old_container_was_removed(self):
        result = self.deploy('no_container')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.root / 'backups').glob('*.tar.gz'))), 1)
        self.assertTrue(any(args[0] == 'compose' and 'run' in args for args in self.calls()))

    def test_mutable_latest_tag_is_rejected(self):
        result = self.deploy(image='ghcr.io/example/evaluation:latest')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls())

    def test_health_checks_database_and_revision_without_model_request(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from post_evaluation.app import app

        with patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=str(self.root / 'data'), APP_REVISION='test-revision'):
            with patch('post_evaluation.llm_client.urlopen', side_effect=AssertionError('Health must stay local')):
                response = TestClient(app).get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok', 'revision': 'test-revision'})
        self.assertTrue((self.root / 'data/evaluations.sqlite3').exists())
