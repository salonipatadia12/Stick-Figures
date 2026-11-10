# End-to-end by tomorrow: task list (coordinator: Claude; contract: docs/WEBAPP.md)

DONE = from the phone: type a topic, make the picks, download a QA-passed video; VPS + Mac worker running; all tests pass.

## Waiting for the user
- [ ] VPS: OK for A cleanup, B firewall (+ is port 3000 public?), C SSH key-only, D deploy (nginx site + certbot, .env, compose, Mac worker)
- [ ] Finance script review (hook pick + approval)
- [ ] Docker Desktop on the Mac: running other projects' containers since lane C's test (quit it if unwanted)

## A. Videos
- [x] Pisa rendered: 51.3 s, all QA pass (over the 45 s target)
- [x] Apollo approved (with the user's edits); tickling approved (with friend + movement)
- [x] Apollo rendered: 47.9 s, all QA pass (space scenes repainted without ground; Aquarius no longer shown landed)
- [x] Tickling rendered: 43.8 s, all QA pass (friend tickles the host in b10)
- [ ] Finance: script at the gate; render after approval
- [ ] Report which hands-off videos are postable as-is

## Build (merged into pipeline-v2, 380 tests pass)
- [x] B web app  - [x] C deploy + Mac worker  - [x] D e2e (fake) + smoke test  - [x] E friend in the host format
- [ ] Deploy to the VPS after OK; smoke test; one real topic from the phone (real e2e)
