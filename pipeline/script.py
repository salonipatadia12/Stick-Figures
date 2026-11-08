"""Topic -> researched script, via Claude (claude-sonnet-5-5) in two budgeted calls.

1. research(): web search. Citations are always on for web search, so every fact keeps the
   URL of the result it came from. Citations can't be combined with structured outputs,
   which is why research is its own call.
2. write():    structured output against the ScriptDraft JSON schema. The script may cite only
   research fact ids; any other number must be framed as an example.

Each call is: cache lookup -> free token count -> ledger.reserve(worst case) -> one request
(the SDK client has max_retries=0) -> ledger.settle(actual from usage). Nothing is retried.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config
from .cost import token_cost, usage_dict
from .models import Beat, Episode, ScriptDraft, Source, beat_range, digest

PROMPT_VERSION = 'script-v3'  # v2: direct web search with URL citations; v3: staging fields (setting, poses, actions, props)
RESEARCH_MAX_SEARCHES = 4
RESEARCH_MAX_TOKENS = 4096
# Search results are billed as input tokens and can't be counted before the call. This is the
# worst case reserved per research request; real usage is recorded afterwards.
RESEARCH_RESULT_TOKENS_BOUND = 100_000
MAX_CONTINUATIONS = 2  # pause_turn resumes of the server-side search loop, each budgeted
SCRIPT_MAX_TOKENS = 16_000
CHARS_PER_TOKEN_ESTIMATE = 3.0  # offline estimate only (dry runs without a key); deliberately high

RESEARCH_SYSTEM = """You gather facts for a 30-60 second explainer video. Use web search.
Write 3 to 6 facts. Each fact is one plain sentence on its own line, with no bullets, numbering,
introduction or conclusion. State only what a search result directly supports.
Prefer facts that explain the mechanism behind the topic, plus at most three statistics.
Prefer primary or reputable sources: government statistics offices, central banks, regulators,
universities, established research organisations and major news outlets.
If a fact only holds for one country or period, name it in the sentence. Do not give advice."""

SCRIPT_RULES = """You write scripts for faceless explainer Shorts: vertical videos of 30-60 seconds,
told with painted stick-figure characters, a narrator and word-synced captions.

Rules:
1. HOOK. Beat 1 has role "hook". Its first sentence is at most 7 words and opens on a concrete
   tension, a question or a surprising contrast. No greeting, no "in this video", no channel talk.
2. STRUCTURE. One idea per beat. Roles in order: hook; then setup, tension and reveal beats;
   one "payoff" beat that delivers exactly what the hook promised; and a final "ending" beat that
   loops back to the opening line or teases a follow-up. Fill hook_promise with that promise.
3. SHOW, DON'T TELL. Every beat's picture shows the idea through action, a visual metaphor or a
   physical diagram (for money: coins draining from a jar). Never a talking head, never a
   character just speaking to the viewer.
4. NO INVENTED FACTS. Use only the numbered research facts provided, citing their ids in
   source_ids. A number that is not from a fact must be framed as an example in the narration
   ("say you take home 3,000 a month") with illustrative_numbers=true. Ordinal dates such as
   "the 20th" are fine.
5. VOICE. Conversational, second person where natural, short sentences, contractions.
6. ON-SCREEN TEXT. 0-5 words that punch the key idea, often the number or the turn. Empty when
   the picture already says it.
7. MOTION. Each beat gets 1-2 recipes. The picture must change on a key word at least every
   ~2.5 seconds, so most beats need one word-anchored recipe whose anchor appears verbatim in that
   beat's narration. Continuous moves (push_in, pan_left, pan_right, parallax, breathe) take an
   empty anchor. Guide: push_in or pan to establish; punch_in on a key word; pose_swap when a
   character reacts (give 1-2 pose_variants: the same frame with a different arm/face pose);
   enter_left/right and exit_left/right for arrivals and departures; impact_shake for a shock;
   text_slam to land the on-screen text; number_counter when a quantity changes (detail
   "0 -> 31"); underline_draw or arrow_draw to point at something; icon_pop for one object
   appearing; checklist_tick for steps (detail "item | item"); split_compare for before/after
   (detail "left | right").
8. TRANSITIONS. "cut" by default. "whip" only for a jump in time or place; "match_cut" only when
   two shots share a shape or object.
9. SOUND. 0-2 sfx per beat from pop, whoosh, ding, coin, thud, paper, typing, each anchored to a
   word in that beat's narration and placed on an action.
10. IMAGE PROMPTS. Describe one painted frame for an image model: which cast ids appear, where,
   pose, expression, props, light and framing. No text, letters or numbers inside the picture.
   Keep recurring props and places consistent across beats. Do not restate the art style.
11. STAGING. The video is built from libraries, so fill shot.staging exactly: setting from the list
   (cafe, bedroom, office, kitchen, living-room, doorway (between living room and kitchen), street, park, classroom, gym, car; "other" only if
   none fits, with setting_detail); one pose per cast id (seated-at-table, standing, bed-edge,
   bed-awake, bed-asleep-top, seated-sofa, seated-desk, seated-bench, driving, passenger, walking); 0-3
   actions from reach, point, shrug, facepalm, pick_up, put_down, throw, drink, type, write, wave,
   nod, shake_head, slump, jolt, gaze, each on a word of this beat's narration; 0-4 props with
   where they sit and who holds them. Use at most 2 settings and 2 characters per video; characters
   hold one pose per beat. What the characters CANNOT do: walk across a room, run, jump, fall,
   fight, dance; crowds; animals. A change of posture (lie_down, sit_up, stand_up, walk) is staged
   as a cut to another beat or base, so put it on the first word of a new beat. Stage such moments
   through the moment before or after, a prop, the far background, or a diagram.

Visual style of this channel:
"""


class ScriptError(RuntimeError):
    pass


def make_client():
    import anthropic
    if not config.has_anthropic_key():
        raise ScriptError('ANTHROPIC_API_KEY is not set. Add it to .env in the repository root.')
    # max_retries=0: the SDK would otherwise retry 429/5xx/timeouts automatically (paid retries).
    return anthropic.Anthropic(max_retries=0, timeout=300.0)


# ------------------------------------------------------------------ prompts

def research_request(episode):
    user = (f'Topic: {episode.topic}\nNiche: {episode.niche or "general"}\n'
            f'Audience: {episode.audience or "general English-speaking audience"}')
    # allowed_callers=['direct']: results go straight into Claude's context, so the answer carries
    # web_search_result_location citations with URLs. The default for this tool version routes
    # searches through code-execution filtering; the 2026-11-04 run returned 4 searches and no
    # usable citations that way.
    return dict(model=config.SCRIPT_MODEL, max_tokens=RESEARCH_MAX_TOKENS, system=RESEARCH_SYSTEM,
                messages=[{'role': 'user', 'content': user}],
                tools=[{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': RESEARCH_MAX_SEARCHES,
                        'allowed_callers': ['direct']}],
                thinking={'type': 'between_tools'}, output_config={'effort': 'medium'})


def script_request(episode, style, facts):
    from anthropic import transform_schema
    lo, hi = beat_range(episode.target_length)
    words = round(episode.target_length * style.words_per_second)
    if facts:
        fact_lines = '\n'.join(f'{f.id}: {f.claim} (source: {f.title or f.url})' for f in facts)
        research = f'Research facts (cite by id):\n{fact_lines}'
    else:
        research = 'No research facts were gathered. Do not state statistics or claims that need a source.'
    user = (f'Topic: {episode.topic}\nNiche: {episode.niche or "general"}\n'
            f'Audience: {episode.audience or "general English-speaking audience"}\n'
            f'Target length: {episode.target_length} seconds: {lo}-{hi} beats and about {words} narration words '
            f'(the narrator reads about {style.words_per_second} words per second).\n'
            f'Aspect: {episode.aspect}\nCast ids: {", ".join(style.cast_ids) or "none"}\n\n{research}')
    schema = transform_schema(ScriptDraft)
    return dict(model=config.SCRIPT_MODEL, max_tokens=SCRIPT_MAX_TOKENS, system=SCRIPT_RULES + style.script_brief,
                messages=[{'role': 'user', 'content': user}], thinking={'type': 'adaptive'},
                output_config={'format': {'type': 'json_schema', 'schema': schema}, 'effort': 'high'})


# ------------------------------------------------------------------ estimates

def count_input_tokens(client, request):
    """Free preflight count. Without a client (dry run) fall back to a high character estimate."""
    counted = {k: v for k, v in request.items() if k not in ('max_tokens',)}
    if client is None:
        text = json.dumps(counted, ensure_ascii=False)
        return int(len(text) / CHARS_PER_TOKEN_ESTIMATE) + 1, 'estimated offline'
    result = client.messages.count_tokens(**counted)
    tokens = getattr(result, 'input_tokens', None)
    if not isinstance(tokens, int) or tokens <= 0:
        raise ScriptError('Token count failed; nothing was sent.')
    return tokens, 'counted'


def research_estimate(input_tokens):
    return token_cost(config.SCRIPT_MODEL, input=input_tokens + RESEARCH_RESULT_TOKENS_BOUND,
                      output=RESEARCH_MAX_TOKENS, web_searches=RESEARCH_MAX_SEARCHES)


def script_estimate(input_tokens):
    return token_cost(config.SCRIPT_MODEL, input=input_tokens, output=SCRIPT_MAX_TOKENS)


def plan(episode, style, client=None, research=True):
    """Cost plan for the script stage without sending any paid request."""
    rows = []
    if research:
        n, how = count_input_tokens(client, research_request(episode))
        rows.append(('research', n, how, research_estimate(n)))
    placeholder = [Source(id=f's{i}', claim='x' * 160, url='https://example.org/' + 'x' * 40) for i in range(1, 7)]
    n, how = count_input_tokens(client, script_request(episode, style, placeholder if research else []))
    rows.append(('script', n, how, script_estimate(n)))
    return rows


# ------------------------------------------------------------------ calls

def _send(client, ledger, step, estimate, detail, request):
    import anthropic
    entry = ledger.reserve(step, 'anthropic', request['model'], estimate, detail)
    try:
        response = client.messages.create(**request)
    except anthropic.APIStatusError as exc:
        # A rejected request (4xx) is not billed; a server error may have been.
        ledger.fail(entry, f'HTTP {exc.status_code}', billed=exc.status_code >= 500)
        raise ScriptError(f'Anthropic returned HTTP {exc.status_code} ({exc.__class__.__name__}). '
                          'No automatic retry was made.') from None
    except anthropic.APIConnectionError as exc:
        ledger.fail(entry, exc.__class__.__name__, billed=True)
        raise ScriptError(f'Could not complete the request ({exc.__class__.__name__}). No automatic retry was made.') from None
    usage = usage_dict(response.usage)
    ledger.settle(entry, token_cost(request['model'], **usage), usage=usage, note=f'stop={response.stop_reason}')
    if response.stop_reason == 'refusal':
        details = getattr(response, 'stop_details', None)
        category = getattr(details, 'category', None) if details else None
        raise ScriptError(f'Claude declined the {step} request (category: {category}). No retry was made.')
    return response


def _cache(base, name, key):
    return Path(base) / 'cache' / f'{name}-{key[:16]}.json'


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    os.replace(tmp, path)


def facts_from_content(content):
    """Turn cited text into facts: one line of the answer = one fact, with its first web citation.

    A line starts at a newline or after any non-text block (search calls and results), and
    uncited lead-in text before the first cited fragment ("Based on the search results, ")
    is dropped, so a fact never carries words that no source supports at its start.
    """
    lines = [[]]  # each line is a list of (text, citations) fragments
    for block in content:
        if getattr(block, 'type', None) != 'text':
            lines.append([])
            continue
        cites = [c for c in (getattr(block, 'citations', None) or []) if citation_url(c)]
        for i, part in enumerate(block.text.split('\n')):
            if i:
                lines.append([])
            if part.strip():
                lines[-1].append((part, cites))
    facts, seen = [], set()
    for fragments in lines:
        first = next((i for i, (_, cites) in enumerate(fragments) if cites), None)
        if first is None:
            continue
        kept = fragments[first:]
        claim = ' '.join(''.join(text for text, _ in kept).split()).strip(' -•*')
        if len(claim) < 20 or claim in seen:
            continue
        seen.add(claim)
        c = kept[0][1][0]
        facts.append(Source(id=f's{len(facts) + 1}', claim=claim, url=citation_url(c),
                            title=getattr(c, 'title', '') or '', quote=getattr(c, 'cited_text', '') or ''))
    return facts[:8]


def citation_url(citation):
    """URL behind a web-search citation (direct results use `url`, search_result blocks use `source`)."""
    kind = getattr(citation, 'type', '')
    url = getattr(citation, 'url', '') if kind == 'web_search_result_location' else (
        getattr(citation, 'source', '') if kind == 'search_result_location' else '')
    return url if isinstance(url, str) and url.startswith(('http://', 'https://')) else ''


def _plain(obj):
    if hasattr(obj, 'model_dump'):
        return obj.model_dump(mode='json')
    if hasattr(obj, '__dict__'):
        return {k: _plain(v) for k, v in vars(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def research(episode, base, ledger, client, regenerate=False):
    request = research_request(episode)
    key = digest([PROMPT_VERSION, 'research', request['model'], request['system'], request['messages'], request['tools']])
    path = _cache(base, 'research', key)
    if path.exists() and not regenerate:
        return [Source.model_validate(f) for f in json.loads(path.read_text())['facts']], path, True
    n, _ = count_input_tokens(client, request)
    messages = list(request['messages'])
    content = []
    for turn in range(MAX_CONTINUATIONS + 1):
        estimate = research_estimate(n)
        response = _send(client, ledger, 'research', estimate,
                         f'web search ≤{RESEARCH_MAX_SEARCHES}' + (f' (resume {turn})' if turn else ''),
                         request | {'messages': messages})
        content.extend(response.content)
        if response.stop_reason != 'pause_turn':
            break
        # Resume the paused server-side loop: send the assistant turn back unchanged.
        messages = messages + [{'role': 'assistant', 'content': response.content}]
        n += usage_dict(response.usage)['input'] + usage_dict(response.usage)['output']
    else:
        raise ScriptError('Research did not finish within the allowed resumes.')
    raw = _cache(base, 'research-raw', key)
    _write_json(raw, dict(content=_plain(content), created=time.strftime('%Y-%m-%dT%H:%M:%S')))
    facts = facts_from_content(content)
    if not facts:
        # Don't cache an empty result and don't pay for a script that was asked to be researched.
        raise ScriptError(f'Research returned no cited facts, so no script was written (raw response: {raw}). '
                          'Re-run with --regenerate research, or use --no-research for a script without statistics.')
    _write_json(path, dict(facts=[f.model_dump() for f in facts], created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                           model=request['model'], prompt_version=PROMPT_VERSION, raw=raw.name))
    return facts, path, False


def write(episode, style, facts, base, ledger, client, regenerate=False):
    request = script_request(episode, style, facts)
    key = digest([PROMPT_VERSION, 'script', request['model'], request['system'], request['messages']])
    path = _cache(base, 'script', key)
    if path.exists() and not regenerate:
        return ScriptDraft.model_validate(json.loads(path.read_text())['draft']), path, True
    n, _ = count_input_tokens(client, request)
    response = _send(client, ledger, 'script', script_estimate(n), 'structured script', request)
    text = next((b.text for b in response.content if getattr(b, 'type', None) == 'text'), '')
    failed = Path(base) / 'cache' / f'failed-script-{time.strftime("%Y%m%d-%H%M%S")}.json'
    if response.stop_reason == 'max_tokens':
        _write_json(failed, dict(reason='max_tokens', raw=text))
        raise ScriptError(f'The script hit the output limit before finishing. Partial output saved to {failed}.')
    try:
        draft = ScriptDraft.model_validate(json.loads(text))
    except (ValueError, TypeError) as exc:
        _write_json(failed, dict(reason='invalid', error=str(exc)[:2000], raw=text))
        raise ScriptError(f'The script response did not match the schema. Saved to {failed} for repair; '
                          'no automatic retry was made.') from None
    _write_json(path, dict(draft=draft.model_dump(), created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                           model=request['model'], prompt_version=PROMPT_VERSION))
    return draft, path, False


def generate(episode, style, base, ledger, client=None, use_research=True, regenerate=()):
    """Fill episode.title, hook_promise, beats and sources. Returns (episode, notes)."""
    client = client or make_client()
    notes = []
    facts = []
    if use_research:
        facts, path, cached = research(episode, base, ledger, client, 'research' in regenerate)
        notes.append(f'research: {len(facts)} cited facts ({"cache" if cached else "new"}: {path.name})')
    draft, path, cached = write(episode, style, facts, base, ledger, client, 'script' in regenerate)
    notes.append(f'script: {len(draft.beats)} beats ({"cache" if cached else "new"}: {path.name})')
    updated = episode.model_copy(update=dict(
        title=draft.title, hook_promise=draft.hook_promise, sources=facts,
        beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)],
        approvals={k: v for k, v in episode.approvals.items() if k != 'script'},
        meta=episode.meta | dict(script=dict(model=config.SCRIPT_MODEL, prompt_version=PROMPT_VERSION,
                                             research=use_research, generated=time.strftime('%Y-%m-%dT%H:%M:%S')))))
    return Episode.model_validate(updated.model_dump()), notes
