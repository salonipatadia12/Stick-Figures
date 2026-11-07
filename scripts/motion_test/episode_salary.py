"""The full salary sample: all nine beats on painted base poses, Kokoro narration, word-synced captions,
SFX and a quiet music bed, mastered to -14 LUFS, then the QA report and contact sheet.

Run with .venv/bin/python from the repository root:
  scripts/motion_test/episode_salary.py [--voice am_michael] [--still T ...] [--workers 4] [--no-qa]
Shots are cached by a digest of their timing and code, so a re-run only redraws what changed.
Outputs go to renders/sample/<slug>/ (git-ignored).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from pipeline import captions as C  # noqa: E402
from pipeline import overlays as O  # noqa: E402
from pipeline import sfx  # noqa: E402

SLUG = 'why-your-salary-disappears-by-the-20th'
EPISODE = ROOT / 'episodes' / SLUG / 'episode.json'
VOICE_DIR = ROOT / 'renders/motion-test/voice'
OUT = ROOT / 'renders/sample' / SLUG
W, H, FPS = 1080, 1920, 30
LEAD_IN, GAP, TAIL = 0.35, 0.25, 1.0
CODE = ['pipeline/rig/puppet.py', 'pipeline/rig/hands.py', 'pipeline/rig/props.py', 'pipeline/rig/actions.py',
        'pipeline/rig/skeleton.py', 'pipeline/overlays.py', 'pipeline/captions.py', 'scripts/motion_test/scene_salary.py',
        'scripts/motion_test/salary_beats.py', 'scripts/motion_test/episode_salary.py']


# ---- narration and the timeline ------------------------------------------------------------------------
def voice_clip(voice: str, beat: str):
    path = VOICE_DIR / f'{voice}-{beat}.wav'
    if not path.exists():
        subprocess.run([str(ROOT / '.venvs/torch/bin/python'), str(ROOT / 'scripts/motion_test/voice.py'), SLUG, str(VOICE_DIR),
                        voice, beat], check=True)
    with wave.open(str(path)) as f:
        sr = f.getframerate()
        a = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
    words = json.loads((VOICE_DIR / f'{voice}-{beat}.words.json').read_text())
    return a, sr, words


def speech_span(a: np.ndarray, sr: int):
    win = int(0.01 * sr)
    rms = np.sqrt(np.convolve(a * a, np.ones(win) / win, 'same'))
    idx = np.nonzero(rms > 0.01)[0]
    return idx[0] / sr, idx[-1] / sr


def timeline(voice: str) -> dict:
    """Each beat's narration trimmed to its speech, laid end to end with a short breath between beats."""
    episode = json.loads(EPISODE.read_text())
    beats, at = [], LEAD_IN
    for b in episode['beats']:
        a, sr, words = voice_clip(voice, b['id'])
        s, e = speech_span(a, sr)
        cut0, cut1 = max(0.0, s - 0.06), min(len(a) / sr, e + 0.10)
        offset = at - cut0
        aligned = C.align(b['narration'], words, offset)
        beats.append(dict(id=b['id'], text=b['narration'], start=at, end=at + (cut1 - cut0), clip=(cut0, cut1), sr=sr,
                          words=[(w.text, w.start) for w in aligned], aligned=aligned, on_screen=b.get('on_screen_text', '')))
        at += (cut1 - cut0) + GAP
    duration = beats[-1]['end'] + TAIL
    # shots cut a beat before its first word, so the new picture lands with the narration
    for i, b in enumerate(beats):
        b['shot_start'] = 0.0 if i == 0 else round(b['start'] - 0.10, 3)
    for i, b in enumerate(beats):
        b['shot_end'] = beats[i + 1]['shot_start'] if i + 1 < len(beats) else duration
    return dict(beats=beats, duration=duration, title=episode['title'], target=episode.get('target_length', 45))


# ---- shots ----------------------------------------------------------------------------------------------
def shots(tl: dict) -> list:
    """[(shot name, t0, t1, beat ids)]. Beats 1-2 are one continuous scene with a cut inside; beat 6 has two shots."""
    b = {x['id']: x for x in tl['beats']}
    from salary_beats import word_times
    T6 = word_times(b['b06']['words'])
    split6 = round(T6['you'] - 0.15, 3)
    return [('b01-02', 0.0, b['b03']['shot_start']), ('b03', b['b03']['shot_start'], b['b03']['shot_end']),
            ('b04', b['b04']['shot_start'], b['b04']['shot_end']), ('b05', b['b05']['shot_start'], b['b05']['shot_end']),
            ('b06a', b['b06']['shot_start'], split6), ('b06b', split6, b['b06']['shot_end']),
            ('b07', b['b07']['shot_start'], b['b07']['shot_end']), ('b08', b['b08']['shot_start'], b['b08']['shot_end']),
            ('b09', b['b09']['shot_start'], b['b09']['shot_end'])]


def b12_times(tl):
    from salary_beats import word_times
    b = {x['id']: x for x in tl['beats']}
    w1, w2 = word_times(b['b01']['words']), word_times(b['b02']['words'])
    T = {k: w1[k] for k in ('payday', 'feels', 'rich', 'so', 'broke', 'a', 'week', 'later')}
    T.update({k: w2[k] for k in ('say', 'lands', 'every', 'other', 'friday', 'it', 'huge')})
    T['amount'] = w2['$1,500']
    return T, b['b02']['shot_start'], b['b03']['shot_start']


class B12:
    """Beats 1-2 (scene_salary.Scene) with their on-screen text: the slam and the $ counter."""

    def __init__(self, tl, load_art=True):
        import scene_salary as S
        self.T, self.cut, self.end = b12_times(tl)
        self.scene = S.Scene(self.T, self.cut, self.end, load_art=load_art)
        self.broke = O.stacked(('Broke', 'a week later'), (118, 70))
        self.example = O.text_layer('Example', 50, fill=(255, 236, 190))

    def frame(self, t):
        f = self.scene.frame(t)
        if t < self.cut:
            O.slam(f, self.broke, t, self.T['week'])
        elif t >= self.T['amount'] - 0.05:
            value = 1500 * self.scene.jar_level(t)
            pop = math.exp(-max(0.0, t - self.T['lands']) * 9) if t >= self.T['lands'] else 0
            size = int(128 * (1 + 0.16 * pop))
            im = O.text_layer(f'${int(round(value / 10) * 10):,}', size, fill=(255, 226, 120))
            f.alpha_composite(self.example, (int(O.TEXT_C[0] - self.example.width / 2), O.TEXT_C[1] - 128))
            f.alpha_composite(im, (int(O.TEXT_C[0] - im.width / 2), O.TEXT_C[1] - 66 - int(8 * pop)))
            O.note_text('Example', 50)
            O.note_text('$ counter', size)
        return f

    def sfx(self):
        sc, T = self.scene, self.T
        ev = [(T['broke'], 'thud', 0.7), (T['lands'], 'coin_big', 1.0), (T['every'], 'coin', 0.3), (T['other'], 'coin', 0.3),
              (T['week'], 'pop', 0.5), (T['huge'], 'whoosh', 0.6)]
        ev += [(b['t0'], 'whoosh', 1.0) for b in sc.bill_flights]
        ev += [(c.landed_at(), 'coin', 0.8) for c in sc.coins[:-1]]
        return ev


def make_scene(name, tl, load_art=True):
    if name == 'b01-02':
        return B12(tl, load_art)
    import salary_beats as SB
    beat = {x['id']: x for x in tl['beats']}['b06' if name.startswith('b06') else name]
    t0, t1 = next((a, b) for n, a, b in shots(tl) if n == name)
    return SB.BEATS[name](SB.word_times(beat['words']), t0, t1, load_art)


def scene_sfx(name, tl):
    sc = make_scene(name, tl, load_art=False)
    return sc.sfx() if name == 'b01-02' else sc.events


def _bases_hash() -> str:
    """Every file of the base library (plates, layers, heads, anchors): a rebuilt base re-renders its shots."""
    h = hashlib.sha256()
    for f in sorted((ROOT / 'styles/cozy/bases').rglob('*')):
        if f.is_file():
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()


BASES_HASH = _bases_hash()


def digest(name, t0, t1, lines) -> str:
    h = hashlib.sha256(f'{name}|{t0:.3f}|{t1:.3f}|{W}x{H}@{FPS}'.encode())
    for p in CODE:
        h.update((ROOT / p).read_bytes())
    h.update(BASES_HASH.encode())
    h.update(json.dumps([(l.text, round(l.start, 3), round(l.end, 3)) for l in lines]).encode())
    return h.hexdigest()[:16]


def render_shot(args):
    """Render one shot's frames (with captions) to an H.264 clip. Runs in a worker process."""
    name, t0, t1, tl, lines, path = args
    scene = make_scene(name, tl)
    f0, f1 = round(t0 * FPS), round(t1 * FPS)
    tmp = path.with_suffix('.partial.mp4')
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
           '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', str(tmp)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    boxes, texts = [], []
    O.take_text_log()
    for i in range(f0, f1):
        t = i / FPS
        frame = scene.frame(t)
        line = C.at(lines, t)
        if line:
            boxes.append(dict(t=round(t, 3), **C.draw(frame, line, t)))
        drawn = O.take_text_log()
        if drawn:                                 # the smallest type on this frame, for the readability check
            small = min(drawn, key=lambda d: d['em'])
            texts.append(dict(t=round(t, 3), **small))
        proc.stdin.write(frame.convert('RGB').tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError(f'ffmpeg failed for {name}')
    tmp.replace(path)
    path.with_suffix('.captions.json').write_text(json.dumps(boxes))
    path.with_suffix('.text.json').write_text(json.dumps(texts))
    return name, f1 - f0


# ---- audio --------------------------------------------------------------------------------------------
def voice_48k(voice, beat_id) -> np.ndarray:
    src = VOICE_DIR / f'{voice}-{beat_id}.wav'
    dst = OUT / 'audio' / f'{voice}-{beat_id}-48k.wav'
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(src), '-ar', str(sfx.SR), '-ac', '1', str(dst)], check=True)
    with wave.open(str(dst)) as f:
        return np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768


def build_audio(tl, voice, events) -> Path:
    clips = []
    for b in tl['beats']:
        a = voice_48k(voice, b['id'])
        c0, c1 = b['clip']
        seg = a[int(c0 * sfx.SR):int(c1 * sfx.SR)].copy()
        ramp = int(0.01 * sfx.SR)
        seg[:ramp] *= np.linspace(0, 1, ramp)
        seg[-ramp:] *= np.linspace(1, 0, ramp)
        clips.append((b['start'], seg))
    mix = sfx.mix(tl['duration'], clips, events)
    raw = OUT / 'audio' / 'mix.wav'
    sfx.write_wav(raw, mix)
    mastered = OUT / 'audio' / 'master.wav'
    sfx.master(raw, mastered)
    return mastered


# ---- main ---------------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--voice', default='am_michael')
    ap.add_argument('--still', type=float, nargs='*')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--no-qa', action='store_true')
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()
    tl = timeline(args.voice)
    lines = C.group([w for b in tl['beats'] for w in b['aligned']])
    plan = shots(tl)
    print(f"duration {tl['duration']:.2f}s, {len(plan)} shots, {len(lines)} caption lines", flush=True)
    if args.still is not None:
        for s in args.still:
            name, t0, t1 = next(p for p in plan if p[1] <= s < p[2])
            frame = make_scene(name, tl).frame(s)
            line = C.at(lines, s)
            if line:
                C.draw(frame, line, s)
            path = OUT / f'still-{s:05.2f}.jpg'
            frame.convert('RGB').save(path, quality=90)
            drawn = O.take_text_log()
            small = min(drawn, key=lambda d: d['em']) if drawn else None
            print(path, name, f"smallest text: {small['em']}px ({small['label']})" if small else '')
        return
    clip_dir = OUT / 'shots'
    clip_dir.mkdir(exist_ok=True)
    jobs, clips = [], []
    for name, t0, t1 in plan:
        sub = [l for l in lines if l.end > t0 and l.start < t1]
        path = clip_dir / f'{name}-{digest(name, t0, t1, sub)}.mp4'
        clips.append(path)
        if not path.exists():
            jobs.append((name, t0, t1, tl, lines, path))
    print('cached:', len(plan) - len(jobs), 'to render:', [j[0] for j in jobs], flush=True)
    if jobs:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for name, n in ex.map(render_shot, sorted(jobs, key=lambda j: -(j[2] - j[1]))):
                print(f'  {name}: {n} frames  {time.perf_counter() - t_start:.0f}s', flush=True)
    listing = OUT / 'shots.txt'
    listing.write_text(''.join(f"file '{p.resolve()}'\n" for p in clips))
    video = OUT / 'video.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', str(video)], check=True)
    events = []
    for name, _, _ in plan:
        events += scene_sfx(name, tl)
    audio = build_audio(tl, args.voice, events)
    final = OUT / f'{SLUG}.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(video), '-i', str(audio), '-map', '0:v', '-map', '1:a',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', f"{tl['duration']:.3f}",
                    '-movflags', '+faststart', str(final)], check=True)
    (OUT / f'{SLUG}.srt').write_text(C.srt(lines))
    caption_boxes, text_sizes = [], []
    for p in clips:
        caption_boxes += json.loads(p.with_suffix('.captions.json').read_text())
        text_sizes += json.loads(p.with_suffix('.text.json').read_text())
    (OUT / 'timeline.json').write_text(json.dumps(dict(
        title=tl['title'], voice=args.voice, duration=round(tl['duration'], 2), shots=[dict(name=n, start=a, end=b) for n, a, b in plan],
        beats=[dict(id=b['id'], start=round(b['start'], 3), end=round(b['end'], 3), words=b['words']) for b in tl['beats']],
        sfx=sorted([dict(t=round(t, 3), name=n, gain=g) for t, n, g in events], key=lambda e: e['t'])), indent=1))
    print(final, f"{tl['duration']:.2f}s", f'{time.perf_counter() - t_start:.0f}s', flush=True)
    if not args.no_qa:
        from pipeline import qa
        report = qa.run(final, tl, lines, caption_boxes, OUT, plan, args.voice, text_sizes=text_sizes,
                        ledger=ROOT / 'episodes' / SLUG / 'ledger.json')
        print(json.dumps(report['summary'], indent=1))


if __name__ == '__main__':
    main()
