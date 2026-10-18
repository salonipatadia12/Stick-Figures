# The Message

An original 29-second stick figure reel about two friends reconnecting.

- [Finished video](outputs/the-message.mp4)
- [Narration script](outputs/the-message-script.txt)
- [Editable source and rebuild instructions](outputs/the-message-source/README.md)

Vertical 1080 × 1920, 30 fps, H.264 video with AAC narration and original notification effects. Previous drafts and temporary environments are excluded.

## Run the renderer

Install FFmpeg first so both `ffmpeg` and `ffprobe` are on PATH. Then:

```sh
cd outputs/the-message-source
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python render.py
```

The rendered file is `outputs/the-message-source/exports/the-message.mp4`, relative to the repository root. Existing narration is included; no speech service connection is needed to render it.
