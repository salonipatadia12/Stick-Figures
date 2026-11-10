# End-to-end by tomorrow: task list (coordinator: Claude; contract: docs/WEBAPP.md)

DONE = from the phone: type a topic, make the picks, download a QA-passed video; VPS + Mac worker running; all tests pass.

## Waiting for the user (batched into one review message)
- [ ] VPS key: the password login was refused (wrong password or root password login off). Add the public key in
      Hostinger's panel, or check the password. Then the read-only VPS report.
- [ ] Mac storage: approve deletions from the list (Trash first).
- [ ] VPS disk cleanup + firewall: proposal after the VPS report; apply after OK.
- [ ] Pisa: approve the hand-edited script (then render).
- [ ] Hands-off test: pick storylines for tickling and Apollo 13; finance: allow up to ~$0.65 or swap the topic.

## A. Videos (coordinator)
- [x] Pisa hand edits (b02, cut b03, b06)
- [ ] Pisa render after approval (scenes pre-painted locally)
- [ ] Hands-off: tickling, Apollo 13, finance → picks → scripts → approval → render; report postable as-is

## B. Web app (agent, worktree)  ## C. Deployment + Mac worker (agent, worktree)  ## D. Tests (agent, worktree)
- [ ] B: webapp/ per contract, unit tests
- [ ] C: deploy/ + macworker/, local Docker run, unit tests
- [ ] D: tests/e2e (fake + real), deploy/smoke_test.sh
- [ ] Merge B, C, D into pipeline-v2; full test suite green
- [ ] Local Docker end-to-end (fake), then one real topic → MP4 locally
- [ ] VPS: deploy after the user's OK (cleanup, firewall, deploy user), smoke test, real topic from the phone
