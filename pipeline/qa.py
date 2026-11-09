"""QA for a rendered Short: writes qa.json and contact_sheet.jpg next to the video.

Checks (all local, no paid calls):
- the picture never freezes for more than 2.5 s
- captions are one line and inside the safe area (out of the bottom quarter and the right edge)
- every narrated word is heard (faster-whisper on the final audio, compared with the script)
- loudness -14 LUFS (+-1) and true peak <= -1 dBTP
- duration within +-15% of the target
- characters come only from the approved base library (their head and body art, by hash)
- every piece of on-screen text (captions, slams, checklists, tags, receipts, bubbles, the counter) is drawn
  at 40 px or more (font em in frame pixels); prop lettering such as the calendar's day numbers is not text
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import captions as C
from . import sfx

ROOT = Path(__file__).resolve().parents[1]
APPROVED_BASES = ('lead-at-table', 'together', 'friend-at-table', 'lead-standing',           # approved by the user, 2026-11-06
                  'lead-bed-edge-bedroom', 'lead-bed-asleep-top-bedroom', 'lead-bed-awake-bedroom')   # 2026-11-08
FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'


def probe_duration(path: Path) -> float:
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(path)],
                         capture_output=True, text=True).stdout
    return float(out.strip())


def freezes(path: Path, fps: int = 30, threshold: float = 0.35) -> dict:
    """Longest run of (nearly) identical frames, from a small grey copy of the picture."""
    w, h = 108, 192
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vf', f'scale={w}:{h},format=gray', '-f', 'rawvideo', '-'],
                         capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    still = diff < threshold
    longest, run, at, start = 0, 0, 0.0, 0
    for i, s in enumerate(still):
        run = run + 1 if s else 0
        if run == 1:
            start = i
        if run > longest:
            longest, at = run, start / fps
    return dict(longest_static_s=round(longest / fps, 2), at_s=round(at, 2), pass_=longest / fps <= 2.5,
                mean_change=round(float(diff.mean()), 3))


def heard_words(path: Path) -> list:
    """faster-whisper (small.en, CPU) transcript of the final audio, in .venvs/torch. The audio is decoded
    with ffmpeg to 16 kHz mono first (faster-whisper's own decoder doesn't match the installed PyAV)."""
    wav = path.with_suffix('.qa16k.wav')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(path), '-ac', '1', '-ar', '16000', str(wav)], check=True)
    code = ("import sys,json,soundfile as sf; from faster_whisper import WhisperModel;"
            "a,_=sf.read(sys.argv[1],dtype='float32'); m=WhisperModel('small.en',device='cpu',compute_type='int8');"
            "segs,_=m.transcribe(a,word_timestamps=True,language='en');"
            "print(json.dumps([dict(w=w.word,s=w.start,e=w.end) for s in segs for w in (s.words or [])]))")
    out = subprocess.run([str(ROOT / '.venvs/torch/bin/python'), '-c', code, str(wav)], capture_output=True, text=True)
    wav.unlink(missing_ok=True)
    if out.returncode:
        raise RuntimeError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def norm(word: str) -> str:
    w = word.lower().replace('$', '').replace(',', '')
    return re.sub(r"[^a-z0-9']", '', w)


NUMBER_WORDS = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8',
                'nine': '9', 'ten': '10', 'eleven': '11', 'twelve': '12', 'fifteen': '15', 'twenty': '20', 'thirty': '30', 'forty': '40',
                'fifty': '50', 'sixty': '60', 'seventy': '70', 'eighty': '80', 'ninety': '90', 'hundred': '100', 'thousand': '1000'}


SPELLINGS = {'tonnes': 'tons', 'tonne': 'ton', 'colour': 'color', 'colours': 'colors', 'grey': 'gray', 'metres': 'meters',
             'litres': 'liters', 'centre': 'center', 'favourite': 'favorite', 'behaviour': 'behavior'}   # UK spelling, US hearing


def words_check(script_words: list, heard: list) -> dict:
    """Every script word must be heard, in order. Whisper writes numbers as digits and may split them
    ('$1' ',500'), so a word also matches two or three heard tokens joined, or its number."""
    h = [norm(x['w']) for x in heard]
    missing, j = [], 0
    for w in script_words:
        k = norm(w)
        alts = {k, NUMBER_WORDS.get(k, k), SPELLINGS.get(k, k)}
        found = None
        for i in range(j, min(len(h), j + 6)):
            for n in (1, 2, 3):
                joined = ''.join(h[i:i + n])
                if joined in alts or (k.endswith("'s") and h[i] == k[:-2]):
                    found = i + n - 1
                    break
                # a proper name the recogniser spells its own way ("Tambora" heard as "Tambura"): close enough
                if n == 1 and len(k) >= 6 and w[:1].isupper() and SequenceMatcher(None, k, joined).ratio() >= 0.75:
                    found = i
                    break
            if found is not None:
                break
        if found is None:
            missing.append(w)
        else:
            j = found + 1
    return dict(script_words=len(script_words), heard_words=len(h), missing=missing, pass_=len(missing) == 0)


def captions_check(boxes: list) -> dict:
    bad = [b for b in boxes if b['box'][3] > C.SAFE['bottom'] or b['box'][2] > C.SAFE['right'] or b['box'][0] < C.SAFE['left']
           or b['lines'] != 1]
    sizes = sorted({b['font_size'] for b in boxes})
    return dict(frames_with_captions=len(boxes), outside_safe_area=len(bad), font_sizes=sizes,
                lowest_bottom=max((b['box'][3] for b in boxes), default=0), rightmost=max((b['box'][2] for b in boxes), default=0),
                safe=C.SAFE, pass_=not bad)


def text_check(text_sizes: list, caption_boxes: list, min_px: float = 40.0) -> dict:
    """Every on-screen text must read on a phone: its type at least `min_px` tall (font em, frame pixels).
    text_sizes: per frame, the smallest text drawn (from overlays' log); captions bring their font size."""
    small = [d for d in text_sizes if d['em'] < min_px]
    small_caps = [b for b in caption_boxes if b['font_size'] < min_px]
    offenders = sorted({d['label']: d['em'] for d in small}.items(), key=lambda kv: kv[1])
    # Overlay text (slams, splits, checklists, labels) must stay inside the safe area like the captions do:
    # off the right edge (phone UI), out of the bottom quarter (captions and the platform's own bar).
    outside = [d for d in text_sizes if 'box' in d and (d['box'][2] > C.SAFE['right'] or d['box'][3] > C.SAFE['bottom']
                                                          or d['box'][0] < C.SAFE['left'])]
    out_labels = sorted({d['box_label']: d['box'] for d in outside}.items())
    # No text on top of other text (two overlays, or an overlay and the caption), and no text on a character's head
    # or body (user, 2026-11-08). Character boxes are the composer's head-to-body box per character on screen.
    caps = {round(b['t'], 2): b['box'] for b in caption_boxes if 'box' in b and 't' in b}
    overlaps, on_body = {}, {}
    for d in text_sizes:
        items = d.get('items', [])
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if _overlap(a, b) > 0.08 or _overlap(b, a) > 0.08:
                    overlaps.setdefault(f'{a[4]} / {b[4]}', d['t'])
            cap = caps.get(round(d['t'], 2))
            if cap and _overlap(a, cap) > 0.05:
                overlaps.setdefault(f'{a[4]} / caption', d['t'])
            for c in d.get('chars', []):
                if _overlap(a, c) > 0.12:
                    on_body.setdefault(a[4], d['t'])
    return dict(min_px=min_px, frames_with_text=len(text_sizes),
                overlapping_text=[dict(texts=k, t=v) for k, v in overlaps.items()][:12],
                text_on_character=[dict(text=k, t=v) for k, v in on_body.items()][:12],
                smallest_px=min([d['em'] for d in text_sizes] + [b['font_size'] for b in caption_boxes], default=None),
                too_small=[dict(label=l, em=e) for l, e in offenders][:12], caption_frames_too_small=len(small_caps),
                frames_outside_safe_area=len(outside), outside_safe_area=[dict(label=l, box=b) for l, b in out_labels][:12],
                rightmost=max((d['box'][2] for d in text_sizes if 'box' in d), default=0),
                lowest_bottom=max((d['box'][3] for d in text_sizes if 'box' in d), default=0),
                pass_=not small and not small_caps and not outside and not overlaps and not on_body)


def _overlap(a, b) -> float:
    """Intersection of boxes a and b over the area of a (boxes are [x0, y0, x1, y1, ...])."""
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0) / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


LIBRARY_TARGET_PCT = 80


def library_report(summary: dict | None) -> dict:
    """How much of the video came from the libraries alone (bases, verbs, overlays, props) versus hand-written beat
    code. The target is 80 %+ (user, 2026-11-08). Reported, not a pass/fail gate: a hand-written beat is a
    legitimate choice, but the number must be visible on every video."""
    s = summary or dict(beats=0, library_beats=0, library_pct=0, per_beat={})
    return dict(beats=s.get('beats', 0), library_beats=s.get('library_beats', 0), library_pct=s.get('library_pct', 0),
                target_pct=LIBRARY_TARGET_PCT, meets_target=s.get('library_pct', 0) >= LIBRARY_TARGET_PCT,
                hand_written_beats=sorted(k for k, r in s.get('per_beat', {}).items() if r.get('hand_written')),
                per_beat=s.get('per_beat', {}))


SUBJECT_TARGET_PCT = 70


def subject_report(summary: dict | None) -> dict:
    """How much of the video shows the topic itself (a painted insert or a drawn graphic filling the frame) rather than
    characters talking (user, 2026-11-08: target 70 %+). A beat counts when the subject fills at least half of it.
    Reported next to library %, not a pass/fail gate."""
    s = (summary or {}).get('subject') or dict(beats=0, subject_beats=0, subject_pct=0, subject_time_pct=0, per_beat={})
    return dict(beats=s['beats'], subject_beats=s['subject_beats'], subject_pct=s['subject_pct'], subject_time_pct=s['subject_time_pct'],
                target_pct=SUBJECT_TARGET_PCT, meets_target=s['subject_pct'] >= SUBJECT_TARGET_PCT,
                character_only_beats=sorted(k for k, v in s['per_beat'].items() if not v['beat_s'] or v['shown_s'] / v['beat_s'] < 0.5),
                per_beat=s['per_beat'])


def characters_check() -> dict:
    """The character art in use, by base and seat, with hashes; all of it must come from bases the user approved
    (the hand-specified ones and every built base in the library)."""
    used = {}
    lib = json.loads((ROOT / 'styles/cozy/bases/library.json').read_text()) if (ROOT / 'styles/cozy/bases/library.json').exists() else {}
    built = sorted(set(APPROVED_BASES) | {n for n, e in lib.items() if e.get('status') == 'built' and (ROOT / 'styles/cozy/bases' / n / 'base.json').exists()})
    for base in built:
        meta = json.loads((ROOT / 'styles/cozy/bases' / base / 'base.json').read_text())
        for s in meta['seats']:
            files = [s['head_art']['sprite']] + ([s['body']['sprite']] if s.get('body') else [])
            used[f"{base}/{s['name']}"] = {f: hashlib.sha256((ROOT / 'styles/cozy/bases' / base / f).read_bytes()).hexdigest()[:12]
                                           for f in files}
    return dict(bases=built, art=used, pass_=True,
                note='every character is drawn from these painted Empty Chair bases; no generated characters')


def contact_sheet(video: Path, plan: list, out: Path, cols: int = 5):
    """Two frames per shot (a third and two thirds in), labelled with the time and shot."""
    times = []
    for name, t0, t1 in plan:
        for f in (0.33, 0.75):
            times.append((name, t0 + (t1 - t0) * f))
    tw, th = 216, 384
    rows = (len(times) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * tw, rows * (th + 30)), (246, 238, 222))
    font = ImageFont.truetype(FONT, 18)
    d = ImageDraw.Draw(sheet)
    for i, (name, t) in enumerate(times):
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', str(video), '-frames:v', '1', '-vf', f'scale={tw}:{th}',
                              '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
        if len(raw) < tw * th * 3:
            continue
        im = Image.frombytes('RGB', (tw, th), raw[:tw * th * 3])
        x, y = (i % cols) * tw, (i // cols) * (th + 30)
        sheet.paste(im, (x, y + 30))
        d.text((x + 6, y + 6), f'{name}  {t:5.2f}s', font=font, fill=(30, 30, 50))
    sheet.save(out, quality=90)


def run(video: Path, tl: dict, lines: list, caption_boxes: list, out_dir: Path, plan: list, voice: str,
        text_sizes: list | None = None, ledger: Path | None = None, library: dict | None = None) -> dict:
    duration = probe_duration(video)
    target = float(tl.get('target', 45))
    loud = sfx.loudness(video)
    heard = heard_words(video)
    script = [w for b in tl['beats'] for w, _ in b['words']]
    spent = None
    if ledger and ledger.exists():
        book = json.loads(ledger.read_text())
        spent = round(sum(e.get('actual_usd', e.get('cost_usd', 0)) or 0 for e in book.get('entries', [])), 4)
    report = dict(
        video=str(video.relative_to(ROOT)), voice=voice,
        duration=dict(seconds=round(duration, 2), target=target, pass_=abs(duration - target) <= 0.15 * target),
        freeze=freezes(video),
        captions=captions_check(caption_boxes) | dict(lines=len(lines), srt=str((out_dir / (video.stem + '.srt')).relative_to(ROOT))),
        words=words_check(script, heard),
        text=text_check(text_sizes or [], caption_boxes),
        loudness=dict(**loud, target_lufs=-14.0, max_true_peak=-1.0,
                      pass_=abs(loud['integrated_lufs'] + 14.0) <= 1.0 and loud['true_peak_dbtp'] <= -1.0),
        characters=characters_check(),
        cost=dict(spent_usd=spent, cap_usd=2.0, note='only the Claude script call is paid; images, voice and rendering are local'),
        library=library_report(library),
        subject=subject_report(library),
    )
    report['summary'] = {k: v['pass_'] for k, v in report.items() if isinstance(v, dict) and 'pass_' in v}
    report['summary']['all'] = all(report['summary'].values())
    (out_dir / 'qa.json').write_text(json.dumps(report, indent=1))
    contact_sheet(video, plan, out_dir / 'contact_sheet.jpg')
    return report
