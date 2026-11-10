"""deploy/smoke_test.sh against local stand-ins only: the mock web app (tests/e2e/mock_webapp.py), a stub `ssh` that
runs the remote script here with stub `docker`/`df`/`free`, and an `openssl s_server` with a self-signed certificate.
Never touches the VPS. Runs under /bin/sh and, when installed, dash (strict POSIX)."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_e2e as e2e  # noqa: E402

h = e2e.h
SCRIPT = h.ROOT / 'deploy' / 'smoke_test.sh'
SHELLS = [s for s in ('/bin/sh', shutil.which('dash')) if s and Path(s).exists()]
OPENSSL = shutil.which('openssl')

STUB_SSH = '''#!/bin/sh
# stub ssh: skip options and the host, run the rest locally
while [ $# -gt 0 ]; do
  case "$1" in -o) shift 2 ;; -*) shift ;; *) shift; break ;; esac
done
exec "$@"
'''
STUB_DOCKER = '''#!/bin/sh
case "$*" in
  "compose version") echo "Docker Compose version v2.30.0" ;;
  "compose ps -a --format"*) cat "$STUB_DIR/ps.txt" ;;
  "compose ps -aq") echo aaa; echo bbb ;;
  inspect*) cat "$STUB_DIR/rc.txt" ;;
  *) echo "stub docker: $*" >&2; exit 1 ;;
esac
'''
STUB_DF = '''#!/bin/sh
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/sda1 100 50 50 $STUB_DISK /"
'''
STUB_FREE = '''#!/bin/sh
echo "               total        used        free      shared  buff/cache   available"
echo "Mem:            $STUB_MEM_TOTAL         400         100           1         500         $STUB_MEM_AVAIL"
'''


@unittest.skipUnless(SCRIPT.exists() and shutil.which('curl'), 'needs deploy/smoke_test.sh and curl')
class SmokeScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = e2e.Stack(app='mock_webapp:app', app_dir=h.HERE, runner=None)
        try:
            cls.stack.start()
        except Exception:
            cls.stack.stop()
            raise
        cls.stubs = cls.stack.tmp / 'stubs'
        cls.stubs.mkdir()
        for name, body in (('ssh', STUB_SSH), ('docker', STUB_DOCKER), ('df', STUB_DF), ('free', STUB_FREE)):
            (cls.stubs / name).write_text(body)
            (cls.stubs / name).chmod(0o755)
        cls.compose_dir = cls.stack.tmp / 'compose'
        cls.compose_dir.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.stack.stop()

    def smoke(self, *args, shell='/bin/sh', **env):
        e = h.safe_env(PUBLIC_URL=self.stack.base, SMOKE_TIMEOUT=5, COMPOSE_DIR=self.compose_dir, STUB_DIR=self.stubs,
                       STUB_DISK='42%', STUB_MEM_TOTAL=1900, STUB_MEM_AVAIL=1200)
        e.update({k: str(v) for k, v in env.items()})
        out = subprocess.run([shell, str(SCRIPT), *args], env=e, capture_output=True, text=True, timeout=120)
        return out.returncode, out.stdout + out.stderr

    def login_env(self, password=None):
        return dict(SMOKE_USER=self.stack.user, SMOKE_PASSWORD=password or self.stack.password)

    def test_all_checks_pass_on_a_healthy_app(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                code, out = self.smoke(shell=shell, **self.login_env())
                self.assertEqual(code, 0, out)
                for line in ('SKIP  tls', 'PASS  health', 'WARN  worker    Mac worker never seen', 'PASS  anon',
                             'PASS  login', 'PASS  history', 'PASS  static    /static/app.css served'):
                    self.assertIn(line, out)
                self.assertIn('smoke: 5 passed, 0 failed, 1 warnings', out)
                self.assertNotIn(self.stack.password, out)

    def test_worker_online_is_reported(self):
        w = subprocess.run(h.worker_cmd(self.stack.base, self.stack.token, '--once'), env=h.safe_env(),
                           capture_output=True, timeout=30)
        self.assertEqual(w.returncode, 0, w.stderr)
        code, out = self.smoke()
        self.assertIn('PASS  worker    Mac worker online', out)
        self.assertIn('SKIP  login', out)
        self.assertEqual(code, 0, out)

    def test_a_wrong_password_fails_without_printing_it(self):
        code, out = self.smoke(**self.login_env('wrong-pass"\\word'))
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL  login     POST /api/login returned 401', out)
        self.assertNotIn('wrong-pass', out)

    def test_unreachable_server_fails(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        code, out = self.smoke(PUBLIC_URL=f'http://127.0.0.1:{port}')
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL  health', out)
        self.assertIn('FAIL  static', out)

    def test_usage_errors(self):
        self.assertEqual(self.smoke('--bogus')[0], 2)
        self.assertEqual(self.smoke(PUBLIC_URL='')[0], 2)

    def test_ssh_mode_healthy_host(self):
        (self.stubs / 'ps.txt').write_text('web|running|healthy\npostgres|running|healthy\ncaddy|running|\n')
        (self.stubs / 'rc.txt').write_text('/stick-web-1|0\n/stick-postgres-1|0\n')
        for shell in SHELLS:
            with self.subTest(shell=shell):
                code, out = self.smoke('--ssh', 'root@stub', shell=shell, PATH=f'{self.stubs}:{os.environ["PATH"]}')
                self.assertEqual(code, 0, out)
                self.assertIn('PASS  compose   3 containers running', out)
                self.assertIn('PASS  restarts  no container restarts', out)
                self.assertIn('PASS  disk      /: 58% free', out)
                self.assertIn('PASS  memory    1200 MB available of 1900 MB (63%)', out)

    def test_ssh_mode_sick_host(self):
        (self.stubs / 'ps.txt').write_text('web|restarting|unhealthy\npostgres|running|healthy\ncaddy|exited|\n')
        (self.stubs / 'rc.txt').write_text('/stick-web-1|7\n/stick-postgres-1|1\n')
        code, out = self.smoke('--ssh', 'root@stub', PATH=f'{self.stubs}:{os.environ["PATH"]}', STUB_DISK='95%',
                               STUB_MEM_AVAIL=80)
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL  compose   not running/healthy: web=restarting/unhealthy, caddy=exited', out)
        self.assertIn('FAIL  restarts  restart loop? stick-web-1=7, stick-postgres-1=1', out)
        self.assertIn('FAIL  disk      /: only 5% free', out)
        self.assertIn('FAIL  memory    only 80 MB available', out)

    def test_ssh_failure_is_reported(self):
        bad = self.stack.tmp / 'badssh'
        bad.mkdir(exist_ok=True)
        (bad / 'ssh').write_text('#!/bin/sh\necho "Permission denied (publickey)." >&2\nexit 255\n')
        (bad / 'ssh').chmod(0o755)
        code, out = self.smoke('--ssh', 'root@stub', PATH=f'{bad}:{os.environ["PATH"]}')
        self.assertEqual(code, 1, out)
        self.assertIn('FAIL  ssh       ssh root@stub failed: Permission denied', out)


@unittest.skipUnless(SCRIPT.exists() and OPENSSL and shutil.which('curl'), 'needs openssl and curl')
class SmokeTlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='smoke-tls-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        cert, key = self.tmp / 'cert.pem', self.tmp / 'key.pem'
        subprocess.run([OPENSSL, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost', '-keyout', str(key), '-out', str(cert)],
                       check=True, capture_output=True, timeout=60)
        self.cert = cert
        self.port = h.free_port()
        self.server = h.Proc('s_server', [OPENSSL, 's_server', '-accept', str(self.port), '-cert', str(cert),
                                          '-key', str(key), '-www', '-quiet'], h.safe_env(), self.tmp)
        self.addCleanup(self.server.stop)

        def up():
            try:
                socket.create_connection(('127.0.0.1', self.port), timeout=1).close()
                return True
            except OSError:
                return False
        h.wait_for(up, 15, what='openssl s_server')
        time.sleep(0.3)

    def smoke(self, **env):
        e = h.safe_env(PUBLIC_URL=f'https://localhost:{self.port}', SMOKE_TIMEOUT=5, **env)
        out = subprocess.run(['/bin/sh', str(SCRIPT)], env=e, capture_output=True, text=True, timeout=60)
        return out.returncode, out.stdout + out.stderr

    def test_trusted_certificate_reports_days_left(self):
        code, out = self.smoke(SMOKE_CA_FILE=self.cert, SMOKE_CERT_MIN_DAYS=7)
        self.assertRegex(out, r'PASS  tls       certificate valid, (29|30) days left')
        self.assertEqual(code, 1, 'the TLS stub serves no /api/health, so health fails')

    def test_short_validity_warns(self):
        code, out = self.smoke(SMOKE_CA_FILE=self.cert, SMOKE_CERT_MIN_DAYS=60)
        self.assertRegex(out, r'WARN  tls       certificate valid but only (29|30) days left')

    def test_untrusted_certificate_fails(self):
        code, out = self.smoke()
        self.assertEqual(code, 1)
        self.assertIn('FAIL  tls       HTTPS request failed', out)


if __name__ == '__main__':
    unittest.main()
