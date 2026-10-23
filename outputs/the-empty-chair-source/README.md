# The Empty Chair

## Revised pacing cut

`../the-empty-chair-recut.mp4` is the 40.04-second rhythm revision. It reuses all
eight selected illustrations without changing their pixels or repainting the eyes.
The story arc is retained with a tighter 33.46-second narration take, short
intentional pauses, 21 unevenly timed shots, object inserts and reaction close-ups.
The score withdraws during the wait and resolves on the return. Cloth/page sounds
and door chimes are synthesized locally. Caption phrases remain short and stable.
`recut.py` owns this edit independently of the retained 49-second original.

```sh
.venv/bin/python outputs/the-empty-chair-source/recut.py --preview
.venv/bin/python outputs/the-empty-chair-source/recut.py
```

The new continuous take is `narration-recut.mp3`; its text and original timing are
saved alongside it. The voice is the same Andrew neural narrator, rate -3%.
All included media can be rebuilt offline; no new artwork or paid image calls
were used for this revision.

## Original 49-second edition

An original story approved by the user before production. A reserved café regular
makes room for a stranger, starts saving his seat, and discovers that their brief
connection mattered to him too. The ending pairs her accomplished sketch with his
endearingly clumsy attempt at drawing their table.

The selected white filled, navy outlined character is retained. The visitor wears a
teal scarf so the two characters remain easy to distinguish. The orange bag, right
chair, green door, cups and sketchbooks carry the visual continuity across the story.

## Deliverables

- `../the-empty-chair.mp4`: portrait H.264/AAC reel, 720×1280, 24 fps.
- `../the-empty-chair.storyboard.jpg`: twelve representative frames.
- `../the-empty-chair.srt`: narration captions aligned to the final edit.
- `../the-empty-chair.timeline.json`: shot and speech timing metadata.
- `../the-empty-chair.validation.json`: media verification results.

The final runtime is 48.96 seconds. The edit uses eight selected illustrations
across nineteen shots. Corrected assets with `-final` in their names are the
selected return scene and drawing insert; the first versions are retained as
production history and are not used by the renderer.

## Rebuild locally

Requires Python 3, Pillow, and FFmpeg on PATH. The project's existing environment
already contains Pillow. From the repository root:

```sh
outputs/the-message-source/.venv/bin/python outputs/the-empty-chair-source/render.py --preview
outputs/the-message-source/.venv/bin/python outputs/the-empty-chair-source/render.py
```

The included illustrations and narration make rebuilding offline possible. No Flow
project or generation credits are required to rebuild. The renderer creates its
quiet plucked music and door chimes locally, with no downloaded music assets.

## Narration and timing

`narration.txt` contains the complete approved story adaptation. The source take is
one continuous en-US-AndrewNeural recording made with the free Edge TTS service at
rate -8%. `narration.srt` is the original take's timing. Postproduction applies a
small pitch-preserving tempo adjustment (0.95), a 0.4-second lead-in, and selected
pauses around waiting and the return. The final image holds after narration so the
two drawings can be read. The complete runtime is calculated in `render.py`.

To regenerate the source narration (online access required):

```sh
outputs/the-message-source/.venv/bin/edge-tts \
  -f outputs/the-empty-chair-source/narration.txt \
  -v en-US-AndrewNeural --rate=-8% \
  --write-media outputs/the-empty-chair-source/narration.mp3 \
  --write-subtitles outputs/the-empty-chair-source/narration.srt
```

If a new recording changes timing, update `VOICE_LENGTH`, `EDIT`, and `PAUSES`;
the included edit is deliberately timed to this take rather than arbitrary prose.

## Artwork and motion

Artwork was created with the built-in image-generation tool, using the user's
selected `../character-focus-reference.png` for identity, then the new café scenes
as continuity references. Full generation and correction prompts are retained in
`asset-prompts.json`. The original selected reference remains unchanged.

This is locally assembled illustrated 2D animation: distinct acting cels and
camera cuts, restrained head nods, drawing/hand gestures, a bag-lifting
gesture and a small laugh. It is not generated continuous full-body footage or a
lip-synced dialogue performance. Narration carries the dialogue. No claim that this
original story is a ranked trending YouTube upload is made.

## Eye flicker correction

`../the-empty-chair-fixed.mp4` is the corrected export. The original blink mask
sampled the visitor's dark head outline as a face fill color, causing a black oval
to flash over his left eye. The sampled-color blink overlays are removed; the
original illustrated eyes remain intact while head and hand movement continues.
`eye-stability-check.json` records eye-area measurements across consecutive frames
in all three scenes with the visitor. To rebuild the corrected filename:

```sh
outputs/the-message-source/.venv/bin/python outputs/the-empty-chair-source/render.py \
  --output outputs/the-empty-chair-fixed.mp4
```
