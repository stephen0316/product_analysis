import hashlib
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from post_evaluation.app import app
from post_evaluation import auth


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, PRODUCT_EVAL_DATA_DIR=self.directory.name, PRODUCT_EVAL_COOKIE_SECURE='false')
        self.environment.start()
        self.client = TestClient(app)
        auth.create_user('alice', 'Alice-initial-password')

    def tearDown(self):
        self.client.close()
        self.environment.stop()
        self.directory.cleanup()

    def login(self, client=None, username='alice', password='Alice-initial-password', change=True):
        client = client or self.client
        response = client.post('/api/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers['X-CSRF-Token'] = response.json()['csrf']
        if change and response.json()['must_change']:
            response = client.post('/api/auth/password', json={'current_password': password, 'new_password': password+'-changed'})
            self.assertEqual(response.status_code, 200, response.text)
            return self.login(client, username, password+'-changed', False)
        return response

    def test_anonymous_cannot_read_write_export_or_guess_resources(self):
        for path in ['/api/config', '/api/models', '/api/runs', '/api/runs/guess',
                     '/api/runs/guess/export.xlsx', '/api/runs/guess/report.docx',
                     '/api/runs/guess/report-preview', '/api/auth/users', '/docs', '/openapi.json']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/models', json={}).status_code, 401)
        self.assertEqual(self.client.get('/api/health').status_code, 200)

    def test_first_login_forces_password_change_then_allows_business_access(self):
        self.login(change=False)
        self.assertEqual(self.client.get('/api/models').status_code, 403)
        self.assertEqual(self.client.post('/api/auth/users', json={'username': 'bob', 'password': 'Bob-initial-password'}).status_code, 403)
        response = self.client.post('/api/auth/password', json={'current_password': 'Alice-initial-password', 'new_password': 'Alice-personal-password'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.login(password='Alice-personal-password', change=False)
        self.assertEqual(self.client.get('/api/models').status_code, 200)
        self.assertEqual(self.client.get('/api/runs').status_code, 200)
        self.assertEqual(self.client.get('/api/auth/users').status_code, 404)

    def test_csrf_and_login_origin_are_checked(self):
        response = self.client.post('/api/auth/login', headers={'Origin':'https://untrusted.example'}, json={'username':'alice','password':'Alice-initial-password'})
        self.assertEqual(response.status_code, 403)
        self.login()
        self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.client.post('/api/runs', json={}).status_code, 403)
        self.client.headers['X-CSRF-Token'] = 'bad-token'
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 403)

    def test_shared_account_supports_multiple_devices_and_has_no_user_management(self):
        self.login()
        second = TestClient(app)
        self.addCleanup(second.close)
        self.login(second, password='Alice-initial-password-changed', change=False)
        self.assertEqual(second.get('/api/models').status_code, 200)
        self.assertEqual(self.client.get('/api/models').status_code, 200)
        self.assertIn(self.client.post('/api/auth/users', json={'username':'bob','password':'Bob-initial-password'}).status_code, (404, 405))
        self.assertIn(self.client.put('/api/auth/users/guess', json={'active':False}).status_code, (404, 405))
        response = self.client.post('/api/auth/password', json={'current_password':'Alice-initial-password-changed','new_password':'Team-new-shared-password'})
        self.assertEqual(response.status_code, 200)
        for client in (self.client, second):
            self.assertEqual(client.get('/api/models').status_code, 401)
        self.login(second, password='Team-new-shared-password', change=False)
        with self.assertRaises(auth.HTTPException):
            auth.create_user('another', 'Another-initial-password')

    def test_server_recovery_revokes_sessions_and_requires_initial_password_change(self):
        self.login()
        auth.reset_password('alice', 'Team-reset-password')
        self.assertEqual(self.client.get('/api/models').status_code, 401)
        self.login(password='Team-reset-password', change=False)
        self.assertEqual(self.client.get('/api/models').status_code, 403)

    def test_legacy_extra_accounts_are_not_accepted_or_deleted(self):
        self.login()
        with auth.connection() as db:
            db.execute('INSERT INTO auth_users VALUES(?,?,?,?,?,?)', ('old-extra','legacy',auth.password_hash('Legacy-initial-password'),1,0,int(time.time())+10))
        response = self.client.post('/api/auth/login', json={'username':'legacy','password':'Legacy-initial-password'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.get('/api/models').status_code, 200)
        with auth.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM auth_users').fetchone()[0], 2)

    def test_expiration_logout_and_cookie_security(self):
        with patch.dict(os.environ, PRODUCT_EVAL_COOKIE_SECURE='true'):
            response = self.client.post('/api/auth/login', json={'username':'alice','password':'Alice-initial-password'})
        cookie = response.headers['set-cookie'].lower()
        for attribute in ['httponly','secure','samesite=strict']:
            self.assertIn(attribute, cookie)
        self.login()
        token = self.client.cookies.get(auth.COOKIE)
        with auth.connection() as db:
            db.execute('UPDATE auth_sessions SET last_seen=?', (int(time.time())-auth.IDLE_SECONDS-1,))
            rows = db.execute('SELECT token_hash FROM auth_sessions').fetchall()
            self.assertTrue(any(row['token_hash']==hashlib.sha256(token.encode()).hexdigest() for row in rows))
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.login(password='Alice-initial-password-changed', change=False)
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/models').status_code, 401)

    def test_failed_logins_are_persistently_rate_limited(self):
        for _ in range(10):
            self.assertEqual(self.client.post('/api/auth/login', json={'username':'alice','password':'incorrect'}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'username':'alice','password':'Alice-initial-password'}).status_code, 429)
        with auth.connection() as db:
            text = str([tuple(row) for row in db.execute('SELECT * FROM auth_events')])
        self.assertNotIn('incorrect', text)
        self.assertNotIn('Alice-initial-password', text)

    def test_existing_model_and_batch_survive_authentication_setup(self):
        # Public health initializes the existing business schema; account setup
        # uses additive tables in the same persistent volume.
        from post_evaluation import store
        models = store.list_models()
        run = store.create_run(2025, '历史批次', model_id=models[0]['id'])
        self.login()
        self.assertTrue(any(item['id']==run['id'] for item in self.client.get('/api/runs').json()))
