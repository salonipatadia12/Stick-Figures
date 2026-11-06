# Stick-Figures audit — Phase 0 of pipeline-v2

Audited 2026-11-04 on branch `pipeline-v2`, created from `origin/main` @ `39b9fe1`.

**Updated 2026-11-04 after review.**
- Your decisions are recorded in Section 7.
- The cleanup in Section 3 is done: the files are in the Release `archive-2026-11`.
- Remotion is dropped. The compositor is Python (Pillow + FFmpeg); see Section 4.
- `reel.py` and the old reel sources moved to `legacy/`. Older paths below say `outputs/`.

## 0. Repository state (read this first)

| Checkout | Commit | State |
|---|---|---|
| `origin/main` | `39b9fe1` (2026-10-26) | The repo the v2 plan describes: studio, Empty Chair, references. |
| local `main` (`Stick_Figures/`) | `f8e17e5` (2026-10-18) | **20 commits behind origin**, plus uncommitted work that is *not* in `origin/main`: `builder/` moved to the repo root, edits to `reel.py`, `tests/test_builder.py`, `examples/*.json` and the README, new `examples/the-ask.json` and `requirements.txt`. |
| `pipeline-v2` (`Stick_Figures-pipeline-v2/`) | `39b9fe1` + this doc | Created as a **separate git worktree** so the uncommitted work above stays untouched. |

`origin/main` went a different way: it kept `builder/` under `outputs/the-message-source/` and
added `cozy.py`, `character.py` and `legacy_visuals.py` there. The local work-in-progress therefore
conflicts with the current code. It is now saved, unmerged, on branch `local-wip-builder` (commit `e7414c6`).

## 1. The engines

Size today: 1,199 lines of builder code, 1,411 lines of bespoke scripts, 1,618 lines of studio
Python plus 261 lines of studio JS/HTML/CSS, and 980 lines of tests.

### 1.1 `reel.py` → `outputs/the-message-source/builder/` (procedural Pillow builder)

**What it does.** Story JSON → frames drawn procedurally in Pillow → edge-tts narration per scene →
FFmpeg MP4 at **1080×1920, 30 fps**. It has 4 fixed layouts (`home`, `desk`, `cafe`, `standing`),
about 7 gestures, 4 expressions and 3 character styles (`expressive`, `classic`, `cozy`). The `cozy`
style only supports notebook scenes. Commands: `prompt` (a copy-paste authoring prompt),
`draft` (OpenAI Responses), `validate`, `preview` and `render`.

**Works (verified today):**
- `validate` passes on all 3 examples.
- `preview` of the cozy example works.
- `render examples/the-message.json --offline` produced a 29.2 s MP4 in 47 s.

**Broken or limited:**
- `the-first-page-cozy.json` cannot render `--offline`. Its narration is not checked in, so it
  needs a live edge-tts call. The README implies the narration is retained.
- `draft` is OpenAI-only and, per its README, has never made a live paid call.
- Every new kind of scene requires new drawing code. This is the "library runs out" problem.
- `references/stickman-video-director/UPSTREAM.md` points to `builder/cozy.py`, which is the wrong path.

**Worth keeping for v2:** the TTS cache key `sha256(text, voice, rate)`, the overwrite protection,
and the decode check after export. Everything else is legacy.

### 1.2 Bespoke illustrated one-offs

The three source folders are `outputs/the-empty-chair-source/`, `outputs/character-motion-source/`
and `outputs/two-minutes-source/`.

**What they do.** Each reel is a hand-built pipeline:

- AI-generated scene paintings.
- Hand-made clean plates and sprite cut-outs, such as `bag-sprite` and `chair-plate`.
- Pixel-coordinate attachment points, such as `lipsync.MOUTHS` and the arm joints in `writing.py`.
- Synthesized foley and music, with Pillow per-frame compositing.
- Output at **720×1280, 24 fps**.

**Works (verified today):** all three reference reels **rebuild offline bit-identically**. The decoded
video MD5 matches the committed MP4, SSIM is 1.000 and `git status` stays clean.

| Rebuild | Time | Result |
|---|---|---|
| `the-empty-chair-source/recut.py --motion` → `the-empty-chair-story-order.mp4` | 46 s | identical |
| `character-motion-source/assemble.py` → `the-first-page-illustrated.mp4` | 131 s | identical |
| `two-minutes-source/render.py` → `two-minutes.mp4` | 14 s | identical |

**Limits.** None of it transfers to a new story. Each new scene needs new masks, plates and joint
coordinates. This is the quality bar for v2, not a reusable engine.

**Worth keeping for v2:**
- The art direction: The Empty Chair defines the `cozy` style pack (decision 6).
- The layering approach (clean plate plus character sprite), which `layers.py` will automate.
- The editing rhythm. The Empty Chair recut cuts 21 shots across 40 s, about 1.9 s per shot,
  which already meets the v2 rule "never static > 2.5 s".

### 1.3 `studio/` (FastAPI app with a painted-cel rig)

**What it does.** The browser workflow is prompt → story → approval → storyboard → approval → MP4.
It has two render modes:

- **rigged:** a 2D rig of painted cels with 13 actions, 6 props, 6 settings, 3 lighting looks and
  at most 2 characters, plus audio-envelope mouth movement.
- **illustrated:** one gpt-image still per scene.

It drafts stories through Anthropic (strict tool use, a `count_tokens` preflight, a **$0.25 cap**,
no retries, failed drafts kept for recovery) or OpenAI. Approvals are keyed by content hash, jobs
run on one worker, the owner password is enforced in production, and it ships with a
Dockerfile and compose file.

**Works (verified today):**
- 76/76 tests pass with `requirements-studio.txt`.
- `scripts/render_dialogue_test.py --story studio/examples/swing-together.json` rendered a 28.4 s
  reel in 41 s, using live edge-tts.

**Broken or limited:**
- **Paid image generation is not budgeted.** Each `/illustrate` makes one gpt-image edit call plus a
  gpt-4.1-mini vision call per scene, with no cost estimate, cap or ledger. Only story drafting is
  capped, and that cap is hard-limited to ≤ $0.25 (`providers.budget_output_tokens`).
- **Captions are not aligned to speech.** Timing is word-count-proportional, with chunks of up to
  9 words and up to 3 lines (`rendering.caption_chunks` and `wrap`).
- The rig's vocabulary is fixed. A topic like "salary disappears" (coins draining, a calendar,
  a bank balance) cannot be expressed in it.
- The rig keeps one composition per scene. The swing example holds the same framing for all
  28 s (see the contact sheet).
- Output is 720×1280 at 24 fps, with single-pass `loudnorm` at I = −17 LUFS. There are no SFX or
  music in the studio path.
- The Docker image build has never been run (the studio README says so).
- The API calls are hand-written with `urllib`, not the official SDK.

**Worth keeping for v2:**
- The budget-guard logic: preflight token count, `max_tokens` derived from the remaining budget,
  fail closed on an unknown price, no retries, keep failed candidates. It generalises into
  `pipeline/cost.py`.
- Approval-by-digest, for the `--approve` gates.
- edge-tts voice casting.
- The app itself, as the Phase 6 `episode.json` editor.

## 2. Tests (`python -m unittest discover -s tests`)

| Module | Tests | Full environment¹ | README "Setup" environment² |
|---|---:|---|---|
| test_builder | 18 | pass | pass |
| test_continuity | 4 | pass | pass |
| test_lipsync | 3 | pass | pass |
| test_writing | 3 | pass | pass |
| test_studio | 13 | pass | **skipped** (no fastapi) |
| test_animation_studio | 9 | pass | **import error** (pydantic) |
| test_automatic_reels | 5 | pass | **import error** (fastapi) |
| test_dialogue | 5 | pass | **import error** (pydantic) |
| test_story_providers | 12 | pass | **import error** (fastapi) |
| test_swings | 4 | pass | **import error** (pydantic) |
| **Total** | **76** | **76 OK, 27.5 s** | **FAILED (errors=5, skipped=1)** |

¹ A new venv with `requirements-studio.txt` on Python 3.14.6 and FFmpeg 8.0.1.
² `outputs/the-message-source/requirements.txt`, which installs only Pillow and edge-tts. This is
what the top-level README's Setup section installs before telling you to run the tests.

So nothing is functionally broken. The failures come from a dependency split that the README
doesn't explain. v2 will have one `pyproject.toml`, with optional extras for each lane.

The local `main` checkout (dirty, 20 commits behind) runs only its own 17 builder tests, which pass.

## 3. Size and cleanup (done: Option A, commit `678e8a3`)

The 81 files are attached to the Release
[`archive-2026-11`](https://github.com/salonipatadia12/Stick-Figures/releases/tag/archive-2026-11),
and all 82 uploads (the files plus `MANIFEST.md`) were verified against GitHub's SHA-256 digests.
They were then removed from the `pipeline-v2` tree. After the removal, all 76 tests pass and the
three references still rebuild byte-identically.

### 3.1 The measurement that changes the plan

| Measurement | Size |
|---|---:|
| Files tracked at `origin/main` (whole tree) | 145.9 MB |
| — of which `outputs/` | ~141 MB |
| `.git` pack | 124.3 MB |
| — blobs **still in the current tree** | **117.6 MB** |
| — blobs only in history (deleted earlier) | 7.8 MB |

Almost all of `.git` is media that the current tree still uses. **Moving files to `archive/` or
deleting them in a new commit does not shrink `.git` or new clones by a single byte**, because the
blobs stay in history. A new commit only shrinks the checkout on disk.

| Option | Checkout | `.git` and clone | Reversible? | Allowed by the ground rules? |
|---|---:|---:|---|---|
| **A. GitHub Release + remove from the tree** (recommended) | 146 → **56 MB** | unchanged (124 MB) | yes: files live in the Release, and history keeps them too | yes |
| B. `git mv` into `archive/` | unchanged | unchanged | yes | yes, but it only tidies |
| C. A + rewrite history (`git filter-repo`) | 56 MB | 124 → **~55 MB** | needs a force-push | **no**, only if you explicitly approve |

Total on disk: about 270 MB today, about 180 MB with A, about 110 MB with C.

Recommendation: **A now**, and decide on C separately. Most of the remaining 56 MB is the
Empty Chair source art, about 30 MB, which the studio demo, the tests and the reference
rebuild all need. Going lower would mean downloading it from the Release on demand. That is
possible, but not worth doing yet. The new layout keeps future renders out of git, so the repo stops growing.

### 3.2 Keep. The three references, plus what code depends on (traced)

I traced every file opened by the three rebuilds and the full test suite, using a Python audit
hook. This list comes from that trace, not from guesswork.

- **Reference renders:**
  - `the-empty-chair-story-order` `.mp4`, `.srt`, `.storyboard.jpg`, `.timeline.json`
  - `the-first-page-illustrated` `.mp4`, `.srt`, `.storyboard.jpg`, `.timeline.json`
  - `two-minutes` `.mp4`, `.storyboard.jpg`, `.timeline.json`

  Together these are 9.1 MB.
- `character-focus-reference.png` (1.6 MB): used by the studio and the Dockerfile. It is **not**
  a v2 style reference; the `cozy` pack is built from The Empty Chair only (Section 7).
- `the-message-source/` (0.2 MB): builder code, plus the narration that `the-message.json` uses offline.
- **Source folders:** all code, READMEs and prompt files, plus the media that the rebuilds and
  tests open:
  - `the-empty-chair-source/`: 15 of its 21 PNGs, `narration-continuity.mp3`/`.srt`,
    `recut-source.wav` and `recut-voice.wav`.
  - `two-minutes-source/`: 3 PNGs, `narration.mp3` and `sfx.wav`.
  - `character-motion-source/`: all 5 asset PNGs, `full-narration.mp3`/`.srt` and
    `full-sound-bed.wav`. That is every media file except a 50 KB leftover.
- Small documents: `character-reference-study.md` and `the-message-script.txt`.

### 3.3 Archive to the Release `archive-2026-11` (89.7 MB in 81 files)

The exact list, with each file's size and SHA-256, is in
[`archive-candidates.txt`](archive-candidates.txt). The groups below summarise it.

**Top-level iteration renders (67 files, 72.8 MB):**

| Group | Files |
|---|---|
| Empty Chair iterations | `the-empty-chair{,-fixed,-recut,-motion,-dialogue,-acting,-subtle,-drawing,-continuity}.mp4`, each with its `.srt`, `.storyboard.jpg`, `.timeline.json` and `.validation.json`, plus the `*-preview.mp4` cuts (acting, subtle, drawing, story-order) |
| First Page variants | `the-first-page{,-redesigned,-cozy}.mp4` with their storyboards |
| Studio rig samples | `reusable-{kindness,first-hello}{,-cafe}.mp4` with their `.srt` |
| Studio screenshots | `studio-preview.jpg`, `studio-flow-preview.jpg`, `reusable-*-preview.jpg`, `studio-validation.json` |
| Character studies | `character-acting-test.{mp4,json}`, `character-acting-storyboard.jpg`, `character-redesign.png`, `character-pose-sheet.png` |
| Original reel | `the-message.mp4` (1.1 MB) |

`the-message.mp4` was the only output of the `reel.py` lane. It is archived (decision 4).

**Unused heavy media inside `the-empty-chair-source/` (14 files, ≈ 16.9 MB):** `voice-final.wav`,
`voice-paced.wav`, `music-and-foley.wav`, `narration.mp3`, `narration-recut.mp3`, `06-return.png`,
`07-books.png`, `motion/writing-plate.png` and `qa-*` (6 files).

Archiving these means `render.py`'s *original 49-second edition* will not rebuild without them.
That edition's MP4 is archived in the same Release, so the pair stays together.

**Inside `character-motion-source/`:** nothing of real size. The only unused files are
`narration.mp3`, `.srt` and `.txt` (50 KB), left over from the archived 10.5 s acting test.
They stay.

The Release's `MANIFEST.md` gives each file's original path and SHA-256, so any file can be
restored to where it was. Another way to restore one: `git checkout 39b9fe1 -- <path>`.

### 3.4 Stop the growth

Today's `.gitignore` ignores `outputs/*` and then whitelists more than 60 files one at a time.
Every iteration got whitelisted and committed. `qa-*` and `recut*.wav` are listed as ignored,
yet they are tracked anyway.

v2 ignores `episodes/*/cache/` and `episodes/*/out/` by default. Finished deliverables go to
Releases, and only `episode.json` plus the approved style packs are committed.

## 4. Compositor: Python (Pillow + FFmpeg). Decided

Remotion is dropped: nothing is installed and nothing depends on Node. Video assembly stays
on what the repo already uses, driven by `episode.json`:

- **One motion-recipe library.** `pipeline/motion.py` holds each recipe as a pure function of
  time and word timings: push_in, pan, punch_in, parallax, breathe, pose_swap, enter/exit,
  impact_shake, text_slam, number_counter, underline/arrow draw-on, icon_pop, checklist_tick and
  split_compare. Each returns a layer transform or overlay. This replaces the three hand-written
  copies of easing math in the builder, the bespoke scripts and the studio.
- **Per-beat segments.** Each beat renders to its own intermediate clip, cached under that beat's
  `beat_digest()`. The final picture is an FFmpeg concat of those clips. After editing one beat,
  only that clip is re-rendered, which is the definition-of-done requirement.
- **Text.** Captions and info graphics are drawn with Pillow at 2× and downsampled, using the
  font bundled in `assets/fonts/`. That gives one-line captions with the current word highlighted.
  Existing measured speed: the builder renders 29 s of 1080×1920 in 47 s.
- **Audio in FFmpeg.** The voice, the SFX placed on word timings and an optional music bed are
  mixed with `sidechaincompress` ducking. Then a two-pass `loudnorm` targets −14 LUFS with true
  peak ≤ −1 dB. The final mux and decode check also run in FFmpeg.
- **Driven by Claude Code.** The pipeline is packaged as a Claude Code skill
  (`.claude/skills/stick-short/`). "Make a stick figure Short about X" runs the `stick` CLI, and
  the approval gates appear in the chat.

## 5. External facts and risks that shape Phases 1–5

- **Image cost decides image quality.** gpt-image-2 is billed per token. About $30 per 1M image
  output tokens is reported consistently, but OpenAI's model page does not list prices or a
  per-size table, so this must be confirmed with one real call. Rough estimate for one Short:
  - 10–12 beat frames plus 4–6 pose edits at **medium** quality, at about $0.06–0.10 each,
    comes to roughly **$1.0–1.6**. That fits within $2.
  - At **high** quality, about $0.17–0.25 each, the same set exceeds $2.

  Default: medium. The ledger records the *actual* usage tokens each call returns, not the estimate.
- **Portrait size.** 1080 isn't a multiple of 16, and the exact sizes gpt-image-2 accepts aren't
  documented on its model page. The plan is to generate at the nearest accepted portrait size and
  scale or crop to 1080×1920. This will be verified in Phase 2.
- **Script cost.** Claude Sonnet 5.5 costs $2 per M input tokens, $10 per M output and $0.10
  per M cache reads (the pricing page, read 2026-11-04; the repo had recorded $0.20 for cache
  reads). Web search costs $10 per 1,000 searches, plus result tokens billed as input. The Phase 1
  script stage reserves at most about $0.45: research up to $0.28 (4 searches, a 100k-token
  result bound) and the script up to $0.17 (16k output tokens). The ledger records actual usage.
- **Voice (superseded on 2026-11-05 by decision 7: local Kokoro and Chatterbox; no ElevenLabs,
  no edge-tts in v2).** ElevenLabs is the narrator for finished videos; its key goes in `.env`.
  edge-tts remains only as the free voice for drafts and previews. It is an unofficial client of
  Microsoft Edge's read-aloud service, with no SLA and unclear terms for commercial use.
- **Word timing.** ElevenLabs' timestamped speech endpoint returns character-level alignment, and
  edge-tts 7.3.1 supports `boundary="WordBoundary"`, so both providers give word timings
  directly. faster-whisper is the fallback aligner and the verification pass. Beat durations
  always come from the real audio. The exact ElevenLabs endpoint will be verified in Phase 3.
- **Cut-outs.** The `cozy` characters are white figures with dark outlines on painted
  backgrounds, so rembg should separate them reasonably well. Phase 2 tests this on The Empty
  Chair frames first. If edges are poor, the fallback is generating the character on a flat key
  colour. Lineart (thin strokes on paper) is deferred with that style.

## 6. Layout and how the lanes coexist

```
pipeline/   cli.py models.py cost.py script.py store.py styles.py review.py      ← Phase 1 (done)
            images.py layers.py voice.py align.py motion.py compose.py qa.py    ← Phases 2-5
styles/     cozy/ (default; built from The Empty Chair only)   lineart/ later
.claude/skills/stick-short/   Claude Code skill that drives the CLI
assets/     sfx/ (CC0, with LICENSES.md)  fonts/
episodes/   <slug>/episode.json  cache/ (ignored)  out/ (ignored)
studio/     unchanged location: the "rig" lane, and in Phase 6 the episode.json editor
legacy/
  reel-builder/   reel.py, the-message-source/ (builder + audio), examples/*.json, the-message-script.txt
  bespoke/        the-empty-chair-source/, character-motion-source/, two-minutes-source/,
                  plus the 3 reference renders beside them (the scripts write to HERE.parent,
                  so their paths keep working)
references/ unchanged (MIT attribution for stickman-video-director)
tests/      existing tests keep running; v2 adds test_pipeline_*.py
docs/       AUDIT.md, later ARCHITECTURE.md
pyproject.toml   `stick` console script; extras: [pipeline] [studio] [legacy]
```

**Why `studio/` stays where it is.** It is a working app with Docker, compose, authentication and
13 passing tests. It is also where the Phase 6 editor will live. Moving it would change the
Dockerfile, tests and imports for no benefit.

What will change: its three paths into `outputs/` (the demo MP4 and SRT, the demo PNGs and
the character reference) will point at `legacy/bespoke/…`.

**Why `reel.py` moves to `legacy/`.** Its JSON schema and its 4 layouts are superseded by
`episode.json`. It stays runnable as `python legacy/reel-builder/reel.py`, and its 18 tests keep passing.

**Order of operations.** Each step is a separate commit, with tests green after each:

1. Create the Release, then remove the archived files from the tree. **Done** (`678e8a3`).
2. Add `pyproject.toml` and the new `.gitignore` entries. **Done in Phase 1.**
3. Move files with `git mv` into `legacy/`, then update the Dockerfile, `studio/app.py`, 4 test
   files, 2 scripts, `examples/the-message.json` and the README. **Done** (separate commit).
   - `outputs/` is gone: the builder is in `legacy/reel-builder/` and the bespoke reels in
     `legacy/bespoke/`.
   - Paths in Sections 1–3 of this audit name the original `outputs/` locations.
   - While rewriting the build-context rules, a pre-existing Docker bug was fixed: `.dockerignore`
     shipped `06-return-final.png`, but the studio demo copies `06-held-doodle.png`.

## 7. Decisions (2026-11-04)

1. **Remotion: dropped.** Video assembly is Python (Pillow + FFmpeg), driven by `episode.json`
   and packaged as a Claude Code skill (Section 4).
2. **Local uncommitted work:** saved to branch `local-wip-builder` (`e7414c6`). It is not merged.
3. **Cleanup:** Option A, done. No history rewrite and no force-push; the `.git` size is fine as it is.
4. **`the-message.mp4`:** archived in the Release.
5. **Voice:** ElevenLabs is set up before the Phase 5 sample, with its key in `.env`. edge-tts is
   kept only as the free fallback for drafts and previews.
6. **Style:** The Empty Chair (`the-empty-chair-story-order.mp4` and its storyboard) is the only
   approved video and the only style reference. It defines the characters, the painted café
   backgrounds, the warm light and the pacing. The First Page, Two Minutes, the studio rig and the
   `reel.py` renders are not style references.

   It becomes the default `cozy` style pack and is used for the Phase 5 sample. `lineart` comes later.
7. **Local open-source models only (2026-11-05).** This replaces decision 5 and the gpt-image plan in
   Section 5.
   - No OpenAI images and no ElevenLabs.
   - Images and voice run on this Mac, using only models whose licences allow commercial use
     (`LOCAL_MODELS.md`).
   - The Claude script call is the only paid step, still under the $2 cap.
   - A rented GPU needs the user's OK first.
8. **Characters must move (2026-11-05).** This replaces the "pose variants and cut-outs from
   generated frames" plan in Phase 2.
   - AI (SDXL + the cozy LoRA) draws only empty backgrounds and props; 2–4 backgrounds per video.
   - Characters are jointed cut-out rigs built from The Empty Chair art (`docs/RIG.md`). Every beat
     has a real body action keyed to the narration, plus idle life. Camera moves alone never count.
   - Narrator: Kokoro `am_michael` or `af_heart`. Chatterbox is dropped from Stick_Figures; its
     weights in the shared model cache belong to Faceless Reels and stay (the user's choice).
   - The unused full-precision Z-Image download (31 GB) was deleted with the user's OK.
   - The two folders were merged: Stick_Figures is the only checkout, on `pipeline-v2`.

## Appendix: how this was verified

- Tests: `.venv/bin/python -m unittest discover -s tests -v`, run in both environments.
- Engine smoke renders: every engine was run from this worktree, with outputs in a scratch
  directory. Bespoke outputs were compared to the committed files with
  `ffmpeg -map 0:v -f md5` and with SSIM.
- Dependency trace: a `sys.addaudithook` wrapper logged every file the rebuilds and tests opened,
  including paths passed to FFmpeg.
- Size: `git rev-list --objects --all | git cat-file --batch-check`, set against `git ls-tree -r`.
- Loudness: `ffmpeg -af ebur128`. The references measure −15.4 to −17.2 LUFS integrated,
  against the v2 target of −14.
