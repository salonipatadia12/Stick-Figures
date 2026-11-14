"""Auto mode (user 2026-11-13): topic to finished video with no stops. The decisions every gate would ask for, made
by fixed rules, shared by `stick make --auto` and the web app's Auto toggle. Each one is written to the episode's
approval log with by="auto ..." so it is always clear what was decided automatically.

- storyline: the first storyline the gate accepts (the gate refuses the format of the video picked last)
- script:    approved only when every check passes; the hooks are tried in order and the first that passes is used.
             A script that fails its checks stops at the script gate as usual.
- bases:     the library's bases are used; a beat whose base is not in the library becomes a painted scene of the
             same place with the host standing in it (the paint step makes it), never a stop for a new base
- scenes:    painted scenes are accepted as they come (the scenes gate is skipped); a scene that failed QA on both
             seeds stops at the scenes gate, since the video cannot be rendered without it
"""
from __future__ import annotations

import re

from .models import InsertCue


def first_storyline(episode) -> tuple[int | None, str]:
    """(n, why): the first storyline the storyline gate would accept, or (None, reason)."""
    from .story import last_picked_format
    if not episode.storylines:
        return None, 'there are no storylines'
    last, last_slug = last_picked_format(episode.slug)
    from . import understand
    interp = (episode.meta or {}).get('interpretation') or {}
    if understand.has_myth(interp):               # a myth to correct: myth_vs_fact, mystery or revelation first
        for n, s in enumerate(episode.storylines, 1):
            if s.format in understand.PREFERRED and s.format != last:
                return n, f'storyline {n} [{s.format}]: the topic corrects a myth ({interp.get("misconception") or "a claim"})'
    for n, s in enumerate(episode.storylines, 1):
        if s.format != last:
            return n, (f'storyline {n} [{s.format}]' + (f' (storyline 1 is a {last}, the format of {last_slug})'
                                                        if n > 1 else ''))
    return None, f'every storyline is a {last}, the format of the last video ({last_slug})'


def _kebab(text: str) -> str:
    return '-'.join(re.findall(r'[a-z0-9]+', (text or '').lower()))[:48]


def resolve_bases(episode, lib=None) -> list:
    """Beats that need a painted base the library does not have become painted scenes of the same place, with the
    host standing in them (host format only; nothing changes for an older episode). Returns notes."""
    from . import bases
    if not episode.host_format:
        return []
    miss = bases.missing(bases.plan(episode, lib))
    beats = {b for d in miss.values() for b in d.get('beats', [])}
    notes = []
    for b in episode.beats:
        if b.id not in beats or b.shot.visual != 'character' or not b.shot.staging:
            continue
        st = b.shot.staging
        place = st.setting_detail if st.setting == 'other' and st.setting_detail else st.setting.replace('-', ' ')
        b.shot.visual = 'insert'
        b.shot.insert = InsertCue(key=_kebab(f'{place} empty'), effects=[],
                                  scene=f'{place}, seen as a place with room for a person to stand, no people, '
                                        'warm natural light')
        if b.shot.host:
            b.shot.host.where = 'scene'
        notes.append(f'{b.id}: no painted base for "{place}" in the library; shown as a painted scene of it with the host '
                     'standing in it')
    return notes


def fix_script(episode) -> list:
    """Known mechanical slips of the local writer, fixed in place before Auto judges the script, so a typo-level
    error never stops an episode (user 2026-11-14: "it never stops to ask me"). Returns notes for the auto log.

    * a split with no top scene (empty insert key): shown as its bottom scene alone, the split labels as labels on it
      (the composer's own fallback, compose_beats.split_fallback);
    * a recipe or effect that needs an anchor word but has none, or one the narration does not say: anchored on the
      beat's last word of four or more letters."""
    from .models import ANCHORED_RECIPES, EffectCue, InsertCue
    notes = []
    for b in episode.beats:
        sh = b.shot
        if sh.visual == 'split' and sh.split and (sh.split.key or '').strip() and \
                (sh.insert is None or not (sh.insert.key or '').strip()):
            effects = list(sh.insert.effects) if sh.insert else []
            for k, label in enumerate(sh.split.labels or []):
                if label and len(effects) < 4 and not any(e.kind == 'label' and e.detail == label for e in effects):
                    anchor = (sh.split.anchors or ['', ''])[k] if k < len(sh.split.anchors or []) else ''
                    effects.append(EffectCue(kind='label', anchor=anchor, at='top' if k == 0 else 'ground', detail=label))
            sh.insert = InsertCue(key=sh.split.key, scene=sh.split.scene, effects=effects)
            sh.visual, sh.split = 'insert', None
            notes.append(f'{b.id}: the split had no top scene; shown as its bottom scene "{sh.insert.key}" with its labels')
        spoken = b.narration.casefold()
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]+", b.narration) if len(w) >= 4]
        fallback = words[-1] if words else ''
        for m in sh.motion:
            if m.recipe in ANCHORED_RECIPES and (not m.anchor or m.anchor.casefold() not in spoken) and fallback:
                notes.append(f'{b.id}: {m.recipe} had no anchor in the narration; anchored on "{fallback}"')
                m.anchor = fallback
            elif m.recipe not in ANCHORED_RECIPES and m.anchor and m.anchor.casefold() not in spoken:
                notes.append(f'{b.id}: {m.recipe} anchor "{m.anchor}" is not said; it now runs the whole beat')
                m.anchor = ''
        for e in (sh.insert.effects if sh.insert and sh.visual == 'insert' else []):
            if e.anchor and e.anchor.casefold() not in spoken:
                notes.append(f'{b.id}: the {e.kind} effect anchor "{e.anchor}" is not said; it now spans the beat')
                e.anchor = ''
    return notes


def script_problems(episode, hook: int | None, style) -> list:
    """The errors that would block the script gate with this hook: Episode.check errors and missing bases."""
    from . import bases
    ep = episode.model_copy(deep=True)
    if ep.hook_options and hook:
        try:
            ep.pick_hook(hook)
        except ValueError as exc:
            return [f'hook: {exc}']
    out = [f'{i.where}: {i.message}' for i in ep.check(style.cast_ids, style.words_per_second) if i.severity == 'error']
    for name, d in bases.missing(bases.plan(ep)).items():
        out.append(f'bases: beats {", ".join(d.get("beats", []))} need a base the library does not have ({name})')
    return out


def pick_hook(episode, style) -> tuple[int | None, list]:
    """(hook, problems): the first hook option whose script passes every check, or (None, the first hook's problems).
    An episode without hook options: (0, problems) with 0 meaning "no hook to pick"."""
    if not episode.hook_options:
        problems = script_problems(episode, None, style)
        return (0 if not problems else None), problems
    first = None
    for n in range(1, len(episode.hook_options) + 1):
        problems = script_problems(episode, n, style)
        if not problems:
            return n, []
        first = first if first is not None else problems
    return None, first or []


def note(episode, step: str, message: str, by: str = 'auto'):
    """Record an auto decision in the episode's approval log (shown with the gates' own entries)."""
    import time
    episode.meta.setdefault('auto_log', []).append(dict(step=step, by=by, at=time.strftime('%Y-%m-%dT%H:%M:%S'),
                                                        message=message))
