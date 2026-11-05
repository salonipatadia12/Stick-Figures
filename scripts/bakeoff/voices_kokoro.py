"""Kokoro (Apache-2.0) audition clips. Run with .venvs/torch/bin/python."""
import sys, time
from pathlib import Path
import numpy as np, soundfile as sf
from kokoro import KPipeline

LINE = sys.argv[1]
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
VOICES = sys.argv[3].split(',')
t0 = time.perf_counter()
pipe = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M')
print(f'load {time.perf_counter() - t0:.1f}s')
for voice in VOICES:
    t = time.perf_counter()
    audio = np.concatenate([np.asarray(a) for _, _, a in pipe(LINE, voice=voice, speed=1.0)])
    sf.write(OUT / f'kokoro-{voice}.wav', audio, 24000)
    print(f'{voice}: {len(audio) / 24000:.2f}s audio in {time.perf_counter() - t:.2f}s')
