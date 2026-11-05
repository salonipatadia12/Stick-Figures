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

Everything is composited locally in Python. The existing studio rig stays as a separate,
zero-cost lane for formats with a recurring character.

Decisions and the audit behind this plan are in [AUDIT.md](AUDIT.md).

## Ground rules

- **Git:** work on `pipeline-v2`. Never push to `main`, force-push or rewrite history.
- **Nothing is deleted without asking.** Files are archived instead; the GitHub Release
  `archive-2026-11` holds the first batch.
- **Keys** come only from `.env`: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, and optionally `GEMINI_API_KEY`.
- **Budget:** every paid call goes through the episode's cost ledger, with a per-video cap
  (`--budget`, default $2.00).
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
  style reference. That means `outputs/the-empty-chair-story-order.mp4` and its storyboard.
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

### 1. Episode spec and script engine (built; waiting on a real run)

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

### 2. Style pack and consistent images

- **Complete `styles/cozy/`:**
  - `style_lock.txt`
  - `character_sheet.png` (front, side and 3 expressions), built from The Empty Chair frames
  - palette and caption spec
  - the SFX map and motion defaults
- **`pipeline/images.py`:** OpenAI gpt-image through `IMAGE_MODEL`, with Gemini optional.
  - Every call passes the character sheet and the style lock.
  - Images are generated at the chosen aspect, at medium quality by default, and cached.
- **Pose variants:** generated as image edits of the same frame.
- **`pipeline/layers.py`:** cuts out the character and keeps a clean background, for parallax,
  bobs and entrances.
- **`--approve style`:** first renders a 3-frame style board (the hook, a middle frame and the end).

### 3. Voice and captions

- **`pipeline/voice.py`:** ElevenLabs is the narrator for finished videos. edge-tts is the free
  voice for drafts and previews. One voice per episode, cached by text and voice.
- **`pipeline/align.py`:** word timings come from the TTS provider, with faster-whisper as the
  fallback. Beat durations come from the real audio; speech is never stretched to fit.
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
- character consistency, by sending the contact sheet and the character sheet to Claude vision

**Sample:** "Why your salary disappears by the 20th" (personal finance), in the **cozy** style,
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
