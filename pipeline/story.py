"""Story before script (user 2026-11-09): for each topic, three storylines in three different formats are pitched
first; the user picks one at the `storyline` gate and only then is the script written around it.

Formats rotate: a new pitch never offers the format of the video picked most recently, nor a format already offered
to another topic still waiting at its storyline gate (so two videos picked together never share a format). The pitch
can say a topic cannot carry a real story; the gate then shows that note instead of forcing one.

The pitch is one structured call through the same ledger as the script (cache by content hash, worst case reserved
first, no retries).
"""
from __future__ import annotations

import json
import time
from typing import get_args

from . import config, store
from .models import FORMATS, Episode, StoryFormat, StoryPitch, digest

STORY_VERSION = 'story-v1'
STORY_MAX_TOKENS = 8_000
ALL_FORMATS = list(get_args(StoryFormat))

STORY_SYSTEM = """You pitch storylines for 30-60 second vertical videos: painted stick figures, a host who talks to the
viewer and acts the story out inside painted scenes, word-synced captions.

The problem you fix: explainers that list thin facts in order ("let me show you", a diagram, a bar chart) all feel the
same. A storyline has a clear conflict or problem, stakes that rise, at least one true twist or surprise, and a payoff
that answers the hook. Each storyline uses a different format from the allowed list.

Rules:
- Stand only on the numbered research facts; cite their ids. Never invent events, numbers, quotes or people. A twist
  must be true. Simplify, never distort.
- The hook is at most 8 words, honest, and the payoff answers it.
- The pitch is 2-3 short sentences a busy person can judge in ten seconds: setup and conflict, the twist, the payoff.
- Picture ideas mix tools: a painted scene that animates (snow falls, a tower tilts), a close-up of a detail, a split
  screen (myth vs fact, then vs now), a painted map with pins, an object the host holds or uses, camera moves, and at
  most one drawn diagram. The host stands in the scenes, at different spots and sizes.
- What the art cannot do: crowds, animals, running, fighting; the host can stand, point, hold things, react.
- If these facts cannot carry a real story without inventing things, set story_supported to false and say why in
  note, and still pitch the three most honest options.

The formats:
"""


def format_guide(allowed) -> str:
    return '\n'.join(f'- {f}: {FORMATS[f]}' for f in allowed)


def last_picked_format(exclude_slug: str = '') -> tuple[str, str]:
    """(format, slug) of the most recently picked storyline in any other episode, or ('', '')."""
    best = ('', '', '')
    for slug in store.list_episodes():
        if slug == exclude_slug:
            continue
        try:
            ep, _ = store.load(slug)
        except ValueError:
            continue
        if not ep.storyline_pick:
            continue
        at = max((e.get('at', '') for e in ep.meta.get('approval_log', []) if e.get('gate') == 'storyline'), default='')
        if at >= best[2]:
            best = (ep.story_format, slug, at)
    return best[0], best[1]


def pending_formats(exclude_slug: str = '') -> dict:
    """{slug: [formats]} offered to other topics still waiting at their storyline gate."""
    out = {}
    for slug in store.list_episodes():
        if slug == exclude_slug:
            continue
        try:
            ep, _ = store.load(slug)
        except ValueError:
            continue
        if ep.storylines and not ep.storyline_pick:
            out[slug] = [s.format for s in ep.storylines]
    return out


def allowed_formats(slug: str) -> tuple[list, list]:
    """(allowed formats, notes). Never the last picked format; not the formats pending elsewhere while 3 remain."""
    notes = []
    allowed = list(ALL_FORMATS)
    last, last_slug = last_picked_format(slug)
    if last in allowed:
        allowed.remove(last)
        notes.append(f'not {last}: the last video ({last_slug}) used it')
    for other, fmts in pending_formats(slug).items():
        rest = [f for f in allowed if f not in fmts]
        if len(rest) >= 3:
            allowed = rest
            notes.append(f'not {", ".join(fmts)}: offered to {other}, still waiting for a pick')
    return allowed, notes


def story_request(episode: Episode, facts, allowed):
    from anthropic import transform_schema
    fact_lines = '\n'.join(f'{f.id}: {f.claim} (source: {f.title or f.url})' for f in facts)
    user = (f'Topic: {episode.topic}\nNiche: {episode.niche or "general"}\n'
            f'Audience: {episode.audience or "general English-speaking audience"}\n'
            f'Length: {episode.target_length} seconds.\n'
            f'Allowed formats (pick 3 different ones that fit this topic best): {", ".join(allowed)}\n\n'
            f'Research facts (cite by id):\n{fact_lines}')
    return dict(model=config.SCRIPT_MODEL, max_tokens=STORY_MAX_TOKENS, system=STORY_SYSTEM + format_guide(allowed),
                messages=[{'role': 'user', 'content': user}], thinking={'type': 'adaptive'},
                output_config={'format': {'type': 'json_schema', 'schema': transform_schema(StoryPitch)}, 'effort': 'high'})


def story_estimate(input_tokens):
    from .cost import token_cost
    return token_cost(config.SCRIPT_MODEL, input=input_tokens, output=STORY_MAX_TOKENS)


def pitch(episode: Episode, base, ledger, client=None, regenerate=False):
    """Research (cached) -> three storylines. Returns (episode with storylines, notes). Stops the pipeline at the
    storyline gate; nothing else is written."""
    from .script import ScriptError, _cache, _send, _write_json, count_input_tokens, keep_reputable, make_client, research
    client = client or make_client()
    facts, rpath, rcached = research(episode, base, ledger, client)
    notes = [f'research: {len(facts)} cited facts ({"cache" if rcached else "new"}: {rpath.name})']
    facts, dropped = keep_reputable(facts)            # storylines stand only on facts the script may use
    notes += [f'  dropped (not government, university or peer-reviewed): {f.url}' for f in dropped]
    if not facts:
        raise ScriptError('No fact came from a government, university or peer-reviewed source, so no storylines were '
                          'pitched.')
    allowed, why = allowed_formats(episode.slug)
    notes += [f'formats: {", ".join(allowed)}'] + [f'  ({w})' for w in why]
    request = story_request(episode, facts, allowed)
    key = digest([STORY_VERSION, request['model'], request['system'], request['messages']])
    path = _cache(base, 'story', key)
    if path.exists() and not regenerate:
        data = json.loads(path.read_text())['pitch']
        cached = True
    else:
        n, _ = count_input_tokens(client, request)
        response = _send(client, ledger, 'storyline', story_estimate(n), 'three storylines', request)
        text = next((b.text for b in response.content if getattr(b, 'type', None) == 'text'), '')
        if response.stop_reason == 'max_tokens':
            raise ScriptError('The storyline pitch hit the output limit; nothing was saved. No automatic retry was made.')
        try:
            data = json.loads(text)
            StoryPitch.model_validate(data)
        except ValueError as exc:
            failed = base / 'cache' / f'failed-story-{time.strftime("%Y%m%d-%H%M%S")}.json'
            _write_json(failed, dict(error=str(exc)[:2000], raw=text))
            raise ScriptError(f'The storyline response did not match the schema; saved to {failed}. No retry was made.') from None
        _write_json(path, dict(pitch=data, created=time.strftime('%Y-%m-%dT%H:%M:%S'), model=request['model'],
                               version=STORY_VERSION, allowed=allowed))
        cached = False
    sp = StoryPitch.model_validate(data)
    fact_ids = {f.id for f in facts}
    for k, s in enumerate(sp.storylines, 1):
        if s.format not in allowed:
            notes.append(f'storyline {k}: format {s.format} is not allowed now (rotation); it cannot be picked')
        unknown = [i for i in s.fact_ids if i not in fact_ids]
        if unknown:
            notes.append(f'storyline {k}: cites unknown facts {unknown}')
    if len({s.format for s in sp.storylines}) < len(sp.storylines):
        notes.append('two storylines share a format')
    notes.append(f'storylines: {len(sp.storylines)} ({"cache" if cached else "new"}: {path.name})')
    updated = episode.model_copy(update=dict(storylines=sp.storylines, storyline_pick=0, sources=facts,
                                             story_note='' if sp.story_supported else (sp.note or 'the writer says the facts cannot carry a real story'),
                                             meta=episode.meta | dict(story=dict(allowed=allowed, version=STORY_VERSION,
                                                                                 generated=time.strftime('%Y-%m-%dT%H:%M:%S')))))
    return Episode.model_validate(updated.model_dump()), notes


def view(episode: Episode) -> str:
    """The storyline gate, as shown to the user."""
    lines = [f'STORYLINES: {episode.topic}  ({episode.slug})']
    if episode.story_note:
        lines.append(f'  NOTE: this topic may not carry a real story: {episode.story_note}')
    src = {s.id: s for s in episode.sources}
    for k, s in enumerate(episode.storylines, 1):
        lines += ['', f'{k}. [{s.format}] {s.title}', f'   Hook: "{s.hook}"', f'   {s.pitch}',
                  f'   Conflict: {s.conflict}', f'   Stakes: {s.stakes}', f'   Twist: {s.twist}', f'   Payoff: {s.payoff}',
                  f'   Pictures: {"; ".join(s.visuals)}',
                  f'   Facts: {", ".join(i + (" (" + src[i].url + ")" if i in src else " (unknown)") for i in s.fact_ids)}']
    return '\n'.join(lines)
