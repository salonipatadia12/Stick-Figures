"""The self-repair loop (user 2026-11-14): after a render, a failed QA check is diagnosed, fixed, the beats it touches
are rendered again (every other shot comes from the clip cache) and QA runs again; at most MAX_ROUNDS rounds. What
is still wrong after that is listed in plain words (`still_wrong`) for the phone, and the episode is "Done, QA failed".

The fixes per check:

* words: the checker first puts the script and the transcript in one canonical form (pipeline/qa.py `canonical`:
  numbers, symbols, units, chemical formulas, plurals). A word still missing is truly unheard: its beat is voiced
  again once, a little slower (`voice_speed`), which changes the audio Whisper hears.
* text: a label on the host or on another text. Round 1 moves it: the host goes to the other side and smaller, an
  insert label moves to the region away from the host. Round 2 drops that label (the narration still says it).
* duration: a video more than 15 % under its target gets one short sentence on the one or two thinnest middle beats,
  written by the local model from the facts those beats already cite (no new facts, no new numbers); only those beats
  are voiced again. The writer's own word target follows the speaking rate Kokoro really has (`pipeline.pacing`).

The fixes are hints in `episodes/<slug>/repairs.json` ({"beats": {id: hints}, "log": [...]}) that the composer applies
in memory (`apply_hints`), so the approved script is untouched; only the duration fix edits the narration, and records
that in the approval log as `self-repair (QA duration)`.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

from . import config

MAX_ROUNDS = 2
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
                ins['effects'] = [e for e in ins['effects'] if not (e.get('kind') == 'label' and e.get('detail') in drop)]
            g = shot.get('graphic') or {}
            if g.get('items'):
                g['items'] = ['' if x in drop else x for x in g['items']]    # the layer stays, its text goes
            if spec.get('on_screen_text') in drop:
                spec['on_screen_text'] = ''
            notes.append(f'{b.id}: dropped {sorted(drop)}')
        for text, region in (h.get('label_at') or {}).items():
            for e in (shot.get('insert') or {}).get('effects') or []:
                if e.get('kind') == 'label' and e.get('detail') == tidy_label(text):
                    e['at'] = region
                    notes.append(f'{b.id}: label "{e["detail"]}" moved to {region}')
        if h.get('insert_key') and shot.get('insert'):
            shot['insert']['key'] = h['insert_key']
            shot['insert']['scene'] = h.get('insert_scene') or shot['insert'].get('scene', '')
            notes.append(f"{b.id}: scene repainted as {h['insert_key']}")
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
        if h.get('myth_mark') and shot.get('motion') is not None:
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
        if h.get('insert_key') and shot.get('insert'):
            shot['insert']['key'] = h['insert_key']
            shot['insert']['scene'] = h.get('insert_scene') or shot['insert'].get('scene', '')
        if h.get('myth_mark'):
            mark_myth(shot, b)
        drop = {tidy_label(x) for x in h.get('drop') or []}
        if drop:
            ins = shot.get('insert') or {}
            ins['effects'] = [e for e in ins.get('effects') or [] if not (e.get('kind') == 'label' and e.get('detail') in drop)]
            g = shot.get('graphic') or {}
            if g.get('items'):
                g['items'] = ['' if x in drop else x for x in g['items']]
            if b.get('on_screen_text') in drop:
                b['on_screen_text'] = ''
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


def diagnose(report: dict, tl: dict, ep_data: dict, repairs: dict) -> list:
    """One action per failed thing: dict(check, beat, cause, fix, hint) where hint is the change to the beat's hints
    (None when there is no automatic fix; `fix` then says what to do by hand)."""
    actions = []
    beats_h = repairs.get('beats') or {}
    words = report.get('words') or {}
    for w in words.get('missing') or []:
        beat = beat_of_word(tl, w)
        h = beats_h.get(beat) or {}
        if beat and not h.get('voice_speed'):
            actions.append(dict(check='words', beat=beat, cause=f'Whisper did not hear "{w}" (no spelling rule explains it)',
                                fix=f'voice {beat} again at speed {SLOWER}', hint=dict(voice_speed=SLOWER)))
        else:
            actions.append(dict(check='words', beat=beat, cause=f'"{w}" was still not heard after the beat was voiced again',
                                fix=None, hint=None))
    text = report.get('text') or {}
    seen = set()
    problems = [(x, 'on the host') for x in text.get('text_on_character') or []] + \
               [(x, 'on another text') for x in text.get('overlapping_text') or []]
    for item, where in problems:
        label = item.get('text') or item.get('label') or (item.get('texts') or [''])[0]
        beat = beat_at(tl, float(item.get('t', 0)))
        if not beat or (beat, label) in seen:
            continue
        seen.add((beat, label))
        h = beats_h.get(beat) or {}
        spec = next((b for b in ep_data.get('beats', []) if b.get('id') == beat), {})
        shot = spec.get('shot') or {}
        is_insert_label = any(e.get('kind') == 'label' and tidy_label(e.get('detail')) == tidy_label(label)
                              for e in (shot.get('insert') or {}).get('effects') or [])
        moved = h.get('host_side') or (h.get('label_at') or {}).get(label)
        if not moved:
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
    for x in sm.get('off_subject') or []:
        h = beats_h.get(x['beat']) or {}
        spec = next((b for b in ep_data.get('beats', []) if b.get('id') == x['beat']), {})
        scene = scene_from_beat(spec)
        if not h.get('insert_key') and scene:
            new_key = re.sub(r'[^a-z0-9]+', '-', f"{x['key']}-{x['beat']}".lower()).strip('-')[:60]
            actions.append(dict(check='story_match', beat=x['beat'], cause=f"the scene \"{x['key']}\" does not show what the beat says",
                                fix=f'paint a new scene from the beat: "{scene[:80]}"',
                                hint=dict(repaint=dict(key=new_key, scene=scene))))
        else:
            actions.append(dict(check='story_match', beat=x['beat'], cause=f"the scene \"{x['key']}\" still does not match its beat",
                                fix=None, hint=None))
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


def lengthen(slug: str, episode, words_per_second: float, seconds_short: float, write=None) -> list:
    """Add one short sentence to the 1-2 thinnest middle beats, from the facts they cite, and re-stamp the script
    approval as the self-repair. `write(system, user, schema)` -> dict (the local model; a stand-in in tests).
    Returns [(beat id, sentence)] actually added."""
    from . import llm, store
    middle = [b for b in episode.beats[1:-1] if b.role not in ('hook', 'payoff', 'loop')] or episode.beats[1:-1]
    need_words = max(4, math.ceil(seconds_short * words_per_second) + 2)
    count = 1 if need_words <= 10 else 2
    thin = sorted(middle, key=lambda b: (len(b.narration.split()), b.id))[:count]
    per = max(5, min(12, math.ceil(need_words / count)))
    facts = {s.id: s.claim for s in episode.sources}
    rows = []
    for b in thin:
        cited = [f'{i}: {facts[i]}' for i in b.source_ids if i in facts] or [f'{i}: {c}' for i, c in list(facts.items())[:4]]
        rows.append(dict(id=b.id, narration=b.narration, facts=cited))
    system = ('You lengthen a narrated explainer by a few seconds. For each beat, write ONE short extra sentence that '
              'follows its narration naturally. Use only what the listed facts say: no new facts, no new numbers, no names '
              f'that are not in them. About {per} words, plain spoken English, no quotes.')
    user = json.dumps(dict(title=episode.title or episode.topic, beats=rows), ensure_ascii=False)
    schema = dict(type='object', required=['beats'], properties=dict(beats=dict(type='array', items=dict(
        type='object', required=['id', 'sentence'], properties=dict(id=dict(type='string'), sentence=dict(type='string'))))))
    if write is None:
        from .cache import for_episode
        cache = for_episode(config.EPISODES_DIR / slug)
        write = lambda s, u, sc: llm.generate_json(s, u, sc, 600, cache=cache, step='repair-duration', temperature=0.3)
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


PERSON = re.compile(r'\b(host|lead|friend|he|him|his|she|her|they|person|people|man|woman|men|women|figure|character|'
                    r'characters|someone|viewer|stands?|standing|holds?|holding|points?|pointing|waves?|looks?|looking)\b', re.I)


def scene_from_beat(spec: dict) -> str:
    """A painted-scene description for a beat: the parts of its visual intent that are about the place or thing
    (every clause about the host or a person dropped), else its narration; never people, never text."""
    parts = re.split(r'(?<=[.;])\s+|,\s+|\s+while\s+|\s+as\s+', str(spec.get('visual_intent') or ''))
    keep = [p.strip(' .;,') for p in parts if p.strip(' .;,') and not PERSON.search(p) and not re.search(r"['\"]", p)]
    core = '; '.join(keep) or ' '.join(str(spec.get('narration') or '').split()).rstrip('.?!')
    if not core:
        return ''
    return f'{core[:220]}. A place or thing only: no people, no figures, no characters, no text or letters.'


def _paint(want: dict, slug: str) -> dict:
    from . import inserts
    return inserts.paint(want, slug)


def run_round(slug: str, report: dict, tl: dict, rnd: int, words_per_second: float = 2.45, write=None, paint=None) -> list:
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
        elif 'repaint' in a['hint']:
            want = a['hint']['repaint']
            try:
                res = (paint or _paint)({want['key']: dict(scene=want['scene'], beats=[a['beat']], base=None)}, slug)
            except Exception as e:                  # FLUX unavailable: report it, never crash the render
                entry['fix'] = f'could not repaint ({type(e).__name__}: {str(e)[:120]})'
                repairs.setdefault('log', []).append(entry)
                continue
            if want['key'] in (res.get('placeholders') or {}):
                entry['fix'] += ' (it would not paint: a placeholder is shown)'
            merge(repairs, a['beat'], dict(insert_key=want['key'], insert_scene=want['scene']))
        else:
            merge(repairs, a['beat'], a['hint'])
        repairs.setdefault('log', []).append(entry)
        applied.append(entry)
    save(slug, repairs)
    return applied
