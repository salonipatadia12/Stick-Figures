# Web app, queue and Mac worker: the contract

Goal (user, 2026-11-10): from a phone, type a topic, make the picks, and download a QA-passed video, with the VPS and
the Mac worker running. This file is the contract the three build lanes share (B web app, C deployment + Mac worker,
D tests). Change it only through the coordinator.

## Shape

```
phone ──HTTPS──► VPS (Docker Compose, small)                       Mac (Apple silicon)
                 caddy (TLS) ─► web (FastAPI, HTML + JSON API)      macworker (launchd)
                                 ├─ runner: research/storylines/      │ pulls jobs over HTTPS,
                                 │  script (only SCRIPT_PROVIDER=      │ one job at a time:
                                 │  anthropic; idle with ollama)       │ pitch, script (ollama: Ollama on the Mac)
                                 ├─ postgres: episodes, jobs, worker   │ paint (FLUX scenes/props)
                                 └─ /data volume: episodes/, outputs/  │ render (voice, compose, QA)
                                         ▲                            │
                                         └──── outbound HTTPS only ───┘
```

- **SCRIPT_PROVIDER** (2026-11-10, Anthropic credits ran out): `ollama` (the default) moves research/storylines
  (`pitch`) and the script (`script`) to the Mac worker, which runs them with the pipeline's free local model
  (Ollama + qwen3.5:9b, a Homebrew service on the Mac); the VPS only queues them. `anthropic` is the original paid
  path on the VPS runner, unchanged. The rest of this file describes both; where they differ it says so.

- The VPS never paints or renders. It runs the web app, the queue, Postgres, the light pipeline steps under
  `SCRIPT_PROVIDER=anthropic` (Anthropic calls: research, storylines, script, through the existing ledger and caps)
  and stores the outputs.
- The Mac does all heavy work: painting (FLUX inserts/props) and rendering (Kokoro voice, compositing, Whisper QA),
  and under `SCRIPT_PROVIDER=ollama` research, storylines and the script on its local model.
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
| `SCRIPT_PROVIDER` | web, runner | `ollama` (default): `pitch`/`script` are Mac jobs; `anthropic`: VPS runner jobs (paid). Both containers read it from the same env file |
| `ANTHROPIC_API_KEY` | web runner | the only paid key; needed only with `SCRIPT_PROVIDER=anthropic` |
| `PUBLIC_URL` | web, caddy | e.g. `https://187-127-179-129.sslip.io` (a real domain later) |
| `SPEND_PREAPPROVED_USD` | web | `0.50`: an Anthropic step that would take a video past this total stops and asks in the UI |
| `STICK_SERVER`, token in Keychain (`security ... -s stick-worker`) | macworker | where to pull jobs from |
| `OLLAMA_HOST` | macworker | default `http://localhost:11434` (checked with `GET /api/version` before every pitch/script job) |

## Data

Postgres tables (SQLAlchemy 2 Core or ORM, psycopg 3; migrations by a simple `CREATE TABLE IF NOT EXISTS` bootstrap is fine):

- `episodes`: `slug` (pk), `topic`, `niche`, `audience`, `state`, `created_at`, `updated_at`, `cost_usd`, `error`.
  The episode itself stays the pipeline's `episode.json` + `ledger.json` under `/data/episodes/<slug>/` (the
  pipeline's `store` reads/writes it; `STICK_EPISODES_DIR=/data/episodes`).
- `jobs`: `id`, `slug`, `kind` (`pitch`, `script` run on the Mac with `SCRIPT_PROVIDER=ollama`, on the VPS runner
  with `anthropic`; `paint`, `render` always run on the Mac), `status`
  (`queued`, `running`, `done`, `failed`), `payload` json, `result` json, `progress` json (`stage`, `pct`, `message`,
  `at`), `attempts`, `lease_until`, `created_at`, `started_at`, `finished_at`, `error`.
- `worker`: one row: `last_seen`, `host`, `version`, `busy_job_id`.
- `files`: `slug`, `name`, `kind` (`mp4`, `srt`, `qa`, `contact_sheet`, `scene_preview`), `size`, `created_at`.

Episode states: `researching` → `storylines` (gate) → `scripting` → `script` (gate: hook + approve) → `painting`
→ `scenes` (gate: approve new scenes, or redo one with a note) → `rendering` → `done` | `done_qa_failed` |
`failed` (with `error`; a retry button re-queues the failed job; paid steps, i.e. pitch/script under `anthropic`,
never retry on their own).
If painting finds nothing new, the episode goes straight to `rendering`.

## Gates (the user decides, unless the user started the video with Auto on)

- **storyline**: show `pipeline.story.view` content as cards (format, hook, pitch, conflict/stakes/twist/payoff,
  facts with links). Pick = the pipeline's storyline gate (rotation rule: a refused format shows the reason).
- **script**: show the script (`pipeline.review.script_view` content, laid out for a phone: beat cards with SAY,
  SHOW, HOST, sources), the three hook options as radio buttons, the story check and validation issues. Approve is
  disabled while `Episode.check` has errors. Approve = `pick_hook(n)` + the script gate, recorded `by='user (web)'`.
- **scenes**: thumbnails of every newly painted scene/prop (uploaded by the Mac), each with its beats and scene
  text. "Approve all" queues `render`; "Redo" on one scene takes a short note and queues `paint` with
  `payload.redo = {key: note}` (the Mac repaints that key as a round-2 art direction, `inserts.jobs(rnd, direction)`).
- Every approval writes the pipeline's own approval log entry.

## Auto mode (user 2026-11-13)

The New Video page has an **Auto** switch (`POST /episodes` field `auto=1`; `POST /api/episodes {"auto": true}`;
stored as `episodes.auto`). With it on, the topic goes to a finished MP4 with no stops. After every step
(`Service.after_step`) the gate it reached is decided by `pipeline/auto.py`, through the same gate code, logged with
`by='auto (web)'`:
- storyline: the first storyline the gate accepts (the rotation rule still applies);
- script: approved only if every check passes, trying the hooks in order; a beat whose base is not in the library
  becomes a painted scene of the same place (the paint step makes it);
- scenes: accepted as painted.

When a rule cannot decide (a script that fails its checks, a scene that failed QA, every storyline refused) the episode
stays at that gate with `episodes.auto_note` saying why; the user acts there as usual, and auto carries on after. A
paid spend stop (`spend_approval`) is never passed automatically. The episode page shows a stepper (Research,
Storyline, Script, Paint, Render, Done) above the live job progress. Without Auto every gate waits for the user.

**Notifications (Web Push, `webapp/push.py`):** the phone is notified when a video is done (QA passed or not), when a
step fails, and when Auto stops at a gate. No account or paid service: the server signs with its own VAPID key pair
(`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` in `/srv/stick/.env`; `python -m webapp.push keys` makes one) and the
browser's push service delivers. `/sw.js` (service worker), `/manifest.webmanifest` (installable),
`GET /api/push/key`, `POST /api/push/subscribe|unsubscribe|test`. On an iPhone, add the site to the Home Screen and
turn notifications on from there (iOS 16.4+).

**Render again:** a finished episode (`done`, `done_qa_failed`) can be queued for a new render with the same approved
script and scenes (`POST /episodes/{slug}/render-again`, `/api/episodes/{slug}/render-again`), e.g. after renderer
fixes on the Mac.

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
| `POST /api/worker/heartbeat` | `{host, version, busy_job_id?}` | `{ok, queued: n, kinds: [...]}` (`kinds`: what this server hands the Mac) |
| `POST /api/worker/claim` | `{kinds: ["pitch","script","paint","render"]}` | 204 none, or `{job_id, kind, slug, payload, episode: <episode.json>, ledger: <ledger.json>}` (+ for pitch/script: `provider`, `cache: {name: text}` of `episodes/<slug>/cache/*.json`, and for pitch `others: {slug: stub episode.json}`); sets `running`, `lease_until = now + 15 min`. Kinds the provider does not route to the Mac are ignored |
| `POST /api/worker/jobs/{id}/progress` | `{stage, pct, message}` | `{ok}`; extends the lease |
| `POST /api/worker/jobs/{id}/files` | multipart `file`, form `name`, `kind` | `{ok}`; saved to `/data/outputs/<slug>/<name>` (names: `[a-z0-9._-]+` only). A pitch/script job uploads only `kind=cache` (at most 20 MB each), saved to `/data/episodes/<slug>/cache/<name>`, not listed as a download |
| `POST /api/worker/jobs/{id}/done` | `{result}` | `{ok}`; advances the episode state |
| `POST /api/worker/jobs/{id}/fail` | `{error}` | `{ok}`; episode `failed` |

A running Mac job whose lease expires goes back to `queued` (free work, which includes pitch/script under `ollama`;
at most 3 attempts). The worker counts as online if `last_seen` is under 2 minutes old.

Pitch/script result (Mac, `ollama`): `{notes: [...], episode: <episode.json>, ledger: <ledger.json>, provider}`;
the new `cache/` files are uploaded first. The server checks the episode (same slug; storylines after pitch, beats
after script), saves `episode.json`/`ledger.json` and moves the episode exactly as the runner does: `storylines`
after pitch, `script` after script (`cost_usd` from the ledger, $0). An episode it cannot use fails the job with the
reason (Retry re-runs it). The job's stored result is `{notes, ran_on: "mac", provider}`.

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
- `pitch` / `script` (`SCRIPT_PROVIDER=ollama`): check Ollama (`GET http://localhost:11434/api/version`; down →
  `fail` with "Ollama is not running on the Mac: brew services start ollama"), write the claimed episode, ledger,
  `cache/` and the `others` stubs into a scratch `episodes/` inside the job folder (the checkout's own `episodes/`
  is not touched), run `story.pitch` / `script.generate` there through `macworker/bridge.py` (the repo's `.venv`;
  `SCRIPT_PROVIDER=ollama`, an empty `ANTHROPIC_API_KEY`, a ledger that refuses any paid reservation), upload the
  new or changed `cache/` files, `done` with the episode and ledger;
- after every job (`macworker/cleanup.py`, never fails or delays the report): a finished `render` deletes its stale
  per-beat clips (files in `renders/sample/<slug>/shots/` that its `shots.txt` no longer lists and that are older than
  it; the listed clips are the render cache); `OLLAMA_MODEL` is unloaded (`keep_alive: 0`) when `/api/ps` shows it
  loaded; what was freed goes to `logs/cleanup.log`;
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
