# Character-focused revision — The First Page

## What the GitHub project actually does

Reference: https://github.com/kaomei/stickman-video-director
Reviewed revision: `5841335db851b5f0352cf77955147393b97beabc`.

The repository is a director skill and prompt library. It first fixes format, style,
character identity and voice, then plans timed actions, shot choices, prop states and
transitions. It outputs standalone generation prompts. The README's Style 4 samples
were generated externally in Flow using Omni 1.1 Flash; they are not rendered by
code shipped in the repository. See its README, `cozy-storytelling.md`,
`omni-flash-prompt-contract.md`, and `tests/reports/style-4.md`.

The earlier local reel adopted descriptive rules, but not that generation pipeline.
It therefore should not be described as matching the README's animation quality.

## What the actual sample shows

I sampled the café and station MP4s at half-second intervals (visual analysis,
not a listening review or certification of every frame).

- Café, roughly 0–2 seconds: the arm extends and places the phone, fingers release,
  and the gaze follows the object. There is anticipation, action and recovery.
- Café, roughly 2–4 seconds: a much closer view catches a sip and a small expression
  change. A circular white head, rounded flared torso and navy limbs stay recognizable.
- Café, roughly 5–8 seconds: a brief hand insert makes the drawing action readable.
  The fingers grip the pen; the wrist, pen and line belong to one action.
- Café, roughly 8–10 seconds: the face and relaxed posture return before the wider
  environment. The environment supports the emotional resolution.
- Station: walking, planted-foot transfers, eye/brow changes and a reaction close-up
  sustain the character's presence. The scenery moves around the character.

The upstream report itself notes unresolved pen-transfer details and does not certify
voice consistency. These are useful visual references, not a guarantee that every
prompt will reproduce them exactly.

## What changes in our direction

| Earlier local reel | Character-focused target |
|---|---|
| Small figure surrounded by a large window | Face and upper body dominate most shots |
| Straight, narrow torso and angular hands | Rounded torso silhouette, readable fingers and grip |
| Nearly constant smile and forward gaze | Eyes follow the pen; hesitation, concentration, then relief |
| Whole-frame zoom and long notebook inserts | Reaction close-ups and a short insert tied to a physical action |
| Hand movement with very little body response | Shoulder anticipation, head tilt, breath and controlled settling |
| Predominantly procedural motion | Generate the acting from a locked character reference and timed directions |

Keep the existing warm background palette. Reduce background contrast/detail near the
face. Avoid scenery-only shots. Keep the character visible for at least 80% of the
pilot. No elastic limbs, wandering hands, or idle bobbing as a substitute for acting.

## Concrete pilot to render

An approximately 8–10 second vertical acting test of the evening writing moment:

1. **Hesitate:** medium close-up. Eyes drop to the blank notebook. Pen pauses just
   above the paper; a small eyebrow lift and shoulder tension make the doubt readable.
2. **Choose:** a quiet exhale. Shoulders ease, the head inclines, and the pen touches
   the paper. Movement begins in the shoulder and finishes at the wrist.
3. **Write:** two or three deliberate strokes, with a brief hand insert if useful.
   The other hand anchors the page. Exactly one pen remains in the gripping hand.
4. **React:** return to the face. Eyes scan the line, mouth shifts to a small relieved
   smile, shoulders settle. Hold the reaction long enough to read it.

This is a quality pilot before applying the direction to the full reel.

## Assets and status

- `../character-focus-reference.png`: generated character/scene keyframe, made with
  the built-in image-generation tool using a README frame as a visual reference.
- `pilot-prompt.txt`: concrete generation prompt; not yet submitted to Flow.
- `image-prompt.txt`: prompt specification used for the new keyframe.

The Flow attempt did not produce a video. This package is retained as planning
material. Current production uses the local illustrated character renderer in
`../character-motion-source/`; its complete reel is `../the-first-page-illustrated.mp4`.
The reference keyframe itself is a still image, not proof of motion.
