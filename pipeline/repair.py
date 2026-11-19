"""The self-repair loop (user 2026-11-14): after a render, a failed QA check is diagnosed, fixed, the beats it touches
are rendered again (every other shot comes from the clip cache) and QA runs again; at most MAX_ROUNDS rounds. What
is still wrong after that is listed in plain words (`still_wrong`) for the phone, and the episode is "Done, QA failed".

The fixes per check:

* words: the checker first puts the script and the transcript in one canonical form (pipeline/qa.py `canonical`:
  numbers, symbols, units, chemical formulas, plurals). A word still missing is truly unheard: its beat is voiced
  again once, a little slower (`voice_speed`), which changes the audio Whisper hears. Still not heard after that:
  the writer gives the word a respelling ("KAIR-uh-jen"), it goes into data/pronunciations.json (pipeline/pronounce.py)
  and the beat is voiced again with it (user 2026-11-19); the captions keep the real spelling. Still not heard: the
  local model picks a plainer synonym that keeps the meaning (`swap_word`), the narration (so the captions),
  labels and anchors get it, and the beat is voiced again.
* text: a label on the host: round 1 moves it (the host goes to the other side and smaller, an insert label moves to
  the region away from the host); round 2 drops that label (the narration still says it). Two texts on each other:
  only the less important one is dropped (`importance`: a myth mark, then the beat's key term, outrank the rest).
* story_match: a picture of the myth (pipeline/understand.py) that nothing on screen marks as the myth gets a MYTH
  label (an insert), "Myth" on a split's first half, or "Myth:" before a diagram's text.
* duration: a video more than 15 % under its target gets one short sentence on the one or two thinnest middle beats,
  written by the local model from the facts those beats already cite (no new facts, no new numbers); only those beats
  are voiced again. The writer's own word target follows the speaking rate Kokoro really has (`pipeline.pacing`).

The fixes are hints in `episodes/<slug>/repairs.json` ({"beats": {id: hints}, "log": [...]}) that the composer applies
in memory (`apply_hints`), so the approved script is untouched; only the duration and the word fixes edit the
narration, and record that in the approval log as `self-repair (QA duration)` / `self-repair (QA words)`.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from . import config

MAX_ROUNDS = 3                      # slower, then a respelling, then a plainer word (user 2026-11-19)
SLOWER = 0.92                       # Kokoro speed for a beat voiced again
OTHER_SIDE = {'left': 'right', 'right': 'left', 'centre': 'left'}
SMALLER = {'close': 'large', 'large': 'medium', 'medium': 'small', 'small': 'small'}
AWAY = {'left': 'right', 'right': 'left', 'centre': 'top'}
CANT = {'freeze': 'part of the video froze', 'captions': 'a caption left the safe area',
        'fill': 'part of the frame was empty', 'host': 'the host was off or wrong on screen',
        'loudness': 'the sound level was off', 'characters': 'a character was drawn wrong',
        'hook': 'the hook is not answered in the payoff', 'placeholders': 'a scene or prop is a placeholder',
        'story_match': 'a picture does not match its beat'}


def path(slug: str) -> Path:
    return config.EPISODES_DIR / slug / 'repairs.json'


def load(slug: str) -> dict:
    try:
        data = json.loads(path(slug).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(slug: str, data: dict):
    p = path(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False) + '\n')


def hints_for(slug: str, beat: str) -> dict:
    return ((load(slug).get('beats') or {}).get(beat)) or {}


# ---- labels as text -------------------------------------------------------------------------------------------
def tidy_label(text: str) -> str:
    """A label written as a key ("dinosaur_fossils_65million_years_old") as words: "Dinosaur fossils 65 million years
    old". Ordinary labels are unchanged. 2026-11-14: the crude-oil script put two snake_case keys on screen."""
    t = str(text or '')
    if '_' not in t:
        return t
    t = re.sub(r'(\d)([A-Za-z])', r'\1 \2', t.replace('_', ' '))
    t = ' '.join(t.split())
    return t[:1].upper() + t[1:]


def _texts(shot: dict):
    """(container, key) pairs for every label-like text in a shot dict, for tidy/drop."""
    ins = shot.get('insert') or {}
    for e in ins.get('effects') or []:
        if e.get('kind') == 'label':
            yield e, 'detail'
    g = shot.get('graphic') or {}
    for k in range(len(g.get('items') or [])):
        yield g['items'], k
    sp = shot.get('split') or {}
    for k in range(len(sp.get('labels') or [])):
        yield sp['labels'], k


def apply_hints(episode, specs: dict, repairs: dict) -> list:
    """Apply tidy labels (always) and the repair hints (`repairs['beats']`) to the composer's in-memory episode
    (pydantic) and beat dicts. Returns notes. Nothing is written to disk."""
    notes = []
    beats = repairs.get('beats') or {}
    for b in episode.beats:
        spec = specs.get(b.id)
        if spec is None:
            continue
        shot = spec.setdefault('shot', {})
        for box, k in list(_texts(shot)):
            box[k] = tidy_label(box[k])
        h = beats.get(b.id) or {}
        drop = {tidy_label(x) for x in h.get('drop') or []}
        if drop:
            ins = shot.get('insert') or {}
            if ins.get('effects'):
                ins['effects'] = [e for e in ins['effects'] if not (e.get('kind') in ('label', 'pin')
                                                                    and tidy_label(e.get('detail') or '') in drop)]
            g = shot.get('graphic') or {}
            if g.get('items'):
                g['items'] = ['' if x in drop else x for x in g['items']]    # the layer stays, its text goes
            sp = shot.get('split') or {}
            if sp.get('labels'):
                sp['labels'] = ['' if x in drop else x for x in sp['labels']]
            if tidy_label(spec.get('on_screen_text') or '') in drop:
                spec['on_screen_text'] = ''
            for m in shot.get('motion') or []:
                if m.get('recipe') == 'text_slam' and tidy_label(m.get('detail') or '') in drop:
                    m['detail'] = ''
                elif m.get('recipe') in ('checklist_tick', 'split_compare') and m.get('detail'):
                    parts = [x.strip() for x in m['detail'].split('|') if x.strip()]
                    left = [x for x in parts if tidy_label(x) not in drop]
                    if len(left) != len(parts):        # a row of a checklist (or a half of a compare) goes
                        if left and (m['recipe'] == 'checklist_tick' or len(left) == 2):
                            m['detail'] = ' | '.join(left)
                        else:
                            m['recipe'], m['anchor'], m['detail'] = 'push_in', '', ''
            notes.append(f'{b.id}: dropped {sorted(drop)}')
        for text, region in (h.get('label_at') or {}).items():
            for e in (shot.get('insert') or {}).get('effects') or []:
                if e.get('kind') == 'label' and e.get('detail') == tidy_label(text):
                    e['at'] = region
                    notes.append(f'{b.id}: label "{e["detail"]}" moved to {region}')
        if h.get('myth_mark'):
            notes.append(f'{b.id}: marked as the myth')
            mark_myth(shot, spec)
        host = shot.get('host')
        if host and (h.get('host_side') or h.get('host_size')):
            host['side'] = h.get('host_side', host.get('side'))
            host['size'] = h.get('host_size', host.get('size'))
            notes.append(f"{b.id}: host moved to {host['side']}, {host['size']}")
        # the pydantic beat follows the dict (the composer reads both)
        if b.shot.insert is not None and shot.get('insert'):
            from .models import InsertCue
            b.shot.insert = InsertCue.model_validate(shot['insert'])
        if b.shot.graphic is not None and shot.get('graphic'):
            b.shot.graphic.items = list(shot['graphic']['items'])
        if b.shot.split is not None and shot.get('split'):
            b.shot.split.labels = list(shot['split']['labels'])
        if b.shot.host is not None and host:
            b.shot.host.side, b.shot.host.size = host['side'], host['size']
        b.on_screen_text = spec.get('on_screen_text', b.on_screen_text)
        if (h.get('myth_mark') or drop) and shot.get('motion') is not None:
            from .models import Motion
            b.shot.motion = [Motion.model_validate(m) for m in shot['motion']]
    return notes


def mark_myth(shot: dict, spec: dict):
    """Make a beat read as the myth on screen: a MYTH label on an insert, "Myth" on a split's first half, or "Myth:"
    before a diagram's on-screen text (slammed)."""
    vis = shot.get('visual')
    if vis == 'insert' and shot.get('insert') is not None:
        fx = shot['insert'].setdefault('effects', [])
        if not any(e.get('kind') == 'label' and e.get('detail') == 'MYTH' for e in fx):
            fx.insert(0, dict(kind='label', anchor='', at='top', detail='MYTH'))
    elif vis == 'split' and shot.get('split'):
        labels = list(shot['split'].get('labels') or ['', ''])
        labels[0] = 'Myth'
        shot['split']['labels'] = labels
    else:
        ost = (spec.get('on_screen_text') or '').strip()
        spec['on_screen_text'] = 'MYTH' if not ost else (ost if ost.lower().startswith('myth') else f'Myth: {ost}')
        motion = shot.setdefault('motion', [])
        if not any(m.get('recipe') == 'text_slam' for m in motion):
            motion.append(dict(recipe='text_slam', anchor='', detail=''))


def repaired_episode_dict(ep_data: dict, repairs: dict) -> dict:
    """The episode as the composer shows it (tidy labels, dropped labels gone), for QA's promised texts."""
    import copy
    ep = copy.deepcopy(ep_data)
    beats = repairs.get('beats') or {}
    for b in ep.get('beats', []):
        shot = b.get('shot') or {}
        sp, ins = shot.get('split') or {}, shot.get('insert') or {}
        if shot.get('visual') == 'split' and sp.get('key') and not (ins.get('key') or '').strip():
            shot['visual'], shot['split'] = 'insert', None       # compose_beats.split_fallback: the bottom scene alone
            shot['insert'] = dict(key=sp['key'], scene=sp.get('scene', ''), effects=list(ins.get('effects') or []))
        for box, k in list(_texts(shot)):
            box[k] = tidy_label(box[k])
        h = beats.get(b.get('id')) or {}
        if h.get('myth_mark'):
            mark_myth(shot, b)
        drop = {tidy_label(x) for x in h.get('drop') or []}
        if drop:
            ins = shot.get('insert') or {}
            ins['effects'] = [e for e in ins.get('effects') or [] if not (e.get('kind') == 'label' and e.get('detail') in drop)]
            g = shot.get('graphic') or {}
            if g.get('items'):
                g['items'] = ['' if x in drop else x for x in g['items']]
            sp = shot.get('split') or {}
            if sp.get('labels'):
                sp['labels'] = ['' if x in drop else x for x in sp['labels']]
            if tidy_label(b.get('on_screen_text') or '') in drop:
                b['on_screen_text'] = ''
            for m in shot.get('motion') or []:
                if m.get('recipe') == 'text_slam' and tidy_label(m.get('detail') or '') in drop:
                    m['detail'] = ''
                elif m.get('recipe') in ('checklist_tick', 'split_compare') and m.get('detail'):
                    parts = [x.strip() for x in m['detail'].split('|') if x.strip()]
                    left = [x for x in parts if tidy_label(x) not in drop]
                    if len(left) != len(parts):        # a row of a checklist (or a half of a compare) goes
                        if left and (m['recipe'] == 'checklist_tick' or len(left) == 2):
                            m['detail'] = ' | '.join(left)
                        else:
                            m['recipe'], m['anchor'], m['detail'] = 'push_in', '', ''
    return ep


# ---- diagnosis -------------------------------------------------------------------------------------------------
def beat_at(tl: dict, t: float) -> str | None:
    for b in tl.get('beats', []):
        if b['start'] - 0.2 <= t < b['end'] + 0.25:
            return b['id']
    return None


def beat_of_word(tl: dict, word: str, start_at: int = 0) -> str | None:
    from .qa import norm
    k = norm(word)
    for b in tl.get('beats', []):
        if any(norm(w) == k for w, _ in b.get('words', [])):
            return b['id']
    return None


def _host_side(ep_data: dict, beat: str, hints: dict) -> str:
    b = next((x for x in ep_data.get('beats', []) if x.get('id') == beat), {})
    host = (b.get('shot') or {}).get('host') or {}
    return hints.get('host_side') or host.get('side') or 'centre'


def _content(text: str) -> set:
    from .qa import _stem, content_words
    return {_stem(w) for w in content_words(str(text or ''))}


def spec_texts(spec: dict) -> list:
    """Every text a beat's spec puts on screen (tidied): insert labels, diagram items, split labels, its on-screen text
    and a text slam's own text."""
    shot = spec.get('shot') or {}
    out = [tidy_label(box[k]) for box, k in _texts(shot)]
    out += [spec.get('on_screen_text') or '']
    out += [m.get('detail') or '' for m in shot.get('motion') or [] if m.get('recipe') == 'text_slam']
    return list(dict.fromkeys(t for t in out if t and t.strip()))


def spec_text(label: str, spec: dict) -> str:
    """The beat's own text a drawn text came from: the same text, else the one text holding all its words (a slam the
    renderer stacked on two lines, an older report's "Is that" of "Is that actually right?"); else the label as given."""
    texts = spec_texts(spec)
    t = tidy_label(label)
    if t in texts or t == 'caption':
        return t
    words = _content(t) or set(t.lower().split())
    holding = [x for x in texts if words and words <= (_content(x) or set(x.lower().split()))]
    return min(holding, key=len) if holding else t


def split_pair(texts: str, spec: dict) -> list:
    """An overlap named as one string "A / B" (QA before the layout manifest) as its two texts. A text stacked on two
    lines was named with the same " / " ("MYTH / Is that / actually right?"), so the cut is the one where both sides
    are texts of the beat; else every piece, as before."""
    pieces = [x.strip() for x in str(texts).split(' / ')]
    known = set(spec_texts(spec)) | {'caption'}
    for i in range(1, len(pieces)):
        a, b = ' '.join(pieces[:i]), ' '.join(pieces[i:])
        if tidy_label(a) in known and tidy_label(b) in known:
            return [a, b]
    return pieces


def importance(text: str, spec: dict, ep_data: dict | None = None) -> tuple:
    """How much a text matters on its beat, for an overlap (user 2026-11-14: drop only the less important one):
    a myth mark first (the story needs it to read as the myth), then the beat's key term (its on-screen text, or a
    label naming what the narration says), then the more of the narration's words it carries; a tie keeps the longer."""
    from .qa import MYTH_MARK
    t = tidy_label(text)
    said = _content(spec.get('narration', ''))
    words = _content(t)
    is_ost = tidy_label(spec.get('on_screen_text') or '') == t
    shared = len(words & said)
    key = is_ost or shared > 0
    return (bool(MYTH_MARK.search(t)), key, is_ost, shared, len(t))


def diagnose(report: dict, tl: dict, ep_data: dict, repairs: dict) -> list:
    """One action per failed thing: dict(check, beat, cause, fix, hint) where hint is the change to the beat's hints
    (None when there is no automatic fix; `fix` then says what to do by hand)."""
    actions = []
    beats_h = repairs.get('beats') or {}
    words = report.get('words') or {}
    for w in words.get('missing') or []:
        beat = beat_of_word(tl, w)
        h = beats_h.get(beat) or {}
        swapped = h.get('swapped') or {}
        if beat and w.lower() in FILLERS and w.lower() not in swapped:
            actions.append(dict(check='words', beat=beat, cause=f'Whisper did not hear the filler "{w}"',
                                fix=f'drop "{w}" from {beat} and voice it again (a filler is never swapped)',
                                hint=dict(swap_word=w)))
        elif beat and not h.get('voice_speed'):
            actions.append(dict(check='words', beat=beat, cause=f'Whisper did not hear "{w}" (no spelling rule explains it)',
                                fix=f'voice {beat} again at speed {SLOWER}', hint=dict(voice_speed=SLOWER)))
        elif beat and w.lower() not in (h.get('respelled') or {}) and w.lower() not in FILLERS:
            actions.append(dict(check='words', beat=beat, cause=f'"{w}" was still not heard after the beat was voiced again',
                                fix=f'give "{w}" a respelling for the voice and voice {beat} again', hint=dict(respell=w)))
        elif beat and w.lower() not in swapped and w.lower() not in {x.lower() for x in swapped.values()}:
            actions.append(dict(check='words', beat=beat, cause=f'"{w}" was still not heard after the beat was voiced again',
                                fix=f'say a plainer word for "{w}" and voice {beat} again', hint=dict(swap_word=w)))
        else:
            actions.append(dict(check='words', beat=beat, cause=f'"{w}" was still not heard after the beat was voiced again',
                                fix=None, hint=None))
    text = report.get('text') or {}
    seen = set()
    problems = [(x, 'on the host') for x in text.get('text_on_character') or []] + \
               [(x, 'on another text') for x in text.get('overlapping_text') or []]
    expanded = []
    for item, where in problems:
        texts = item.get('texts')
        if texts and not item.get('text'):           # an overlap ([A, B]): only the less important text gives way
            beat = item.get('beat') or beat_at(tl, float(item.get('t', 0)))
            spec = next((b for b in ep_data.get('beats', []) if b.get('id') == beat), {})
            parts = texts if isinstance(texts, list) else split_pair(texts, spec)
            on_caption = 'caption' in parts
            # the caption itself never gives way; a text is named as the beat has it, so a drop matches it
            parts = [spec_text(x, spec) for x in parts if x and x != 'caption']
            if on_caption and parts:
                expanded += [(dict(item, text=x, keep='the caption'), where) for x in parts]
            elif len(parts) > 1:
                ranked = sorted(parts, key=lambda x: importance(x, spec, ep_data), reverse=True)
                keep, give = ranked[0], ranked[-1]
                expanded.append((dict(item, text=give, keep=keep), where))
            else:
                expanded += [(dict(item, text=x), where) for x in parts]
        else:
            expanded.append((item, where))
    for item, where in expanded:
        label = item.get('text') or item.get('label') or ''
        beat = item.get('beat') or beat_at(tl, float(item.get('t', 0)))
        if not beat or (beat, label) in seen:
            continue
        seen.add((beat, label))
        h = beats_h.get(beat) or {}
        spec = next((b for b in ep_data.get('beats', []) if b.get('id') == beat), {})
        shot = spec.get('shot') or {}
        is_insert_label = any(e.get('kind') == 'label' and tidy_label(e.get('detail')) == tidy_label(label)
                              for e in (shot.get('insert') or {}).get('effects') or [])
        moved = h.get('host_side') or (h.get('label_at') or {}).get(label)
        if item.get('keep'):                         # on another text: moving the host would not part them
            if label not in (h.get('drop') or []):
                hint = dict(drop=[label])
                fix = f'drop the label "{tidy_label(label)}"; keep ' + ('the caption' if item['keep'] == 'the caption' else
                                                                         f'"{tidy_label(item["keep"])}", the more important text')
            else:
                hint, fix = None, None
        elif not moved:
            side = _host_side(ep_data, beat, h)
            if is_insert_label:
                hint = dict(label_at={label: AWAY.get(side, 'top')})
                fix = f'move the label "{tidy_label(label)}" to the {AWAY.get(side, "top")}, away from the host'
            else:
                size = ((shot.get('host') or {}).get('size')) or 'medium'
                hint = dict(host_side=OTHER_SIDE.get(side, 'left'), host_size=SMALLER.get(size, 'small'))
                fix = f'move the host to the {hint["host_side"]} and make it {hint["host_size"]}'
        elif label not in (h.get('drop') or []):
            hint = dict(drop=[label])
            fix = f'drop the label "{tidy_label(label)}" (the narration still says it)'
        else:
            hint, fix = None, None
        actions.append(dict(check='text', beat=beat, cause=f'the text "{tidy_label(label)}" sat {where}', fix=fix, hint=hint))
    for k in ('too_small', 'outside_safe_area'):
        for item in text.get(k) or []:
            actions.append(dict(check='text', beat=None, cause=f'a text was {k.replace("_", " ")}: {str(item)[:80]}', fix=None, hint=None))
    for item in text.get('missing_text') or []:
        actions.append(dict(check='text', beat=item.get('beat'), cause=f'the promised text "{item.get("text")}" never showed',
                            fix=None, hint=None))
    sm = report.get('story_match') or {}
    for x in sm.get('unmarked_myth') or []:
        h = beats_h.get(x['beat']) or {}
        if not h.get('myth_mark'):
            actions.append(dict(check='story_match', beat=x['beat'], cause=f"the picture shows the myth ({', '.join(x['shows'])}) "
                                'but nothing on screen says it is the myth', fix='mark it: a MYTH label on the picture',
                                hint=dict(myth_mark=True)))
        else:
            actions.append(dict(check='story_match', beat=x['beat'], cause='the myth is still not marked on screen', fix=None, hint=None))
    dur = report.get('duration') or {}
    if dur and not dur.get('pass_'):
        sec, target = float(dur.get('seconds', 0)), float(dur.get('target', 45))
        if sec < target and not repairs.get('lengthened'):
            actions.append(dict(check='duration', beat=None, cause=f'the video is {sec:.1f} s, under the {target:.0f} s target by more '
                                f'than 15 %', fix='add one short sentence to the thinnest middle beats', hint='lengthen'))
        else:
            actions.append(dict(check='duration', beat=None, cause=f'the video is {sec:.1f} s against a {target:.0f} s target',
                                fix=None, hint=None))
    return actions


def still_wrong(report: dict, actions: list | None = None) -> list:
    """What is wrong with this render, in plain words, one line per problem."""
    lines = []
    summary = report.get('summary') or {}
    acts = actions if actions is not None else []
    by_check = {}
    for a in acts:
        by_check.setdefault(a['check'], []).append(a)
    for check, ok in summary.items():
        if check == 'all' or ok:
            continue
        if by_check.get(check):
            for a in by_check[check]:
                lines.append((f"{a['beat']}: " if a.get('beat') else '') + a['cause'])
        elif check == 'placeholders':
            lines.append((report.get('placeholders') or {}).get('note') or CANT[check])
        else:
            lines.append(CANT.get(check, f'the {check} check failed'))
    return lines


# ---- applying ----------------------------------------------------------------------------------------------------
def merge(repairs: dict, beat: str, hint: dict):
    h = repairs.setdefault('beats', {}).setdefault(beat, {})
    for k, v in hint.items():
        if k == 'drop':
            h['drop'] = sorted(set(h.get('drop') or []) | set(v))
        elif k == 'label_at':
            h.setdefault('label_at', {}).update(v)
        else:
            h[k] = v


def lengthen(slug: str, episode, words_per_second: float, seconds_short: float, write=None, ask=None) -> list:
    """Add one short sentence to the 1-2 thinnest middle beats, from the facts they cite, and re-stamp the script
    approval as the self-repair. `write(system, user, schema)` -> dict (the local model; a stand-in in tests).
    Returns [(beat id, sentence)] actually added."""
    from . import grounding, llm, store
    # only beats that cite facts, each sentence checked against them (2026-11-15 crude oil -5: a question beat "Is that
    # actually right?" got the answer added, and a transition before the twist got the twist)
    middle = [b for b in episode.beats[1:-1] if b.role not in ('hook', 'loop') and b.source_ids]
    if not middle:
        return []
    need_words = max(4, math.ceil(seconds_short * words_per_second) + 2)
    count = 1 if need_words <= 10 else 2
    thin = sorted(middle, key=lambda b: (len(b.narration.split()), b.id))[:count]
    per = max(5, min(12, math.ceil(need_words / count)))
    facts = {s.id: s.claim for s in episode.sources}
    rows = []
    for b in thin:
        cited = [f'{i}: {facts[i]}' for i in b.source_ids if i in facts]
        rows.append(dict(id=b.id, narration=b.narration, facts=cited))
    system = ('You lengthen a narrated explainer by a few seconds. For each beat, write ONE short extra sentence that '
              'follows its narration naturally. Use only what the listed facts say: no new facts, no new numbers, no names '
              f'that are not in them. About {per} words, plain spoken English, no quotes.')
    user = json.dumps(dict(title=episode.title or episode.topic, beats=rows), ensure_ascii=False)
    schema = dict(type='object', required=['beats'], properties=dict(beats=dict(type='array', items=dict(
        type='object', required=['id', 'sentence'], properties=dict(id=dict(type='string'), sentence=dict(type='string'))))))
    from . import entail
    if write is None:
        from .cache import for_episode
        cache = for_episode(config.EPISODES_DIR / slug)
        write = lambda s, u, sc: llm.generate_json(s, u, sc, 600, cache=cache, step='repair-duration', temperature=0.3)
        ask = ask or entail.default_ask(cache)        # each added sentence is fact-checked like the script's own
    was = episode.is_approved('script')               # before the narration changes
    answer = write(system, user, schema)
    allowed_numbers = set(re.findall(r'\d[\d,.]*', ' '.join(r['narration'] + ' ' + ' '.join(r['facts']) for r in rows)))
    added = []
    for item in (answer or {}).get('beats') or []:
        b = next((x for x in thin if x.id == item.get('id')), None)
        sentence = ' '.join(str(item.get('sentence') or '').split()).strip('"\' ')
        if b is None or not sentence or not 3 <= len(sentence.split()) <= 16:
            continue
        if set(re.findall(r'\d[\d,.]*', sentence)) - allowed_numbers:
            continue                                # a number the facts don't have: not added
        if sentence[-1] not in '.!?':
            sentence += '.'
        research = entail.research_text(b.source_ids, episode.sources)
        if not research or grounding.narrowed(sentence, research):
            continue                                # no fact to say it from, or a range made narrower
        others = [x.narration for x in episode.beats if x.id != b.id]
        if entail.repeats(sentence, others) or grounding.repeated_claim(
                [sentence], [('other', entail.sentences(o)) for o in others], grounding.topic_words(episode)):
            continue                                # another beat says it already
        if ask is not None and entail.supported(sentence, research, ask)[0] is False:
            continue                                # says more than the cited fact: not added
        b.narration = f'{b.narration.rstrip()} {sentence}'
        added.append((b.id, sentence))
    if added:
        base = config.EPISODES_DIR / slug
        if was:
            episode.approvals['script'] = episode.script_digest()
            episode.meta.setdefault('approval_log', []).append(dict(
                gate='script', by='self-repair (QA duration)', at=time.strftime('%Y-%m-%dT%H:%M:%S'),
                digest=episode.approvals['script'][:12], added=[dict(beat=i, sentence=s) for i, s in added]))
        store.save(episode, base)
    return added


SWAP_SYSTEM = ('A speech recogniser keeps mishearing one word of a narrated line in an explainer video. Say the line '
               'again with that word replaced by a plainer, common word (or two) that is easy to pronounce and keeps '
               'the meaning. Change the words right next to it only if the grammar needs it (e.g. "sank to turn into '
               'fuel" -> "sank to become fuel"); keep every other word as it is. Give the new line and the word(s) used '
               'in place of the misheard one.')
SWAP_SCHEMA = dict(type='object', required=['line', 'replacement'],
                   properties=dict(line=dict(type='string'), replacement=dict(type='string')))
WORD = re.compile(r"[A-Za-z0-9']+")


def added(old: str, new_line: str) -> str:
    """The words the new line has in place of the old line's changed words ("make ... become" -> "make become")."""
    import difflib
    a, b = WORD.findall(old), WORD.findall(new_line)
    ops = difflib.SequenceMatcher(None, [w.lower() for w in a], [w.lower() for w in b]).get_opcodes()
    return ' '.join(w for tag, i1, i2, j1, j2 in ops if tag in ('replace', 'insert') for w in b[j1:j2])


def swapped_line(old: str, word: str, new_line: str, new: str) -> bool:
    """The model's line is usable: the misheard word is gone, the replacement is in it, at most three words changed
    (2026-11-14: a one-word swap gave "sank to become into fuel"), and no new number."""
    import difflib
    a, b = [w.lower() for w in WORD.findall(old)], [w.lower() for w in WORD.findall(new_line)]
    if word.lower() in b or not all(w in b for w in WORD.findall(new.lower())):
        return False
    changed = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes()
                  if tag != 'equal')
    return 1 <= changed <= 3 and not (set(re.findall(r'\d+', new_line)) - set(re.findall(r'\d+', old)))


def _swap_in(text: str, word: str, new: str) -> str:
    """`word` replaced by `new` as a whole word, keeping a capital first letter."""
    def sub(m):
        got = m.group(0)
        return new[:1].upper() + new[1:] if got[:1].isupper() else new
    return re.sub(rf"(?<![A-Za-z']){re.escape(word)}(?![A-Za-z'])", sub, text, flags=re.I)


SLANG = set('goo gunk gloop glop muck stuff junk thingy thing poop dino dinos yucky gross ick icky gooey sludge '
            'whatchamacallit doohickey critters'.split())
REWRITTEN = '(the line said again)'


DROPPED = '(filler dropped)'
# user 2026-11-15: filler words are never swapped ("Hmm" became "yes": "Yes, yes. Is that actually right?"); a filler
# Whisper does not hear is dropped from the line
FILLERS = set('hmm hm hmmm mm mmm oh ooh ah ahh aha uh um umm er huh wow whoa well yeah yep okay ok hey'.split())


def drop_filler(line: str, word: str) -> str:
    """The line without the filler where it opens a sentence ("Hmm. Is that right?" -> "Is that right?"), the next word
    capitalised; '' when nothing would be left or the word does not open a sentence there."""
    pat = re.compile(rf"(^|(?<=[.!?]\s))\s*{re.escape(word)}\b[\s,.!?…-]*", re.I)
    new = pat.sub('', line).strip()
    if new == line.strip() or not re.search(r'[A-Za-z]', new):
        return ''
    return re.sub(r'(^|[.!?]\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), new)


def _plainer(slug, episode, b, beat_id, word, write=None, check=None) -> tuple:
    """(new line, the word said instead or REWRITTEN) for a word Whisper did not hear; ('', '') when none."""
    from . import llm
    if write is None:
        from .cache import for_episode
        cache = for_episode(config.EPISODES_DIR / slug)
        write = lambda s_, u, sc: llm.generate_json(s_, u, sc, 80, cache=cache, step='repair-words', temperature=0.2)
        from . import entail
        check = check or entail.default_ask(cache)
    answer = write(SWAP_SYSTEM, json.dumps(dict(line=b.narration, word=word), ensure_ascii=False), SWAP_SCHEMA) or {}
    new = ' '.join(str(answer.get('replacement') or '').split()).strip('"\'.,!? ')
    line = ' '.join(str(answer.get('line') or '').split()).strip('"\' ')
    if line and not re.fullmatch(r"[A-Za-z][A-Za-z' -]*", new or '-'):
        new = added(b.narration, line)                # 2026-11-14: "make ... become" for a good line: the new words
    judge = write if check is None else check
    clean = (new and len(new.split()) <= 3 and re.fullmatch(r"[A-Za-z][A-Za-z' -]*", new)
             and new.lower() != word.lower() and new.lower()[:5] != word.lower()[:5]
             and not set(new.lower().split()) & SLANG)    # 2026-11-15: "rock goo" for "kerogen"
    if clean and not line:                            # no line given: the word alone is swapped
        line = _swap_in(b.narration, word, new)
    if clean and (not swapped_line(b.narration, word, line, new) or new.lower() not in line.lower()):
        clean = False                                 # a split replacement ("make ... become") is no clean swap
    if clean:
        from . import entail                          # the new line passes the same checks as any narration
        ids = list(b.source_ids)
        ok, _ = entail.verdict(line, entail.research_text(ids, episode.sources), entail.allowed_groups(episode, ids),
                               judge, entail.CHECK_SYSTEM if ids else entail.NONFACT_SYSTEM)
        clean = ok is not False
    if not clean:                                     # no clean swap: the whole line again, through best-of-N
        from . import bestof
        line = bestof.rewrite_beat(episode, beat_id, avoid=[word], ask=judge)
        if not line:
            return '', ''
        new = REWRITTEN
    return line, new


def swap_word(slug: str, episode, beat_id: str, word: str, write=None, check=None) -> str:
    """A word Whisper still did not hear after a slower take: the local model picks a plainer synonym that keeps the
    meaning; the narration (so the captions too, which follow it), the on-screen text, labels and anchors that use the
    word get it, and the script approval is re-stamped as the self-repair. A beat that cites facts keeps the swap only
    when the new line passes the fact check (pipeline/entail.py). Returns the new word ('' when none)."""
    from . import store
    b = next((x for x in episode.beats if x.id == beat_id), None)
    if b is None or not re.search(rf"(?<![A-Za-z']){re.escape(word)}(?![A-Za-z'])", b.narration, re.I):
        return ''
    opens = re.search(rf"(^|[.!?]\s)\s*{re.escape(word)}\b", b.narration, re.I)
    if word.lower() in FILLERS and opens:             # a filler is never swapped: dropped, or left alone
        line, new = drop_filler(b.narration, word), DROPPED
    else:
        line, new = _plainer(slug, episode, b, beat_id, word, write, check)
    if not line:
        return ''
    if line[-1] not in '.!?' and b.narration.rstrip()[-1:] in '.!?':
        line += b.narration.rstrip()[-1]
    was = episode.is_approved('script')               # before the narration changes
    old_line = b.narration
    b.narration = line
    sh = b.shot
    if new in (REWRITTEN, DROPPED):                   # 2026-11-15: the marker itself went into a label; the texts keep
        sh = None                                     # their words (the text check judged them against the facts)
    else:
        b.on_screen_text = _swap_in(b.on_screen_text or '', word, new)
    for m in (sh.motion if sh else []):
        m.anchor = _swap_in(m.anchor or '', word, new)
    if sh and sh.insert:
        for e in sh.insert.effects:
            e.detail, e.anchor = _swap_in(e.detail or '', word, new), _swap_in(e.anchor or '', word, new)
    if sh and sh.graphic:
        sh.graphic.items = [_swap_in(x, word, new) for x in sh.graphic.items]
        sh.graphic.anchors = [_swap_in(x, word, new) for x in sh.graphic.anchors]
    if sh and sh.split:
        sh.split.labels = [_swap_in(x, word, new) for x in sh.split.labels]
        sh.split.anchors = [_swap_in(x, word, new) for x in sh.split.anchors]
    if new in (REWRITTEN, DROPPED):                   # words the rewritten line no longer says: re-anchored
        from . import auto
        auto.fix_script(episode)
    if was:
        episode.approvals['script'] = episode.script_digest()
        episode.meta.setdefault('approval_log', []).append(dict(
            gate='script', by='self-repair (QA words)', at=time.strftime('%Y-%m-%dT%H:%M:%S'),
            digest=episode.approvals['script'][:12], swapped=dict(beat=beat_id, word=word, now=new, was=old_line)))
    store.save(episode, config.EPISODES_DIR / slug)
    return new


RESPELL_SYSTEM = """Give a pronunciation respelling of one English word for a text-to-speech voice that says it wrong:
syllables joined by hyphens, the stressed syllable in CAPITALS, each syllable spelled the plain way English readers
sound it out ("kerogen" -> "KAIR-uh-jen", "Rayleigh" -> "RAY-lee", "catagenesis" -> "kat-uh-JEN-uh-sis"). Use "uh" for a
weak vowel, "ay" for the vowel of day, "ee" for see, "eye" or a final "y" for my, "oh" for go, "oo" for food. Letters
and hyphens only."""
RESPELL_SCHEMA = dict(type='object', required=['say'], properties=dict(say=dict(type='string')))


def respell(word: str, line: str, write=None) -> str:
    """The writer's respelling of a word the voice says so that Whisper does not hear it ('' when none is usable):
    Claude Code on the subscription when it is the writer and logged in, else the local model."""
    from . import pronounce
    user = f'Word: {word}\nThe sentence it is in: {line}'
    if write is not None:
        a = write(RESPELL_SYSTEM, user, RESPELL_SCHEMA) or {}
    else:
        import os
        from . import claude_code, llm
        if os.getenv('SCRIPT_PROVIDER', '').strip().lower() != 'ollama' and not claude_code.preflight():
            with llm.claude_route():
                a = llm.generate_json(RESPELL_SYSTEM, user, RESPELL_SCHEMA, 200, step='repair', detail=f'respell {word}')
        else:
            a = llm.generate_json(RESPELL_SYSTEM, user, RESPELL_SCHEMA, 200, step='repair', detail=f'respell {word}',
                                  temperature=0.0)
    say = ' '.join(str((a or {}).get('say') or '').split()).strip(' ."\'')
    return say if pronounce.valid_say(say) else ''


def run_round(slug: str, report: dict, tl: dict, rnd: int, words_per_second: float = 2.45, write=None) -> list:
    """Diagnose the report, apply every automatic fix and log it. Returns the applied log entries (empty: nothing
    left to try)."""
    from . import store
    ep_path = config.EPISODES_DIR / slug / 'episode.json'
    ep_data = json.loads(ep_path.read_text())
    repairs = load(slug)
    actions = diagnose(report, tl, ep_data, repairs)
    applied = []
    for a in actions:
        if a['hint'] is None:
            continue
        entry = dict(round=rnd, check=a['check'], beat=a['beat'], cause=a['cause'], fix=a['fix'])
        if a['hint'] == 'lengthen':
            episode, _ = store.load(slug)
            short = float(report['duration']['target']) * 0.9 - float(report['duration']['seconds'])
            try:
                added = lengthen(slug, episode, words_per_second, short, write=write)
            except Exception as e:                  # the local model is down: report it, never crash the render
                entry['fix'] = f'could not lengthen ({type(e).__name__}: {str(e)[:120]})'
                repairs.setdefault('log', []).append(entry)
                continue
            repairs['lengthened'] = True
            if not added:
                entry['fix'] = 'the model wrote nothing usable from the facts; not lengthened'
                repairs.setdefault('log', []).append(entry)
                continue
            entry['beat'] = ', '.join(i for i, _ in added)
            entry['fix'] = 'added: ' + ' | '.join(f'{i}: "{s}"' for i, s in added)
        elif 'respell' in a['hint']:
            word = a['hint']['respell']
            line = next((b.get('narration', '') for b in ep_data.get('beats', []) if b.get('id') == a['beat']), '')
            try:
                say = respell(word, line, write=write)
            except Exception as e:                  # the writer is down: report it, never crash the render
                say, entry['fix'] = '', f'could not respell "{word}" ({type(e).__name__}: {str(e)[:120]})'
            from . import pronounce
            if say and pronounce.add(word, say, f'{slug} {a["beat"]}'):
                entry['fix'] = f'the voice now says "{word}" as "{say}" (data/pronunciations.json); {a["beat"]} voiced again'
            elif not entry['fix'].startswith('could not'):
                entry['fix'] = f'no usable respelling for "{word}"'
            merge(repairs, a['beat'], dict(respelled={**(repairs.get('beats', {}).get(a['beat'], {}).get('respelled') or {}),
                                                      word.lower(): say}))
            if not say:
                repairs.setdefault('log', []).append(entry)
                continue
        elif 'swap_word' in a['hint']:
            episode, _ = store.load(slug)
            word = a['hint']['swap_word']
            try:
                now = swap_word(slug, episode, a['beat'], word, write=write)
            except Exception as e:                  # the local model is down: report it, never crash the render
                now, entry['fix'] = '', f'could not pick a plainer word ({type(e).__name__}: {str(e)[:120]})'
            if not now:
                entry['fix'] = entry['fix'] if entry['fix'].startswith('could not') else \
                    f'no usable plainer word for "{word}"'
                merge(repairs, a['beat'], dict(swapped={**(repairs.get('beats', {}).get(a['beat'], {}).get('swapped') or {}),
                                                        word.lower(): ''}))
                repairs.setdefault('log', []).append(entry)
                continue
            merge(repairs, a['beat'], dict(swapped={**(repairs.get('beats', {}).get(a['beat'], {}).get('swapped') or {}),
                                                    word.lower(): now}))
            entry['fix'] = (f'dropped the filler "{word}" ("{episode.beats[[x.id for x in episode.beats].index(a["beat"])].narration}") '
                            f'and voiced {a["beat"]} again (captions follow)' if now == DROPPED else
                            f'no clean plainer word for "{word}": the line was written again by best-of-N '
                            f'("{episode.beats[[x.id for x in episode.beats].index(a["beat"])].narration}") and '
                            f'{a["beat"]} voiced again (captions follow)' if now == REWRITTEN else
                            f'said "{now}" instead of "{word}" and voiced {a["beat"]} again (captions follow)')
        else:
            merge(repairs, a['beat'], a['hint'])
        repairs.setdefault('log', []).append(entry)
        applied.append(entry)
    save(slug, repairs)
    return applied
