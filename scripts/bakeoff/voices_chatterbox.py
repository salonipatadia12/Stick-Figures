"""Chatterbox (MIT) audition clip with its built-in voice. Run with .venvs/chatterbox/bin/python.

Kept as the Phase 2 bake-off record only: Chatterbox was dropped on 2026-11-05 and its environment is gone.
"""
import sys, time
from pathlib import Path
import torch, torchaudio
from chatterbox.tts import ChatterboxTTS

LINE = sys.argv[1]
OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
device = 'mps' if torch.backends.mps.is_available() else 'cpu'
t0 = time.perf_counter()
model = ChatterboxTTS.from_pretrained(device=device)
print(f'load {time.perf_counter() - t0:.1f}s on {device}')
for name, exaggeration, cfg in [('default', 0.5, 0.5), ('expressive', 0.7, 0.3)]:
    torch.manual_seed(7)
    t = time.perf_counter()
    wav = model.generate(LINE, exaggeration=exaggeration, cfg_weight=cfg)
    torchaudio.save(str(OUT / f'chatterbox-{name}.wav'), wav.cpu(), model.sr)
    print(f'{name}: {wav.shape[-1] / model.sr:.2f}s audio in {time.perf_counter() - t:.2f}s')
