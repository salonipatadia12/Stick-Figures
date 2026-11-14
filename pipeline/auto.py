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
