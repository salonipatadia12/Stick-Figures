"""Login, session cookie, health and the worker token."""
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

from webapp import auth
from webapp.testing import PASSWORD, USER, WORKER_TOKEN, Harness

ROOT = Path(__file__).resolve().parents[2]


class PasswordHashTest(unittest.TestCase):
    def test_scrypt_format_and_verify(self):
        h = auth.hash_password('s3cret-pass')
        scheme, salt, key = h.split('$')
        self.assertEqual(scheme, 'scrypt')
        self.assertEqual(len(bytes.fromhex(salt)), 16)
        self.assertEqual(len(bytes.fromhex(key)), 32)
        self.assertTrue(auth.verify_password('s3cret-pass', h))
        self.assertFalse(auth.verify_password('wrong', h))
        self.assertFalse(auth.verify_password('s3cret-pass', 'bcrypt$00$00'))
        self.assertFalse(auth.verify_password('s3cret-pass', 'garbage'))
        # the documented parameters: n=2**14, r=8, p=1, dklen=32
        want = hashlib.scrypt(b's3cret-pass', salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32).hex()
        self.assertEqual(key, want)

    def test_hashpw_module_prints_a_hash(self):
        out = subprocess.run([sys.executable, '-m', 'webapp.hashpw'], input='a-long-password\n', capture_output=True,
                             text=True, cwd=ROOT, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(auth.verify_password('a-long-password', out.stdout.strip()))


class LoginTest(unittest.TestCase):
    def test_pages_and_api_need_a_login(self):
        with Harness() as h:
            r = h.client.get('/', follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(r.headers['location'], '/login')
            self.assertEqual(h.client.get('/api/episodes').status_code, 401)
            self.assertEqual(h.client.post('/api/episodes', json=dict(topic='x y z')).status_code, 401)
            self.assertEqual(h.client.get('/files/a/b.mp4').status_code, 401)
            self.assertEqual(h.client.get('/login').status_code, 200)

    def test_wrong_password_is_refused(self):
        with Harness() as h:
            r = h.client.post('/api/login', json=dict(user=USER, password='nope'))
            self.assertEqual(r.status_code, 401)
            self.assertNotIn(auth.COOKIE, r.cookies)
            r = h.client.post('/api/login', json=dict(user='someone', password=PASSWORD))
            self.assertEqual(r.status_code, 401)
            r = h.client.post('/login', data=dict(user=USER, password='nope'), follow_redirects=False)
            self.assertEqual(r.status_code, 401)
            self.assertIn('Wrong user name or password', r.text)

    def test_login_sets_a_signed_httponly_lax_cookie(self):
        with Harness() as h:
            r = h.login()
            cookie = r.headers['set-cookie']
            self.assertIn(f'{auth.COOKIE}=', cookie)
            self.assertIn('HttpOnly', cookie)
            self.assertIn('SameSite=lax', cookie.replace('Lax', 'lax'))
            self.assertNotIn('Secure', cookie)                       # PUBLIC_URL is http in this test
            self.assertEqual(h.client.get('/api/episodes').status_code, 200)
            self.assertEqual(h.client.get('/').status_code, 200)
            value = h.client.cookies.get(auth.COOKIE)
            h.client.cookies.clear()
            h.client.cookies.set(auth.COOKIE, value[:-2] + ('xx' if not value.endswith('xx') else 'yy'))
            self.assertEqual(h.client.get('/api/episodes').status_code, 401)

    def test_form_login_and_logout(self):
        with Harness() as h:
            r = h.client.post('/login', data=dict(user=USER, password=PASSWORD), follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            self.assertEqual(h.client.get('/api/episodes').status_code, 200)
            h.client.post('/logout', follow_redirects=False)
            self.assertEqual(h.client.get('/api/episodes').status_code, 401)

    def test_cookie_is_secure_when_public_url_is_https(self):
        with Harness(public_url='https://stick.example.org') as h:
            r = h.login()
            self.assertIn('Secure', r.headers['set-cookie'])

    def test_cross_site_form_posts_are_refused(self):
        with Harness() as h:
            h.login()
            r = h.client.post('/episodes', data=dict(topic='Why is the sky blue'), headers={'Origin': 'https://evil.example'},
                              follow_redirects=False)
            self.assertEqual(r.status_code, 403)


class HealthTest(unittest.TestCase):
    def test_health_needs_no_login_and_shows_no_secrets(self):
        with Harness() as h:
            r = h.client.get('/api/health')
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(set(body), {'ok', 'db', 'worker'})
            self.assertTrue(body['ok'] and body['db'])
            self.assertEqual(body['worker'], dict(online=False, last_seen=None, busy=False))
            for secret in (h.settings.session_secret, h.settings.worker_token_sha256, h.settings.app_password_hash,
                           WORKER_TOKEN, USER):
                self.assertNotIn(secret, r.text)
            h.w('post', '/api/worker/heartbeat', json=dict(host='mac', version='1'))
            w = h.client.get('/api/health').json()['worker']
            self.assertTrue(w['online'])
            self.assertFalse(w['busy'])
            self.assertTrue(w['last_seen'].endswith('Z'))


class WorkerTokenTest(unittest.TestCase):
    def test_worker_routes_need_the_bearer_token(self):
        with Harness() as h:
            h.login()                                   # a user session is not a worker token
            routes = [('/api/worker/heartbeat', dict(host='x', version='1')), ('/api/worker/claim', dict(kinds=['paint'])),
                      ('/api/worker/jobs/1/progress', dict(stage='a', pct=1, message='m')),
                      ('/api/worker/jobs/1/done', dict(result={})), ('/api/worker/jobs/1/fail', dict(error='x'))]
            for path, body in routes:
                for headers in ({}, {'Authorization': 'Bearer wrong'}, {'Authorization': WORKER_TOKEN},
                                {'Authorization': 'Bearer ' + hashlib.sha256(WORKER_TOKEN.encode()).hexdigest()}):
                    r = h.client.post(path, json=body, headers=headers)
                    self.assertEqual(r.status_code, 401, (path, headers))
            r = h.client.post('/api/worker/jobs/1/files', files=dict(file=('a.mp4', b'x')), data=dict(name='a.mp4', kind='mp4'))
            self.assertEqual(r.status_code, 401)
            r = h.w('post', '/api/worker/heartbeat', json=dict(host='mac', version='1'))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), dict(ok=True, queued=0))

    def test_no_configured_token_rejects_everything(self):
        with Harness() as h:
            h.settings.worker_token_sha256 = ''
            self.assertEqual(h.w('post', '/api/worker/heartbeat', json={}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
