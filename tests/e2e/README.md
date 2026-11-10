# End-to-end tests and the smoke test

Contract: `docs/WEBAPP.md`. Run everything from the repository root with the main venv (`.venv/bin/python`).

| File | What |
|---|---|
| `test_e2e.py` | topic → storylines → script → scenes (with one redo) → MP4 through the JSON API; fake mode (default) and real mode |
| `fake_worker.py` | a stand-in Mac worker: paint uploads two Pillow JPEG previews, render makes a 2 s ffmpeg `testsrc` MP4, SRT, `qa.json`, contact sheet |
| `test_fake_worker.py` | the fake worker against `mock_server.py` (a test double of the worker API) |
| `test_e2e_mock.py` | the fake-mode flows of `test_e2e.py` against `mock_webapp.py` (a test double of the whole web app, written from the contract) |
| `test_smoke_script.py` | `deploy/smoke_test.sh` against the mock web app, stub `ssh`/`docker`/`df`/`free`, and a self-signed `openssl s_server` |

The mocks are test doubles, not the app. If `test_e2e_mock` passes and `test_e2e` fails, the real `webapp/` and the
contract as read here differ.

## Fake mode (no money, no FLUX, no network)

```sh
.venv/bin/python -m unittest tests.e2e.test_e2e -v           # the real webapp/, skipped until it exists
.venv/bin/python -m unittest discover -s tests/e2e -v         # everything in this folder
```

It starts `uvicorn webapp.app:app` and `python -m webapp.runner` on a free local port with `STICK_FAKE_ANTHROPIC=1`,
SQLite and `STICK_DATA_DIR` in a temp dir, a generated user, password (hashed with `python -m webapp.hashpw`, else
the contract's scrypt format) and worker token, and `ANTHROPIC_API_KEY=''` (which also keeps `.env` from supplying
one). Then, through the JSON API only:

- `FakeFlowTest`: health, login, create (empty topic → 400; a form post with a foreign `Origin` → 403;
  `?view=status` answers), storylines (wrong-state actions refused, state unchanged), pick 1, script (bad hook
  refused), approve hook 1, the fake worker paints, one redo round (`payload.redo = {key: note}`), approve, render,
  `done`. Downloads the MP4 (ffprobe > 1 s), SRT (parses), `qa.json` (`all` true), `?dl=1` attachment; `render`
  block (`qa_pass`, `duration`); cost shown ($0) and history lists it; every episode page seen returns 200 on a
  phone user agent; anonymous requests, a bad worker token and the token used as a login are all refused;
  `POST /api/logout` ends the session. A `spend_approval` state fails fake mode at once (fake spend is $0).
- `FakeFailuresTest`: worker offline (job stays `queued`, `worker.online` false, then runs when the worker comes);
  `--fail-render` → `done_qa_failed`; a worker `/fail` → `failed`, then `retry` → finishes.
- `LeaseTest`: `--crash-after-claim`, then the same job is claimed again after the lease. Runs only when `webapp/`
  reads a short-lease variable (`WORKER_LEASE_SECONDS`, or `STICK_LEASE_SECONDS`/`LEASE_SECONDS`/`JOB_LEASE_SECONDS`);
  otherwise skipped with that reason. `webapp/` currently has none (`Settings.mac_lease_minutes = 15`), so it skips;
  lease expiry is covered by B's `tests/webapp` unit tests and by `test_fake_worker` against the mock.

Knobs: `E2E_STEP_TIMEOUT` (s per state change, default 120), `E2E_TEST_TIMEOUT` (hard limit per test, default 600),
`E2E_VERBOSE=1` (print states), `E2E_KEEP=1` (keep the temp dir with the logs of the web app, runner and workers).
Every subprocess runs in its own process group and is killed in cleanup.

The fake worker on its own:

```sh
STICK_WORKER_TOKEN=... .venv/bin/python tests/e2e/fake_worker.py --server http://127.0.0.1:8000 [--once [--wait 30]] \
    [--fail-render] [--fail-kind paint|render] [--crash-after-claim] [--journal jobs.jsonl]
```

## Real mode (costs money; only when the coordinator asks)

The same flow against a real deployment with the real Mac worker. Anthropic research, storylines and script are paid
(the server's `SPEND_PREAPPROVED_USD` still applies); the Mac paints and renders (allow ~60 min). It prints a cost
warning and refuses to run without `E2E_REAL_CONFIRM=yes`.

```sh
E2E_REAL=1 E2E_REAL_CONFIRM=yes E2E_SERVER=https://stick.srv1747356.hstgr.cloud E2E_USER=... E2E_PASSWORD=... \
E2E_TOPIC="Why the Leaning Tower of Pisa leans" .venv/bin/python tests/e2e/test_e2e.py --real
```

It never approves on its own. Without `E2E_PICKS` it stops at each gate, prints the episode URL and waits (up to
`E2E_GATE_TIMEOUT`, default 3600 s) for the human to pick on the phone. `E2E_PICKS=story=1,hook=1` makes those two
picks; the scenes gate still waits for the human unless `scenes=approve` is added. Render wait: `E2E_RENDER_TIMEOUT`
(default 3600 s). If the server stops at `spend_approval`, the test prints the URL and waits: it never allows more
spend. At the end it downloads and checks the files and prints the cost.

## Smoke test (after a deploy)

```sh
SMOKE_USER=... SMOKE_PASSWORD=... deploy/smoke_test.sh [--ssh root@host]   # PUBLIC_URL defaults to https://stick.srv1747356.hstgr.cloud
```

One line per check (`PASS`/`WARN`/`FAIL`/`SKIP`) and a summary; exit 1 if anything failed. Checks: TLS (curl verifies
the chain and host; days left from `openssl`, warn under `SMOKE_CERT_MIN_DAYS`, default 14), `/api/health` ok with db
ok, worker online or last seen (offline is a warning: the Mac may sleep), anonymous and bad-token requests refused,
login and the history page (only with `SMOKE_USER`/`SMOKE_PASSWORD`), a `/static/` asset referenced by `/login`.
`--ssh` adds read-only host checks in `COMPOSE_DIR` (default `/srv/stick`): `docker compose ps` all running and healthy,
restart counts (warn > 0, fail ≥ 5), disk free (warn < 20 %, fail < 10 %), memory available (warn < 15 %, fail
< 100 MB). The password goes to curl through a 0600 temp file, never the command line or the output.
