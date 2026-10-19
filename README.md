# Stick Figures — reusable reel builder

Create new narrated stick figure reels by changing a story JSON file, not animation code. The renderer uses an original expressive character with articulated arms, restrained gestures and anchored prop contacts. Four fixed scene layouts keep movement and text aligned.

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

Top-level `character_style`: `expressive` (default) or `classic`. This applies consistently to every scene in the reel.

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
