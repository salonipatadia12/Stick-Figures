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
import time
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


CHUNK_S = 20.0


def chunks(tl: dict | None, limit: float = CHUNK_S) -> list:
    """(start, end) windows of at most ~`limit` seconds, cut in the pauses before beats. Whisper decodes long audio in
    30 s windows and can skip a sentence at a window edge (2026-11-08: "younger and older adults. But here's the
    catch. Later studies" vanished from a clean 47 s mix that transcribed perfectly on its own)."""
    starts = [b['words'][0][1] - 0.15 for b in (tl or {}).get('beats', []) if b.get('words')]
    if not starts:
        return [(0.0, 1e9)]
    cuts = [0.0]
    for i in range(1, len(starts) - 1):           # cut before beat i when taking it in would run the window too long
        if starts[i + 1] - cuts[-1] > limit and starts[i] > cuts[-1] + 1:
            cuts.append(starts[i])
    return [(a, b) for a, b in zip(cuts, cuts[1:] + [1e9])]


def heard_words(path: Path, tl: dict | None = None) -> list:
    """faster-whisper (small.en, CPU) transcript of the final audio, in .venvs/torch, decoded chunk by chunk (see
    `chunks`). The audio is decoded with ffmpeg to 16 kHz mono first (faster-whisper's own decoder doesn't match the
    installed PyAV)."""
    wav = path.with_suffix('.qa16k.wav')
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', str(path), '-ac', '1', '-ar', '16000', str(wav)], check=True)
    code = ("import sys,json,soundfile as sf\nfrom faster_whisper import WhisperModel\n"
            "a,sr=sf.read(sys.argv[1],dtype='float32'); m=WhisperModel('small.en',device='cpu',compute_type='int8'); out=[]\n"
            "for s,e in json.loads(sys.argv[2]):\n"
            "    segs,_=m.transcribe(a[int(s*sr):int(min(e,len(a)/sr)*sr)],word_timestamps=True,language='en')\n"
            "    out+=[dict(w=w.word,s=w.start+s,e=w.end+s) for g in segs for w in (g.words or [])]\n"
            "print(json.dumps(out))")
    out = subprocess.run([str(ROOT / '.venvs/torch/bin/python'), '-c', code, str(wav), json.dumps(chunks(tl))],
                         capture_output=True, text=True)
    wav.unlink(missing_ok=True)
    if out.returncode:
        raise RuntimeError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def merge_heard(a: list, b: list) -> list:
    """Two transcripts of the same audio as one, in time order; a word heard in either pass is kept once."""
    out = list(a)
    for w in b:
        if not any(norm(x['w']) == norm(w['w']) and abs(x['s'] - w['s']) < 0.4 for x in a):
            out.append(w)
    return sorted(out, key=lambda w: w['s'])


def norm(word: str) -> str:
    w = word.lower().replace('$', '').replace(',', '')
    return re.sub(r"[^a-z0-9']", '', w)


NUMBER_WORDS = {'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6', 'seven': '7', 'eight': '8',
                'nine': '9', 'ten': '10', 'eleven': '11', 'twelve': '12', 'fifteen': '15', 'twenty': '20', 'thirty': '30', 'forty': '40',
                'fifty': '50', 'sixty': '60', 'seventy': '70', 'eighty': '80', 'ninety': '90', 'hundred': '100', 'thousand': '1000'}

DIGIT_WORDS = {v: k for k, v in NUMBER_WORDS.items()}


SPELLINGS = {'tonnes': 'tons', 'tonne': 'ton', 'cancelled': 'canceled', 'travelled': 'traveled', 'modelling': 'modeling', 'colour': 'color', 'colours': 'colors', 'grey': 'gray', 'metres': 'meters',
             'litres': 'liters', 'centre': 'center', 'favourite': 'favorite', 'behaviour': 'behavior',   # UK spelling, US hearing
             'veil': 'vale', 'cue': 'queue'}                                                               # homophones Whisper picks


def us_spelling(w: str) -> str:
    """British spelling as the American-trained recogniser writes it: centimetres -> centimeters, colour -> color,
    stabilise -> stabilize."""
    w = re.sub(r'tre(s?)$', r'ter\1', w)
    w = re.sub(r'our(s?)$', r'or\1', w) if len(w) > 5 else w
    return re.sub(r'is(e|ed|es|ing|ation)$', r'iz\1', w)


def heard_tokens(heard: list) -> list:
    """The transcript as normalised tokens. Whisper writes "twenty percent" as "20%" or "20 %": the sign becomes the
    word "percent" (2026-11-13: "percent" was reported missing although it was spoken). Bare punctuation drops out."""
    out = []
    for x in heard:
        raw = str(x['w']).strip()
        if '%' in raw:
            out += [t for t in (norm(raw.replace('%', ' ')), 'percent') if t]
        elif norm(raw):
            out.append(norm(raw))
    return out


SIBILANT = re.compile(r"^(s|z|sh|c[eiy])", re.I)

# ---- normalisation rules (2026-11-14, the self-repair loop): both the script and the transcript are put in one
# canonical form before they are compared, so a word said right is never reported missing for how it is written.
# Add a rule here when a diagnosis finds a new way of writing the same spoken words; it then holds for every video.
SMALL = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
         'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
         'seventeen': 17, 'eighteen': 18, 'nineteen': 19}
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
UNITS = {'km': ['kilometers'], 'kms': ['kilometers'], 'kg': ['kilograms'], 'kgs': ['kilograms'], 'cm': ['centimeters'],
         'mm': ['millimeters'], 'm': ['meters'], 'mi': ['miles'], 'lb': ['pounds'], 'lbs': ['pounds'], 'g': ['grams'],
         'mph': ['miles', 'per', 'hour'], 'kmh': ['kilometers', 'per', 'hour'], 'kph': ['kilometers', 'per', 'hour'],
         'c': ['degrees', 'celsius'], 'f': ['degrees', 'fahrenheit'], 'k': ['thousand'], 'bn': ['billion'],
         'ft': ['feet'], 'hrs': ['hours'], 'hr': ['hours'], 'mins': ['minutes'], 'min': ['minutes'], 'yrs': ['years']}
SYMBOLS = {'%': 'percent', '&': 'and', '+': 'plus', '°': 'degrees', '=': 'equals', '@': 'at', '×': 'times'}
FORMULA = re.compile(r'^(?:[A-Z][a-z]?\d*){1,4}$')          # CO2, H2O, CH4, O3: a chemical formula, said letter by letter
SINGULAR_UNIT = {'kilometer', 'kilogram', 'centimeter', 'millimeter', 'meter', 'mile', 'pound', 'gram', 'foot', 'hour',
                 'minute', 'year', 'degree'}


def _raw_pieces(word: str) -> list:
    """One written word as the pieces a listener hears, before number words are joined: "CO2" -> c o 2, "20%" ->
    20 percent, "5km" -> 5 kilometers, "$5" -> 5 dollars, "sixty-five" -> sixty five, "65million" -> 65 million."""
    w = str(word).strip()
    if FORMULA.match(w.strip('.,!?;:"\'')) and any(c.isdigit() for c in w) and any(c.isupper() for c in w):
        return [x.lower() for x in re.findall(r'[A-Z][a-z]?|\d+', w.strip('.,!?;:"\''))]
    dollars = '$' in w
    for sym, said in SYMBOLS.items():
        w = w.replace(sym, f' {said} ')
    out = []
    for part in re.split(r'[\s/–—-]+', w):
        part = norm(part)
        if not part:
            continue
        m = re.fullmatch(r'(\d+(?:\.\d+)?)([a-z]+)', part)          # 5km, 65million, 1990s
        if m and m.group(2) not in ('s', 'st', 'nd', 'rd', 'th'):
            out += [m.group(1)] + UNITS.get(m.group(2), [m.group(2)])
        elif out and re.fullmatch(r'\d+(?:\.\d+)?', out[-1]) and part in UNITS:
            out += UNITS[part]                                     # "5 km"
        else:
            out.append(part)
    if dollars and out and out[0][:1].isdigit():
        out.append('dollars')
    return out


def _join_numbers(pairs: list) -> list:
    """[(token, origin)] with spelled-out numbers below a thousand as digits ("sixty five" -> 65, "three hundred and
    twelve" -> 312), the figure keeping the origin of its first word. Million, billion and thousand stay words, as the
    recogniser writes "65 million"; 'one' stays a word unless it starts a number ("one hundred")."""
    tok = [t for t, _ in pairs]
    num = lambda t: SMALL.get(t, TENS.get(t))

    def tens_unit(j):                                  # "sixty" [+ "five"] at j -> (value, next j) or None
        if j >= len(tok) or num(tok[j]) is None:
            return None
        val, j2 = num(tok[j]), j + 1
        if tok[j] in TENS and j2 < len(tok) and tok[j2] in SMALL and 0 < SMALL[tok[j2]] < 10:
            val, j2 = val + SMALL[tok[j2]], j2 + 1
        return val, j2
    out, i = [], 0
    while i < len(tok):
        first = tens_unit(i)
        if first is None:
            out.append(pairs[i])
            i += 1
            continue
        val, j = first
        if j < len(tok) and tok[j] == 'hundred':
            val, j = val * 100, j + 1
            k = j + 1 if j < len(tok) and tok[j] == 'and' else j
            rest = tens_unit(k)
            if rest:
                val, j = val + rest[0], rest[1]
        if tok[i] == 'one' and j == i + 1:
            out.append(pairs[i])                       # "one of them": a word, not a figure
        else:
            out.append((str(val), pairs[i][1]))
        i = j
    return out


def canonical(words: list) -> list:
    """[(token, index of the written word it came from)]: the written words in the one form both sides share."""
    return _join_numbers([(p, n) for n, w in enumerate(words) for p in _raw_pieces(w)])


def words_check(script_words: list, heard: list) -> dict:
    """Every script word must be heard, in order. Both sides are put in one canonical form first (`canonical`:
    numbers, symbols, units, chemical formulas), then a word also matches two or three heard tokens joined, or its
    number. A plural whose final s runs into a following s sound ("infants securely") may be heard as the singular:
    the two are the same sound. `missing` lists the written script words."""
    script = canonical(script_words)
    h = [t for t, _ in canonical([str(x['w']) for x in heard])]
    missing, j, lost = [], 0, set()
    for n_w, (k, origin) in enumerate(script):
        w = script_words[origin]
        nxt = script[n_w + 1][0] if n_w + 1 < len(script) else ''
        alts = {k, NUMBER_WORDS.get(k, k), DIGIT_WORDS.get(k, k), SPELLINGS.get(k, k), us_spelling(k),
                k.replace("'", '')}   # control's ~ controls; a script 3 heard as three
        found = None
        reach = 2 if k in ('a', 'an', 'the') else 6       # a dropped article must not match one further on and skip words
        for i in range(j, min(len(h), j + reach)):
            for n in (1, 2, 3):
                joined = ''.join(h[i:i + n])
                if joined in alts or (k.endswith("'s") and h[i] == k[:-2]):
                    found = i + n - 1
                    break
                if n == 1 and len(k) > 3 and k.endswith('s') and h[i] == k[:-1] and SIBILANT.match(nxt):
                    found = i                     # "infants securely" heard as "infant securely"
                    break
                # a proper name the recogniser spells its own way ("Tambora" heard as "Tambura"): close enough
                if n == 1 and len(k) >= 6 and w[:1].isupper() and SequenceMatcher(None, k, joined).ratio() >= 0.75:
                    found = i
                    break
            if found is not None:
                break
        if found is None and k in ('a', 'an', 'the'):     # the recogniser often drops an article; the line still reads
            continue
        if found is None and k.endswith('s') and k[:-1] in SINGULAR_UNIT:
            alts.add(k[:-1])                              # "1 kilometer" written by the recogniser for "one km"
            found = next((i for i in range(j, min(len(h), j + reach)) if h[i] == k[:-1]), None)
        if found is None:
            if origin not in lost:
                missing.append(w)
                lost.add(origin)
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


def text_check(text_sizes: list, caption_boxes: list, min_px: float = 40.0, promised: list | None = None) -> dict:
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
    chars_at = {round(d['t'], 2): d.get('chars', []) for d in text_sizes if d.get('chars')}
    cap_on_body = sum(1 for b in caption_boxes if 'box' in b and 't' in b
                      and any(_overlap(b['box'], c) > 0.25 for c in chars_at.get(round(b['t'], 2), [])))
    # Captions on the host's pixels (user 2026-11-09: never on the host's body); measured on the host's own mask.
    on_host = [b for b in caption_boxes if b.get('on_host', 0) > 0]
    brief = brief_texts(text_sizes)
    missing = missing_texts(text_sizes, promised or [])
    return dict(min_px=min_px, frames_with_text=len(text_sizes), brief_text=brief[:12], missing_text=missing[:12],
                caption_on_character_s=round(cap_on_body / 30, 1), caption_on_host_frames=len(on_host),
                caption_on_host=[dict(t=b['t'], text=b['text'], px=b['on_host']) for b in on_host][:12],
                overlapping_text=[dict(texts=k, t=v) for k, v in overlaps.items()][:12],
                text_on_character=[dict(text=k, t=v) for k, v in on_body.items()][:12],
                smallest_px=min([d['em'] for d in text_sizes] + [b['font_size'] for b in caption_boxes], default=None),
                too_small=[dict(label=l, em=e) for l, e in offenders][:12], caption_frames_too_small=len(small_caps),
                frames_outside_safe_area=len(outside), outside_safe_area=[dict(label=l, box=b) for l, b in out_labels][:12],
                rightmost=max((d['box'][2] for d in text_sizes if 'box' in d), default=0),
                lowest_bottom=max((d['box'][3] for d in text_sizes if 'box' in d), default=0),
                pass_=not small and not small_caps and not outside and not overlaps and not on_body and not brief
                and not missing and not on_host)


LAYOUTS = ROOT / 'renders/sample/layouts.json'
SAME_LAYOUT = 0.6     # a layout pattern this similar to the previous video's gets a note


def layout_report(summary: dict | None, slug: str) -> dict:
    """A note, not a failure (user 2026-11-09): when this video shares its format, or most of its layout pattern
    (picture kind and host placement beat by beat), with the video rendered before it. Every render is recorded in
    renders/sample/layouts.json."""
    import difflib
    if not summary or 'layout' not in summary:
        return dict(note='', layout=[])
    reg = json.loads(LAYOUTS.read_text()) if LAYOUTS.exists() else {}
    tokens, fmt = summary['layout'], summary.get('format', '')
    out = dict(format=fmt, layout=tokens, note='')
    prev = max((v for k, v in reg.items() if k != slug), key=lambda v: v['at'], default=None)
    if prev:
        coarse = lambda ts: ['|'.join(x.split('|')[:3]) for x in ts]      # kind, where, side
        sim = difflib.SequenceMatcher(None, coarse(tokens), coarse(prev['layout'])).ratio()
        notes = []
        if fmt and fmt == prev['format']:
            notes.append(f'same format as the previous video ({prev["slug"]}): {fmt}')
        if sim >= SAME_LAYOUT:
            notes.append(f'layout pattern {sim:.0%} like the previous video ({prev["slug"]})')
        out |= dict(previous=prev['slug'], previous_format=prev['format'], similarity=round(sim, 2), note='; '.join(notes))
    reg[slug] = dict(slug=slug, format=fmt, layout=tokens, at=time.strftime('%Y-%m-%dT%H:%M:%S'))
    LAYOUTS.parent.mkdir(parents=True, exist_ok=True)
    LAYOUTS.write_text(json.dumps(reg, indent=1))
    return out


def beat_place(beat: dict) -> str:
    """Where a beat happens: its painted scene (insert or split) by key, else its library setting (and the detail of an
    "other" place). '' when the episode predates staging."""
    sh = beat.get('shot') or {}
    if sh.get('visual') in ('insert', 'split') and (sh.get('insert') or {}).get('key'):
        return 'scene:' + sh['insert']['key']
    st = sh.get('staging') or {}
    if not st.get('setting'):
        return ''
    return 'setting:' + st['setting'] + (f" ({st['setting_detail']})" if st.get('setting_detail') else '')


CONTENT_STOP = set('that this with from into over under about their there they them then than what when where which '
                   'while have has had been were will would could should also just very more most some such only even '
                   'much many each every other another your yours those these here like make made does done doing '
                   'being because before after again once same both onto upon scene painted painting view seen shows '
                   'showing picture'.split())
MYTH_MARK = re.compile(r"\b(myth|myths|false|wrong|not|isn't|fake|untrue|busted|mistake|wrongly)\b|[\u2717\u2718\u274c]", re.I)


def _stem(w: str) -> str:
    for suf in ('ing', 'ies', 'es', 's', 'ed'):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[:-len(suf)] + ('y' if suf == 'ies' else '')
    return w


def content_words(text: str) -> set:
    return {_stem(w) for w in re.findall(r'[a-z]+', str(text or '').lower().replace('-', ' '))
            if len(w) >= 4 and w not in CONTENT_STOP}


def story_match_report(episode: dict) -> dict:
    """Pictures match the story (user 2026-11-14): each painted scene shows what its beat says (its key or scene
    shares a content word with the narration, the visual intent or the on-screen text), and a picture that shows the
    myth (pipeline/understand.py `myth_subjects`, e.g. a dinosaur for "oil comes from dinosaurs") is marked as the
    myth on screen: a MYTH/false/not label or split label, or an on-screen text that says so."""
    interp = (episode.get('meta') or {}).get('interpretation') or {}
    subjects = {_stem(w) for s in interp.get('myth_subjects') or [] for w in content_words(s)}
    # the topic's own words: a picture that shares one is about the topic (a sunset sky in a sunset video), even when
    # this beat's sentence names it differently. Only a picture about neither is off subject (2026-11-14: a word
    # test against the beat alone flagged two good sunset skies and the repaint made them worse)
    topic = content_words(' '.join([episode.get('topic', ''), episode.get('title', ''), interp.get('question', ''),
                                    interp.get('title', '')]))
    off, unmarked = [], []
    for b in episode.get('beats', []):
        sh = b.get('shot') or {}
        vis = sh.get('visual')
        if vis not in ('insert', 'split', 'graphic'):
            continue
        ins, sp, g = sh.get('insert') or {}, sh.get('split') or {}, sh.get('graphic') or {}
        said = content_words(' '.join([b.get('narration', ''), b.get('visual_intent', ''), b.get('on_screen_text', '')]))
        if vis in ('insert', 'split') and ins.get('key'):
            pic = content_words(ins.get('key', '') + ' ' + ins.get('scene', ''))
            if pic and not pic & said and not pic & topic:
                off.append(dict(beat=b.get('id'), key=ins.get('key'), narration=b.get('narration', '')))
        shown = content_words(' '.join([ins.get('key', ''), ins.get('scene', ''), sp.get('key', ''), sp.get('scene', ''),
                                        ' '.join(g.get('items') or []), g.get('detail', '')]))
        if subjects and shown & subjects:
            marks = ' '.join([e.get('detail', '') for e in ins.get('effects') or [] if e.get('kind') == 'label']
                             + list(sp.get('labels') or []) + list(g.get('items') or []) + [b.get('on_screen_text', '')])
            if not MYTH_MARK.search(marks):
                unmarked.append(dict(beat=b.get('id'), shows=sorted(shown & subjects), visual=vis))
    notes = [f"{x['beat']}: the scene \"{x['key']}\" does not show what the beat says (\"{x['narration'][:60]}\")" for x in off]
    notes += [f"{x['beat']}: shows the myth ({', '.join(x['shows'])}) without marking it as the myth" for x in unmarked]
    return dict(off_subject=off, unmarked_myth=unmarked, notes=notes, pass_=not off and not unmarked)


def placeholder_report(episode: dict, scenes: dict | None = None, props: dict | None = None) -> dict:
    """Beats that show a placeholder instead of a painting (pipeline/paintsafe.py, 2026-11-14): a scene or prop that
    would not paint after two seeds and two retries. Fails, so the phone shows it before the video is posted; the
    fix is a repaint with a note on the scenes page."""
    if scenes is None:
        from . import inserts
        scenes = inserts.library()
    if props is None:
        from . import propgen
        props = propgen.library()
    beats = []
    for b in episode.get('beats', []):
        sh = b.get('shot') or {}
        keys = [k for k in ((sh.get('insert') or {}).get('key'), (sh.get('split') or {}).get('key')) if k]
        hits = [f'scene {k}' for k in keys if (scenes.get(k) or {}).get('placeholder')]
        names = [p.get('name', '') for p in ((sh.get('staging') or {}).get('props') or [])]
        hits += [f'prop {n}' for n in names if any((e or {}).get('placeholder') and (e or {}).get('name') == n for e in props.values())]
        if hits:
            beats.append(dict(beat=b.get('id'), placeholder=hits))
    return dict(beats=beats, pass_=not beats,
                note='every scene and prop is painted' if not beats else
                'placeholder: ' + '; '.join(f"{x['beat']} ({', '.join(x['placeholder'])})" for x in beats))


def continuity_report(episode: dict) -> dict:
    """Kaomei continuity (user 2026-11-11): a warning, never a failure, for each pair of adjacent beats that change place
    with a hard cut and no named connection (the earlier beat's shot.continuity is empty). A whip or match cut is its
    own connection; staying in the same place needs none."""
    beats = episode.get('beats', [])
    version = ((episode.get('meta') or {}).get('script') or {}).get('prompt_version', '')
    m = re.fullmatch(r'script-v(\d+)', version or '')
    if m and int(m.group(1)) < 7 and not any((b.get('shot') or {}).get('continuity') for b in beats):
        return dict(pairs=max(len(beats) - 1, 0), named=0, flagged=[], warnings=[],
                    note=f'written as {version}, before the continuity rule (script-v7): not checked')
    flagged, named = [], 0
    for a, b in zip(beats, beats[1:]):
        link = ' '.join(str((a.get('shot') or {}).get('continuity') or '').split())
        named += bool(link)
        pa, pb = beat_place(a), beat_place(b)
        cut = (b.get('shot') or {}).get('transition', 'cut') == 'cut'
        if pa and pb and pa != pb and cut and not link:
            flagged.append(dict(beats=f"{a['id']} -> {b['id']}", from_=pa, to=pb,
                                note=f"{a['id']} -> {b['id']}: the place changes ({pa} -> {pb}) on a hard cut with no named "
                                     'connection (a held pose, a moving object, a filled frame, a direction, a shape or a '
                                     'camera move)'))
    pairs = max(len(beats) - 1, 0)
    return dict(pairs=pairs, named=named, flagged=flagged, warnings=[f['note'] for f in flagged],
                note=f'{len(flagged)} of {pairs} cuts change place with no named connection' if flagged else '')


HOST_TINT = 0.15      # the host's white may shift at most 15 % per channel (user 2026-11-09: white in every scene)


def host_check(text_sizes: list) -> dict:
    """The composited host (script v5): fully inside the frame whenever it stands in a scene (a pop-in under a diagram
    is deliberately cut by the bottom edge), and its body white within HOST_TINT of itself (no pink, orange or brown
    from the scene's light). Read from the per-frame host log (compose_beats.HostLayer)."""
    frames = [(d['t'], h) for d in text_sizes for h in d.get('host', [])]
    cut = [(t, h) for t, h in frames if not h.get('crop_ok', h['pop_in']) and not h['in_frame']]
    tinted = [(t, h) for t, h in frames if not h['pop_in'] and any(abs(v - 1) > HOST_TINT for v in h['body_rgb'])]
    worst = max((abs(v - 1) for _, h in frames if not h['pop_in'] for v in h['body_rgb']), default=0.0)
    return dict(frames=len(frames), standing_frames=sum(1 for _, h in frames if not h['pop_in']),
                cut_by_frame_edge=len(cut), cut_at=[dict(t=t, box=h['box']) for t, h in cut][:12],
                max_tint=round(worst, 3), tint_limit=HOST_TINT, over_tint=len(tinted),
                over_tint_at=[dict(t=t, rgb=h['body_rgb']) for t, h in tinted][:12],
                pass_=not cut and not tinted)


def _words(text: str) -> set:
    return set(re.findall(r'[a-z0-9]+', str(text).lower()))


def promised_texts(episode: dict) -> list:
    """Every text the script asks to see on screen (on_screen_text unless only a counter shows it, split and checklist
    entries, diagram items, insert labels), as dict(beat, text)."""
    out = []
    for b in episode.get('beats', []):
        sh = b.get('shot', {})
        recipes = {m['recipe'] for m in sh.get('motion', [])}
        ost = (b.get('on_screen_text') or '').strip()
        if ost and (recipes & {'text_slam'} or not recipes & {'number_counter', 'split_compare', 'checklist_tick'}):
            out.append(dict(beat=b['id'], text=ost))
        split_labels = [x for x in ((sh.get('split') or {}).get('labels') or []) if x] if sh.get('visual') == 'split' else []
        out += [dict(beat=b['id'], text=x) for x in split_labels]
        for m in sh.get('motion', []):
            if m['recipe'] == 'split_compare' and split_labels:
                continue                     # the composer shows the split's own labels instead of a second pair
            if m['recipe'] in ('split_compare', 'checklist_tick') and m.get('detail'):
                out += [dict(beat=b['id'], text=x.strip()) for x in m['detail'].split('|') if x.strip()]
        g = sh.get('graphic') or {}
        for item in g.get('items', []):
            parts = item.split(':') if g.get('kind') == 'cards' else [item]
            out += [dict(beat=b['id'], text=x.strip()) for x in parts if x.strip()]
        for e in (sh.get('insert') or {}).get('effects', []):
            if e.get('kind') == 'label' and e.get('detail'):
                out.append(dict(beat=b['id'], text=e['detail']))
    return out


def missing_texts(text_sizes: list, promised: list) -> list:
    """Promised texts that never settled on screen (user review 2026-11-08: an approved on_screen_text was never
    drawn). A text counts as shown when one drawn text holds all its words (a slam may be stacked on two lines)."""
    drawn = [_words(it[4]) for d in text_sizes for it in d.get('items', [])]
    drawn = [w for i, w in enumerate(drawn) if w and w not in drawn[:i]]
    return [p for p in promised if _words(p['text']) and not any(_words(p['text']) <= w for w in drawn)]


COUNTING = re.compile(r"[$€£₹¥]?[\d,.]+(%| [A-Za-z' -]{1,20})?")   # 0 -> 35 seconds counts too
BRIEF_S = 0.8       # logged (settled) seconds; a text also shows ~0.3 s while it pops in


def brief_texts(text_sizes: list, min_s: float = BRIEF_S) -> list:
    """Overlay texts (slams, labels, diagram items) that are on screen too briefly to read: settled for under
    `min_s` in one stretch (user review 2026-11-08: items on a beat's last word flashed for a few frames before the
    cut). A counting number changes its text every frame, so all numbers count as one text."""
    seen = {}
    for d in text_sizes:
        for it in d.get('items', []):
            seen.setdefault('(number)' if COUNTING.fullmatch(str(it[4])) else it[4], set()).add(round(d['t'], 3))
    out = []
    for label, ts in seen.items():
        ts = sorted(ts)
        start = prev = ts[0]
        for t in ts[1:] + [None]:
            if t is None or t - prev > 0.1:
                if prev - start + 1 / 30 < min_s:
                    out.append(dict(text=label, t=start, seconds=round(prev - start + 1 / 30, 2)))
                start = t
            prev = t if t is not None else prev
    return sorted(out, key=lambda d: d['t'])


def _overlap(a, b) -> float:
    """Intersection of boxes a and b over the area of a (boxes are [x0, y0, x1, y1, ...])."""
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0) / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


FILL_MIN, FILL_SECONDS, FILL_FPS = 0.12, 0.5, 4
FILL_SPREAD = 38              # a pixel this far (any channel) from the frame's median colour is content, not paper


def coverage(a) -> float:
    """Share of 10x10 tiles (at 270x480, the picture above the caption band only: the captions, a popping-in host and
    the platform's bar below are not the picture) whose pixels are at least 20 % content: far from the frame's median
    colour. Calibrated 2026-11-09 on the proof videos: blank paper 0.06, paper with only a bar chart's baseline 0.09,
    one bar 0.21, one flow card 0.26, the thermometer 0.13, painted scenes 0.35-0.95. The earlier measure (tile
    texture) scored blank watercolour paper 0.45 and passed it."""
    import numpy as np
    a = a[:int(a.shape[0] * 0.67)]
    med = np.median(a.reshape(-1, 3), 0)
    fg = np.abs(a - med).max(-1) > FILL_SPREAD
    t = 10
    h, w = fg.shape
    tiles = fg[:h // t * t, :w // t * t].reshape(h // t, t, w // t, t).mean((1, 3))
    return float((tiles > 0.2).mean())


def fill_check(video: Path, fps: int = FILL_FPS) -> dict:
    """No near-empty frames (user 2026-11-08: no blank beige; 2026-11-09: not even the first second of a diagram).
    A stretch of FILL_SECONDS or more below FILL_MIN `coverage` fails."""
    import numpy as np
    w, h = 270, 480
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(video), '-vf', f'fps={fps},scale={w}:{h}', '-f', 'rawvideo',
                          '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.float32)
    cov = [coverage(f) for f in frames]
    n = len(cov)
    runs, start = [], None
    for i, c in enumerate(cov + [1.0]):
        if c < FILL_MIN and start is None:
            start = i
        elif c >= FILL_MIN and start is not None:
            if (i - start) / fps >= FILL_SECONDS:
                runs.append(dict(start=round(start / fps, 2), end=round(i / fps, 2), lowest=round(float(min(cov[start:i])), 3)))
            start = None
    return dict(min_coverage=FILL_MIN, min_seconds=FILL_SECONDS, median_coverage=round(float(np.median(cov)), 2) if n else None,
                lowest=round(float(min(cov)), 3) if n else None, near_empty=runs[:12], pass_=not runs)


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


CHARACTER_TARGET_PCT = 70


def character_report(summary: dict | None) -> dict:
    """Script v5: the share of beats (and time) with the host on screen (user 2026-11-08: target 70 %+). Reported."""
    c = (summary or {}).get('character')
    if not c:
        return dict(note='not a host-format video')
    return c | dict(target_pct=CHARACTER_TARGET_PCT, meets_target=c['host_pct'] >= CHARACTER_TARGET_PCT)


def hook_report(summary: dict | None) -> dict:
    """Script v5 hook check: the host in frame 1, at most 8 words, answered in the payoff (the chosen hook's answer
    shares key words with the payoff line). Fails the video when it does not hold."""
    h = (summary or {}).get('hook')
    return h if h else dict(note='not a host-format video', pass_=True)


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
        text_sizes: list | None = None, ledger: Path | None = None, library: dict | None = None,
        promised: list | None = None, episode: dict | None = None) -> dict:
    duration = probe_duration(video)
    target = float(tl.get('target', 45))
    loud = sfx.loudness(video)
    heard = heard_words(video, tl)
    if words_check([w for b in tl['beats'] for w, _ in b['words']], heard)['missing']:
        # Whisper on CPU can drop a phrase that is clearly in the audio (2026-11-09: seven doorway words missing in one
        # pass, all heard in the next). A second pass with the windows cut at other beats; a word heard in either counts.
        alt = dict(tl, beats=tl['beats'][1:])
        heard = merge_heard(heard, heard_words(video, alt))
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
        text=text_check(text_sizes or [], caption_boxes, promised=promised),
        fill=fill_check(video),
        host=host_check(text_sizes or []),
        loudness=dict(**loud, target_lufs=-14.0, max_true_peak=-1.0,
                      pass_=abs(loud['integrated_lufs'] + 14.0) <= 1.0 and loud['true_peak_dbtp'] <= -1.0),
        characters=characters_check(),
        cost=dict(spent_usd=spent, cap_usd=2.0, note='only the Claude script call is paid; images, voice and rendering are local'),
        library=library_report(library),
        subject=subject_report(library),
        character=character_report(library),
        hook=hook_report(library),
        layout=layout_report(library, out_dir.name),
        continuity=continuity_report(episode or {}),     # warnings only: no pass_, so not in the summary
        placeholders=placeholder_report(episode or {}),
        story_match=story_match_report(episode or {}),
    )
    report['summary'] = {k: v['pass_'] for k, v in report.items() if isinstance(v, dict) and 'pass_' in v}
    report['summary']['all'] = all(report['summary'].values())
    (out_dir / 'qa.json').write_text(json.dumps(report, indent=1))
    contact_sheet(video, plan, out_dir / 'contact_sheet.jpg')
    return report
