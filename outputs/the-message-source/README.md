> For new stories, use the [reusable builder guide](../../README.md) and repository-root `reel.py`. The files described below preserve the original reel exactly.

# The Message — stick figure reel

A 29.23-second original friendship story, rendered at 1080 × 1920, 30 fps, with conversational English narration.

## Rebuild

Requires Python 3.10+, FFmpeg and ffprobe on PATH. From the repository root, run:

```sh
cd outputs/the-message-source
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python render.py
```

The included MP3 narration allows offline rendering. Output is `exports/the-message.mp4`. On macOS the renderer uses installed Arial fonts; on Linux it uses DejaVu Sans. Font differences may slightly change text width.

## Edit narration

Edit `narrate.py`, then run:

```sh
python narrate.py
python render.py
```

Regeneration calls Microsoft Edge online text-to-speech via edge-tts and requires network access. Voice: en-US-AndrewNeural, rate -3%. Each scene duration follows its narration with a short pause.

Original drawings and synthesized notification sounds are generated in `render.py`. No source footage from the YouTube reference is included.
