# Deploying Stick Figures: the VPS stack and the Mac worker

The contract is `docs/WEBAPP.md`. On the VPS: the host's existing **nginx** (TLS by certbot) proxies
`https://stick.srv1747356.hstgr.cloud` to the `web` container on `127.0.0.1:8020`; `runner` does the light
pipeline steps when `SCRIPT_PROVIDER=anthropic` (with `ollama`, the default, it idles); `postgres` holds the queue;
the `data` volume holds `/data/episodes` and `/data/outputs`. Compose project `stick`, everything under `/srv/stick`.
The Mac runs `macworker` (launchd), which pulls paint/render jobs, and pitch/script jobs under `ollama`, over HTTPS;
nothing connects into the Mac.

| File | What |
|---|---|
| `compose.yaml` | `web`, `runner`, `postgres` (+ `caddy`, local-dev profile only) |
| `Dockerfile.web` (+ `.dockerignore`) | python:3.12-slim, non-root uid 10001; pipeline/, webapp/, schemas/, styles JSON, the fake-mode fixtures |
| `requirements-web.txt` | pinned deps for the image |
| `nginx/stick.conf` | the nginx site (proxy to 127.0.0.1:8020, 300 MB uploads, 15 min timeouts) |
| `install.sh`, `deploy.sh`, `rollback.sh`, `backup.sh`, `firewall.sh`, `lib.sh` | VPS scripts (POSIX sh) |
| `.env.example` | the env file's keys (no real values) |
| `launchd/` | the Mac worker's LaunchAgent and its installer |
| `dev/` | a stub app for checking the stack without the web app (local only) |

Every script prints what it does, never prints a secret, and touches only the `stick` project, `/srv/stick` and
(with `--apply-nginx` only) `/etc/nginx/sites-*/stick.conf`. Nothing prunes Docker on the VPS.

Memory limits: web 384 MB, runner 384 MB, postgres 256 MB (about 190 MB used idle in a local test). Logs rotate
at 3 x 10 MB per container.

## 1. First install (VPS)

```sh
# as the deploy user; Docker 29 + Compose 2.40 are already there
sudo mkdir -p /srv/stick && sudo chown "$USER": /srv/stick && chmod 750 /srv/stick
git clone --branch pipeline-v2 <repo url> /srv/stick/app
cd /srv/stick/app
cp deploy/.env.example /srv/stick/.env && chmod 600 /srv/stick/.env
nano /srv/stick/.env          # see "The env file" below
sh deploy/install.sh          # stack up + health; PRINTS the nginx/certbot commands
```

`install.sh` checks Docker/Compose (installs nothing; if Docker were missing it prints the commands and stops),
creates `/srv/stick/backups`, refuses an env file that is not mode 600 or still has placeholders, checks that
`127.0.0.1:8020` is free, builds `stick-web:<commit>`, starts `web runner postgres`, waits for
`/api/health`, records the deploy and runs `deploy/smoke_test.sh` if present.

**nginx + certificate (needs the user's OK).** `install.sh` prints exactly:

```sh
sudo install -m 644 deploy/nginx/stick.conf /etc/nginx/sites-available/stick.conf
sudo ln -sfn /etc/nginx/sites-available/stick.conf /etc/nginx/sites-enabled/stick.conf
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d stick.srv1747356.hstgr.cloud --redirect --non-interactive --keep-until-expiring
sudo nginx -t && sudo systemctl reload nginx
```

Run them by hand, or `sh deploy/install.sh --apply-nginx` to have the script run them (an existing different
`stick.conf` is backed up first; if a step fails it removes only our `sites-enabled/stick.conf` link so the other
sites' reloads and renewals keep working). Certbot edits `stick.conf` in place to add the 443 server and the
redirect; later installs keep that file as it is.

**Firewall (needs the user's OK).** `sh deploy/firewall.sh` prints the ufw rules (22, 80, 443, plus
`EXTRA_PORTS="5678/tcp 8080"` for what already runs there). `sudo sh deploy/firewall.sh --apply` applies them. If
ufw is already active only the `allow` lines matter. The stack itself publishes nothing publicly.

### The env file (`/srv/stick/.env`, mode 600)

- Random values: `openssl rand -hex 32` (SESSION_SECRET), `openssl rand -hex 24` (POSTGRES_PASSWORD; hex so it is
  safe inside DATABASE_URL).
- `APP_PASSWORD_HASH`: after the first build,
  `docker compose --env-file /srv/stick/.env -f deploy/compose.yaml run --rm --no-deps web python -m webapp.hashpw`
  and paste the output **in single quotes** (`APP_PASSWORD_HASH='scrypt$...$...'`); without the quotes Compose
  reads each `$` as a variable. `install.sh` refuses the unquoted form.
- `WORKER_TOKEN_SHA256`: see "Worker token" below.
- `PUBLIC_URL=https://stick.srv1747356.hstgr.cloud`; `WEB_PORT` (default 8020) must match `nginx/stick.conf`.
- `SCRIPT_PROVIDER`: `ollama` (the default when unset) hands research/storylines and the script to the Mac worker,
  which runs them on its local Ollama model for free; `anthropic` keeps them on the VPS runner (paid, needs
  `ANTHROPIC_API_KEY`, the spend rule). `web` and `runner` read the same env file, so they agree; after a change,
  `up -d` recreates both. Jobs already queued follow the new routing.

## 2. Deploy a new version

```sh
cd /srv/stick/app && sh deploy/deploy.sh
```

Backup first (skip with `SKIP_BACKUP=1`), `git fetch` + fast-forward `pipeline-v2` (refuses local changes),
local tag `deploy-<UTC time>` (not pushed), build `stick-web:<short commit>`, `up -d`, health, record the deploy
(`/srv/stick/current`, `previous`, `deploys.log`), smoke test, keep the newest 3 `stick-web` images. If health
fails it prints the rollback command.

## 3. Roll back

```sh
sh deploy/rollback.sh                                   # to the deploy before the current one
sh deploy/rollback.sh --to deploy-20261110-091500       # any deploy tag or commit
sh deploy/rollback.sh --to <tag> --restore-db 20261110-033000 [--restore-data 20261110-033000] [--yes]
```

It checks out the target (detached; the next `deploy.sh` returns to `pipeline-v2`), reuses
`stick-web:<commit>` if it still exists or builds it, and starts it. `--restore-db` first takes a fresh backup,
stops web and runner, and runs `pg_restore --clean`. `--restore-data` unpacks that backup's `episodes/` and
`outputs/` over `/data` (newer files stay). Rolling back across a database schema change needs `--restore-db`.

## 4. Backups and restore

```sh
sh deploy/backup.sh                       # /srv/stick/backups/<UTC time>/{db.dump,data.tar.gz,commit}, newest 7 kept
KEEP_OUTPUTS=20 sh deploy/backup.sh       # also keep only the 20 newest video folders in /data/outputs
```

Cron, daily at 03:30:
`30 3 * * * cd /srv/stick/app && KEEP_OUTPUTS=20 sh deploy/backup.sh >> /srv/stick/backups/backup.log 2>&1`.
With 7 GB free on the VPS, watch `du -sh /srv/stick/backups` and `docker system df`; a finished video with its
previews is about 20 to 60 MB. Videos dropped by `KEEP_OUTPUTS` stay in the backups (which also rotate) and on the
Mac in `renders/sample/<slug>/`; their download links on the site stop working.

Restore = `rollback.sh --to <current tag> --restore-db <backup> [--restore-data <backup>]`. A single file by hand:
`tar -xzf /srv/stick/backups/<time>/data.tar.gz outputs/<slug>/<slug>.mp4`.

## 5. The Mac worker

Runs in the user's `Stick_Figures` checkout with its `.venv`:

```sh
.venv/bin/python -m macworker --dry-run    # config, token, server health + one heartbeat; claims nothing
.venv/bin/python -m macworker --once -v    # claim at most one job, run it, report, exit
.venv/bin/python -m macworker              # the loop (what launchd runs)
```

Config: `STICK_SERVER` (required, https), `STICK_REPO` (defaults to the checkout holding `macworker/`),
`STICK_RENDER_WORKERS` (4), `STICK_WORKER_PULL=1` (git pull --ff-only on a clean `pipeline-v2` while idle; off by
default), `STICK_JOB_TIMEOUT` (4 h), `OLLAMA_HOST` (default `http://localhost:11434`), from the environment or
`~/.config/stick-worker/config`. The token comes
from the Keychain (`security find-generic-password -s stick-worker -w`). Log: `~/Library/Logs/stick-worker.log`
(rotated, 3 x 5 MB). Job state: `~/Library/Application Support/stick-worker/jobs/<id>/` (outcome, uploads done,
command output, hard links of the files to upload).

What it does: heartbeat + claim every 30 s while idle; one job at a time; `caffeinate -i -w <worker pid>` held only
while a job runs; progress from the render output (`bNN: ... frames` lines, then QA) and per FLUX candidate while
painting; a busy heartbeat and the last progress again every 2 minutes keep the 15-minute lease; uploads, then
`done`/`fail`. A finished job is written to disk before anything is sent: on a network error it backs off
(5 s ... 5 min) and resumes the remaining uploads and the report, also after a restart. A job stopped mid-way (Mac
shut down, launchd stop) is not reported; its lease expires and the server queues it again. It only ever stops its
own child processes. `caffeinate -i` prevents idle sleep, not a closed lid.

pitch/script (a server on `SCRIPT_PROVIDER=ollama`): the worker always asks for `pitch, script, paint, render`; the
server hands out only what its provider routes to the Mac. Ollama runs as a Homebrew service
(`brew services start ollama`; `ollama pull qwen3.5:9b` once), so the LaunchAgent needs no change. Each such job
first checks `GET http://localhost:11434/api/version`; if nothing answers, the job fails with "Ollama is not running
on the Mac: brew services start ollama" (the phone shows it; Retry re-queues it, free). Otherwise the worker writes
the claimed episode, its ledger and `cache/` files (plus stubs of the server's other episodes, which the storyline
rotation rule reads) into `jobs/<id>/episodes/`, runs `story.pitch` / `script.generate` there through
`macworker/bridge.py` with `SCRIPT_PROVIDER=ollama`, an empty `ANTHROPIC_API_KEY` and a ledger that refuses any paid
reservation, uploads the new `cache/` files (kind `cache`) and reports `done` with the updated `episode.json` and
`ledger.json`. A lapsed lease re-queues these like paint/render (free work). `--dry-run` also prints Ollama's state;
it counts as a problem only when the server hands pitch/script to the Mac.

Install the launchd agent (the user runs this):

```sh
security add-generic-password -U -s stick-worker -a stick -w     # paste the token at the prompt
sh deploy/launchd/install_worker.sh https://stick.srv1747356.hstgr.cloud
sh deploy/launchd/install_worker.sh --uninstall                   # stop and unload (the plist is moved aside)
```

## 6. Worker token rotation

1. On the Mac, make a new token, store it (replacing the old one) and print only its hash:

   ```sh
   TOKEN=$(openssl rand -hex 32)
   security add-generic-password -U -s stick-worker -a stick -w "$TOKEN"   # briefly visible in this Mac's process list
   printf %s "$TOKEN" | shasum -a 256 | cut -d' ' -f1                     # the hash: the only thing that leaves the Mac
   unset TOKEN
   ```

2. On the VPS, put the hash in `/srv/stick/.env` as `WORKER_TOKEN_SHA256=<hash>`, then
   `docker compose --env-file /srv/stick/.env -f deploy/compose.yaml up -d web` (recreates web with the new env).
3. On the Mac: `launchctl kickstart -k gui/$(id -u)/com.stickfigures.worker`, then check
   `.venv/bin/python -m macworker --dry-run` says "the token works". Until step 3 the worker gets 401s and retries
   every 5 minutes; nothing is lost.

## Local check (this Mac, Docker Desktop)

```sh
cp deploy/.env.example /tmp/stick.env   # set the "Local development only" values, fake secrets, chmod 600
export STICK_ENV_FILE=/tmp/stick.env STICK_HOME=/tmp/stickhome STICK_COMPOSE_ARGS="-p sticktest" CURL_INSECURE=1
sh deploy/install.sh                                                     # web + runner + postgres on 127.0.0.1:8020
docker compose --env-file $STICK_ENV_FILE -p sticktest -f deploy/compose.yaml --profile caddy up -d caddy   # TLS on :18443
docker compose --env-file $STICK_ENV_FILE -p sticktest -f deploy/compose.yaml --profile caddy down -v      # clean up
```

Without the web app, add `-f deploy/dev/compose.stub.yaml` (a stub `/api/health` with a real database check).
