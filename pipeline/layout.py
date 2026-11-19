"""Layout before render (user 2026-11-19): "place every label with the host's box and the safe area as hard limits before
rendering (shrink -> wrap -> move to the nearest free slot -> drop the least important). QA's text checks should then
only confirm. The repair loop stays as a backstop."

The runner samples each beat's frames through the real composer before the render (scripts/motion_test/
episode_runner.py `layout_pass`): every text drawn and every character box, exactly as the QA reads them. A text on a
character (the same overlap QA counts) or outside the safe area (captions.SAFE) takes the next step of its ladder:

    shrink  the type smaller, never below overlays.TEXT_MIN_PX           hint fit[text].scale
    wrap    two lines                                                  hint fit[text].wrap
    move    an insert label to the region away from the host; any other text: the host to the other side, smaller
    drop    the text goes (the narration still says it); of two texts on a beat the less important one goes first

The hints go into repairs.json like the repair loop's (pipeline/repair.py), so the render, its cache and QA all see
them. Each step is tried once per text; a beat is sampled again until it is clean or LAYOUT_ROUNDS have run.
"""
from __future__ import annotations

import re

LAYOUT_ROUNDS = 5
SAMPLE_EVERY = 6                  # frames (5 a second at 30 fps)
ON_BODY = 0.12                    # the share of a text's box on a character that QA fails (pipeline/qa.py text_check)
SHRINK = 0.78
PREFIX = re.compile(r'^(checklist|tag|bubble|receipt): ')


def name_of(d: dict) -> str:
    """The text as the episode has it (a slam stacked on two lines is its one on_screen_text)."""
    return PREFIX.sub('', str(d.get('source') or d.get('label') or ''))


def problems(beat: str, t: float, drawn: list, chars: list) -> list:
    """[{beat, text, why, em, box, t}] for the texts of one sampled frame that sit on a character or leave the safe
    area (the QA's own measures)."""
    from . import captions as C
    from .qa import _overlap
    out = []
    for d in drawn:
        box = d.get('box')
        if not box:
            continue
        text = name_of(d)
        if any(_overlap(list(box) + [text], c) > ON_BODY for c in chars):
            out.append(dict(beat=beat, text=text, why='host', em=d.get('em', 60), box=list(box), t=round(t, 2)))
        elif box[2] > C.SAFE['right'] or box[3] > C.SAFE['bottom'] or box[0] < C.SAFE['left']:
            out.append(dict(beat=beat, text=text, why='safe', em=d.get('em', 60), box=list(box), t=round(t, 2)))
    return out


def ladder(text: str, why: str, h: dict, shot: dict, em: float) -> tuple[dict, str] | tuple[None, str]:
    """(the hint of the next step for this text, what it does), or (None, why nothing is left)."""
    from .overlays import TEXT_MIN_PX
    from .repair import AWAY, OTHER_SIDE, SMALLER, tidy_label
    tried = (h.get('layout') or {}).get(text) or []
    fit = dict((h.get('fit') or {}).get(text) or {})
    if 'shrink' not in tried and em * SHRINK >= TEXT_MIN_PX:
        fit['scale'] = round(max(TEXT_MIN_PX / em, SHRINK) * fit.get('scale', 1.0), 3)
        return dict(fit={**(h.get('fit') or {}), text: fit}, layout={**(h.get('layout') or {}), text: tried + ['shrink']}), \
            f'type {round(100 * fit["scale"])} %'
    if 'wrap' not in tried and ' ' in text.strip():
        fit['wrap'] = True
        return dict(fit={**(h.get('fit') or {}), text: fit}, layout={**(h.get('layout') or {}), text: tried + ['wrap']}), \
            'two lines'
    if 'move' not in tried:
        step = dict(layout={**(h.get('layout') or {}), text: tried + ['move']})
        is_label = any(e.get('kind') == 'label' and tidy_label(e.get('detail') or '') == tidy_label(text)
                       for e in (shot.get('insert') or {}).get('effects') or [])
        side = h.get('host_side') or (shot.get('host') or {}).get('side') or 'centre'
        if is_label:
            region = 'centre' if why == 'safe' else AWAY.get(side, 'top')
            step['label_at'] = {**(h.get('label_at') or {}), text: region}
            return step, f'moved to the {region}'
        if why == 'host' and (shot.get('host') or {}).get('where') not in (None, 'off'):
            size = h.get('host_size') or (shot.get('host') or {}).get('size') or 'medium'
            step.update(host_side=OTHER_SIDE.get(side, 'left'), host_size=SMALLER.get(size, 'small'))
            return step, f'the host to the {step["host_side"]}, {step["host_size"]}'
        tried = tried + ['move']
    if 'drop' not in tried:
        return dict(drop=sorted(set(h.get('drop') or []) | {text}), layout={**(h.get('layout') or {}), text: tried + ['drop']}), \
            'dropped (the narration still says it)'
    return None, 'nothing left to try'


def fixes(found: list, ep_data: dict, repairs: dict) -> list:
    """Apply one ladder step per (beat, text) found, into repairs (in place). Returns the log entries
    [{beat, text, why, fix}]. Of the texts of one beat that sit on the host, the least important moves first."""
    from .repair import importance, merge
    by = {}
    for p in found:
        by.setdefault((p['beat'], p['text']), p)
    specs = {b.get('id'): b for b in ep_data.get('beats') or []}
    log = []
    beats = {}
    for (beat, text), p in by.items():
        beats.setdefault(beat, []).append(p)
    for beat, items in beats.items():
        spec = specs.get(beat) or {}
        # the least important text of the beat gives way first; one step per text per round
        items.sort(key=lambda p: importance(p['text'], spec, ep_data))
        for p in items:
            h = (repairs.get('beats') or {}).get(beat) or {}
            hint, what = ladder(p['text'], p['why'], h, spec.get('shot') or {}, float(p['em'] or 60))
            entry = dict(round=0, check='layout', beat=beat, text=p['text'],
                         cause=f'the text "{p["text"]}" ' + ('sat on the host' if p['why'] == 'host' else
                                                              'left the safe area'), fix=what)
            log.append(entry)
            if hint is not None:
                merge(repairs, beat, hint)
    return log
