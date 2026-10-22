# Illustrated character motion study

This local animation study develops the character the user selected in
`../character-focus-reference.png`. The output is
`../character-acting-test.mp4` (10.5 seconds, 720×1280, 24 fps, H.264/AAC).
It is a restrained 2D cutout/puppet animation, not a Flow-generated video.

## What is preserved

- White circular head with a soft cool shadow and navy outline.
- Tilted oval eyes, tiny highlights, curved brows and a modest smile.
- Rounded white torso, shaded edge, dark limbs and articulated-looking fingers.
- One pen in the gripping hand; the other hand stays on the notebook.
- Warm café setting with the face and hands given the main screen area.

## Acting beats

- 0–4 seconds: small anticipatory head dip, blink and quiet breath; pen hesitates.
- 4–7.8 seconds: the wrist makes a short series of writing strokes. The pen and hand
  translate as one rigid sprite. The two arm bones retain their original lengths.
  Ink is driven by the same pen-tip trajectory.
- 8–10.5 seconds: pen lifts slightly and the head gently straightens. The original
  expression is retained rather than inventing a different face.

There are two shot scales, both keeping the character visible. No caption panel or
scenery-only insert covers the figure. This is a first acting test, not a finished
30-second reel. Broad turns, page flips, walking and large facial changes require
additional drawn poses and joints.

## Rebuild offline

Use the existing environment from the repository root:

```sh
outputs/the-message-source/.venv/bin/python outputs/character-motion-source/render.py
outputs/the-message-source/.venv/bin/python outputs/character-motion-source/render.py --preview
```

A fresh environment needs Python, Pillow 13 and FFmpeg on PATH. The narration is
included, so rebuilding does not need network access, Google Flow credits, an API
key or an image-generation call.

To change the output filename:

```sh
python render.py --output /absolute/path/to/new-test.mp4
```

`render.py` contains the registration, shot framing, timing, head movement and arm
rig. The illustration layers are registered to the notebook contacts. The body and
head receive a small displacement field; upper arm, forearm, hand and pen use rigid
transforms and a two-bone inverse-kinematics solve. This avoids distorting the torso
when the hand moves. The resting arm is held with the body base.

## Voiceover

One continuous en-US-AndrewNeural take, rate -15%, generated with edge-tts:

> Ava kept waiting for the perfect first sentence. Tonight, she took a breath, and
> wrote a bad one. It was a start.

`narration.mp3`, `narration.txt` and `narration.srt` are included. Speech is normalized
on export; there is a short lead-in and a final visual hold. A new speech recording
can replace `narration.mp3`; adjust animation timings if its delivery differs.

## Artwork provenance

The supplied reference was generated earlier with the built-in image-generation
tool and selected by the user. The three files in `assets/` were prepared with that
same tool from the approved reference. They are not frames from a Flow generation.
The prompt specifications are in `asset-prompts.md`.

- `cafe-plate.png`: background with the character and held pen removed.
- `character.png`: transparent foreground with the original character and pen.
- `body-base.png`: transparent head, torso and resting arm, with the writing arm
  removed so it can be rigged separately.

The artistically shaded head is preserved throughout this test. Eyelid cels are a
small procedural addition. There is no claim that the prototype reproduces the
README demo's full generative acting or that a still image alone provides walking
or arbitrary body turns.

## Complete reel: The First Page

`../the-first-page-illustrated.mp4` assembles the character into a 30.5-second
story, with a single continuous narration recording, two additional expression
cels, purposeful medium/close cuts, persistent notebook content, captions,
small time-jump labels, and an original quiet synthesized sound bed.

```sh
outputs/the-message-source/.venv/bin/python outputs/character-motion-source/assemble.py --preview
outputs/the-message-source/.venv/bin/python outputs/character-motion-source/assemble.py
```

Run those commands from the repository root. The renderer uses bundled artwork and
`full-narration.mp3`; no network is needed to rebuild. `full-narration.txt` and
`full-narration.srt` contain the script and speech timing before the 0.25-second lead-in.
The output SRT accounts for that lead-in. The timeline JSON records shot boundaries,
expressions and captions. Narration uses Andrew at -15%, as one take rather than
separately synthesized scene fragments. The sound bed is synthesized locally with
Python; no external music recording is used.

`body-doubt.png` and `body-relief.png` were created with the built-in image-generation
tool as edits of `body-base.png`: change only the facial marks to hesitant/worried
or quietly relieved, while preserving the head contour, shading, torso, arm,
941×1672 framing and transparency. They follow the approved character's design.

This is a first complete assembled reel for review. It uses one café setting and
restrained puppet animation; it does not simulate walking or broad body turns.
