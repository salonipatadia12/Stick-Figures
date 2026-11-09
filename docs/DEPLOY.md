# Deployment plan: VPS + this Mac as the GPU worker

Status: **plan, waiting for the user's OK.** Nothing is installed on the VPS yet. The spec check
(`scripts/deploy/vps_spec_check.sh`) only reads; it changes nothing.

## Shape

```
          you (browser / CLI)
                 │ HTTPS
        ┌────────▼─────────────────────── VPS (no GPU, Docker) ───────────────────────┐
        │ caddy (TLS) ─► app (API + review pages: gates, picks, approvals)             │
        │                 │                                                           │
        │                 ├─ postgres: episodes, job queue, cost ledger               │
        │                 ├─ worker-cpu: script calls (Claude), Kokoro TTS, Whisper QA,│
        │                 │              compositing + ffmpeg renders                  │
        │                 └─ storage volume: episodes/, styles/ libraries, renders/    │
        └──────────────────────────────▲──────────────────────────────────────────────┘
                                       │ HTTPS, outbound only (the Mac calls the VPS)
                          ┌────────────┴────────────┐
                          │ Mac (M5): gpu-worker     │  FLUX.2 klein (mflux): bases, plates,
                          │ launchd agent            │  inserts, props
                          └──────────────────────────┘
```

## The VPS (no GPU)

- **Docker Compose**, five services: `caddy` (HTTPS, Let's Encrypt), `app` (the `stick` pipeline behind a small API
  and the review pages), `worker-cpu` (everything that runs on a CPU), `postgres` (state, queue, ledger) and a named
  storage volume.
- **Runs here:** the script calls (the only paid step, through the same ledger and the $2 per-video cap), Kokoro
  TTS (82M, CPU), Whisper QA (small.en, CPU int8), compositing and ffmpeg renders, storage.
- **Queue:** a `jobs` table (kind, payload, status, lease_until, attempts). Workers claim with
  `SELECT … FOR UPDATE SKIP LOCKED`. GPU jobs (`kind = gpu.flux_*`) are only ever claimed by the Mac.
- **Secrets:** `/srv/stick/.env` on the VPS only (mode 600, owner `deploy`), loaded with Compose `env_file`, never
  in git or in images. It holds the Anthropic key, the Postgres password and the GPU worker's token hash.
- **Access:** an SSH key, not the password. Steps: install the Mac's ed25519 public key for a new non-root `deploy`
  user (one last login with the password, which you run or approve), confirm the key login works, then turn off
  password and root login (`PasswordAuthentication no`, `PermitRootLogin no`). The password file can then be
  archived. Firewall: only 22, 80 and 443 open; fail2ban on SSH.
- **Code:** the VPS pulls `pipeline-v2` with a read-only deploy key. Nothing ever pushes to `main`.

## The Mac (GPU worker)

- `stick gpu-worker`: a small Python loop in `.venvs/mlx`. Every 30–60 s it asks the VPS
  `POST /api/gpu/claim` (HTTPS, `Authorization: Bearer <worker token>`). On a job it downloads the inputs, runs
  FLUX locally, uploads the result (`PUT /api/gpu/jobs/<id>/result`) and heartbeats while it works.
- **No inbound ports.** The Mac only makes outbound HTTPS calls; nothing on the VPS can reach into the Mac.
- **Its own token:** scoped to GPU jobs only. It can claim, heartbeat and upload, nothing else. The token is stored
  in the macOS Keychain; the VPS keeps only its hash. It can be revoked and rotated on the VPS.
- **launchd:** a LaunchAgent (`~/Library/LaunchAgents/com.stickfigures.gpuworker.plist`, `KeepAlive`, logs under
  `~/Library/Logs/`) starts the worker at login and restarts it if it stops.
- **Awake only while there is work:** while a job runs, the worker holds `caffeinate -i -w <pid>`, which stops idle
  sleep. With no jobs it releases it, so the Mac sleeps as usual.
- **Mac offline:** jobs simply wait in the queue. The VPS shows "waiting for the GPU worker (last seen …)". A job
  whose lease expires (the Mac slept mid-job) goes back to the queue on its own. GPU work is local and free, so a
  second run costs nothing; paid steps never retry automatically.
- **Memory:** it shares the 16 GB Mac with Faceless Reels, Chrome and a VM, so it runs one FLUX job at a time and
  never kills other processes.

## What moves where

| Step | Where | Notes |
|---|---|---|
| research + script (Claude) | VPS | the only paid step; ledger + $2 cap unchanged |
| script / bases / props / hook gates | VPS pages | the same gates, in the browser instead of the terminal |
| bases, plates, inserts, props (FLUX) | Mac | queued GPU jobs |
| base building, host cut-out (OpenCV) | VPS | CPU only |
| voice (Kokoro), captions, Whisper QA | VPS | CPU only |
| compositing + render (ffmpeg) | VPS | the main CPU load; time scales with cores |
| libraries + renders | VPS volume | nightly backup to a second place you choose (optional) |

## Rollout, each step for your OK

1. **Spec check (read-only):** OS, CPU cores, RAM, free disk, Docker, open ports, firewall.
2. **SSH key + `deploy` user,** then password login turned off.
3. **Docker Compose stack** with the CLI pipeline unchanged (`stick` inside `app`); render one known video on the
   VPS and compare it with the Mac render (byte-for-byte frames where the code is deterministic).
4. **Queue + GPU worker:** the Mac claims one insert job end to end; then the launchd agent.
5. **Review pages** for the gates (script, bases, hooks), then switch the default from the Mac CLI to the VPS.

## What the VPS needs

- **Minimum:** 4 vCPU, 8 GB RAM, 40 GB free disk, Ubuntu 22.04/24.04 or Debian 12, Docker 24+.
- **Comfortable:** 8 vCPU, 16 GB RAM. A 45 s video renders in about 4 minutes on 4 workers on the M5; on a 4-vCPU
  VPS, expect roughly 10–15 minutes.
- **Disk:** about 0.4 GB per finished video with its cached shot clips and audio; libraries about 0.2 GB today.
