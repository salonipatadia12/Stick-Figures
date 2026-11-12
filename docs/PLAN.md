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

**Since 2026-11-10 the pipeline is $0 per video.** The Anthropic credits ran out, so research, storylines and scripts
moved to free local tools: DuckDuckGo search with page extraction for research, and Ollama with Qwen 2.5 (Apache-2.0;
`qwen3.5:9b` on this 16 GB Mac, chosen over `qwen2.5:14b` on 2026-11-11) for the writing (`SCRIPT_PROVIDER=ollama`, the default). The editorial rules and the
checker are unchanged; the Claude path stays behind `SCRIPT_PROVIDER=anthropic`. Details in
[LOCAL_MODELS.md](LOCAL_MODELS.md#scripts-ollama--qwen-25-2026-11-10).

Decisions and the audit behind this plan are in [AUDIT.md](AUDIT.md).

## Ground rules

- **Git:** work on `pipeline-v2`. Never push to `main`, force-push or rewrite history.
- **Nothing is deleted without asking.** Files are archived instead; the GitHub Release
  `archive-2026-11` holds the first batch. The one standing exception (user 2026-11-11) is the cleanup in §9: old
  search and model caches, old shared page-cache entries, `__pycache__`, `git gc`, and stale per-beat clips after a
  finished render. It never touches the repo, episodes, rendered videos, `.env` files or keys.
- **Keys** come only from `.env`. With `SCRIPT_PROVIDER=ollama` (the default since 2026-11-10) none is needed;
  `ANTHROPIC_API_KEY` is used only by the paid path (`SCRIPT_PROVIDER=anthropic`). Nothing that needs a key, an
  account or a free tier is used for search.
- **Local models only** for images and voice, and only under licences that allow commercial use.
  No FLUX "dev"-licence models, no F5-TTS or XTTS (non-commercial). A rented GPU is never used
  without asking first.
- **Budget:** every paid call goes through the episode's cost ledger, with a per-video cap
  (`--budget`, default $2.00). With local models and `SCRIPT_PROVIDER=ollama` no step is paid; local model calls
  and DuckDuckGo searches are recorded in the ledger at $0.00 (providers `ollama`, `duckduckgo`).
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
- **`lineart`, `retro-tv`, `colorful` (2026-11-11, direction only):** visual directions adapted from kaomei's
  B1, B3 and B2 specs (§9). The writer follows them; the painted libraries exist only for cozy, so a render still
  uses the cozy art until each pack's bases, host and inserts are painted.

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
- **Since 2026-11-10 (default `SCRIPT_PROVIDER=ollama`, $0):** research is DuckDuckGo (six parallel queries: four
  templates and two from the model) -> reputable pages fetched while the searches still run -> at most two facts per
  page, each quoted from the page and kept with its URL (`pipeline/search.py`). The script is two stages on the local
  model: an outline, then each beat's staging in parallel, with the output constrained to the library's values and
  the narration's words, then the same `Episode.check()` and at most three rounds of repairs of only the failing parts
  (`pipeline/script.py`, `write_free`). Three cache layers: searches 24 h, pages 7 days (shared), model answers by
  prompt hash (`pipeline/cache.py`).
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

**Status (2026-11-06):** the full sample is built with these. Beats 1–9 are on four painted bases
(`styles/cozy/bases/`), choreographed in `scripts/motion_test/scene_salary.py` (beats 1–2) and
`salary_beats.py` (beats 3–9), and rendered by `scripts/motion_test/episode_salary.py`.

**Round 5 (2026-11-07, user review of the full sample):** the beat-2 "W" pose replaced (hands on the table
edge, leaning in); head sprites cut at their own outline with no backing ring (the ellipse is re-fitted
to the stroke; all four bases rebuilt, round-4 bases archived in `renders/bases/archive/round4/`); the
receipts read RENT → BILLS → LEFT and MON … FRI → PAYDAY and are at least as big as the checklist; the
jars read BILLS → WEEK 1 → WEEK 2; the crossed-off calendar week stays visible in beats 8–9 at the beat-1
size; the clink uses each character's inner arm; a QA check fails any on-screen text under 40 px.

### 2b. Base poses a script needs (`pipeline/bases.py`, `stick bases`)

When a script needs a setting or a pose the painted-base library doesn't have, that is a pipeline
step, not a one-off (user, 2026-11-07):

1. `stick make` (after the script gate) prints the **base plan**: per beat, the setting, pose, view and
   cast read from the shot text, and the library base that serves it (`styles/cozy/bases/library.json`).
   A top-down request falls back to the eye-level base of the same pose; two characters with no
   two-shot base fall back to one base each, cut as two shots. A beat can force a base with
   `shot.layers: ["base:<name>"]`.
2. Missing bases stop at the **`bases` gate**. `stick bases <slug> --candidates` renders two FLUX.2 klein
   candidates per missing base (local, free) and a review sheet in `renders/bases/candidates/`.
3. The user picks one per base: `stick approve <slug> --gate bases --pick <base>=<file> ...`. The pick is
   recorded in the library with its provenance, and the base is built into layers for this and every
   later video. Picks may come over several calls; the gate closes when nothing is missing.
4. The user may reject a base's candidates (`--reject <base>="reason"`, recorded in
   `episode.meta['bases']['rejections']`). The next `stick bases --candidates --direction <base>="..."`
   renders a redo round: three new seeds, the user's direction in place of the pose template, and the
   approved bases of the same setting as FLUX reference images so the new pose keeps the same room, bed
   and props. Each round gets its own sheet and jobs file (`-r2`), earlier ones stay on disk.

The description step is keyword-based for now; the next script prompt version should ask Claude for
explicit `setting` and `pose` fields per beat (a paid-prompt change, so not done mid-episode).

**First use (video 2, 2026-11-08):** three bedroom bases. The lying-awake base took three rounds (the
user rejected round 1 with a written brief; round 2 kept the reference's legs out of bed and was redone
by the pipeline before being shown). Continuity between bases is a FLUX edit on an approved candidate
(quilt, headboard and clock made to match), shown next to the other bases before use; small props that
appear in several bases (the alarm clock) are one sprite cut from one base and placed by `places` in
each. The user's standing rule: new bases and their edits are always shown on a sheet before anything
is built.

### 2c. Rendering an episode (`scripts/motion_test/episode_runner.py`)

`Runner(slug, beats_module)` does what `episode_salary.py` did for the sample, for any episode: Kokoro
narration per beat (`renders/voice/<slug>/`), the timeline, one cached H.264 clip per shot, captions,
SFX, master, QA. A beats module gives `BEATS` (shot name → `beat_base.Beat` subclass) and optionally
`shots(tl)`. Video 2 is `episode_sleep.py` + `sleep_beats.py`; props no base has are code-drawn in
`pipeline/rig/drawn.py`. The salary sample still runs on its own script; moving it onto the runner is
a cleanup for later.

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

**Status (2026-11-06):**
- Kokoro clips are trimmed to their speech and laid end to end with a 0.25 s breath.
- `pipeline/captions.py` aligns the script's own spelling to the word timings, groups lines by
  meaning (whole clauses, split where a phrase starts, no one-word orphans), highlights the current
  word, burns them in above the bottom quarter and writes the `.srt`.

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

**Status (2026-11-06):**
- Shots render in parallel and are cached by a digest of their timing and code.
- On-screen graphics are in `pipeline/overlays.py`: slams, the checklist card, ink arrows and circles,
  a speech bubble, tags, receipts.
- SFX are synthesized in `pipeline/sfx.py`, so there is no sample library and no licences. A quiet
  plucked bed is ducked under the voice, and a two-pass loudnorm masters the mix to −14 LUFS with
  true peak at most −1.5 dB.

### 5. QA and the first end-to-end Short

**`pipeline/qa.py`** writes `qa.json` and `contact_sheet.jpg`, checking:

- freezes in the picture area
- captions: one line, inside safe areas, every narrated word present
- loudness and true peak
- duration within ±15% of the target
- on-screen text at least 40 px tall (type size on the 1080×1920 frame), captions included
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

### 7. Any topic (north star, user 2026-11-08; A, B, G built 2026-11-08, C and D built and awaiting their first run)

**Where it stands (2026-11-08):**
- A: `shot.staging` (setting, poses, actions, props) is in the schema (`pipeline/models.py`, prompt `script-v3`);
  `Episode.check` validates it and prints feasibility warnings with staging suggestions; `pipeline/bases.py`
  plans from it (keywords remain the fallback for v1/v2 episodes); the review shows a STAGE line per beat.
- B: `pipeline/rig/verbs.py` (16 in-place verbs returning `Move`s with hold/flight schedules) and
  `scripts/motion_test/compose_beats.py` (`for_episode(slug)` → BEATS, shots, LIBRARY_REPORT). Posture verbs cut
  to the second base when the library has it, else a punch-in with a note. Run an episode with
  `scripts/motion_test/episode_compose.py <slug>`.
- C: `stick props <slug> [--generate]` (`pipeline/propgen.py`, `scripts/motion_test/flux_props.py`): text-to-image
  at 512 px, key-out, QA (one piece, size, border, colour), prop library `styles/cozy/props/library.json`; a
  sheet only for props that fail both seeds.
- D: `stick pack starter --candidates` (`pipeline/basepack.py`): 24 bases, one sheet per setting under
  `renders/bases/candidates/pack-starter-<setting>-candidates.jpg`; `--pick/--reject/--direction` as at the
  bases gate. Rendered 2026-11-08 (48 candidates), waiting for the user's picks. The per-episode bases gate
  reuses the pack's places, poses and two-character prompts (`bases.candidate_jobs`), skips bases the pack has
  candidates for (`basepack.covers`), and inserts need no render. `stick build-bases` (`pipeline/basebuild.py`,
  `scripts/motion_test/build_pack.py`) renders the two FLUX plates per approved base (arms removed, characters
  removed; two seeds for the arms plate, the better one picked by how much navy it removed), derives the spec
  with `spec_from_plates.py` (one or two characters; the scarf marks the friend) and builds the layers. Arms at
  the sides derive cleanly; arms crossed in front still need hand-written rests. Seated-at-table and desk torsos
  stay painted (no table layer to derive); sofa and bench torsos lean.
- C ran on the three proof scripts: 17 of 20 props passed QA into `styles/cozy/props/`; the sunset's
  sunbeam, dust specks and photo failed both seeds (thin or translucent things FLUX does not render as one
  object) and have drawn stand-ins (`drawn.sunbeam`, `drawn.specks`, `drawn.photo`) proposed to the user.
- G: `qa.json['library']` on every render (`library_pct`, hand-written beats, per-beat detail); the runner
  prints it. Video 2 scored 0 % (all beats hand-written), the salary episode composes at 100 % (untested on
  screen).
- E and F: not started.
- H (2026-11-08, user: the proof videos were 100 % library but talking heads; viewers must SEE the idea):
  script v4 gives every beat a visual type: `character`, `insert` (a full-frame painted scene of the subject with
  no characters) or `graphic` (a code-drawn diagram), and an optional `reaction_anchor` that cuts from the insert
  to the cast reacting. Explainers aim for at least half of the beats showing the subject; numbers drawn on screen
  must be numbers the beat says. Inserts: `stick inserts <slug> --generate` (`pipeline/inserts.py`,
  `scripts/motion_test/flux_scenes.py`, `scene_qa.py`) paints each scene as a FLUX.2 klein edit against an Empty
  Chair frame used as a style reference (closer to the look than text-to-image, tested on one scene), two seeds,
  QA (detail, exposure, no character head, picks the better seed), a question only when both fail; library
  `styles/cozy/inserts/library.json`. Crossfade variants (the same place at sunset) are edits of the first scene.
  Animation in code (`pipeline/rig/inserts.py`): ash/snow/dust/embers/rain particles, a drifting haze band, colour
  grades, dim/flash, a moving sun, crossfades, push/pan/shake, labels and arrows. Graphics
  (`pipeline/rig/graphics.py`): flow, bounce, scatter, waves, two_paths, bars, thermometer, cards, layers.
  Second score in qa.json: `subject` (beats where the subject fills at least half of the beat; target 70 %), next to
  `library`. QA also fails text over text, text over the caption, and text on a character's head or body; the text
  slot shows one text at a time and moves off the characters; standing characters are never framed as body-filling
  close-ups. Voice clips are cached by their text (a changed line is re-voiced, the old take archived).
  Timing (review of the first insert renders): every text, label and diagram item lands at least 1.2 s before its
  shot cuts (`overlays.settle`), light/colour changes finish 0.6 s before the cut (`rig/inserts.settle_effects`),
  and an on_screen_text with no text recipe is still slammed on the first word it shares with the narration (the
  script gate warns about it, and about a number_counter that would drop the text's words). QA also fails text on
  screen for under ~1 s (`text.brief_text`) and any text the script promises that never appears
  (`text.missing_text`). Subscript digits (SO₂, CO₂) are drawn by hand (the UI font has none). Shot clips are cached
  on their beat's script entry and the insert/prop libraries too. The words check transcribes in windows of at most
  20 s cut in the pauses before beats (Whisper skipped a whole sentence at a 30 s window edge of a clean mix).
- Host format (script v5, user 2026-11-08/09): the lead HOSTS every video (michael voice, lip-sync from word timings,
  eyes to camera) and tells it end to end: hook (<= 8 words, host in frame 1, answered in the payoff) -> promise ->
  story -> payoff -> loop; three hook options picked at the script gate (`stick approve --hook N`). The host is
  composited full-body into every painted scene (`HostLayer` in compose_beats: a body cut once from a standing base,
  `scripts/motion_test/host_cutout.py` -> `styles/cozy/host/`, the clean s426 head, the scene's light, a contact
  shadow, off-centre so captions stay clear) or pops in under a diagram to point; character beats use an approved
  standing base of the setting, else the nearest painted scene of that setting. Host verbs: shiver, look_up, snap,
  freeze; a host `cut_on` word moves the host to the next spot by a cut (no walk cycles). Diagrams are drawn on
  watercolour paper with a hand-drawn wobble. Sound: no music by default (`--music` adds the soft bed), only the
  script's own cues, at most 6. QA adds `character` (host on screen, target 70 %), `hook` (pass/fail), `fill`
  (no near-empty frames) and `text.caption_on_character_s`. The writer's schema is sent with a flat `picture`
  (a nested insert/graphic schema compiled to a grammar the API rejects).
- Host fixes (user 2026-11-09): one host look: character beats stand the composited host in the base's empty plate
  (key `base:<name>`), never the base's painted figure. The host is lit AFTER the scene's colour grade by
  `light_match` (tint <= 6 % per channel, light within 12 %, a thin rim of the scene's highlight on the lit edge), so
  it stays white in a red sunset. It is clamped fully inside the frame (pop-ins excepted). Arms rest relaxed at the
  sides; `acting` gives every clause a gesture (question -> shrug, surprise -> step back, conclusion -> nod, big
  number -> open hands, "you" -> lean in, a thing in the scene -> present it; otherwise talking hands), and fills any
  still stretch of 1.6 s or more. Captions slide sideways, then rise above the head, to stay off the host's pixels.
  A diagram's first item is on the paper from the first frame (the thermometer and the atmosphere's layers are drawn
  at once). QA: `fill` measures content against the frame's median colour (blank paper 0.06, fail < 0.12 for 0.5 s;
  the old texture measure passed blank watercolour paper), `host` fails a standing host cut by the frame edge or
  tinted more than 15 %, and `text` fails any caption on the host's pixels.
- The friend in the host format (user 2026-11-10): a beat whose cast has `friend` composites it too (`FriendLayer`
  in compose_beats, a sibling of HostLayer drawn by the same code: light match, contact shadow, clamp, size). Its body
  is cut once from friend-standing-street (`host_cutout.py friend-standing-street` -> `styles/cozy/host/friend-standing`,
  teal scarf kept, painted hands erased). It stands opposite the host, facing it, with the host's head size and feet
  line, and walks in on an enter_left/right recipe. Its verbs are aimed at the host (reach, point, nod, shrug, gaze,
  tickle); a friend `reach` toward the lead while holding a feather is a `tickle` (the feather's tip wiggles at the
  host's side for 1.2 s, the friend standing just close enough) and the host `laugh`s on it. Held props: the props
  library, else drawn (`drawn.feather_stick`, `drawn.bill`). Captions and QA (`host`, `text.caption_on_host`) count the
  friend's pixels as the host's. Seated staging (a cafe table) is played standing.

The pipeline must make stick-figure videos about any topic, not café explainers. Videos 1 and 2 each
needed a hand-written beats module (video 2: 9 beats, every one with at least one bespoke pose or
prop). The next phase turns those one-offs into libraries, so a new script is composed from them and
hand-written code is the exception. Target: 80 %+ of a video's beats built from libraries alone.

Order below is by dependency. Steps A–C, E and G are code and tests only (free). Step D is local FLUX
time plus the user's one approval round per setting. Step F is the only part that reopens the look.

**A. Script v2: explicit staging fields (prompt change, paid on the next script call).**
`DraftShot` gains `setting` (one noun phrase from a fixed list plus "other: …"), `poses` (cast id →
pose from a fixed list: seated-at-table, standing, lying-on-back, bed-edge, walking…), `actions` (0–3 of
`{who, verb, target, anchor}` with `verb` from the action library's vocabulary in step B) and `props`
(`{name, held_by, where}`). `visual_intent` and `image_prompt` stay for the picture. Base planning
(`pipeline/bases.py`) reads `setting` + `poses` + `cast` directly; the keyword path stays as a fallback
for episodes written under schema v1, so nothing is regenerated. The validator adds **feasibility
checks at the script gate** (user item 8): a verb outside the vocabulary, a posture change inside one
beat (sit → stand), a walk longer than a few steps, a crowd above the street-figure count, animals, or
fast physical action is printed as a warning with a staging suggestion ("cut to the arrival on 'then';
use `lead-standing` + push_in"). Warnings don't block; the user decides at the gate.

**B. Action library (`pipeline/rig/actions.py`, from videos 1 and 2).** Existing: idle, shrug,
facepalm, arms_wide, rub_hands, look. Add parameterised verbs, each `verb(t0, who, target=None,
dur=…, side='auto')` returning an `Action` that resolves hand shapes and reach from the base
geometry: `reach`, `point`, `pick_up` / `put_down` (prop enters or leaves the held set with gripping
shapes, as the b09 cup lift does), `throw`, `drink`, `type`, `write`, `wave`, `nod`, `shake_head`,
`slump`, `jolt`, `gaze`. Posture verbs `lie_down`, `sit_up`, `stand_up` and `walk` cannot be done on
a painted torso; they become **staging verbs** the composer resolves to a match cut between two bases
of the same setting on the anchor word, or (walk) to a pan with the code-drawn distant figure. A
generic beats module (`compose_beats.py`) builds each beat's `Performance` from the script's
`actions` + word times, so an episode renders with no per-beat code when every verb resolves. The
two existing beats modules are refactored onto the verbs where the result is unchanged on screen
(checked with frame diffs against the current renders); they are not re-rendered.

**C. Props step (`stick props <slug>`, automatic).** For each script prop not already in
`styles/cozy/props/library.json`, base `props` or `pipeline/rig/drawn.py`: one FLUX.2 klein job, two
seeds, 512 px, the prop alone on a plain neutral ground in the Empty Chair palette (about 25 s each),
then cut with `key_neutral` + `clean_alpha`, and QA: one connected component, 15–70 % alpha
coverage, no border contact, palette distance within the style's range, not a character. Pass →
the sprite enters the prop library with provenance and is placed by the composer. Fail on both seeds
→ the prop goes on a one-row sheet and the user is asked; this is the only time props prompt.

**D. Starter base pack (one approval round per setting).** Proposed settings: office desk,
kitchen counter, living-room sofa, street/sidewalk (standing), park bench, classroom (desk and
board), gym (bench or mat), car interior (driver's seat). Per setting three bases: lead alone, friend
alone, together. 24 bases × 2 candidates ≈ 48 FLUX images ≈ 65 min, plus two plates per approved base
≈ 65 min, all local and free. One sheet per setting; the user picks once per base (the existing
`bases` gate, no episode needed: `stick bases --pack starter`). Builder: a `spec_from_plates` helper
derives head ellipse, shoulders, hands and the occluder from the plate diffs and prints a debug
overlay, so a base no longer needs a hand-written spec; a spec override stays possible.

**E. Info-graphics layer (`pipeline/infographics.py`).** One API for every component:
`component(frame, t, spec, t_in, centre, size)` with reveal animation and the text log QA already
reads. Components: counter, checklist, receipt, bar chart, line chart, arrow, comparison split,
timeline. The script recipe `infographic` carries the spec in `detail` (JSON). The existing
`text_slam`, `checklist_tick`, `split_compare`, `arrow_draw` and `number_counter` move onto it.

**F. Formats and cast.** 16:9 long-form: `W, H` come from `episode.aspect`; captions and text
positions from the safe area, not constants. Painted bases are portrait, so a landscape video needs
landscape candidates (FLUX at 1680 × 944) for the settings it uses; the starter pack renders landscape
only for settings a long-form topic needs. New cast (boss, kid, older person): FLUX candidates in the
Empty Chair style with the lead as reference, approved on a sheet, built with the same head/arms rig;
`style.cast_ids` grows and the script prompt may cast them.

**G. Library report on every video.** The composer marks a beat `library` when no hand-written
`Beat` subclass was needed; `qa.json` gains `library_pct` and the per-beat list, and the render
report prints it. Target ≥ 80 %. Video 2 scored 0 % by this rule (every beat had bespoke code), so
this is the baseline.

**Proof.** Three topics far from each other, 9:16, each through the whole pipeline with
`compose_beats` only: a history story, a psychology tip and a science explainer (candidates: the 1816
"year without a summer"; why you forget why you walked into a room; why sunsets are red). Expected
paid cost ≈ $0.19 each (research + script), well inside the $2.00 cap per video. Gates as usual:
script (user), bases when a setting is missing (user, sheet), props only on QA failure. Where a beat
can't be composed, it is written by hand and counted; the report gives each video's library %. If a
video scores under 80 %, the missing verbs or components go into the libraries before the next one.

### 8. Storytelling (user 2026-11-09)

The proof videos all used one template (hook -> "let me show you" -> paper diagram with the host popping up
bottom-left -> bar chart -> loop) and told thin facts in order. Built 2026-11-09:
- **Story before script** (`pipeline/story.py`, gate `storyline`): research, then three storylines in three different
  formats (mini_story, myth_vs_fact, what_if, countdown, mystery, pov, experiment), each with a hook, a 2-3 line
  pitch, conflict, stakes, twist, payoff, picture ideas and its facts. The user picks one (`stick approve --gate
  storyline --story N`); only then is the script written around it (script-v6). If the facts cannot carry a real
  story the pitch says so (`story_note`) instead of forcing one.
- **Formats rotate:** never the format of the video picked last; topics waiting at their storyline gate are offered
  disjoint formats, so two videos picked together differ. The gate refuses a repeat.
- **Story check at the script gate** (`Episode.story_report`): an early "problem" beat, rising stakes ("stakes",
  or two "clue"/"reason" beats for a mystery or countdown), a "twist"/"reveal" before the payoff, and a payoff that
  answers the hook. Failures are errors.
- **Visual variety:** host `side` (left/centre/right) and `size` (small/medium/large/close) per beat, a held prop
  (`host.holds`, from the props library), pop-ins from either bottom corner; `split` (two painted scenes stacked
  with labels, the second wiping in on its word); `focus` (a close-up push into one region); `pin` (a map pin on a
  painted map); at most two diagrams per video. QA `layout` notes (does not fail) a video whose format or layout
  pattern repeats the previous one (`renders/sample/layouts.json`).

### 9. Kaomei Director integration and cleanup safety (user 2026-11-11; built)

Adapted from [kaomei/stickman-video-director](https://github.com/kaomei/stickman-video-director) (MIT), Mode A
(Director) ideas only. Details, credit, and what was not taken: [KAOMEI_INTEGRATION.md](KAOMEI_INTEGRATION.md).
- **Story:** a `revelation` format (hook breaks a belief, then the hidden mechanism, the truth, a comment question).
- **Script rules (script-v7):** rule 7 adds three timed phases per beat ([0-1s] inherit, [1-3s] transform, [3-end]
  land and set up the next) and at least three visual devices; rule 8 adds continuity, so each beat names how its
  last frame sets up the next (`shot.continuity`); rule 18 sets pacing by length (30 s: 7-8 beats, 45 s: 9-11,
  60 s: 12-14), and the local writer's beat aim follows it. The rule changes invalidate cached script outputs (user:
  fine). Research caches are keyed separately and are not re-run.
- **QA:** `continuity` warns (never fails) when adjacent beats change place on a hard cut with no named link.
  Scripts written before v7 are "not checked".
- **`stick package <slug>`:** a thumbnail from the rendered video, 5 titles, description and hashtags, a pinned
  comment and a pre-publish checklist in `episodes/<slug>/out/`. $0: rules by default, `--titles` makes one local
  Ollama call. Nothing is uploaded; "your final approval" stays pending until you give it.
- **Style packs:** `lineart`, `retro-tv`, `colorful` (direction only; see Style).

Cleanup, so a lost or full disk never takes the work with it:
- **`scripts/cleanup.sh`:** a dry run by default; `--apply` deletes and logs every file and the space freed to
  `logs/cleanup.log`. It removes only:
  - search and model caches older than 7 days, for episodes with a rendered video
  - shared page-cache entries older than 7 days
  - `__pycache__`
  - plus a `git gc`
  
  The research, storyline and script records stay, as provenance. It deletes regular files one by one inside the
  repo (no `rm -r`, no symlinks followed) and refuses to run anywhere that is not this repo.
- **`deploy/com.stick-figures.cleanup.plist`:** a launchd agent that runs `cleanup.sh --apply` every Sunday at
  04:00. It is not installed automatically; the install lines are in the file.
- **After each Mac worker job (`macworker/cleanup.py`):** stale per-beat clips (`renders/sample/<slug>/shots/` files
  that the finished render's `shots.txt` no longer lists, and older than it) are deleted. The listed clips are the
  render cache, so editing one beat still re-renders only that beat. The Ollama model is unloaded when it is in
  memory. Space freed is logged. On 2026-11-11 the existing renders held about 294 MB of stale clips; they go after
  each episode's next render.

### Backlog

- Character polish: fingers and hands (user 2026-11-09: later, not now).

## Definition of done

One request produces a 30–60 s vertical Short on an arbitrary topic, with:

- a hook in the first 2 s
- consistent characters
- motion on every beat
- word-synced one-line captions
- SFX and clean audio
- a QA report

The total cost stays under the budget cap. Re-running after editing one beat regenerates only that beat.
