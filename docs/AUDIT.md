# Stick-Figures audit — Phase 0 of pipeline-v2

Audited 2026-11-04 on branch `pipeline-v2`, created from `origin/main` @ `39b9fe1`.
Nothing has been moved or deleted. Sections 3 and 6 are proposals awaiting approval.

## 0. Repository state (read this first)

| Checkout | Commit | State |
|---|---|---|
| `origin/main` | `39b9fe1` (2026-10-26) | The repo the v2 plan describes: studio, Empty Chair, references. |
| local `main` (`Stick_Figures/`) | `f8e17e5` (2026-10-18) | **20 commits behind origin**, plus uncommitted work that is *not* in `origin/main`: `builder/` moved to the repo root, edits to `reel.py`, `tests/test_builder.py`, `examples/*.json` and the README, new `examples/the-ask.json` and `requirements.txt`. |
| `pipeline-v2` (`Stick_Figures-pipeline-v2/`) | `39b9fe1` + this doc | Created as a **separate git worktree** so the uncommitted work above stays untouched. |

`origin/main` went a different way: it kept `builder/` under `outputs/the-message-source/` and
added `cozy.py`, `character.py` and `legacy_visuals.py` there. The local work-in-progress therefore
conflicts with the current code and needs a decision (Section 7, Q2).

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
- The art direction: `character-focus-reference.png` becomes the `cozy` style reference.
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

## 3. Size and cleanup proposal (nothing moved yet)

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
- `character-focus-reference.png` (1.6 MB): used by the studio and the Dockerfile, and becomes
  the `cozy` style reference.
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

`the-message.mp4` is the only output of the `reel.py` lane. See Q4 in Section 7.

**Unused heavy media inside `the-empty-chair-source/` (14 files, ≈ 16.9 MB):** `voice-final.wav`,
`voice-paced.wav`, `music-and-foley.wav`, `narration.mp3`, `narration-recut.mp3`, `06-return.png`,
`07-books.png`, `motion/writing-plate.png` and `qa-*` (6 files).

Archiving these means `render.py`'s *original 49-second edition* will not rebuild without them.
That edition's MP4 is archived in the same Release, so the pair stays together.

**Inside `character-motion-source/`:** nothing of real size. The only unused files are
`narration.mp3`, `.srt` and `.txt` (50 KB), left over from the archived 10.5 s acting test.
They stay.

The Release will contain a `MANIFEST.md` with each file's original path and SHA-256, so any
file can be restored to where it was.

### 3.4 Stop the growth

Today's `.gitignore` ignores `outputs/*` and then whitelists more than 60 files one at a time.
Every iteration got whitelisted and committed. `qa-*` and `recut*.wav` are listed as ignored,
yet they are tracked anyway.

v2 ignores `episodes/*/cache/` and `episodes/*/out/` by default. Finished deliverables go to
Releases, and only `episode.json` plus the approved style packs are committed.

## 4. Compositor: Remotion vs FFmpeg/MoviePy

**Recommendation: use Remotion for the picture, and FFmpeg for audio mastering, muxing and QA.**
This depends on the license question below.

Arguments for Remotion:
- The motion-recipe library maps directly onto React components: `spring()`/`interpolate()`
  for punch-ins, text slams, counters, draw-on underlines and icon pops.
- CSS does text layout, which handles per-word caption highlights, safe areas and font fallback.
- Remotion Studio previews any frame without a full render.
- A typed `render_plan.json` passed as `inputProps` keeps it driven by `episode.json`.

Today every one of those effects is hand-written easing math in Pillow, in three separate
copies (builder, bespoke and studio).

Arguments against Remotion, and how to contain them:
- **A second toolchain** (Node plus headless Chromium). Node 24 is installed here. Python owns
  the plan; Remotion only renders it.
- **Audio.** Remotion can mix audio, but two-pass `loudnorm` to −14 LUFS with true peak ≤ −1 dB and
  sidechain ducking are better done in FFmpeg. So Remotion renders the picture (or the picture plus
  a draft mix), and FFmpeg builds the final audio and muxes it.
- **Lock-in.** v2 keeps a renderer-neutral `render_plan.json` (layers, keyframes, word timings and
  cues). If the license is unacceptable, a Pillow/FFmpeg renderer can consume the same plan. That
  covers the same ground as MoviePy without MoviePy's speed or text limits.

**License** (from the [LICENSE](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md)
and [pricing](https://www.remotion.pro/license) pages, read 2026-11-04):
- **Free** for individuals, non-profits and **for-profit companies with up to 3 employees**,
  including commercial use.
- Otherwise a Company License is required. "Creators" costs $25 per seat per month and covers
  manual or local work. "**Automators**" costs **$0.01 per render with a $100/month minimum** and
  covers batch or automated rendering and prompt-to-video products. Enterprise starts at $500/month.
- A one-command `stick make` pipeline is the Automators use case, so for a company with more than
  3 people the realistic cost is $100/month. **I need your headcount to say which applies** (Q1).

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
- **Script cost.** Claude Sonnet 5.5 is recorded in the repo at $2 / $10 per M tokens (verified
  2026-10-25). A researched script, including a few web searches, should cost about $0.05–0.15.
- **Voice licensing.** edge-tts is an unofficial client for Microsoft Edge's read-aloud service.
  It has no SLA, and it is unclear whether automated commercial use is allowed. The same voices
  (Andrew, Ava, Sonia) are sold as licensed Azure AI Speech neural voices. Proposal: keep edge-tts
  as the free default for development, and add a licensed provider before monetized publishing (Q5).
- **Word timing.** edge-tts 7.3.1 supports `boundary="WordBoundary"`, so the default voice
  provides exact word timings itself. faster-whisper stays as the aligner for ElevenLabs,
  uploaded audio and verification. Beat durations still come from the real audio.
- **Cut-outs on line art.** rembg is trained on photo-like subjects, and thin black strokes on
  near-white paper are a hard case for it. Phase 2 will test rembg first, with two fallbacks:
  threshold or flood-fill masks (lineart is two-tone), or generating the character on a flat key
  colour.

## 6. Proposed layout and how the lanes coexist

```
pipeline/   cli.py script.py images.py layers.py voice.py align.py qa.py cost.py models.py   ← new primary lane
styles/     lineart/  cozy/ (character_reference.png ← outputs/character-focus-reference.png)
compose/    Remotion project (reads render_plan.json)
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

What does change: its three paths into `outputs/` (the demo MP4 and SRT, the demo PNGs and
the character reference) point at `legacy/bespoke/…` and `styles/cozy/…`.

**Why `reel.py` moves to `legacy/`.** Its JSON schema and its 4 layouts are superseded by
`episode.json`. It stays runnable as `python legacy/reel-builder/reel.py`, and its 18 tests keep passing.

**Order of operations.** Each step is a separate commit, with tests green after each:

1. Create the Release, then remove the archived files from the tree.
2. Move files with `git mv` into `legacy/` and `styles/`, then update the Dockerfile,
   `studio/app.py`, 4 test files, 2 scripts, `examples/the-message.json` and the README.
3. Add `pyproject.toml` and the new `.gitignore`.

## 7. Decisions needed before Phase 1

1. **Remotion license:** how many employees does the company have? 3 or fewer means it's free;
   otherwise Automators at $100/month, or I build the Pillow/FFmpeg renderer for the same plan.
2. **Local uncommitted work on `main`** (the builder move and `the-ask.json`): keep it on a branch
   such as `wip/local-builder-move` for reference, port `the-ask.json` into legacy, or drop it?
   Until you decide, it stays untouched.
3. **Cleanup:** do Option A (Release + remove), and is Option C (history rewrite plus
   force-push) ever acceptable?
4. Archive `the-message.mp4` (1.1 MB, the only output of the reel.py lane), or keep it as that
   lane's reference?
5. **Voice for published videos:** edge-tts is fine for development. Do you want a licensed TTS
   (Azure Speech or ElevenLabs) wired in before the Phase 5 sample?

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
