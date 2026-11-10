# Web app, queue and Mac worker: the contract

Goal (user, 2026-11-10): from a phone, type a topic, make the picks, and download a QA-passed video, with the VPS and
the Mac worker running. This file is the contract the three build lanes share (B web app, C deployment + Mac worker,
D tests). Change it only through the coordinator.

## Shape

```
phone ──HTTPS──► VPS (Docker Compose, small)                       Mac (Apple silicon)
                 caddy (TLS) ─► web (FastAPI, HTML + JSON API)      macworker (launchd)
                                 ├─ runner: research/storylines/      │ pulls jobs over HTTPS,
                                 │  script (Anthropic, the pipeline)   │ one heavy job at a time:
                                 ├─ postgres: episodes, jobs, worker   │ paint (FLUX scenes/props)
                                 └─ /data volume: episodes/, outputs/  │ render (voice, compose, QA)
                                         ▲                            │
                                         └──── outbound HTTPS only ───┘
```

- The VPS never paints or renders. It runs the web app, the queue, Postgres, the light pipeline steps (Anthropic
  calls: research, storylines, script, through the existing ledger and caps) and stores the outputs.
- The Mac does all heavy work: painting (FLUX inserts/props) and rendering (Kokoro voice, compositing, Whisper QA).
  It pulls jobs; nothing connects into the Mac. Jobs wait in the queue while the Mac is offline or asleep.
- One heavy job at a time on the Mac (a single queue; the worker claims one job, finishes it, then claims the next).

## Repository layout (one owner per directory, so lanes merge cleanly)

| Path | Owner | What |
|---|---|---|
| `webapp/` | B | FastAPI app (`webapp.app:app`), DB, queue, VPS runner, templates, static |
| `tests/webapp/` | B | unit tests (SQLite or a throwaway Postgres; no network, no paid calls) |
| `macworker/` | C | the Mac worker package (`python -m macworker`), job handlers, Keychain token |
| `deploy/` | C | `compose.yaml`, `Dockerfile.web`, `Caddyfile`, `.env.example`, `install.sh`, `backup.sh`, `rollback.sh`, `launchd/` plist + `install_worker.sh` |
| `tests/worker/` | C | worker unit tests (fake server, no FLUX) |
| `tests/e2e/` | D | end-to-end test (topic → MP4 through the HTTP API) with a `fake` mode and a `real` mode |
| `deploy/smoke_test.sh` | D | post-deploy smoke test |
| `pipeline/`, `scripts/`, `styles/`, `episodes/` | coordinator | the existing pipeline: lanes call it, they do not change it (ask the coordinator) |

The old `studio/`, root `compose.yaml` and root `Dockerfile` are a retired experiment: do not touch or reuse them.

## Configuration (environment; secrets only in `/srv/stick/.env` on the VPS, never in git)

| Variable | Used by | Meaning |
|---|---|---|
| `DATABASE_URL` | web | `postgresql+psycopg://stick:...@postgres:5432/stick` (tests may use `sqlite:///...`) |
| `STICK_DATA_DIR` | web | `/data`; episodes in `/data/episodes` (also exported as `STICK_EPISODES_DIR`), outputs in `/data/outputs/<slug>/` |
| `APP_USER`, `APP_PASSWORD_HASH` | web | the single login; hash = `scrypt$<salt hex>$<hash hex>` (n=2**14, r=8, p=1, dklen=32); `python -m webapp.hashpw` prints one |
| `SESSION_SECRET` | web | cookie signing key |
| `WORKER_TOKEN_SHA256` | web | sha256 hex of the Mac worker's bearer token (the VPS never stores the token) |
| `ANTHROPIC_API_KEY` | web runner | the only paid key |
| `PUBLIC_URL` | web, caddy | e.g. `https://187-127-179-129.sslip.io` (a real domain later) |
| `SPEND_PREAPPROVED_USD` | web | `0.50`: an Anthropic step that would take a video past this total stops and asks in the UI |
| `STICK_SERVER`, token in Keychain (`security ... -s stick-worker`) | macworker | where to pull jobs from |

## Data

Postgres tables (SQLAlchemy 2 Core or ORM, psycopg 3; migrations by a simple `CREATE TABLE IF NOT EXISTS` bootstrap is fine):

- `episodes`: `slug` (pk), `topic`, `niche`, `audience`, `state`, `created_at`, `updated_at`, `cost_usd`, `error`.
  The episode itself stays the pipeline's `episode.json` + `ledger.json` under `/data/episodes/<slug>/` (the
  pipeline's `store` reads/writes it; `STICK_EPISODES_DIR=/data/episodes`).
- `jobs`: `id`, `slug`, `kind` (`pitch`, `script` run on the VPS; `paint`, `render` run on the Mac), `status`
  (`queued`, `running`, `done`, `failed`), `payload` json, `result` json, `progress` json (`stage`, `pct`, `message`,
  `at`), `attempts`, `lease_until`, `created_at`, `started_at`, `finished_at`, `error`.
- `worker`: one row: `last_seen`, `host`, `version`, `busy_job_id`.
- `files`: `slug`, `name`, `kind` (`mp4`, `srt`, `qa`, `contact_sheet`, `scene_preview`), `size`, `created_at`.

Episode states: `researching` → `storylines` (gate) → `scripting` → `script` (gate: hook + approve) → `painting`
→ `scenes` (gate: approve new scenes, or redo one with a note) → `rendering` → `done` | `done_qa_failed` |
`failed` (with `error`; a retry button re-queues the failed job; paid steps never retry on their own).
If painting finds nothing new, the episode goes straight to `rendering`.

## Gates (the user decides; the app never approves on its own)

- **storyline**: show `pipeline.story.view` content as cards (format, hook, pitch, conflict/stakes/twist/payoff,
  facts with links). Pick = the pipeline's storyline gate (rotation rule: a refused format shows the reason).
- **script**: show the script (`pipeline.review.script_view` content, laid out for a phone: beat cards with SAY,
  SHOW, HOST, sources), the three hook options as radio buttons, the story check and validation issues. Approve is
  disabled while `Episode.check` has errors. Approve = `pick_hook(n)` + the script gate, recorded `by='user (web)'`.
- **scenes**: thumbnails of every newly painted scene/prop (uploaded by the Mac), each with its beats and scene
  text. "Approve all" queues `render`; "Redo" on one scene takes a short note and queues `paint` with
  `payload.redo = {key: note}` (the Mac repaints that key as a round-2 art direction, `inserts.jobs(rnd, direction)`).
- Every approval writes the pipeline's own approval log entry. No auto-approval anywhere.

## HTTP API

Browser routes (session cookie, HTML; mobile-first, no build step, plain HTML + a little JS, polling every 3 s):
`GET/POST /login`, `POST /logout`, `GET /` (history: topic, state, cost, date, links), `GET /new`, `POST /episodes`,
`GET /episodes/{slug}` (renders the page for the current state, live progress incl. Mac worker status),
`GET /files/{slug}/{name}` (download; `Content-Disposition: attachment` with `?dl=1`).

JSON API (same session cookie; `POST /api/login` `{user, password}` sets it). D's e2e test uses only these:

| Method, path | Body | Response |
|---|---|---|
| `POST /api/login` | `{user, password}` | `{ok}` + cookie |
| `GET /api/health` | | `{ok, db, worker: {online, last_seen, busy}}` (no auth; no secrets) |
| `POST /api/episodes` | `{topic, niche?, audience?}` | `{slug}`; queues `pitch` |
| `GET /api/episodes` | | `[{slug, topic, state, cost_usd, created_at}]` |
| `GET /api/episodes/{slug}` | | `{slug, topic, state, cost_usd, error, job: {kind, status, progress}, worker: {...}, storylines?, script?, scenes?, files?}` |
| `POST /api/episodes/{slug}/storyline` | `{story: 1-3}` | `{ok}`; queues `script` |
| `POST /api/episodes/{slug}/script/approve` | `{hook: 1-3}` | `{ok}` or 409 `{errors}`; queues `paint` |
| `POST /api/episodes/{slug}/scenes/approve` | `{}` | `{ok}`; queues `render` |
| `POST /api/episodes/{slug}/scenes/redo` | `{key, note}` | `{ok}`; queues `paint` |
| `POST /api/episodes/{slug}/retry` | `{}` | re-queues the failed job |

Worker API (`Authorization: Bearer <token>`; sha256 must equal `WORKER_TOKEN_SHA256`; nothing else accepts it):

| Method, path | Body | Response |
|---|---|---|
| `POST /api/worker/heartbeat` | `{host, version, busy_job_id?}` | `{ok, queued: n}` |
| `POST /api/worker/claim` | `{kinds: ["paint","render"]}` | 204 none, or `{job_id, kind, slug, payload, episode: <episode.json>, ledger: <ledger.json>}`; sets `running`, `lease_until = now + 15 min` |
| `POST /api/worker/jobs/{id}/progress` | `{stage, pct, message}` | `{ok}`; extends the lease |
| `POST /api/worker/jobs/{id}/files` | multipart `file`, form `name`, `kind` | `{ok}`; saved to `/data/outputs/<slug>/<name>` (names: `[a-z0-9._-]+` only) |
| `POST /api/worker/jobs/{id}/done` | `{result}` | `{ok}`; advances the episode state |
| `POST /api/worker/jobs/{id}/fail` | `{error}` | `{ok}`; episode `failed` |

A running Mac job whose lease expires goes back to `queued` (free work; at most 3 attempts). The worker counts as
online if `last_seen` is under 2 minutes old.

Paint result: `{new: [{key, kind: "scene"|"prop", name: "<key>.jpg", scene, beats}], failed: [{key, why}]}` (the
previews are uploaded first as `scene_preview` files). Render result: `{duration, qa_pass: bool, qa_summary}`;
files `<slug>.mp4`, `<slug>.srt`, `qa.json`, `contact_sheet.jpg`. `qa_pass` false → `done_qa_failed`.

## What the Mac worker runs (C)

In the user's `Stick_Figures` checkout on `pipeline-v2`, with its venvs:
- before a job: write the received `episode.json`/`ledger.json` to `episodes/<slug>/` (pull the latest `pipeline-v2`
  only when idle, never mid-job);
- `paint`: `stick inserts <slug> --generate`, `stick props <slug> --generate` (a `redo` payload repaints those keys
  as round 2 with the note as art direction, archiving round 1), previews as 720px JPEGs, upload, `done`;
- `render`: `.venv/bin/python scripts/motion_test/episode_compose.py <slug> --workers 4`, progress from its output
  (`bNN: frames ... s` lines, then QA), upload the files, `done` with `qa_pass` from `qa.json['all']`;
- `caffeinate -i -w <pid>` only while a job runs; the launchd agent (`KeepAlive`) polls every 30 s while idle.
- Never kills other processes; one job at a time; the token from the Keychain; logs in `~/Library/Logs/stick-worker.log`.

## Fake mode (tests; no money, no FLUX)

- `STICK_FAKE_ANTHROPIC=1` (B): the runner's pipeline calls get a fake client (`tests/pipeline_fixtures.FakeClient`
  with the fixtures' research, storyline and script responses) instead of `pipeline.script.make_client()`. Nothing
  paid is ever sent in this mode; the ledger records the fake usage as $0.
- `tests/e2e/fake_worker.py` (D): speaks the worker API like the Mac, with canned outputs (a 2 s ffmpeg `testsrc`
  MP4, an SRT, a passing `qa.json`, two preview JPEGs). The real Mac worker (C) has a `--once` mode for tests.
- Ports: Caddy's host ports are `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (default 80/443) in case the VPS already
  runs something on them; the web app listens on 8000 inside the network only.

## Out of scope for v1 (say so in the UI if hit)

- A script that needs a base the library does not have stops at the script gate with that message (the host format
  rarely does: scenes are painted inserts).
- Music (no-music is the default), 16:9, multiple users.
