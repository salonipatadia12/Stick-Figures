# End-to-end by tomorrow: task list (coordinator: Claude; contract: docs/WEBAPP.md)

DONE = from the phone: type a topic, make the picks, download a QA-passed video; VPS + Mac worker running; all tests pass.

## Waiting for the user
- [ ] Free pipeline live (Ollama qwen3.5:9b, picked 2026-11-11; keyless research approved; doi.org judged by its publisher): user retries the sunsets topic from the phone
- [ ] One real topic end to end from the phone (https://stick.srv1747356.hstgr.cloud, user `saloni`, password in the Mac Keychain item `stick-app`)
- [x] Port 3000 localhost-only (user, 2026-11-10); capture still logging until ~2026-11-11 17:30 UTC
- [x] Docker Desktop: local tests done (386 OK, none need Docker); user may quit it

## VPS (approved 2026-11-10: B firewall, C key-only, D deploy; A cleanup skipped)
- [x] ufw on: 22, 80, 443, 3000; everything else was already localhost-only
- [x] SSH key-only (/etc/ssh/sshd_config.d/01-stick-keyonly.conf); hPanel Web Console is the serial console (ttyS0, /bin/login), unaffected
- [x] Deployed b83a3f9: bare repo /srv/stick/repo.git (push from the Mac), /srv/stick/app, .env 600, nginx site + certificate
- [x] Smoke test 10/10 (fixed: lib.sh passed the URL as an argument; compose check by project; disk check by GB)
- [x] Mac worker launchd agent installed, online

## A. Videos
- [x] Apollo rendered: 47.9 s, all QA pass
- [x] Tickling rendered: 43.8 s, all QA pass
- [x] Finance rendered: 63.4 s; all QA pass except duration (target 45 +-15%); trims + b08 graphic await the user
- [x] Finance trimmed (user-approved): 49.6 s, all QA pass
- [x] Pisa re-rendered: 49.7 s, all QA pass (one lean 5.5 deg right, before/after split with upright guide, cut-away standing, b06 trimmed)
