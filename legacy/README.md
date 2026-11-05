# Legacy lanes

These are the engines that came before pipeline-v2. They are kept runnable, and their tests
still run in `tests/`. New Shorts are made with the `stick` CLI (see the root README).

## `reel-builder/`

This is the procedural Pillow builder: a JSON story drawn with 4 fixed layouts.

```sh
python legacy/reel-builder/reel.py render legacy/reel-builder/examples/the-message.json --output renders/the-message.mp4 --offline
```

- `the-message-source/`: the builder package, the original renderer and its narration.
- `examples/`: story JSON files for this builder. They are unrelated to `episode.json`.

## `bespoke/`

These are the hand-built illustrated reels. Each has its own artwork, masks and rebuild script:
`the-empty-chair-source/`, `character-motion-source/` and `two-minutes-source/`. The finished
references sit beside them, because each script writes its output to the parent folder.

**`the-empty-chair-story-order.mp4`** (and its storyboard) is the only approved video. It is the
style reference for the default `cozy` pack in `styles/cozy/`.

The studio (`studio/`) also reads its finished example and its character reference from this folder.

Rebuild the reference offline:

```sh
.venv/bin/python legacy/bespoke/the-empty-chair-source/recut.py --motion
```

Earlier iterations of these reels are in the GitHub Release `archive-2026-11`.
