# Stick Figures — reusable reel builder

## Browser studio

The new [Story Studio](studio/README.md) adds a browser workflow: prompt → editable
story → story approval → illustrations → storyboard approval → narrated MP4.
It includes the corrected **The Empty Chair** as a finished example and supports
manual stories, uploaded artwork, and your own voice recordings without an AI key.
AI drafting and illustration require a server-side `OPENAI_API_KEY`; no key is
configured in the checked-in project.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-studio.txt
cp .env.example .env
.venv/bin/uvicorn studio.app:app --env-file .env --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. [Server/Docker setup, limits and tests](studio/README.md).
This is a local application with deployment files, not an already provisioned
public service. The original JSON/CLI renderer remains available below.

## Original JSON builder

Create new narrated stick figure reels by changing a story JSON file, not animation code. The renderer uses an original expressive character with articulated arms, restrained gestures and anchored prop contacts. Four fixed scene layouts keep movement and text aligned.

## Stickman Video Director integration

The new `cozy` renderer adapts the **Style 4** character and directing principles
from [kaomei/stickman-video-director](https://github.com/kaomei/stickman-video-director).
It adds a white filled torso, illustrated study/café/garden settings, purposeful
wide/close/overhead cuts, and notebook content that persists across scenes.
Writing hands and pencils share an anchor; feet stay planted.

[Watch the updated 28-second reel](outputs/the-first-page-cozy.mp4) ·
[Storyboard](outputs/the-first-page-cozy.storyboard.jpg) ·
[Integration and MIT attribution](references/stickman-video-director/UPSTREAM.md)

```sh
python reel.py preview examples/the-first-page-cozy.json --output renders/cozy.jpg
python reel.py render examples/the-first-page-cozy.json --output renders/cozy.mp4
```

This is a local adaptation of the repository's visual/directing guidance.
The upstream project supplies a skill and prompts, not a renderer; this project
does not run its Flow/Gemini generation workflow or reuse its video clips.
The updated example retains the previous Andrew narration.

For reusable notebook stories, set `character_style` to `cozy`, `background` to
`desk`, and `message` to an empty string. Set `setting` to `study`, `cafe`, or
`garden`; `camera` to `auto`, `wide`, `close`, or `detail`; and `page_lines` to
0–7 existing lines. `notebook_title` sets the cover/page heading (24 characters);
`page_text` supplies up to eight newline-separated lines of 26 characters each.
Keep those fields consistent across scenes. `type` writes one additional line. `auto` schedules cuts
around the action. These controls are deliberately limited to notebook scenes;
other objects and actions need additional drawing components.

## New character design

New renders use `"character_style": "expressive"` by default: simple oval eyes, bold rounded limbs, small hands and feet, and a teal scarf. The previous character is available with `"character_style": "classic"`. Existing exported videos are preserved.

[Before / after](outputs/character-redesign.png) · [Pose sheet](outputs/character-pose-sheet.png) · [Narrated redesigned reel](outputs/the-first-page-redesigned.mp4) · [Reference study and engagement counts](outputs/character-reference-study.md)

```sh
python reel.py render examples/the-first-page.json --output renders/redesigned.mp4
python scripts/character_review.py outputs
```

The design combines simpler facial detail with readable bent joints, grounded feet and deliberate motion. It is an original local drawing system; reference video footage/assets are not bundled. See the reference study for what was actually sampled and the limitations of engagement comparisons.

## Setup

Requires Python 3.10+, FFmpeg and ffprobe on PATH. macOS uses installed Arial; Linux needs DejaVu Sans (`fonts-dejavu-core` on Debian/Ubuntu).

From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r outputs/the-message-source/requirements.txt
python reel.py --help
```

On Windows, font paths currently need adapting in `outputs/the-message-source/builder/art.py`. macOS/Linux are the supported defaults.

## Make a reel from a different prompt — no paid API needed

**1. Create an authoring prompt:**

```sh
python reel.py prompt \
  --prompt "A 30-second story about someone who keeps waiting to write the perfect first page" \
  --output authoring.md
```

**2. Paste `authoring.md` into your writing assistant** (including this Codex chat). Save the returned JSON as `my-story.json`. You can also copy and edit `examples/the-first-page.json` directly. A short topic still needs a writing step; the local renderer does not invent an arbitrary story itself.

**3. Check the story and inspect its storyboard:**

```sh
python reel.py validate my-story.json
python reel.py preview my-story.json --output renders/storyboard.jpg
```

**4. Render it:**

```sh
python reel.py render my-story.json --output renders/my-reel.mp4
```

The first render creates neural narration through edge-tts, which needs internet access. There is no API key for that speech path, but service availability can change. Audio is cached by narration text, voice and speaking rate. Later renders reuse it; edits to those fields generate new audio.

The output folder contains the MP4, a storyboard, a timing JSON, a render log, and an ignored `.reel-cache` directory. Existing MP4s are protected: add `--force` deliberately to replace one.

## Optional: automatically draft a story with the OpenAI API

This is an optional paid API path, separate from the no-API workflow above. Configure `OPENAI_API_KEY` in your environment. Do not put keys into story files or commit them. Use an available model that supports Responses structured outputs:

```sh
python reel.py draft \
  --prompt "A warm 30-second story about asking a friend for help" \
  --model YOUR_SUPPORTED_MODEL \
  --output my-story.json
python reel.py preview my-story.json --output renders/storyboard.jpg
python reel.py render my-story.json --output renders/my-reel.mp4
```

Drafting saves a validated storyboard rather than immediately rendering, so you can review the story before speech generation. The integration follows [OpenAI's structured outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs). Its request/response handling is covered by mocked tests; a live paid API call has not been tested in this project.

## Ready-to-run examples

Rebuild **The Message** entirely offline, using the included narration:

```sh
python reel.py render examples/the-message.json \
  --output renders/the-message.mp4 --offline
```

Make **The First Page**, a different story using desk and standing scenes:

```sh
python reel.py render examples/the-first-page.json \
  --output renders/the-first-page.mp4
```

[Watch The Message](outputs/the-message.mp4) · [Watch The First Page](outputs/the-first-page.mp4)

## Editing a story

```json
{
  "version": 1,
  "title": "The First Page",
  "voice": "en-US-AndrewNeural",
  "rate": "-3%",
  "scenes": [
    {
      "narration": "She waited for the perfect idea. The page stayed empty.",
      "caption": "The page stayed empty.",
      "background": "desk",
      "action": "idle",
      "expression": "neutral",
      "overlay": "MONDAY",
      "message": "",
      "event_delay": 0.6,
      "duration": 3,
      "audio": ""
    }
  ]
}
```

| Background | Supported actions | Visible scene |
|---|---|---|
| `home` | `idle`, `read`, `type`, `send`, `think`, `nod`, `celebrate` | Seated character; phone for read/type/send |
| `desk` | `idle`, `read`, `type`, `think`, `nod` | Character with a notebook; type makes a writing gesture |
| `cafe` | `idle`, `nod` | Two people with coffee |
| `standing` | `idle`, `think`, `nod`, `celebrate` | One standing character |

Expressions: `neutral`, `happy`, `tired`, `shock`.

Top-level `character_style`: `expressive` (default), `classic`, or `cozy` (the notebook scene system described above). This applies consistently to every scene in the reel.

- `narration` controls the spoken words. Audio duration determines scene length, with a short pause afterward.
- `caption` is the short bottom caption; `overlay` is a top label; `message` is an optional message panel. Keep these concise. Validation fits text into fixed safe regions and rejects text that cannot fit.
- `event_delay` is when a message panel appears, in seconds from scene start.
- `duration` is used for scenes without narration/audio. It is not a target for speeding up narrated speech.
- `audio` optionally points to your own local narration file, relative to the JSON file. When supplied, that file takes precedence over generated speech; changing narration text will not replace your chosen recording.
- `voice` and `rate` configure generated narration. A different voice/rate requires new cached speech.

## Offline and motion previews

```sh
# No speech/network: estimate timings from narration text for a motion preview
python reel.py render my-story.json --output renders/silent.mp4 --silent

# No network: require all narration to be supplied or already cached
python reel.py render my-story.json --output renders/offline.mp4 --offline
```

`--silent` includes a silent audio track. `--offline` fails with a clear error if any narration is missing. Use the same output directory to reuse its narration cache.

## Scope

The builder supports small everyday stories using the listed layouts, expressions and gestures. A prompt cannot currently create arbitrary new locations, clothing, objects, camera angles or fight choreography. Those need new drawing components. Output is fixed at 1080 × 1920, 30 fps, H.264/AAC; stories support 1–20 scenes up to 180 seconds total. The first example shows a roughly 30-second result, not a promise that every prompt produces exactly 30 seconds.

The original renderer and its exact shipped video remain available under [outputs/the-message-source](outputs/the-message-source/README.md). New projects should use `reel.py`.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests cover fixed arm lengths, reachable contacts throughout supported gestures, classic-style compatibility, validation, text overflow, audio-cache changes, missing API credentials, mocked structured responses, output overwrite protection, side-effect-free imports, and a real FFmpeg export/decode.

## Character-first production direction

The latest visual target is [this generated reference](outputs/character-focus-reference.png).
It puts the face, upper body and hands in the foreground, closely following the
README café character from Stickman Video Director. The earlier Pillow render is
not equivalent to the generative animation used by that repository's demos.

[Production analysis and pilot plan](outputs/character-focus-production/README.md)
records the exact distinction and the proposed acting beats. The
[pilot prompt](outputs/character-focus-production/pilot-prompt.txt) is a prepared
external-generation input, not evidence that a video has already been generated.
The Flow attempt did not produce a video. Production now uses the local illustrated
character renderer described below; the original local outputs remain available for comparison.

## Local illustrated character acting study

The approved shaded character is now animated locally in a
[10.5-second narrated study](outputs/character-acting-test.mp4), with blinking,
a small head movement and breathing, a rigidly held pen, and a two-bone writing arm.
The background stays still while the character carries the action.
[Source, artwork, narration and rebuild instructions](outputs/character-motion-source/README.md)
are included. Rebuilding uses Pillow/FFmpeg and the bundled narration; it does not
require Flow, credits or an API key. This is a motion study before a full reel.

## Complete illustrated reel

[The First Page — complete 30.5-second illustrated cut](outputs/the-first-page-illustrated.mp4)
puts the approved character into a complete story, with continuous narration,
worried/relieved expressions, a notebook that fills over time, captions, original
quiet music and timed pencil sounds. The background remains secondary to the face
and hands. Rebuild it locally with:

```sh
outputs/the-message-source/.venv/bin/python outputs/character-motion-source/assemble.py
```

[Storyboard](outputs/the-first-page-illustrated.storyboard.jpg) ·
[Source and instructions](outputs/character-motion-source/README.md)

## New short story: Two Minutes

[Watch the 10.5-second reel](outputs/two-minutes.mp4): an everyday scrolling joke with
three distinct illustrations (daylight study → nighttime sofa → sunrise bedroom),
new narration, captions, subtle 2D motion and an alarm punchline.
[Source, offline rebuild and research notes](outputs/two-minutes-source/README.md).
The topic follows recurring recent scrolling stories; it is not presented as a
verified highest-engagement trend.

## The Empty Chair — approved story

[Watch the revised 40-second reel](outputs/the-empty-chair-recut.mp4): a café regular makes room
for a stranger, waits for his return, and discovers he has tried drawing their table.
The story was approved before production. It uses the selected illustrated character,
distinct acting scenes, narration, captions and quiet original music.

[Source and offline rebuild instructions](outputs/the-empty-chair-source/README.md) ·
[Storyboard](outputs/the-empty-chair-recut.storyboard.jpg)

The rhythm revision keeps the illustrations and approved story arc, with tighter
narration, varied shot lengths, a quieter waiting sequence and a musical return.
The studio now offers a script-reading view and a focused scene-by-scene storyboard.


To rebuild the illustrated example with articulated bag lifting, a chair pull,
head turns and writing gestures, run:

```sh
.venv/bin/python outputs/the-empty-chair-source/recut.py --motion
```

This edition uses layered 2D animation with reconstructed clean plates. Original
face artwork is preserved without blink overlays. The scene-specific prop rigs
are in `outputs/the-empty-chair-source/motion.py`; arbitrary new illustrations
need their own prop masks and attachment points.

Dialogue mouth animation is defined in `outputs/the-empty-chair-source/lipsync.py`.
It uses manually timed dialogue words, cartoon mouth shapes and the included
speech waveform to close the mouth in gaps. It does not animate the character
during narrator-only passages. New dialogue requires new timings.

The acting revision holds one camera composition per scene. The ending includes
a brief chuckle with original eyes and a small shoulder lift, then an outward chair pull
with a coherent chair layer and attached gripping hand. Rebuild with the same
`recut.py --motion` command; output is `outputs/the-empty-chair-drawing.mp4`.


The opening writing action now uses `outputs/the-empty-chair-source/writing.py`:
the original hand and pen move together, two fixed-length arm segments bend at
the elbow, and each ink stroke ends at the pen tip. Transfers lift the pen and
leave no ink. This drawing animation is calibrated to the café artwork; new
artwork needs its own attachment points and clean background plates.
