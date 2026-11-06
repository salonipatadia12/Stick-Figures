"""Kokoro-82M (Apache-2.0) narration with word timings, one WAV per beat.
Run with .venvs/torch/bin/python. Usage: voice.py SLUG OUT_DIR VOICE BEAT_ID [BEAT_ID ...]
Writes {VOICE}-{BEAT}.wav (24 kHz) and {VOICE}-{BEAT}.words.json ([{word, start, end}] in seconds).
"""
import json, sys
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

ROOT = Path(__file__).resolve().parents[2]
SLUG, OUT, VOICE, BEATS = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4:]
OUT.mkdir(parents=True, exist_ok=True)
episode = json.loads((ROOT / 'episodes' / SLUG / 'episode.json').read_text())
beats = {b['id']: b for b in episode['beats']}
pipe = KPipeline(lang_code='a' if VOICE.startswith('a') else 'b', repo_id='hexgrad/Kokoro-82M')
for beat_id in BEATS:
    text = beats[beat_id]['narration']
    audio, words, offset = [], [], 0.0
    for result in pipe(text, voice=VOICE, speed=1.0):
        chunk = result.audio.numpy() if hasattr(result.audio, 'numpy') else np.asarray(result.audio)
        for token in result.tokens or []:
            if token.start_ts is not None and token.text.strip() and any(c.isalnum() for c in token.text):
                words.append(dict(word=token.text, start=round(offset + token.start_ts, 3), end=round(offset + token.end_ts, 3)))
        audio.append(chunk)
        offset += len(chunk) / 24000
    sf.write(OUT / f'{VOICE}-{beat_id}.wav', np.concatenate(audio), 24000)
    (OUT / f'{VOICE}-{beat_id}.words.json').write_text(json.dumps(words, indent=1))
    print(beat_id, VOICE, f'{offset:.2f}s', ' '.join(f'{w["word"]}@{w["start"]:.2f}' for w in words), flush=True)
