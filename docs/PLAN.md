# pipeline-v2 plan

**Goal:** one request produces a finished, narrated, captioned stick-figure Short about any topic.
That request is either the command `stick make "Why your salary disappears by the 20th"`, or
asking Claude Code to "make a stick figure Short about X".

The approach is:

- an AI-generated, style-locked painting for each beat
- meaningful micro-animation
- the narrator's voice
- captions synced word by word
- sound effects

Images and narration come from open-source models whose licences allow commercial use, running
locally on this Mac (see [LOCAL_MODELS.md](LOCAL_MODELS.md)). Everything is composited locally in
Python. The existing studio rig stays as a separate, zero-cost lane for formats with a recurring
character.

Decisions and the audit behind this plan are in [AUDIT.md](AUDIT.md).

## Ground rules

- **Git:** work on `pipeline-v2`. Never push to `main`, force-push or rewrite history.
- **Nothing is deleted without asking.** Files are archived instead; the GitHub Release
  `archive-2026-11` holds the first batch.
- **Keys** come only from `.env`. The only one is `ANTHROPIC_API_KEY`, for the script.
- **Local models only** for images and voice, and only under licences that allow commercial use.
  No FLUX "dev"-licence models, no F5-TTS or XTTS (non-commercial). A rented GPU is never used
  without asking first.
- **Budget:** every paid call goes through the episode's cost ledger, with a per-video cap
  (`--budget`, default $2.00). With local models, the script-writing call is the only paid step.
  - Each step's worst case is reserved before it is sent.
  - If the cap would be exceeded, the pipeline stops cleanly, records nothing and sends nothing.
  - There are no automatic paid retries. The SDK clients use `max_retries=0`, and Anthropic's
    server-side refusal fallback stays off. Both stay off until you decide otherwise.
  - **Phase 5 sample:** if the full Short would exceed $2.00, stop and ask before raising the cap
    to $3.00. Never lower image quality to fit the budget.
- **Caching:** script, images and audio are cached by content hash, and each beat has its own
  digest. After editing one beat, only that beat is regenerated.
- **Proof after each phase:** run the tests and show real output: frames, a contact sheet and the MP4.

## Style

- **`cozy` (default):** taken from The Empty Chair, which is the only approved video and the only
  style reference. That means `legacy/bespoke/the-empty-chair-story-order.mp4` and its storyboard.
  It covers:
  - white round-headed figures with navy outlines (`lead`, plus a `friend` in a teal scarf)
  - painted café-world backgrounds in warm golden light
  - steady, character-dominant framing, changing picture about every 1.9 s

  The First Page, Two Minutes, the studio rig and the `reel.py` renders are not references.
- **`lineart`:** later, as a second style.

## Phases

### 0. Audit and cleanup (done)

- Wrote `docs/AUDIT.md`.
- Moved 81 iteration files (89.7 MB) to the Release `archive-2026-11`.
- Saved the local work-in-progress on branch `local-wip-builder`.

### 1. Episode spec and script engine (done; the salary script is approved)

- **`episode.json`** is the single source of truth. It is defined by Pydantic models, with a
  JSON schema in `schemas/episode.schema.json`.
  - Episode fields: topic, niche, audience, style, aspect, target length, voice, then the beats.
  - Each beat has: id, role, narration, on-screen text (≤ 5 words), visual intent, a shot
    (framing, cast, image prompt, pose variants, 1–2 motion recipes with anchor words,
    transition, layers), sfx with anchor words, source ids, and a duration filled from the audio.
- **The script is written in two budgeted calls to `claude-sonnet-5-5`:**
  1. Web-search research. Citations are kept, so every fact has a real URL.
  2. A structured-output script. It may cite only those facts, or numbers clearly framed as examples.
- **The editorial rules are enforced in code:**
  - the hook lands in ~2 s, with no greeting
  - one idea per beat, 7–14 beats for 30–60 s
  - a payoff that delivers the hook's promise, then a loop-back or teaser ending
  - visuals that act out the idea (no talking heads)
  - no unsourced numbers
  - motion anchors that appear in the narration
- **The script gate** is on by default. A terminal prompts approve/edit; in Claude Code the
  script is shown in the chat. `--auto` skips the gate but refuses scripts that fail the checks.
- **Claude Code skill:** `.claude/skills/stick-short/`.

### 2. Backgrounds, props and moving characters (changed 2026-11-05: characters must move)

Not a slideshow of AI pictures: the characters are animated cut-out rigs, like The Empty Chair
motion edition. See `docs/RIG.md`.

- **AI draws only EMPTY backgrounds and props**, never characters: SDXL + the cozy LoRA
  (CreativeML Open RAIL++-M), prompted with "nobody" and a no-people negative prompt.
  - 2–4 backgrounds per video, reused across beats with different framings.
  - Props (wallet, coin, ...) on a plain background, cut out automatically.
  - Props whose insides animate (calendar marks, a filling jar, cash, a moth) are drawn in code
    in the same cel style.
- **Characters sit on painted base poses** (changed 2026-11-06), as in The Empty Chair motion edition.
  - The seated body, legs, chair and table are an Empty Chair painting (`styles/cozy/bases/`).
  - Only the head, arms, hands and props are animated on top: the frame's own head with a live face,
    thick navy arms coming out from behind the torso, and the anatomical hand library. Held props
    move with the hand.
  - The base's café is the Empty Chair café itself. Other places or poses (standing, other facings)
    are new bases made with FLUX.2 klein from the Empty Chair frames, and only approved ones are used.
- **On-screen text** stays out of the bottom quarter and the right edge (the Shorts title and
  buttons); the $ counter sits in the upper-middle.
- **Every beat needs a real body action** that matches the narration, plus idle life (breathing,
  blinks, weight shift). Camera moves alone never count as movement.
- **Actions are keyed to narration words** (Kokoro word timings), so the same choreography
  re-times itself to the voice.
- **FLUX.2 klein 4B** (Apache-2.0, 8-bit, local) makes the clean plates and new base poses from the
  Empty Chair frames. A Z-Image LoRA only as a free side experiment. Qwen-Image-Edit stays parked
  (too big for 16 GB).
- **Proof stop:** a 10–15 s animated test of beats 1–2 (`scripts/motion_test/beats_1_2.py`).

### 3. Voice and captions

- **`pipeline/voice.py`:** Kokoro-82M (Apache-2.0), local. One voice per episode, cached by text
  and voice. `am_michael` is the default narrator (`styles/cozy/style.json`); `af_heart` is the
  alternate. Chatterbox was dropped on 2026-11-05.
- **Word timings** come from Kokoro itself; faster-whisper (MIT) checks that every word was spoken.
  Beat durations come from the real audio; speech is never stretched to fit.
- **Captions:**
  - one line at a time, grouped by meaning
  - the current word highlighted
  - burned into the video, plus an `.srt` file

### 4. Compositor (Python: Pillow + FFmpeg)

- **`pipeline/motion.py`** is the recipe library:
  - camera: push_in, pan, punch_in
  - character: parallax, breathe, pose_swap, enter/exit, impact_shake
  - info: text_slam, number_counter, underline/arrow draw-on, icon_pop, checklist_tick, split_compare
  - transitions: cut by default; whip or match cut only when motivated
- **The picture never stays static for more than 2.5 s.** Changes land on the anchor words.
- **Per-beat clips** are cached by the beat digest, then concatenated by FFmpeg.
- **SFX:** a small CC0 library in `assets/sfx/`, placed on the anchor words.
- **Audio master:** an optional music bed ducked under the voice, then two-pass `loudnorm`
  to −14 LUFS with true peak ≤ −1 dB.

### 5. QA and the first end-to-end Short

**`pipeline/qa.py`** writes `qa.json` and `contact_sheet.jpg`, checking:

- freezes in the picture area
- captions: one line, inside safe areas, every narrated word present
- loudness and true peak
- duration within ±15% of the target
- character consistency, checked locally. A Claude vision check of the contact sheet is optional
  and off by default, because it would be a second paid call.

**Sample:** "Why Your Paycheck Is Gone a Week Later" (personal finance, for US salaried workers on
any pay schedule; the brief was "Why your salary disappears by the 20th"), in the **cozy** style,
at 9:16, about 45 s. It ships with the MP4, SRT, contact sheet, `qa.json` and the actual cost.

### 6. After the sample is approved

- **`stick batch topics.csv`:** several episodes under a total budget.
- **`stick package`:** title options, description, hashtags and a thumbnail frame.
- **`lineart` and further style packs:** retro-TV and colourful hand-drawn.
- **Hero shots:** an optional image-to-video lane for at most 1–2 beats, within the budget.
- **Studio:** reads and edits `episode.json`.

## Definition of done

One request produces a 30–60 s vertical Short on an arbitrary topic, with:

- a hook in the first 2 s
- consistent characters
- motion on every beat
- word-synced one-line captions
- SFX and clean audio
- a QA report

The total cost stays under the budget cap. Re-running after editing one beat regenerates only that beat.
