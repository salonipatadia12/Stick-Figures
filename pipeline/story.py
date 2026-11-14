"""Story before script (user 2026-11-09): for each topic, three storylines in three different formats are pitched
first; the user picks one at the `storyline` gate and only then is the script written around it.

Formats rotate: a new pitch never offers the format of the video picked most recently, nor a format already offered
to another topic still waiting at its storyline gate (so two videos picked together never share a format). The pitch
can say a topic cannot carry a real story; the gate then shows that note instead of forcing one.

The pitch is one structured call through the same ledger as the script (cache by content hash, worst case reserved
first, no retries). Under SCRIPT_PROVIDER=ollama (the default) the same prompt goes to the local model, free, with
its output constrained to the allowed formats and fact ids, and at most two repairs when a hook runs long or two
storylines share a format.
"""
from __future__ import annotations

import json
import time
from typing import get_args

from . import config, store
from .models import FORMATS, Episode, Source, StoryFormat, StoryPitch, digest

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


def twist_rules(episode) -> str:
    """The fact-first lines of the pitch prompt: the twist fact picked in code and the word-precision rule."""
    twist = ((episode.meta or {}).get('interpretation') or {}).get('twist') or {}
    out = ''
    if twist.get('claim'):
        out += (f'\n\nTWIST FACT (picked from the research): {twist["id"]}: {twist["claim"]}\nEvery storyline\'s twist '
                'IS this fact, told in plain words; cite it. The payoff stands on research facts too.')
    return out + ('\nWORDS: when a fact names a group of things ("animals and plants (diatoms)", "marine"), use that '
                  "group's own words or a direct plain-English equivalent (\"tiny sea plants and animals\", "
                  '"plankton"), never a broader or different category ("bacteria", "microbes", "bugs").')


def story_request(episode: Episode, facts, allowed):
    from anthropic import transform_schema
    fact_lines = '\n'.join(f'{f.id}: {f.claim} (source: {f.title or f.url})' for f in facts)
    from .understand import topic_text
    user = (f'{topic_text(episode)}\nNiche: {episode.niche or "general"}\n'
            f'Audience: {episode.audience or "general English-speaking audience"}\n'
            f'Length: {episode.target_length} seconds.\n'
            f'Allowed formats (pick 3 different ones that fit this topic best): {", ".join(allowed)}\n\n'
            f'Research facts (cite by id):\n{fact_lines}' + twist_rules(episode))
    return dict(model=config.SCRIPT_MODEL, max_tokens=STORY_MAX_TOKENS, system=STORY_SYSTEM + format_guide(allowed),
                messages=[{'role': 'user', 'content': user}], thinking={'type': 'adaptive'},
                output_config={'format': {'type': 'json_schema', 'schema': transform_schema(StoryPitch)}, 'effort': 'high'})


def story_estimate(input_tokens):
    from .cost import token_cost
    return token_cost(config.SCRIPT_MODEL, input=input_tokens, output=STORY_MAX_TOKENS)


def story_schema(allowed, fact_ids) -> dict:
    """StoryPitch for the local model: exactly three storylines, formats only from the allowed list, fact ids only
    from the research (Ollama's schema-constrained output cannot produce anything else)."""
    from .script import _arr, _enum, _obj, _s
    line = _obj(dict(format=_enum(allowed), title=_s(), hook=_s(), pitch=_s(), conflict=_s(), stakes=_s(), twist=_s(),
                     payoff=_s(), visuals=_arr(_s(), 3, 5), fact_ids=_arr(_enum(fact_ids) if fact_ids else _s(), 1 if fact_ids else 0)))
    return _obj(dict(story_supported={'type': 'boolean'}, note=_s(), storylines=_arr(line, 3, 3)))


def story_issues(data, allowed) -> list:
    """What the local pitch must get right before it is shown (the same things the gate notes)."""
    from .models import HOOK_MAX_WORDS
    import re
    try:
        sp = StoryPitch.model_validate(data)
    except ValueError as exc:
        return [f'the answer does not fit the shape: {str(exc)[:300]}']
    out = []
    if len(sp.storylines) != 3:
        out.append(f'{len(sp.storylines)} storylines; give exactly 3')
    if len({s.format for s in sp.storylines}) < len(sp.storylines):
        out.append('two storylines share a format; each must use a different allowed format')
    for k, s in enumerate(sp.storylines, 1):
        n = len(re.findall(r"[\w'’-]+", s.hook))
        if n > HOOK_MAX_WORDS:
            out.append(f'storyline {k}: the hook "{s.hook}" is {n} words; at most {HOOK_MAX_WORDS}')
        if s.format not in allowed:
            out.append(f'storyline {k}: format {s.format} is not allowed')
    return out


STORY_REPAIRS = 2
STORY_LOCAL_MAX_TOKENS = 1600    # three storylines are ~700 tokens; small enough to stay in the 4096-token window


def _pitch_free(episode, facts, allowed, base, ledger, regenerate):
    """(pitch data, cache path, cached) from the local model, with at most STORY_REPAIRS repairs."""
    from . import llm
    from .cache import for_episode
    from .script import ScriptError, _cache, _write_json, shape
    cache = for_episode(base)
    system = STORY_SYSTEM + format_guide(allowed)
    fact_lines = '\n'.join(f'{f.id}: {f.claim} <{f.url}>' for f in facts)
    from .understand import topic_text
    user = (f'{topic_text(episode)}\nNiche: {episode.niche or "general"}\n'
            f'Audience: {episode.audience or "general English-speaking audience"}\n'
            f'Length: {episode.target_length} seconds.\n'
            f'Allowed formats (pick 3 different ones that fit this topic best): {", ".join(allowed)}\n\n'
            f'Research facts (cite by id):\n{fact_lines}' + twist_rules(episode))
    schema = story_schema(allowed, [f.id for f in facts])
    model = config.ollama_model()
    key = digest([STORY_VERSION, 'ollama', model, system, user, STORY_REPAIRS])
    path = _cache(base, 'story', key)
    if path.exists() and not regenerate:
        return json.loads(path.read_text())['pitch'], path, True
    data, issues, log = None, [], []
    for attempt in range(STORY_REPAIRS + 1):
        ask = user + '\n\nAnswer with JSON only: ' + shape(schema)
        if data is not None:
            ask += ('\n\nYOUR PREVIOUS ANSWER:\n' + json.dumps(data, ensure_ascii=False, separators=(',', ':'))
                    + '\n\nTHE CHECKER FOUND THESE PROBLEMS. Fix every one and keep everything else:\n'
                    + '\n'.join(f'- {m}' for m in issues))
        t = time.time()
        try:
            data = llm.generate_json(system, ask, schema, STORY_LOCAL_MAX_TOKENS, temperature=0.5 if attempt == 0 else 0.3,
                                     seed=41 + attempt, cache=cache, refresh=regenerate, ledger=ledger, step='storyline',
                                     detail='three storylines' + (f' (repair {attempt})' if attempt else ''))
        except llm.LLMError as exc:
            raise ScriptError(f'The local model could not pitch storylines: {exc}') from None
        issues = story_issues(data, allowed)
        log.append(dict(attempt=attempt, seconds=round(time.time() - t, 1), issues=issues))
        if not issues:
            break
    try:
        StoryPitch.model_validate(data)
    except ValueError as exc:
        failed = base / 'cache' / f'failed-story-{time.strftime("%Y%m%d-%H%M%S")}.json'
        _write_json(failed, dict(error=str(exc)[:2000], raw=data))
        raise ScriptError(f'The storyline answer did not match the schema; saved to {failed}.') from None
    _write_json(path, dict(pitch=data, created=time.strftime('%Y-%m-%dT%H:%M:%S'), model=model, provider='ollama',
                           version=STORY_VERSION, allowed=allowed, attempts=log))
    LAST_PITCH.clear()
    LAST_PITCH.update(attempts=log, cache=dict(cache.stats))
    return data, path, False


LAST_PITCH: dict = {}


def pitch(episode: Episode, base, ledger, client=None, regenerate=False):
    """Research (cached) -> three storylines. Returns (episode with storylines, notes). Stops the pipeline at the
    storyline gate; nothing else is written.

    SCRIPT_PROVIDER=ollama (default): free and local, `client` is ignored and no key is needed; the model is unloaded
    at the end. SCRIPT_PROVIDER=anthropic: one budgeted Claude call, as before."""
    from .script import provider
    prov = provider(client)
    LAST_PITCH.clear()
    try:
        return _pitch(episode, base, ledger, client, regenerate, prov)
    finally:
        if prov == 'ollama' and config.ollama_unload_after_step():
            from . import llm
            llm.unload()


def _pitch(episode, base, ledger, client, regenerate, prov):
    from .script import (ScriptError, _cache, _send, _write_json, count_input_tokens, free_notes, keep_reputable,
                         make_client, research)
    client = (client or make_client()) if prov == 'anthropic' else None
    from . import understand
    from .cache import for_episode
    interp = (episode.meta or {}).get('interpretation')
    if not interp or interp.get('topic') != episode.topic or interp.get('version') != understand.UNDERSTAND_VERSION:
        # topic understanding (user 2026-11-14): the typed topic as a real question, its claims, the angle
        interp = understand.interpret(episode.topic, cache=for_episode(base), ledger=ledger)
        episode.meta['interpretation'] = interp
        if interp.get('claims'):                  # every claim inside the topic is searched as well
            focus = dict(episode.meta.get('research_focus') or {})
            focus['find'] = list(dict.fromkeys(list(focus.get('find') or []) + interp['claims']))[:4]
            episode.meta['research_focus'] = focus
    if interp.get('source') == 'model' and interp.get('title'):
        episode.title = interp['title']
    facts, rpath, rcached = research(episode, base, ledger, client)
    notes = [f'understood: {interp.get("question")} (title "{interp.get("title")}"; {len(interp.get("claims") or [])} '
             f'claim(s){"; " + interp["note"] if interp.get("note") else ""})']
    notes.append(f'research: {len(facts)} cited facts ({"cache" if rcached else "new"}: {rpath.name})')
    if prov == 'ollama':
        notes += free_notes('research')
    facts, dropped = keep_reputable(facts)            # storylines stand only on facts the script may use
    notes += [f'  dropped (not government, university or peer-reviewed): {f.url}' for f in dropped]
    if not facts:
        raise ScriptError('No fact came from a government, university or peer-reviewed source, so no storylines were '
                          'pitched.')
    interp['verdicts'] = understand.verify(interp, facts, cache=for_episode(base), ledger=ledger)
    if prov == 'ollama':
        facts, more = claim_followup(episode, interp, facts, base, ledger)
        notes += more
    from . import grounding
    tid = grounding.twist_fact(interp, facts)     # fact-first (user 2026-11-14): the twist is a fact, picked in code
    by = {f.id: f for f in facts}
    interp['twist'] = dict(id=tid, claim=by[tid].claim) if tid in by else {}
    episode.meta['interpretation'] = interp
    notes += [f'  claim "{v["claim"]}": {v["verdict"]}' for v in interp['verdicts']]
    if tid in by:
        notes.append(f'twist fact: {tid}: {by[tid].claim}')
    allowed, why = allowed_formats(episode.slug)
    allowed = understand.preferred_formats(interp, allowed)   # a myth to correct: myth_vs_fact, mystery, revelation first
    notes += [f'formats: {", ".join(allowed)}'] + [f'  ({w})' for w in why]
    if prov == 'ollama':
        data, path, cached = _pitch_free(episode, facts, allowed, base, ledger, regenerate)
        for a in LAST_PITCH.get('attempts', []):
            notes.append(f'  storyline call {a["attempt"] + 1}: {a["seconds"]} s'
                         + (f'; fixing: {"; ".join(a["issues"])}' if a['issues'] else ''))
        return _finish(episode, data, path, cached, facts, allowed, notes)
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
    return _finish(episode, data, path, cached, facts, allowed, notes)


def claim_followup(episode, interp, facts, base, ledger, search_claim=None, verify=None):
    """A claim inside the topic that came back unverified because no fact mentions it gets two targeted searches of
    its own (user 2026-11-14) before it is left unverified. New facts join the research (meta['claim_facts'], so the
    script step has them under the same ids) and the claim is checked again. Returns (facts, notes)."""
    from . import search, understand
    from .cache import for_episode
    from .script import keep_reputable
    cache = for_episode(base)
    search_claim = search_claim or (lambda c: search.claim_followup(c, interp.get('question', ''), cache, ledger))
    verify = verify or (lambda i, f: understand.verify(i, f, cache=cache, ledger=ledger))
    done = episode.meta.setdefault('claim_followup', [])
    notes, facts = [], list(facts)
    for k, v in enumerate(interp.get('verdicts') or []):
        if v['verdict'] != 'unverified' or v.get('fact_ids') or any(d['claim'] == v['claim'] for d in done):
            continue
        queries, found = search_claim(v['claim'])
        import difflib
        new = []
        for f in found:                               # the same fact twice (two pages of one site): kept once
            seen = [x.claim.lower() for x in facts] + [x['claim'].lower() for x in new]
            if f['quote'] not in {x.quote for x in facts} and \
                    not any(difflib.SequenceMatcher(None, f['claim'].lower(), c).ratio() > 0.8 for c in seen):
                new.append(f)
        kept, _ = keep_reputable([Source(id='s0', claim=f['claim'], url=f['url'], title=f.get('title') or '',
                                         quote=f['quote']) for f in new])
        kept = [f.model_copy(update=dict(id=f's{len(facts) + i}')) for i, f in enumerate(kept, 1)]
        facts += kept
        episode.meta.setdefault('claim_facts', []).extend(f.model_dump(exclude={'id'}) for f in kept)
        done.append(dict(claim=v['claim'], queries=queries, facts=[f.id for f in kept]))
        again = verify(dict(interp, claims=[v['claim']]), facts) if kept else []
        if again and again[0]['verdict'] != 'unverified':
            interp['verdicts'][k] = again[0]
        else:
            v['why'] = (f'no fact mentions it, even after two searches for it ({"; ".join(queries)})'
                        + (f'; {len(kept)} new fact(s) did not settle it' if kept else ''))
        notes.append(f'  claim "{v["claim"]}": searched again ({"; ".join(queries)}), {len(kept)} new fact(s) -> '
                     f'{interp["verdicts"][k]["verdict"]}')
    return facts, notes


def check_storylines(episode, sp, facts, ask=None) -> list:
    """Fact-first (user 2026-11-14): each storyline's twist must be the twist fact (word precision in code, then the
    yes/no judge against its quote), else it becomes the fact as research wrote it; a sentence of the pitch, conflict,
    stakes or payoff that names a group of living things no fact names ("tiny microbes") is cut, and a field left
    empty that way says the twist fact. Returns the changes [{storyline, field, was, now, why}] (in place)."""
    from . import entail, grounding
    interp = (episode.meta or {}).get('interpretation') or {}
    twist = interp.get('twist') or {}
    by = {f.id: f for f in facts}
    myth = grounding.myth_groups(interp, facts)
    every = grounding.allowed_for(grounding.fact_texts(facts)) | myth
    out = []
    for k, s in enumerate(sp.storylines, 1):
        if twist.get('id') in by:
            tid = twist['id']
            allowed = grounding.allowed_for(grounding.fact_texts(facts, {tid})) | myth
            ok, why = entail.verdict(s.twist, entail.research_text([tid], facts), allowed, ask) if ask else (
                not grounding.imprecise(s.twist, allowed), 'word precision')
            if ok is False:
                out.append(dict(storyline=k, field='twist', was=s.twist, now=by[tid].claim, why=why))
                s.twist = by[tid].claim
            if tid not in s.fact_ids:
                s.fact_ids = list(s.fact_ids) + [tid]
        for field in ('pitch', 'conflict', 'stakes', 'payoff'):
            text = getattr(s, field)
            parts = entail.sentences(text)
            keep = [p for p in parts if not grounding.imprecise(p, every)]
            if len(keep) == len(parts):
                continue
            now = ' '.join(keep) or (twist.get('claim') or '')
            bad = sorted({w for p in parts for w in grounding.imprecise(p, every)})
            out.append(dict(storyline=k, field=field, was=text, now=now, why=grounding.precision_why(bad, every)))
            setattr(s, field, now)
    return out


def _finish(episode, data, path, cached, facts, allowed, notes):
    sp = StoryPitch.model_validate(data)
    from . import entail
    from .script import provider
    from .cache import for_episode
    changes = check_storylines(episode, sp, facts, entail.default_ask(for_episode(path.parent.parent))
                               if provider() == 'ollama' else None)
    episode.meta['story_check'] = dict(twist_fact=((episode.meta.get('interpretation') or {}).get('twist') or {}).get('id', ''),
                                       changes=changes)
    notes += [f'storyline {c["storyline"]} {c["field"]}: "{c["was"]}" -> "{c["now"]}" ({c["why"]})' for c in changes]
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
