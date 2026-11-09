"""Render a whole episode from its beats module: Kokoro narration laid on a timeline, one H.264 clip per shot
(cached by a digest of its timing, code and bases), word-synced captions, SFX and a music bed, the master,
then the QA report and contact sheet.

An episode script configures a Runner with its slug, its beats module (a dict BEATS: shot name -> Beat class,
and optional `shots(tl)` for cuts inside a beat) and runs it:

  runner = Runner(SLUG, beats_module, extra_code=[...])
  runner.main()            # --voice, --still T ..., --workers N, --no-qa

Outputs go to renders/sample/<slug>/ (git-ignored); narration to renders/voice/<slug>/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from pipeline import captions as C  # noqa: E402
from pipeline import overlays as O  # noqa: E402
from pipeline import sfx  # noqa: E402

W, H, FPS = 1080, 1920, 30


def text_summary(drawn: list) -> dict:
    """One record per frame for QA: the smallest type drawn, and the text box that reaches furthest right/down."""
    small = min(drawn, key=lambda d: d['em'])
    out = dict(label=small['label'], em=small['em'])
    boxed = [d for d in drawn if 'box' in d]
    if boxed:
        left = min(d['box'][0] for d in boxed)
        far = max(boxed, key=lambda d: (d['box'][2] - 1080) + (d['box'][3] - 1440))      # the one nearest the right/bottom limits
        out['box'] = [left, min(d['box'][1] for d in boxed), far['box'][2], max(d['box'][3] for d in boxed)]
        out['box_label'] = far['label']
    return out
LEAD_IN, GAP, TAIL = 0.35, 0.25, 1.0
RIG_CODE = ['pipeline/rig/puppet.py', 'pipeline/rig/hands.py', 'pipeline/rig/props.py', 'pipeline/rig/actions.py',
            'pipeline/rig/skeleton.py', 'pipeline/overlays.py', 'pipeline/captions.py', 'scripts/motion_test/beat_base.py',
            'scripts/motion_test/episode_runner.py']


class Runner:
    def __init__(self, slug: str, beats, extra_code: list | None = None, voice: str = 'am_michael'):
        self.slug = slug
        self.beats_name = beats if isinstance(beats, str) else beats.__name__   # the beats module (imported lazily: workers pickle the Runner)
        self.episode_path = ROOT / 'episodes' / slug / 'episode.json'
        self.voice_dir = ROOT / 'renders/voice' / slug
        self.out = ROOT / 'renders/sample' / slug
        self.code = RIG_CODE + (extra_code or [])
        self.default_voice = voice
        self._bases_hash = None

    def __getstate__(self):
        # workers get a Runner without the composed beats (their classes are made at run time and cannot be
        # pickled); each worker composes them again from the script, which is deterministic
        state = dict(self.__dict__)
        state.pop('_composed', None)
        return state

    @property
    def beats(self):
        """The beats module, or what `for_episode(slug)` composes from the script (compose_beats)."""
        import importlib
        mod = importlib.import_module(self.beats_name)
        if hasattr(mod, 'for_episode'):
            if getattr(self, '_composed', None) is None or self._composed.slug != self.slug:
                self._composed = mod.for_episode(self.slug, overrides=getattr(self, 'overrides', None))
            return self._composed
        return mod

    def library_summary(self) -> dict:
        """Beats built from the libraries alone versus hand-written code (docs/PLAN.md §7 G)."""
        beats = self.beats
        if hasattr(beats, 'library_summary'):
            return beats.library_summary()
        rep = getattr(beats, 'LIBRARY_REPORT', {})
        lib = sum(1 for r in rep.values() if not r.get('hand_written'))
        return dict(beats=len(rep), library_beats=lib, library_pct=round(100 * lib / len(rep)) if rep else 0, per_beat=rep)

    # ---- narration and the timeline --------------------------------------------------------------
    def voice_clip(self, voice: str, beat: str):
        path = self.voice_dir / f'{voice}-{beat}.wav'
        if not path.exists():
            subprocess.run([str(ROOT / '.venvs/torch/bin/python'), str(ROOT / 'scripts/motion_test/voice.py'), self.slug,
                            str(self.voice_dir), voice, beat], check=True)
        with wave.open(str(path)) as f:
            sr = f.getframerate()
            a = np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768
        words = json.loads((self.voice_dir / f'{voice}-{beat}.words.json').read_text())
        return a, sr, words

    @staticmethod
    def speech_span(a: np.ndarray, sr: int):
        win = int(0.01 * sr)
        rms = np.sqrt(np.convolve(a * a, np.ones(win) / win, 'same'))
        idx = np.nonzero(rms > 0.01)[0]
        return idx[0] / sr, idx[-1] / sr

    def timeline(self, voice: str) -> dict:
        """Each beat's narration trimmed to its speech, laid end to end with a short breath between beats."""
        episode = json.loads(self.episode_path.read_text())
        beats, at = [], LEAD_IN
        for b in episode['beats']:
            a, sr, words = self.voice_clip(voice, b['id'])
            s, e = self.speech_span(a, sr)
            cut0, cut1 = max(0.0, s - 0.06), min(len(a) / sr, e + 0.10)
            offset = at - cut0
            aligned = C.align(b['narration'], words, offset)
            beats.append(dict(id=b['id'], text=b['narration'], start=at, end=at + (cut1 - cut0), clip=(cut0, cut1), sr=sr,
                              words=[(w.text, w.start) for w in aligned], aligned=aligned, on_screen=b.get('on_screen_text', ''),
                              motion=b.get('motion') or b.get('shot', {}).get('motion', [])))
            at += (cut1 - cut0) + GAP
        duration = beats[-1]['end'] + TAIL
        for i, b in enumerate(beats):                 # shots cut a beat before its first word
            b['shot_start'] = 0.0 if i == 0 else round(b['start'] - 0.10, 3)
        for i, b in enumerate(beats):
            b['shot_end'] = beats[i + 1]['shot_start'] if i + 1 < len(beats) else duration
        return dict(beats=beats, duration=duration, title=episode['title'], target=episode.get('target_length', 45))

    # ---- shots -------------------------------------------------------------------------------------
    def shots(self, tl: dict) -> list:
        """[(shot name, t0, t1)]: one shot per beat unless the beats module splits some (`shots(tl)`)."""
        if hasattr(self.beats, 'shots'):
            return self.beats.shots(tl)
        return [(b['id'], b['shot_start'], b['shot_end']) for b in tl['beats']]

    def make_scene(self, name: str, tl: dict, load_art: bool = True):
        beat_id = name.split('-')[0].rstrip('ab') if name not in {b['id'] for b in tl['beats']} else name
        beat = {x['id']: x for x in tl['beats']}[beat_id]
        t0, t1 = next((a, b) for n, a, b in self.shots(tl) if n == name)
        return self.beats.BEATS[name](self.beats.word_times(beat['words']), t0, t1, load_art)

    def scene_sfx(self, name, tl):
        return self.make_scene(name, tl, load_art=False).events

    def bases_hash(self) -> str:
        if self._bases_hash is None:
            h = hashlib.sha256()
            for f in sorted((ROOT / 'styles/cozy/bases').rglob('*')):
                if f.is_file():
                    h.update(f.name.encode())
                    h.update(f.read_bytes())
            self._bases_hash = h.hexdigest()
        return self._bases_hash

    def digest(self, name, t0, t1, lines) -> str:
        h = hashlib.sha256(f'{name}|{t0:.3f}|{t1:.3f}|{W}x{H}@{FPS}'.encode())
        for p in self.code:
            h.update((ROOT / p).read_bytes())
        h.update(self.bases_hash().encode())
        h.update(json.dumps([(l.text, round(l.start, 3), round(l.end, 3)) for l in lines]).encode())
        return h.hexdigest()[:16]

    def render_shot(self, args):
        """Render one shot's frames (with captions) to an H.264 clip. Runs in a worker process."""
        name, t0, t1, tl, lines, path = args
        scene = self.make_scene(name, tl)
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
            if drawn:
                texts.append(dict(t=round(t, 3), **text_summary(drawn)))
            proc.stdin.write(frame.convert('RGB').tobytes())
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError(f'ffmpeg failed for {name}')
        tmp.replace(path)
        path.with_suffix('.captions.json').write_text(json.dumps(boxes))
        path.with_suffix('.text.json').write_text(json.dumps(texts))
        return name, f1 - f0

    # ---- audio -------------------------------------------------------------------------------------
    def voice_48k(self, voice, beat_id) -> np.ndarray:
        src = self.voice_dir / f'{voice}-{beat_id}.wav'
        dst = self.out / 'audio' / f'{voice}-{beat_id}-48k.wav'
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(src), '-ar', str(sfx.SR), '-ac', '1', str(dst)], check=True)
        with wave.open(str(dst)) as f:
            return np.frombuffer(f.readframes(f.getnframes()), np.int16).astype(np.float32) / 32768

    def build_audio(self, tl, voice, events) -> Path:
        clips = []
        for b in tl['beats']:
            a = self.voice_48k(voice, b['id'])
            c0, c1 = b['clip']
            seg = a[int(c0 * sfx.SR):int(c1 * sfx.SR)].copy()
            ramp = int(0.01 * sfx.SR)
            seg[:ramp] *= np.linspace(0, 1, ramp)
            seg[-ramp:] *= np.linspace(1, 0, ramp)
            clips.append((b['start'], seg))
        mix = sfx.mix(tl['duration'], clips, events)
        raw = self.out / 'audio' / 'mix.wav'
        sfx.write_wav(raw, mix)
        mastered = self.out / 'audio' / 'master.wav'
        sfx.master(raw, mastered)
        return mastered

    # ---- main --------------------------------------------------------------------------------------
    def main(self, argv=None):
        ap = argparse.ArgumentParser()
        ap.add_argument('--voice', default=self.default_voice)
        ap.add_argument('--still', type=float, nargs='*')
        ap.add_argument('--workers', type=int, default=4)
        ap.add_argument('--no-qa', action='store_true')
        args = ap.parse_args(argv)
        self.out.mkdir(parents=True, exist_ok=True)
        t_start = time.perf_counter()
        tl = self.timeline(args.voice)
        lines = C.group([w for b in tl['beats'] for w in b['aligned']])
        plan = self.shots(tl)
        print(f"duration {tl['duration']:.2f}s, {len(plan)} shots, {len(lines)} caption lines", flush=True)
        if args.still is not None:
            for s in args.still:
                name, t0, t1 = next(p for p in plan if p[1] <= s < p[2])
                frame = self.make_scene(name, tl).frame(s)
                line = C.at(lines, s)
                if line:
                    C.draw(frame, line, s)
                path = self.out / f'still-{s:05.2f}.jpg'
                frame.convert('RGB').save(path, quality=90)
                drawn = O.take_text_log()
                small = text_summary(drawn) if drawn else None
                print(path, name, f"smallest text: {small['em']}px ({small['label']})" if small else '',
                      f"text box right {small['box'][2]} bottom {small['box'][3]} ({small['box_label']})" if small and 'box' in small else '')
            return
        clip_dir = self.out / 'shots'
        clip_dir.mkdir(exist_ok=True)
        jobs, clips = [], []
        for name, t0, t1 in plan:
            sub = [l for l in lines if l.end > t0 and l.start < t1]
            path = clip_dir / f'{name}-{self.digest(name, t0, t1, sub)}.mp4'
            clips.append(path)
            if not path.exists():
                jobs.append((name, t0, t1, tl, lines, path))
        print('cached:', len(plan) - len(jobs), 'to render:', [j[0] for j in jobs], flush=True)
        if jobs:
            with ProcessPoolExecutor(max_workers=args.workers) as ex:
                for name, n in ex.map(self.render_shot, sorted(jobs, key=lambda j: -(j[2] - j[1]))):
                    print(f'  {name}: {n} frames  {time.perf_counter() - t_start:.0f}s', flush=True)
        listing = self.out / 'shots.txt'
        listing.write_text(''.join(f"file '{p.resolve()}'\n" for p in clips))
        video = self.out / 'video.mp4'
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy', str(video)], check=True)
        events = []
        for name, _, _ in plan:
            events += self.scene_sfx(name, tl)
        audio = self.build_audio(tl, args.voice, events)
        final = self.out / f'{self.slug}.mp4'
        subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(video), '-i', str(audio), '-map', '0:v', '-map', '1:a',
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', f"{tl['duration']:.3f}",
                        '-movflags', '+faststart', str(final)], check=True)
        (self.out / f'{self.slug}.srt').write_text(C.srt(lines))
        caption_boxes, text_sizes = [], []
        for p in clips:
            caption_boxes += json.loads(p.with_suffix('.captions.json').read_text())
            text_sizes += json.loads(p.with_suffix('.text.json').read_text())
        (self.out / 'timeline.json').write_text(json.dumps(dict(
            title=tl['title'], voice=args.voice, duration=round(tl['duration'], 2), shots=[dict(name=n, start=a, end=b) for n, a, b in plan],
            beats=[dict(id=b['id'], start=round(b['start'], 3), end=round(b['end'], 3), words=b['words']) for b in tl['beats']],
            sfx=sorted([dict(t=round(t, 3), name=n, gain=g) for t, n, g in events], key=lambda e: e['t'])), indent=1))
        print(final, f"{tl['duration']:.2f}s", f'{time.perf_counter() - t_start:.0f}s', flush=True)
        if not args.no_qa:
            from pipeline import qa
            report = qa.run(final, tl, lines, caption_boxes, self.out, plan, args.voice, text_sizes=text_sizes,
                            ledger=ROOT / 'episodes' / self.slug / 'ledger.json', library=self.library_summary())
            print(json.dumps(report['summary'], indent=1))
            lib = report['library']
            print(f"library: {lib['library_beats']}/{lib['beats']} beats from the libraries alone = {lib['library_pct']}% (target {lib['target_pct']}%)")
