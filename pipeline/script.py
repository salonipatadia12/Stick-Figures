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
import re
import os
import time
from pathlib import Path

from . import config
from .cost import token_cost, usage_dict
from .models import Beat, Episode, ScriptDraft, Source, beat_range, digest, from_wire, wire_schema

PROMPT_VERSION = 'script-v6'  # v2: direct web search with URL citations; v3: staging fields (setting, poses, actions, props); v4: visual types (character, insert, graphic); v5: host format, three hook options; v6: written from a picked storyline, story roles, visual variety
# The research cache is keyed on its own version: a script-prompt change must never re-run (and re-pay) research.
# Frozen at the value the 2026-11-08 research runs were cached under.
RESEARCH_VERSION = 'research-v6'  # v6: fact lines straight from cited results; source filtering in code (keep_reputable)
RESEARCH_MAX_SEARCHES = 4
RESEARCH_MAX_TOKENS = 4096
# Search results are billed as input tokens and can't be counted before the call. This is the
# worst case reserved per research request; real usage is recorded afterwards.
RESEARCH_RESULT_TOKENS_BOUND = 100_000
MAX_CONTINUATIONS = 2  # pause_turn resumes of the server-side search loop, each budgeted
SCRIPT_MAX_TOKENS = 24_000  # v5 drafts carry three full hook beats
CHARS_PER_TOKEN_ESTIMATE = 3.0  # offline estimate only (dry runs without a key); deliberately high

# Never searched (2026-11-09: the Pisa results were mostly ResearchGate/academia.edu copies, written up uncited).
BLOCKED_DOMAINS = ('wikipedia.org', 'researchgate.net', 'academia.edu', 'slideshare.net', 'scribd.com', 'medium.com',
                   'quora.com', 'reddit.com', 'youtube.com', 'pinterest.com', 'coursehero.com', 'studocu.com', 'intechopen.com')

RESEARCH_SYSTEM = """You gather facts for a 30-60 second explainer video. Use web search.
Write 4 to 8 facts. Each fact is one plain sentence on its own line, written directly from one search result so that
it carries that result's citation, and ending with that result's URL in angle brackets: "<https://...>". No bullets, numbering, introduction, conclusion or notes, and never discuss which
sources you used, chose or excluded: the answer is only the fact lines.
State only what a search result directly supports. Prefer facts that explain the mechanism behind the topic, plus at
most three statistics.
Prefer government agencies and statistics offices, universities and their research pages, and peer-reviewed journals
(publisher pages, PubMed Central, DOI links): facts from any other kind of source are discarded automatically, so take
each fact from one of those when you can.
If a fact only holds for one country or period, name it in the sentence. Do not give advice."""

SCRIPT_RULES = """You write scripts for explainer Shorts: vertical videos of 30-60 seconds, painted stick figures,
a narrated voice and word-synced captions.

FORMAT (every video): the lead stick figure is the HOST. The host talks to the viewer in first person or "you"
(the narration IS the host speaking, lips moving, eyes to camera) and walks them through the topic end to end,
acting the story out inside the pictures.

Rules:
1. STORY. Tell the PICKED STORYLINE below, in its format, as a story: a clear conflict or problem early, stakes that
   rise, at least one true twist or surprise, and a payoff that answers the hook. Never a list of facts in order.
   Roles: beat 1 "hook" -> middle beats, each tagged with what it does for the story: "setup", "problem" (the
   conflict, by beat 2-4), "stakes" (the stakes rise), "clue" (a mystery's clues), "reason" (a countdown's reasons,
   counting down), "twist" (the surprise), "reveal" -> one "payoff" beat that answers the hook -> one "loop" beat whose
   last words tie back to the hook line. A "promise" beat is allowed only when the format needs it, and is never "let
   me show you". Fill hook_promise with the question the hook opens.
2. HOOK. The hook beat's narration is ONLY the hook line: at most 8 words, a question or a bold claim the payoff
   answers. It must be honest: never walked back or contradicted later. Frame 1 already shows the host in a
   surprising situation that makes the line land (e.g. "It's June. Why is it snowing?" with the host in a t-shirt
   beside snow on summer flowers; "Wait... why did I come in here?" as the host walks through a door and freezes;
   "Same sun, same sky. So why red?" as the host snaps their fingers and the sky flips from blue to red).
   No greeting, no "in this video". Write 3 genuinely different hook_options; option 1 is the storyline's hook and is
   used in beats. Each option carries its frame one, how the payoff answers it, and its own loop_line.
3. THE HOST IS IN THE STORY. Every beat fills shot.host (and puts "lead" in cast when the host is on screen). Put the host INSIDE the explainer scenes, full body,
   acting out each line: on the beach watching the volcano, shivering in the frosted field, walking through the
   doorway, looking up at the red sky. Diagrams may fill the frame briefly with the host popping in at the edge to
   point (where "pop_in"). Aim for the host on screen in at least 70% of beats. Set host.placement: side (left,
   centre, right) and size (small: the place dominates; medium: full body; large: knees up, near the camera; close:
   head and shoulders, for a reaction or an aside), VARIED beat to beat; add "watching" when the host watches the scene
   instead of talking to the viewer, and "holding <thing>" when it holds up or uses a key object ("jar of honey"). The friend appears only when the
   story needs a second person (someone to ask, to contrast with); no cafe or sofa chatter unless the story is set
   there.
4. NO INVENTED FACTS. Use only the numbered research facts provided, citing their ids in source_ids. A number
   that is not from a fact must be framed as an example in the narration ("say you take home 3,000 a month") with
   illustrative_numbers=true. Ordinal dates such as "the 20th" are fine.
5. VOICE. The host talking: conversational, short sentences, contractions, "I" and "you".
6. ON-SCREEN TEXT. 0-5 words that punch the key idea, often the number or the turn. Empty when the picture already
   says it. Show it with a text_slam (or number_counter / split_compare / checklist_tick).
7. MOTION. Each beat gets 1-2 recipes. The picture must change on a key word at least every ~2.5 seconds, so most
   beats need one word-anchored change (a host action, an insert effect, a graphic item or a recipe) whose anchor
   appears verbatim in that beat's narration. Continuous moves (push_in, pan_left, pan_right, parallax, breathe)
   take an empty anchor. Guide: push_in or pan to establish; punch_in on a key word; impact_shake for a shock;
   text_slam to land the on-screen text; number_counter when a quantity changes (detail "0 -> 31").
8. TRANSITIONS. "cut" by default. "whip" only for a jump in time or place; "match_cut" only when two shots share a
   shape or object.
9. SOUND. No music, no ambience. At most 5-6 sound effects in the whole video, each meaningful (an eruption thud,
   a door, a finger snap): 0-1 per beat, from pop, whoosh, ding, coin, thud, paper, typing, anchored to a word.
   No sound for every label or diagram item.
10. IMAGE PROMPTS. Describe one painted frame for an image model: which cast ids appear, where, pose, expression,
   props, light and framing. No text, letters or numbers inside the picture. Keep recurring props and places
   consistent across beats. Do not restate the art style.
11. STAGING. The video is built from libraries, so fill shot.staging exactly: setting from the list (cafe, bedroom,
   office, kitchen, living-room, doorway (between living room and kitchen), street, park, classroom, gym, car;
   "other: <the place in 3-6 words>" only if none fits); one pose per cast id (seated-at-table, standing, bed-edge,
   bed-awake, bed-asleep-top, seated-sofa, seated-desk, seated-bench, driving, passenger, walking); 0-3 actions from
   reach, point, shrug, facepalm, pick_up, put_down, throw, drink, type, write, wave, nod, shake_head, slump, jolt,
   gaze, shiver, look_up, snap, freeze, each on a word of this beat's narration; 0-4 props with where they sit and
   who holds them. When the host is on screen, "lead" is in shot.cast with its pose and verbs in staging. Use at most 2 characters per video; a
   character holds one pose per beat. What the characters CANNOT do: run, jump, fall, fight, dance; crowds; animals.
   Walking is a short walk-in or walk-through inside one picture, not across town. In an insert scene the host
   stands or walks; seated poses need a setting.
12. SHOW THE SUBJECT. Viewers must SEE the idea. Each beat sets shot.visual: "insert" (a full-frame painted scene
   of the thing, place or process, e.g. a volcano erupting, a frosted farm, a street at sunset; fill the insert_* fields
   of shot.picture with a kebab-case key, the scene painted WITHOUT any people (the host is added on top, matched to its light and
   scale), and 0-4 effects in shot.picture such as ash/snow/dust/embers/haze particles, a colour grade "blue -> red", sun_path
   "high -> low", a crossfade to a changed version of the same place, push/pan, labels and arrows, each on a word),
   "graphic" (a drawn diagram in the painted style: flow A -> B -> C, bounce, scatter, two_paths, waves, bars,
   thermometer, cards, layers; fill the graphic field of shot.picture) or "character" (the host in a setting from the library, when the
   setting is the story). Most beats are inserts with the host in them; at least 70% show the subject. When a
   setting IS the subject (a doorway for a video about doorways), list it in subject_settings. Numbers drawn on
   screen (labels, split labels, graphic items) must be numbers this beat says. reaction_anchor stays empty in
   this format (the host is already in the picture).
   More tools: "split" (two painted scenes stacked, top = insert_*, bottom = picture.split, each labelled: myth vs fact, then
   vs now, here vs there); a painted map is an insert whose scene is an illustrated map ("illustrated map of Italy,
   no text"), with "pin" effects on regions and arrows for routes; "focus" pushes into one region of a scene for a
   close-up of a detail; push, pan and shake move the camera.
13. VARIETY (user 2026-11-09: every video looked like the same template). Paper diagrams are one tool, not the
   default: at most 2 graphics per video, and never a bar chart unless the comparison IS the story. No two beats in a
   row with the same visual type and the same host spot and size. Never the template hook -> "let me show you" ->
   diagram -> bar chart -> loop. Use at least three different tools in a video (scene animation, close-up/focus,
   split, map, a held object, camera moves, a diagram).
14. NOTHING BEYOND THE SOURCE (user 2026-11-10). Every factual line says only what its cited fact says. No
   extrapolation, no absolutes the fact does not state ("never touched", "always", "the only way"), no motives or
   feelings the source does not report. If the story needs a step the facts do not support, leave the step out.
15. ONE PLACE, ITS OWN SCENES. When the topic happens in a real place that is not a library setting (a landmark, a
   city, a lab), every beat that shows the host shows that place: a painted scene (insert) of it, never a generic
   library setting (street, park, office...) as a stand-in.
16. HOST SCALE (user 2026-11-10: never dwarfed by a giant prop). In a close-up of objects (a jar on a counter, a
   bottle, a cell) the host is "large" or "close" (near the camera) or pops in; "small" and "medium" full-body
   placements are for places (a room, a street, a landmark). Held props are hand-sized.
17. NOTHING STATIC (user 2026-11-10). Nothing on screen holds still for more than 2.5 seconds (about 6 narration
   words): anchor a camera move, an effect, a label, a split reveal or a host gesture on a word in every longer stretch.

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
    focus = episode.meta.get('research_focus') or {}
    if focus.get('find'):                      # the user's own list of events the story needs (user 2026-11-10)
        user += ('\n\nThe story needs a cited fact for each of these, in this order:\n'
                 + '\n'.join(f'- {x}' for x in focus['find']))
    if focus.get('prefer'):
        user += '\nLook first in: ' + '; '.join(focus['prefer']) + '.'
    # allowed_callers=['direct']: results go straight into Claude's context, so the answer carries
    # web_search_result_location citations with URLs. The default for this tool version routes
    # searches through code-execution filtering; the 2026-11-04 run returned 4 searches and no
    # usable citations that way.
    return dict(model=config.SCRIPT_MODEL, max_tokens=RESEARCH_MAX_TOKENS, system=RESEARCH_SYSTEM,
                messages=[{'role': 'user', 'content': user}],
                tools=[{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': RESEARCH_MAX_SEARCHES,
                        'allowed_callers': ['direct'], 'blocked_domains': list(BLOCKED_DOMAINS)}],
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
    sl = episode.storyline
    if sl:
        from .models import FORMATS
        user += (f'\n\nPICKED STORYLINE (format {sl.format}: {FORMATS[sl.format]}):\nTitle: {sl.title}\nHook: {sl.hook}\n'
                 f'Pitch: {sl.pitch}\nConflict: {sl.conflict}\nStakes: {sl.stakes}\nTwist: {sl.twist}\nPayoff: {sl.payoff}\n'
                 f'Picture ideas: {"; ".join(sl.visuals)}\n'
                 'The storyline was pitched from earlier research. Its fact ids no longer apply: cite only the research '
                 'facts above. Any line of the storyline (a number, a date, an event) that those facts do not support is '
                 'DROPPED or rewritten without it; never keep an unsourced claim to save the story.')
    schema = wire_schema(transform_schema(ScriptDraft))
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
        reason = ''
        body = getattr(exc, 'body', None)
        if isinstance(body, dict):
            reason = str((body.get('error') or {}).get('message', ''))[:400]
        raise ScriptError(f'Anthropic returned HTTP {exc.status_code} ({exc.__class__.__name__})'
                          + (f': {reason}' if reason else '') + '. No automatic retry was made.') from None
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
    results = {}                                  # url -> title of every result this search returned
    for block in content:
        if getattr(block, 'type', None) == 'web_search_tool_result' and isinstance(getattr(block, 'content', None), list):
            for r in block.content:
                if getattr(r, 'url', ''):
                    results[r.url.rstrip('/')] = getattr(r, 'title', '') or ''
    facts, seen = [], set()
    for fragments in lines:
        text = ' '.join(''.join(t for t, _ in fragments).split())
        tail = TRAILING_URL.search(text)
        first = next((i for i, (_, cites) in enumerate(fragments) if cites), None)
        if first is not None:                     # the API's own citation, with the quoted text: preferred
            kept = fragments[first:]
            claim = TRAILING_URL.sub('', ' '.join(''.join(t for t, _ in kept).split())).strip(' -•*')
            c = kept[0][1][0]
            url, title, quote = citation_url(c), getattr(c, 'title', '') or '', getattr(c, 'cited_text', '') or ''
        elif tail and tail.group(1).rstrip('/') in results:
            # no citation came back (it happens: 2026-11-10, 5 of 8 runs): the line names the result it came from,
            # and that URL really was returned by this search; there is no quote to show for it
            claim = TRAILING_URL.sub('', text).strip(' -•*')
            url, title, quote = tail.group(1), results[tail.group(1).rstrip('/')], ''
        else:
            continue
        if len(claim) < 20 or claim in seen:
            continue
        seen.add(claim)
        facts.append(Source(id=f's{len(facts) + 1}', claim=claim, url=url, title=title, quote=quote))
    return facts[:8]


TRAILING_URL = re.compile(r'\s*<?\(?(https?://[^\s<>()]+?)\)?>?\s*$')


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


# Hosts of peer-reviewed publishers and indexes; plus any government (.gov, .gov.xx, .gouv.fr, .gob.xx) or university
# (.edu, .edu.xx, .ac.xx, uni-*/univ-* hosts) domain. Patents are excluded even on government sites (a claim, not a
# reviewed finding). Checked 2026-11-09 against the user's rule: government, university, peer-reviewed only.
PEER_REVIEWED = ('pmc.ncbi.nlm.nih.gov', 'pubmed.ncbi.nlm.nih.gov', 'ncbi.nlm.nih.gov', 'doi.org', 'sciencedirect.com',
                 'springer.com', 'link.springer.com', 'nature.com', 'science.org', 'wiley.com', 'tandfonline.com',
                 'mdpi.com', 'frontiersin.org', 'plos.org', 'academic.oup.com', 'cambridge.org', 'royalsocietypublishing.org',
                 'pnas.org', 'cell.com', 'bmj.com', 'thelancet.com', 'jstor.org', 'icevirtuallibrary.com', 'ascelibrary.org',
                 'iopscience.iop.org', 'pubs.acs.org', 'annualreviews.org', 'biomedcentral.com', 'bmcpsychology.biomedcentral.com',
                 'agu.org', 'agupubs.onlinelibrary.wiley.com', 'journals.ametsoc.org', 'copernicus.org', 'sagepub.com',
                 'jamanetwork.com', 'nejm.org', 'elifesciences.org', 'europepmc.org', 'scielo.org', 'ieeexplore.ieee.org',
                 'jiban.or.jp', 'jstage.jst.go.jp',   # Soils and Foundations (Japanese Geotechnical Society): user 2026-11-10
                 'issmge.org', 'geocasehistoriesjournal.org')   # ISSMGE proceedings and its case-histories journal
NOT_REVIEWED = ('patent', 'uspto', 'ppubs', 'espacenet', 'wikipedia.org', 'blog', 'medium.com', 'academia.edu',
                'researchgate.net', 'slideshare.net', 'scribd.com', 'intechopen.com', 'coursehero.com', 'studocu.com')


def reputable(url: str) -> bool:
    """Government, university or peer-reviewed (user rule, 2026-11-09)."""
    import re as _re
    from urllib.parse import urlparse
    u = urlparse(url)
    host = (u.hostname or '').lower()
    if any(x in host or x in u.path.lower() for x in NOT_REVIEWED):
        return False
    if _re.search(r'(\.gov|\.gov\.[a-z]{2}|\.gouv\.[a-z]{2}|\.gob\.[a-z]{2}|\.mil|\.edu|\.edu\.[a-z]{2}|\.ac\.[a-z]{2})$', host):
        return True
    if _re.search(r'(^|\.)(uni|univ)[a-z-]*\.[a-z.]+$', host) or '.europa.eu' in host or host.endswith('europa.eu'):
        return True
    return any(host == h or host.endswith('.' + h) for h in PEER_REVIEWED)


def doi_link(url: str) -> str:
    """A search-engine lookup of a DOI (scholar.google.com/scholar_lookup?doi=...) is that DOI's own link."""
    from urllib.parse import parse_qs, unquote, urlparse
    u = urlparse(url)
    doi = parse_qs(u.query).get('doi', [''])[0] if 'scholar' in u.netloc else ''
    return f'https://doi.org/{unquote(doi)}' if doi.startswith('10.') else url


def keep_reputable(facts):
    """(kept facts renumbered s1.., dropped facts). A dropped fact's line cannot be written (user: drop it)."""
    facts = [f.model_copy(update=dict(url=doi_link(f.url))) for f in facts]
    kept = [f for f in facts if reputable(f.url)]
    dropped = [f for f in facts if not reputable(f.url)]
    return [f.model_copy(update=dict(id=f's{i}')) for i, f in enumerate(kept, 1)], dropped


def research(episode, base, ledger, client, regenerate=False):
    request = research_request(episode)
    key = digest([RESEARCH_VERSION, 'research', request['model'], request['system'], request['messages'], request['tools']])
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
        data, wire_notes = from_wire(json.loads(text))
        draft = ScriptDraft.model_validate(data)
    except (ValueError, TypeError) as exc:
        _write_json(failed, dict(reason='invalid', error=str(exc)[:2000], raw=text))
        raise ScriptError(f'The script response did not match the schema. Saved to {failed} for repair; '
                          'no automatic retry was made.') from None
    _write_json(path, dict(draft=draft.model_dump(), created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                           model=request['model'], prompt_version=PROMPT_VERSION, wire_notes=wire_notes, raw=text))
    return draft, path, False


def checked_facts(path) -> list:
    """Facts read by hand from their sources (each with the exact quote), in place of the research call: a JSON list
    of {claim, url, title, quote}. Ids are s1, s2... in file order."""
    rows = json.loads(Path(path).read_text())
    return [Source(id=f's{k}', claim=r['claim'], url=r['url'], title=r.get('title', ''), quote=r.get('quote', ''))
            for k, r in enumerate(rows, 1)]


def generate(episode, style, base, ledger, client=None, use_research=True, regenerate=(), facts_file=None):
    """Fill episode.title, hook_promise, beats and sources. Returns (episode, notes)."""
    if not episode.storyline:
        raise ScriptError(f'No storyline picked for {episode.slug}: story before script (stick make --episode '
                          f'{episode.slug} pitches three; stick approve --gate storyline --story N picks one).')
    client = client or make_client()
    notes = []
    facts = []
    if facts_file or use_research:
        if facts_file:                                 # checked by hand: no research call
            facts = checked_facts(facts_file)
            notes.append(f'facts: {len(facts)} checked by hand from their sources ({Path(facts_file).name}); no research call')
        else:
            facts, path, cached = research(episode, base, ledger, client, 'research' in regenerate)
            notes.append(f'research: {len(facts)} cited facts ({"cache" if cached else "new"}: {path.name})')
        facts, dropped = keep_reputable(facts)
        for f in dropped:
            notes.append(f'  dropped (not government, university or peer-reviewed): {f.url}')
        if not facts:
            raise ScriptError('No fact came from a government, university or peer-reviewed source, so no script was '
                              'written. Re-run research, or pick a topic with better sources.')
    draft, path, cached = write(episode, style, facts, base, ledger, client, 'script' in regenerate)
    notes.append(f'script: {len(draft.beats)} beats ({"cache" if cached else "new"}: {path.name})')
    updated = episode.model_copy(update=dict(
        title=draft.title, hook_promise=draft.hook_promise, sources=facts,
        hook_options=draft.hook_options, hook_pick=0, subject_settings=draft.subject_settings,
        beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)],
        approvals={k: v for k, v in episode.approvals.items() if k != 'script'},
        meta=episode.meta | dict(script=dict(model=config.SCRIPT_MODEL, prompt_version=PROMPT_VERSION,
                                             research=use_research, generated=time.strftime('%Y-%m-%dT%H:%M:%S')))))
    return Episode.model_validate(updated.model_dump()), notes
