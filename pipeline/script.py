"""Topic -> researched script.

SCRIPT_PROVIDER=ollama (the default since 2026-11-10, $0): research by DuckDuckGo and fetched pages
(pipeline/search.py), the script by a local model in two stages with a bounded repair loop (write_free below).

SCRIPT_PROVIDER=anthropic (the earlier path, kept as it was): Claude (claude-sonnet-5-5) in two budgeted calls.

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
   gaze, shiver, look_up, snap, freeze, tickle, laugh, each on a word of this beat's narration; 0-4 props with where they sit and
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

# SCRIPT_RULES, compressed for a local model (SCRIPT_PROVIDER=ollama): the same 17 rules without the history notes,
# overlapping rules merged, enum lists left to the output schema (which only allows the listed values). Under 1,200
# words. Split in two because the local writer works in two stages: the outline (story, hook, narration, picture
# type, sources) and then each beat's staging, in parallel.
SCRIPT_RULES_COMPACT_OUTLINE = """You write explainer Shorts: 30-60 s vertical videos, painted stick figures, a narrated voice, word-synced captions.
The lead stick figure is the HOST: it talks to the viewer ("I", "you"), lips moving, and acts the story out inside the
pictures.

STORY. Tell the PICKED STORYLINE, in its format, as a story, never a list of facts: a clear problem early, stakes that
rise, at least one true twist, a payoff that answers the hook. Beat roles in order: beat 1 "hook"; middle beats are
"setup", "problem" (the conflict, by beat 2-4), "stakes" (stakes rise), "clue" (mystery clues), "reason" (countdown
reasons, counting down), "twist" (the surprise), "reveal"; then ONE "payoff" beat that answers the hook; the LAST beat
"loop", whose last words tie back to the hook line. "promise" only when the format needs it, never "let me show you".
A mystery or countdown needs at least two "clue"/"reason" beats after the problem; other formats at least one "stakes".
HOOK. Beat 1's narration is ONLY the hook line: at most 8 words, a question or bold honest claim the payoff answers,
never walked back. No greeting, no "in this video". Frame 1 already shows the host in a surprising situation that
makes the line land. Write 3 different hook_options; option 1 is the storyline's hook and is beat 1's narration. Each
option: line, frame_one (what the first frame shows), answer (one sentence: how the payoff answers it), loop_line (the
last beat's narration for that hook; it echoes a word of the hook). hook_promise = the question the hook opens.
FACTS. Use only the numbered research facts; cite their ids in source_ids on every beat that uses one. A number that
is not in a cited fact is framed as an example in the narration ("say you earn 3,000") with illustrative_numbers=true.
Ordinal dates ("the 20th") are fine. Every factual line says only what its fact says: no extrapolation, no absolutes
the fact does not state, no motives or feelings. If the story needs a step the facts do not support, leave it out.
Any storyline line the facts do not support is dropped.
VOICE. Conversational, short sentences, contractions, one idea per beat.
ON-SCREEN TEXT. 0-5 words that punch the key idea (often the number or the turn); empty when the picture says it.
Numbers on screen are numbers the beat says.
PICTURE TYPE per beat. Viewers must SEE the idea: "insert" (a full-frame painted scene of the thing, place or process,
with the host standing in it) for most beats; "graphic" (a drawn diagram: flow, bounce, scatter, two_paths, waves, bars,
thermometer, cards, layers) at most 2 per video, never a bar chart unless the comparison IS the story; "split" (two
painted scenes stacked: myth vs fact, then vs now); "character" (the host in a library setting) only when that setting
is the story. At least 70 % of beats show the subject. Use at least three different tools across the video (scene
animation, close-up, split, map, held object, camera moves, diagram). Never the template hook -> "let me show you" ->
diagram -> bar chart -> loop. When the topic happens in one real place (a landmark, a city), show that place in every
beat. subject_settings: settings that ARE the subject (e.g. "doorway" for a video about doorways), usually empty."""

SCRIPT_RULES_COMPACT_BEAT = """You stage ONE beat of an explainer Short (painted stick figures; the lead is the HOST, who talks to the viewer
and acts the story out inside the picture). The beat's narration and picture type are fixed; fill in how it looks.

HOST. host.where: "scene" (full body inside the picture), "pop_in" (pops in at a frame edge to point at a diagram) or
"off" (rare). When the host is on screen, "lead" is in cast with a pose. side left/centre/right and size small (the
place dominates), medium (full body), large (knees up, near the camera), close (head and shoulders: a reaction or an
aside): vary them beat to beat. In a close-up of objects (a jar, a cell) the host is large or close or pops in; small and
medium are for places. watching=true when the host watches the scene instead of talking to the viewer. holds: a
hand-sized prop the host holds up or uses, or "". doing: what the host does, 4-15 words. The friend appears only when
the story needs a second person. At most 2 characters; each holds one pose per beat. The characters cannot run, jump,
fall, fight or dance; no crowds, no animals. In a painted scene the host stands or walks a few steps.
STAGING. setting: the library place, or "other" with setting_detail (the place in 3-6 words) when none fits (an insert
of a real place, a sky, a lab). One pose per cast id. 0-3 actions, each a verb on a word of THIS narration. 0-4 props
with where they sit and who holds them.
INSERT (picture type insert or split). key: kebab-case scene name ("street-sunset"). scene: the place or thing painted
WITHOUT any people or text, 10-30 words: what is in frame, time of day, weather. 0-4 effects, each on a narration
word: particles (ash, snow, dust, embers, rain, haze), grade (detail "blue -> red" or "warm -> cold"), dim, flash,
sun_path (detail "high -> low"), crossfade (detail: the same place, changed), push, pan, shake, focus (close-up push into
a region), label (detail: 1-3 words), arrow (detail "from -> to" regions), pin (a map pin; detail its label), cross (a
red X over an idea ruled out). A painted map is an insert of an illustrated map, with pins and arrows.
SPLIT. The bottom scene (key, scene without people) and two labels of 1-3 words (top, bottom), each on a narration word.
GRAPHIC. kind, 1-4 items (1-3 words each, reading order) with the narration word where each appears; detail: bars
relative heights "3 | 5", thermometer "from -> to", two_paths "both", else "". Only numbers this beat says.
MOTION. 1-2 recipes. The picture changes on a key word at least every 2.5 s (about 6 words): anchor a camera move, an
effect, a label, a split reveal or a host action on a word in every longer stretch. Continuous moves (push_in, pan_left,
pan_right, parallax, breathe) take anchor "". punch_in on a key word; impact_shake for a shock; text_slam lands the
on-screen text (anchor: a word of it that is spoken); number_counter when a quantity changes (detail "0 -> 31"). Every
anchor is a word of THIS beat's narration.
SOUND. 0-1 effect per beat, only when meaningful (a thud, a door, a finger snap), anchored to a word; at most 5-6 in
the whole video, never one for every label.
visual_intent: one sentence on how the picture SHOWS the idea through action, a metaphor or a diagram."""

SCRIPT_RULES_COMPACT = SCRIPT_RULES_COMPACT_OUTLINE + '\n\n' + SCRIPT_RULES_COMPACT_BEAT


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
PEER_REVIEWED = ('pmc.ncbi.nlm.nih.gov', 'pubmed.ncbi.nlm.nih.gov', 'ncbi.nlm.nih.gov', 'sciencedirect.com',
                 'linkinghub.elsevier.com',   # Elsevier's DOI router to ScienceDirect
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


DOI_HOSTS = ('doi.org', 'dx.doi.org')
_DOI_TARGETS: dict = {}


def resolve_doi(doi: str) -> str:
    """Where a DOI points (doi.org's keyless handle API), or '' when that can't be found out. Kept per process."""
    if doi in _DOI_TARGETS:
        return _DOI_TARGETS[doi]
    import json as _json
    import urllib.parse
    import urllib.request
    try:
        api = f'https://doi.org/api/handles/{urllib.parse.quote(doi, safe="/")}?type=URL'
        with urllib.request.urlopen(api, timeout=10) as r:
            data = _json.load(r)
        url = next((v['data']['value'] for v in data.get('values', []) if v.get('type') == 'URL'), '')
    except Exception:
        return ''                                       # unknown now; not remembered, so a later call may succeed
    _DOI_TARGETS[doi] = url
    return url


def reputable(url: str) -> bool:
    """Government, university or peer-reviewed (user rule, 2026-11-09). A doi.org link counts only when the DOI
    resolves to such a site: a DOI says nothing about review (user 2026-11-11, after a doi.org paper from an
    unlisted publisher said refraction makes sunsets red)."""
    import re as _re
    from urllib.parse import unquote, urlparse
    u = urlparse(url)
    host = (u.hostname or '').lower()
    if any(x in host or x in u.path.lower() for x in NOT_REVIEWED):
        return False
    if host in DOI_HOSTS:
        doi = unquote(u.path.lstrip('/'))
        target = resolve_doi(doi) if doi.startswith('10.') else ''
        return bool(target) and (urlparse(target).hostname or '').lower() not in DOI_HOSTS and reputable(target)
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
    if provider(client) == 'ollama':                   # free: DuckDuckGo + the local model (pipeline/search.py)
        return research_free(episode, base, ledger, regenerate)
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
    if provider(client) == 'ollama':                   # free: the two-stage local writer
        return write_free(episode, style, facts, base, ledger, regenerate)
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


# ------------------------------------------------------------------ free path (SCRIPT_PROVIDER=ollama)
#
# Research: pipeline/search.py (DuckDuckGo -> reputable pages -> facts quoted from them). Script, in two stages:
#   A. one short call writes the outline: title, hook options, and per beat the role, narration, on-screen text,
#      picture type and cited fact ids (the structural rules only: SCRIPT_RULES_COMPACT_OUTLINE);
#   B. one short call per beat fills its staging (host, cast, setting, poses, actions, props, scene or diagram,
#      motion, sound), in parallel: SCRIPT_RULES_COMPACT_BEAT. Anchor fields may only take words of that beat's
#      narration and every list of names only its allowed values (Ollama's schema-constrained output).
# Then the draft is checked by the same Episode.check() as any script. Errors go back to the model, a few rounds at
# most: outline-level errors (story shape, hook, unsourced numbers) to the outline call, beat-level errors to that
# beat's call only. No rule is loosened: what still fails after the last round is shown at the script gate.

FREE_RESEARCH_VERSION = 'research-free-v1'
FREE_SCRIPT_VERSION = 'script-free-v2'   # v2: longer beats ask for word-anchored actions and effects
FREE_REPAIR_ROUNDS = 3          # rounds of "here are the checker's errors, fix them" after the first draft
OUTLINE_REPAIRS = 2             # repairs of the outline before any beat is staged
OUTLINE_MAX_TOKENS, BEAT_MAX_TOKENS = 1400, 900     # measured: outline ~860 tokens, a beat ~270-420
OUTLINE_ROLES = ['hook', 'setup', 'problem', 'stakes', 'clue', 'reason', 'twist', 'reveal', 'promise', 'payoff', 'loop']
OUTLINE_LEVEL = ('on-screen text has', 'empty narration', 'cites unknown sources', 'with no source', 'greeting',
                 'hook sentence', 'the last beat must', 'the first beat must')
SIDES = ('centre', 'left', 'right')
# suggested host sizes per picture type, rotated beat to beat (rule 16: places small/medium; close-ups large/close)
SIZES = {'insert': ('medium', 'small', 'large', 'medium'), 'split': ('medium', 'small'),
         'graphic': ('medium', 'large'), 'character': ('medium', 'large', 'close')}
LAST_RUN: dict = {}             # timings and cache behaviour of the latest free run, shown in the notes


def provider(client=None) -> str:
    return config.script_provider(client)


def _s():
    return {'type': 'string'}


def _enum(values):
    return {'type': 'string', 'enum': list(dict.fromkeys(values))}


def _arr(items, lo=None, hi=None):
    d = {'type': 'array', 'items': items}
    if lo is not None:
        d['minItems'] = lo
    if hi is not None:
        d['maxItems'] = hi
    return d


def _obj(props):
    return {'type': 'object', 'properties': props, 'required': list(props)}


def shape(schema) -> str:
    """The schema as a short field guide for the prompt: names and types only."""
    t = schema.get('type')
    if 'enum' in schema:
        vals = schema['enum']
        return 'one of ' + '|'.join(v or '""' for v in vals) if len(vals) <= 12 else 'one of the allowed values'
    if t == 'object':
        return '{' + ', '.join(f'{k}: {shape(v)}' for k, v in schema['properties'].items()) + '}'
    if t == 'array':
        n = f' ({schema.get("minItems", 0)}-{schema["maxItems"]})' if 'maxItems' in schema else ''
        return f'[{shape(schema["items"])}]{n}'
    return {'string': 'str', 'boolean': 'bool', 'integer': 'int', 'number': 'num'}.get(t, 'str')


def anchor_words(narration: str) -> list:
    """The words of a narration, as written: the only values an anchor field may take."""
    words = [w.strip("-'’") for w in re.findall(r"[\w'’-]+", narration)]
    return list(dict.fromkeys(w for w in words if w))


def _kebab(text: str) -> str:
    return '-'.join(re.findall(r'[a-z0-9]+', (text or '').lower()))[:60]


def outline_schema(fact_ids, lo, hi):
    src = _arr(_enum(fact_ids)) if fact_ids else _arr(_s(), 0, 0)
    hook = _obj(dict(line=_s(), frame_one=_s(), answer=_s(), loop_line=_s()))
    beat = _obj(dict(role=_enum(OUTLINE_ROLES), narration=_s(), on_screen_text=_s(),
                     visual=_enum(['insert', 'graphic', 'split', 'character']), source_ids=src,
                     illustrative_numbers={'type': 'boolean'}))
    return _obj(dict(title=_s(), hook_promise=_s(), hook_options=_arr(hook, 3, 3), subject_settings=_arr(_s(), 0, 2),
                     beats=_arr(beat, lo, hi)))


def beat_schema(ob, index, cast_ids):
    from typing import get_args
    from .models import GraphicKind, HostSize, InsertEffect, LIGHT_WORDS, MotionRecipe, PoseName, Region, Setting, SfxName, STAGING_VERBS, Verb
    words = anchor_words(ob['narration'])
    w, w0, cast = _enum(words), _enum([''] + words), _enum(cast_ids)
    # pop_in is for diagrams (the host pops in at the edge to point); frame 1 always shows the host
    where = ['pop_in', 'scene'] if ob['visual'] == 'graphic' else ['scene']
    host = _obj(dict(where=_enum(where if index == 0 else where + ['off']),
                     side=_enum(['left', 'centre', 'right']), size=_enum(get_args(HostSize)), watching={'type': 'boolean'},
                     holds=_s(), doing=_s()))
    # rule 17 (nothing static for more than 2.5 s, about 6 words): a longer line asks for word-anchored changes, within
    # the rules' own ranges (0-3 actions, 0-4 effects)
    n_words = len(ob['narration'].split())
    need = 0 if n_words <= 6 else 1 if n_words <= 13 else 2
    staging = _obj(dict(setting=_enum(get_args(Setting)), setting_detail=_s(),
                        poses=_arr(_obj(dict(who=cast, pose=_enum(get_args(PoseName)))), 0, 2),
                        actions=_arr(_obj(dict(who=cast, verb=_enum(v for v in get_args(Verb) if v not in STAGING_VERBS),
                                               target=_s(), anchor=w)), need, 3),
                        props=_arr(_obj(dict(name=_s(), held_by=_enum([''] + list(cast_ids)), where=_s())), 0, 4)))
    props = dict(visual_intent=_s(), host=host, cast=_arr(cast, 0, 2), staging=staging)
    if ob['visual'] in ('insert', 'split'):
        props['insert'] = _obj(dict(key=_s(), scene=_s(), effects=_arr(_obj(dict(
            kind=_enum(get_args(InsertEffect)), anchor=w0, at=_enum(get_args(Region)), detail=_s())), min(need, 1), 4)))
    if ob['visual'] == 'split':
        props['split'] = _obj(dict(key=_s(), scene=_s(), labels=_arr(_s(), 2, 2), anchors=_arr(w, 2, 2)))
    if ob['visual'] == 'graphic':
        kinds = [k for k in get_args(GraphicKind) if k != 'two_paths' or LIGHT_WORDS.search(ob['narration'])]
        props['graphic'] = _obj(dict(kind=_enum(kinds), items=_arr(_s(), 1, 4), anchors=_arr(w, 0, 4), detail=_s()))
    props['motion'] = _arr(_obj(dict(recipe=_enum(get_args(MotionRecipe)), anchor=w0, detail=_s())), 1, 2)
    props['sfx'] = _arr(_obj(dict(name=_enum(get_args(SfxName)), anchor=w)), 0, 1)
    return _obj(props)


def per_beat(words, lo, hi) -> str:
    """Words per beat (after the hook) that add up to the target, as a range: '12-16'."""
    n = max(1, round((lo + hi) / 2) - 1)
    mid = max(6, round((words - 6) / n))
    return f'{max(5, mid - 2)}-{mid + 2}'


def _free_context(episode, style, facts) -> dict:
    from .models import FORMATS
    lo, hi = beat_range(episode.target_length)
    words = round(episode.target_length * style.words_per_second)
    aim_lo, aim_hi = max(lo, round(episode.target_length / 5.5)), min(hi, round(episode.target_length / 4))
    head = (f'Topic: {episode.topic}\nNiche: {episode.niche or "general"}\n'
            f'Audience: {episode.audience or "general English-speaking audience"}\n'
            f'Length: {episode.target_length} s: {lo}-{hi} beats (aim for {aim_lo}-{aim_hi}) and about {words} narration '
            f'words in total ({round(words * 0.85)}-{round(words * 1.15)}; the narrator reads {style.words_per_second} '
            f'words per second). The hook is at most 8 words; every other beat is one or two short sentences of about '
            f'{per_beat(words, aim_lo, aim_hi)} words.\nCast ids: {", ".join(style.cast_ids)} (lead is the host).')
    if facts:
        research = 'Research facts (cite by id):\n' + '\n'.join(f'{f.id}: {f.claim} <{f.url}>' for f in facts)
    else:
        research = 'No research facts were gathered. Do not state statistics or claims that need a source.'
    sl = episode.storyline
    story = (f'PICKED STORYLINE (format {sl.format}: {FORMATS[sl.format]}):\nHook: {sl.hook}\nPitch: {sl.pitch}\n'
             f'Twist: {sl.twist}\nPayoff: {sl.payoff}\nIt was pitched from earlier research: cite only the facts above, '
             'and drop any line of it they do not support.') if sl else ''
    brief = f'Topic: {episode.topic}\n' + (f'Story ({sl.format}): {sl.title}. {sl.pitch}' if sl else '')
    return dict(outline_user=f'{head}\n\n{research}\n\n{story}'.strip(), beat_head=brief,
                cast=list(style.cast_ids) or ['lead'], target=episode.target_length, wps=style.words_per_second)


def _normalise_outline(o):
    """Beat 1 says hook option 1 exactly; option 1's loop line is what the loop beat says."""
    beats, opts = o.get('beats') or [], o.get('hook_options') or []
    for b in beats:
        b['narration'] = ' '.join(str(b.get('narration', '')).split())
        b['on_screen_text'] = ' '.join(str(b.get('on_screen_text', '')).split())
        if not re.search(r'\w', b['on_screen_text']):     # a lone "?" or "!" is no on-screen text
            b['on_screen_text'] = ''
        b['source_ids'] = list(dict.fromkeys(b.get('source_ids') or []))
    if beats and opts:
        beats[0]['narration'] = ' '.join(opts[0]['line'].split())
        if beats[-1].get('role') == 'loop':
            opts[0]['loop_line'] = beats[-1]['narration']
    return o


PLACEHOLDER_DETAIL = dict(
    visual_intent='placeholder',
    shot=dict(host=dict(where='scene', side='centre', size='medium', holds='', doing='stands in the scene', to_camera=True),
              visual='character', insert=None, graphic=None, split=None, cast=['lead'],
              staging=dict(setting='street', setting_detail='', poses=[dict(who='lead', pose='standing')], actions=[], props=[]),
              motion=[dict(recipe='push_in', anchor='', detail='')]),
    sfx=[])


def _draft_dict(o, details) -> dict:
    beats = [dict(role=ob['role'], narration=ob['narration'], on_screen_text=ob['on_screen_text'],
                  visual_intent=d['visual_intent'], shot=d['shot'], sfx=d['sfx'], source_ids=ob['source_ids'],
                  illustrative_numbers=bool(ob['illustrative_numbers'])) for ob, d in zip(o['beats'], details)]
    opts = [dict(line=h['line'], frame_one=h['frame_one'], answer=h['answer'], loop_line=h['loop_line'])
            for h in o['hook_options']]
    return dict(title=o['title'], hook_promise=o['hook_promise'], hook_options=opts,
                subject_settings=list(o.get('subject_settings') or []), beats=beats)


def _episode_with(episode, facts, draft):
    updated = episode.model_copy(update=dict(
        title=draft.title, hook_promise=draft.hook_promise, sources=facts, hook_options=draft.hook_options, hook_pick=0,
        subject_settings=draft.subject_settings, beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)]))
    return Episode.model_validate(updated.model_dump())


def _is_outline_issue(issue) -> bool:
    return not re.fullmatch(r'b\d{2}', issue.where) or any(p in issue.message for p in OUTLINE_LEVEL)


def _outline_issues(episode, style, facts, o) -> list:
    """Outline-level errors of the real checker on the outline with placeholder staging, plus the writer's own limits
    the checker leaves to the gate (at most 2 graphics; the word count it warns about)."""
    try:
        draft = ScriptDraft.model_validate(_draft_dict(o, [json.loads(json.dumps(PLACEHOLDER_DETAIL))] * len(o['beats'])))
        ep = _episode_with(episode, facts, draft)
    except ValueError as exc:
        return [f'the outline does not fit the script shape: {str(exc)[:300]}']
    msgs = [f'{i.where}: {i.message}' for i in ep.check(style.cast_ids, style.words_per_second)
            if i.severity == 'error' and _is_outline_issue(i)]
    words, expected = ep.word_count(), episode.target_length * style.words_per_second
    if not 0.75 * expected <= words <= 1.2 * expected:
        n = max(1, len(o['beats']) - 1)
        each = round((expected - len(o['beats'][0]['narration'].split())) / n)
        msgs.append(f'beats: the narration has {words} words in total but the video needs about {round(expected)} '
                    f'({round(0.8 * expected)}-{round(1.15 * expected)}): ' +
                    (f'make every beat after the hook about {each} words (add a second short sentence that says what the '
                     'beat shows), keeping the facts as they are' if words < expected else
                     f'cut every beat after the hook to about {each} words'))
    from .models import _shares_word
    for k, h in enumerate(o.get('hook_options') or [], 1):   # a checker warning the writer can fix: the loop ties back
        if not _shares_word(h.get('loop_line', ''), h.get('line', '')):
            msgs.append(f'hook {k}: the loop line "{h.get("loop_line", "")}" must repeat a word of the hook "{h.get("line", "")}"'
                        + (" (it is also the last beat's narration)" if k == 1 else ''))
    return msgs


def _cap_graphics(o) -> list:
    """Rule 13, at most 2 diagrams per video: later graphic beats become painted scenes. Returns notes."""
    notes, seen = [], 0
    for k, b in enumerate(o['beats'], 1):
        if b['visual'] == 'graphic':
            seen += 1
            if seen > 2:
                b['visual'] = 'insert'
                notes.append(f'b{k:02d}: a third diagram; shown as a painted scene (at most 2 diagrams per video)')
    return notes


def _outline_call(ctx, schema, cache, ledger, previous=None, issues=(), attempt=0):
    from . import llm
    user = ctx['outline_user']
    if previous is not None:
        user += ('\n\nYOUR PREVIOUS OUTLINE:\n' + json.dumps(previous, ensure_ascii=False, separators=(',', ':'))
                 + '\n\nTHE CHECKER FOUND THESE PROBLEMS. Fix every one and keep everything else:\n'
                 + '\n'.join(f'- {m}' for m in issues))
    user += '\n\nAnswer with JSON only: ' + shape(schema)
    try:
        return llm.generate_json(SCRIPT_RULES_COMPACT_OUTLINE, user, schema, OUTLINE_MAX_TOKENS,
                                 temperature=0.4 if previous is None else 0.3, seed=21 + attempt, cache=cache,
                                 ledger=ledger, step='script', detail='outline' + (f' (repair {attempt})' if attempt else ''))
    except llm.LLMError as exc:
        raise ScriptError(f'The local model could not write the outline: {exc}') from None


def _placement(i, visual='insert'):
    sizes = SIZES.get(visual, SIZES['insert'])
    return SIDES[i % len(SIDES)], sizes[i % len(sizes)]


def _beat_prompt(ctx, o, i, schema, previous=None, issues=()):
    ob = o['beats'][i]
    side, size = _placement(i, ob['visual'])
    lines = '\n'.join(f'b{k + 1:02d} {b["role"]} [{b["visual"]}]: {b["narration"]}' for k, b in enumerate(o['beats']))
    user = (f'{ctx["beat_head"]}\nThe whole video:\n{lines}\n\n'
            f'STAGE b{i + 1:02d} (role {ob["role"]}, picture type {ob["visual"]}).\nNarration: "{ob["narration"]}"\n'
            f'On-screen text: "{ob["on_screen_text"] or "(none)"}"\n'
            f'Suggested host placement: {side} {size} (pick another if the picture needs it).\n'
            f'Cast ids: {", ".join(ctx["cast"])}. Anchor fields take only words of this narration; put the actions, '
            'effects and motion anchors on different words spread across the line (a change about every 6 words).')
    if previous is not None:
        user += ('\n\nYOUR PREVIOUS ANSWER:\n' + json.dumps(previous, ensure_ascii=False, separators=(',', ':'))
                 + '\n\nTHE CHECKER FOUND THESE PROBLEMS. Fix every one and keep everything else:\n'
                 + '\n'.join(f'- {m}' for m in issues))
    user += '\n\nAnswer with JSON only: ' + shape(schema)
    return user


def _detail_from(raw, ob, cast_ids):
    """The model's beat answer as DraftBeat fields. Only mechanical fixes: "lead" in cast when the host is on screen,
    one pose per cast member, no action or held prop for someone not in the picture, kebab-case scene keys, graphic
    anchors that do not pair with the items are dropped (the checker allows none)."""
    h = raw['host']
    cast = [c for c in dict.fromkeys(raw.get('cast') or []) if c in cast_ids]
    if h['where'] != 'off' and 'lead' not in cast:
        cast.insert(0, 'lead')
    st = raw['staging']
    poses = {}
    for p in st.get('poses') or []:
        if p['who'] in cast and p['who'] not in poses:
            poses[p['who']] = p['pose']
    poses = [dict(who=c, pose=poses.get(c, 'standing')) for c in cast]
    insert = graphic = split = None
    if ob['visual'] in ('insert', 'split') and raw.get('insert'):
        ins = raw['insert']
        insert = dict(key=_kebab(ins['key']) or _kebab(ins['scene'])[:40], scene=ins['scene'],
                      effects=[dict(kind=e['kind'], anchor=e['anchor'], at=e['at'], detail=e['detail'])
                               for e in ins.get('effects') or []][:4])
    if ob['visual'] == 'split' and raw.get('split'):
        sp = raw['split']
        split = dict(key=_kebab(sp['key']) or _kebab(sp['scene'])[:40], scene=sp['scene'], labels=list(sp['labels']),
                     anchors=list(sp.get('anchors') or []))
    if ob['visual'] == 'graphic' and raw.get('graphic'):
        g = raw['graphic']
        anchors = list(g.get('anchors') or [])
        graphic = dict(kind=g['kind'], items=list(g['items']), anchors=anchors if len(anchors) == len(g['items']) else [],
                       detail=g.get('detail', ''))
    setting, detail = st['setting'], ' '.join(str(st.get('setting_detail', '')).split())
    if setting == 'other' and not detail and insert:
        detail = insert['key'].replace('-', ' ')
    shot = dict(host=dict(where=h['where'], side=h['side'], size=h['size'], holds=h.get('holds', ''), doing=h['doing'],
                          to_camera=not h.get('watching', False)),
                visual=ob['visual'], insert=insert, graphic=graphic, split=split, cast=cast,
                staging=dict(setting=setting, setting_detail=detail if setting == 'other' else '', poses=poses,
                             actions=[a for a in st.get('actions') or [] if a['who'] in cast][:3],
                             props=[dict(name=p['name'], held_by=p['held_by'] if p['held_by'] in cast else '', where=p['where'])
                                    for p in st.get('props') or []][:4]),
                motion=[dict(recipe=m['recipe'], anchor=m['anchor'], detail=m['detail']) for m in raw['motion']][:2])
    return dict(visual_intent=raw['visual_intent'], shot=shot,
                sfx=[dict(name=s['name'], anchor=s['anchor']) for s in raw.get('sfx') or []][:1])


def _vary_placement(details):
    """Rule 13: no two beats in a row with the same picture type and the same host spot and size."""
    for prev, cur in zip(details, details[1:]):
        a, b = prev['shot'], cur['shot']
        if (a['visual'] == b['visual'] and a['host']['where'] == b['host']['where'] != 'off'
                and (a['host']['side'], a['host']['size']) == (b['host']['side'], b['host']['size'])):
            b['host']['side'] = SIDES[(SIDES.index(b['host']['side']) + 1) % len(SIDES)]


def _stage_beats(ctx, o, indices, cache, ledger, previous=None, issues=None, attempt=0):
    """{index: raw answer or LLMError} for the given beats, in parallel."""
    from . import llm
    prompts = []
    for i in indices:
        schema = beat_schema(o['beats'][i], i, ctx['cast'])
        prev = (previous or {}).get(i)
        msgs = (issues or {}).get(i, ())
        prompts.append(dict(system=SCRIPT_RULES_COMPACT_BEAT, user=_beat_prompt(ctx, o, i, schema, prev, msgs),
                            json_schema=schema, max_tokens=BEAT_MAX_TOKENS, temperature=0.2, seed=31 + attempt,
                            cache=cache, ledger=ledger, step='script',
                            detail=f'b{i + 1:02d} staging' + (f' (repair {attempt})' if attempt else '')))
    return dict(zip(indices, llm.generate_batch(prompts, as_json=True)))


def write_free(episode, style, facts, base, ledger, regenerate=False):
    """The two-stage local writer with the bounded repair loop. Returns (ScriptDraft, cache path, cached)."""
    from .cache import for_episode
    cache = for_episode(base)
    ctx = _free_context(episode, style, facts)
    model = config.ollama_model()
    key = digest([FREE_SCRIPT_VERSION, PROMPT_VERSION, model, SCRIPT_RULES_COMPACT, ctx, FREE_REPAIR_ROUNDS])
    path = _cache(base, 'script', key)
    if path.exists() and not regenerate:
        data = json.loads(path.read_text())
        LAST_RUN['script'] = dict(cached=True, timings=data.get('timings', {}), repairs=data.get('repairs', []),
                                  remaining=data.get('remaining', []))
        return ScriptDraft.model_validate(data['draft']), path, True
    lo, hi = beat_range(episode.target_length)
    oschema = outline_schema([f.id for f in facts], lo, hi)
    times, log = {}, []
    t0 = time.time()
    o = _normalise_outline(_outline_call(ctx, oschema, cache, ledger))
    times['outline_s'] = round(time.time() - t0, 1)
    for attempt in range(1, OUTLINE_REPAIRS + 1):
        issues = _outline_issues(episode, style, facts, o)
        if not issues:
            break
        t = time.time()
        log.append(dict(stage='outline', attempt=attempt, issues=issues))
        o = _normalise_outline(_outline_call(ctx, oschema, cache, ledger, o, issues, attempt))
        log[-1]['seconds'] = round(time.time() - t, 1)
    times['outline_repairs_s'] = round(time.time() - t0 - times['outline_s'], 1)
    capped = _cap_graphics(o)
    if capped:
        log.append(dict(stage='outline', attempt=0, issues=capped, seconds=0))
    t = time.time()
    raws = _stage_beats(ctx, o, list(range(len(o['beats']))), cache, ledger)
    failed = {i: [f'the answer could not be used ({r})'] for i, r in raws.items() if isinstance(r, Exception)}
    if failed:                                    # one more try for a call that failed outright
        raws.update(_stage_beats(ctx, o, list(failed), cache, ledger, issues=failed, attempt=1))
        bad = [f'b{i + 1:02d}' for i, r in raws.items() if isinstance(r, Exception)]
        if bad:
            raise ScriptError(f'The local model could not stage {", ".join(bad)}; nothing was saved.')
    times['details_s'] = round(time.time() - t, 1)
    details = [_detail_from(raws[i], o['beats'][i], ctx['cast']) for i in range(len(o['beats']))]
    _vary_placement(details)
    errors = []
    for rnd in range(1, FREE_REPAIR_ROUNDS + 2):
        draft = ScriptDraft.model_validate(_draft_dict(o, details))
        ep = _episode_with(episode, facts, draft)
        errors = [i for i in ep.check(style.cast_ids, style.words_per_second) if i.severity == 'error']
        if not errors or rnd > FREE_REPAIR_ROUNDS:
            break
        t = time.time()
        entry = dict(stage='repair', round=rnd, errors=[str(e) for e in errors])
        outline_errs = [e for e in errors if _is_outline_issue(e)]
        restage = []
        if outline_errs:
            before = [(b['role'], b['narration'], b['visual']) for b in o['beats']]
            o = _normalise_outline(_outline_call(ctx, oschema, cache, ledger, o, [f'{e.where}: {e.message}' for e in outline_errs],
                                                 10 + rnd))
            after = [(b['role'], b['narration'], b['visual']) for b in o['beats']]
            restage = [i for i in range(len(after)) if i >= len(before) or after[i] != before[i]]
            details = (details + [None] * len(after))[:len(after)]
        beat_errs = {}
        for e in errors:
            if not _is_outline_issue(e):
                i = int(e.where[1:]) - 1
                if i < len(o['beats']) and i not in restage:
                    beat_errs.setdefault(i, []).append(e.message)
        if restage:
            got = _stage_beats(ctx, o, restage, cache, ledger, attempt=rnd)
            for i, r in got.items():
                if isinstance(r, Exception):
                    details[i] = details[i] or json.loads(json.dumps(PLACEHOLDER_DETAIL))
                else:
                    raws[i] = r
                    details[i] = _detail_from(r, o['beats'][i], ctx['cast'])
        if beat_errs:
            got = _stage_beats(ctx, o, sorted(beat_errs), cache, ledger, previous={i: raws.get(i) for i in beat_errs},
                               issues=beat_errs, attempt=rnd)
            for i, r in got.items():
                if not isinstance(r, Exception):
                    raws[i] = r
                    details[i] = _detail_from(r, o['beats'][i], ctx['cast'])
        _vary_placement(details)
        entry.update(outline=bool(outline_errs), restaged=[f'b{i + 1:02d}' for i in restage],
                     beats=[f'b{i + 1:02d}' for i in sorted(beat_errs)], seconds=round(time.time() - t, 1))
        log.append(entry)
    times['repairs_s'] = round(sum(e.get('seconds', 0) for e in log if e['stage'] == 'repair'), 1)
    times['total_s'] = round(time.time() - t0, 1)
    remaining = [str(e) for e in errors]
    _write_json(path, dict(draft=draft.model_dump(), created=time.strftime('%Y-%m-%dT%H:%M:%S'), model=model,
                           provider='ollama', prompt_version=PROMPT_VERSION, writer=FREE_SCRIPT_VERSION,
                           outline=o, timings=times, repairs=log, remaining=remaining, cache=dict(cache.stats)))
    LAST_RUN['script'] = dict(cached=False, timings=times, repairs=log, remaining=remaining, cache=dict(cache.stats))
    return draft, path, False


def research_free(episode, base, ledger, regenerate=False):
    """research() for SCRIPT_PROVIDER=ollama: (facts, cache path, cached)."""
    from . import search
    from .cache import for_episode
    cache = for_episode(base)
    focus = episode.meta.get('research_focus') or {}
    key = digest([FREE_RESEARCH_VERSION, config.ollama_model(), episode.topic, episode.niche, episode.audience, focus,
                  search.QUERY_TEMPLATES, search.FACT_SYSTEM])
    path = _cache(base, 'research', key)
    if path.exists() and not regenerate:
        data = json.loads(path.read_text())
        LAST_RUN['research'] = dict(cached=True, timings=data.get('timings', {}), notes=data.get('notes', []))
        return [Source.model_validate(f) for f in data['facts']], path, True
    timings, notes = {}, []
    facts = search.research_topic(episode.topic, episode.niche, episode.audience, cache, ledger=ledger, focus=focus,
                                  fresh=regenerate, timings=timings, notes=notes)
    LAST_RUN['research'] = dict(cached=False, timings=timings, notes=notes, cache=dict(cache.stats))
    if not facts:
        raise ScriptError('Research found no facts on government, university or peer-reviewed pages, so nothing was '
                          'written. Re-run with --regenerate research later (the search engines may have refused this '
                          'machine for a while), or use --facts with facts checked by hand.')
    _write_json(path, dict(facts=[f.model_dump() for f in facts], created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                           model=config.ollama_model(), provider='ollama', version=FREE_RESEARCH_VERSION,
                           timings=timings, notes=notes, cache=dict(cache.stats)))
    return facts, path, False


def free_notes(kind: str) -> list:
    """Timing and cache lines for the notes of the latest free research or script run."""
    run = LAST_RUN.get(kind) or {}
    if not run:
        return []
    t = run.get('timings', {})
    if kind == 'research':
        out = [f'  research timing: queries {t.get("queries_s", "-")} s, searches done {t.get("search_s", "-")} s, '
               f'pages fetched {t.get("fetch_s", "-")} s, facts read {t.get("facts_s", "-")} s, total {t.get("total_s", "-")} s '
               f'(wall clock from the start; the stages overlap)' + (' [from the research cache]' if run.get('cached') else '')]
        out += [f'  {n}' for n in run.get('notes', [])]
        if t.get('engines'):
            out.append(f'  search engines: {", ".join(t["engines"])}; {t.get("reputable_pages", 0)} reputable pages, '
                       f'{t.get("pages_with_text", 0)} with text')
    else:
        out = [f'  script timing: outline {t.get("outline_s", "-")} s, outline repairs {t.get("outline_repairs_s", "-")} s, '
               f'beat staging {t.get("details_s", "-")} s, repairs {t.get("repairs_s", "-")} s, total {t.get("total_s", "-")} s'
               + (' [from the script cache]' if run.get('cached') else '')]
        for e in run.get('repairs', []):
            if e['stage'] == 'outline' and not e['attempt']:
                out += [f'  {n}' for n in e['issues']]
            elif e['stage'] == 'outline':
                out.append(f'  outline repair {e["attempt"]}: {len(e["issues"])} issue(s), {e.get("seconds", "-")} s')
            else:
                out.append(f'  repair round {e["round"]}: {len(e["errors"])} error(s); outline {"yes" if e["outline"] else "no"}, '
                           f'beats {", ".join(e["beats"] + e["restaged"]) or "none"}, {e["seconds"]} s')
        if run.get('remaining'):
            out.append(f'  {len(run["remaining"])} error(s) remain after {FREE_REPAIR_ROUNDS} repair rounds: shown at the script gate')
    if run.get('cache'):
        c = run['cache']
        out.append(f'  cache: search {c["search_hit"]} hit / {c["search_miss"]} miss, pages {c["page_hit"]} hit / '
                   f'{c["page_miss"]} miss, model {c["llm_hit"]} hit / {c["llm_miss"]} miss')
    return out


def checked_facts(path) -> list:
    """Facts read by hand from their sources (each with the exact quote), in place of the research call: a JSON list
    of {claim, url, title, quote}. Ids are s1, s2... in file order."""
    rows = json.loads(Path(path).read_text())
    return [Source(id=f's{k}', claim=r['claim'], url=r['url'], title=r.get('title', ''), quote=r.get('quote', ''))
            for k, r in enumerate(rows, 1)]


def generate(episode, style, base, ledger, client=None, use_research=True, regenerate=(), facts_file=None):
    """Fill episode.title, hook_promise, beats and sources. Returns (episode, notes).

    SCRIPT_PROVIDER=ollama (default): free and local; `client` is ignored and no key is needed. The model is unloaded
    when the step ends so the next step (FLUX) gets the memory. SCRIPT_PROVIDER=anthropic: the paid path as before."""
    if not episode.storyline:
        raise ScriptError(f'No storyline picked for {episode.slug}: story before script (stick make --episode '
                          f'{episode.slug} pitches three; stick approve --gate storyline --story N picks one).')
    prov = provider(client)
    client = (client or make_client()) if prov == 'anthropic' else None
    LAST_RUN.clear()
    try:
        return _generate(episode, style, base, ledger, client, prov, use_research, regenerate, facts_file)
    finally:
        if prov == 'ollama' and config.ollama_unload_after_step():
            from . import llm
            llm.unload()


def _generate(episode, style, base, ledger, client, prov, use_research, regenerate, facts_file):
    notes = []
    facts = []
    if facts_file or use_research:
        if facts_file:                                 # checked by hand: no research call
            facts = checked_facts(facts_file)
            notes.append(f'facts: {len(facts)} checked by hand from their sources ({Path(facts_file).name}); no research call')
        else:
            facts, path, cached = research(episode, base, ledger, client, 'research' in regenerate)
            notes.append(f'research: {len(facts)} cited facts ({"cache" if cached else "new"}: {path.name})')
            notes += free_notes('research')
        facts, dropped = keep_reputable(facts)
        for f in dropped:
            notes.append(f'  dropped (not government, university or peer-reviewed): {f.url}')
        if not facts:
            raise ScriptError('No fact came from a government, university or peer-reviewed source, so no script was '
                              'written. Re-run research, or pick a topic with better sources.')
    draft, path, cached = write(episode, style, facts, base, ledger, client, 'script' in regenerate)
    notes.append(f'script: {len(draft.beats)} beats ({"cache" if cached else "new"}: {path.name})')
    notes += free_notes('script')
    model = config.SCRIPT_MODEL if prov == 'anthropic' else config.ollama_model()
    script_meta = dict(model=model, prompt_version=PROMPT_VERSION, research=use_research,
                       generated=time.strftime('%Y-%m-%dT%H:%M:%S'))
    if prov == 'ollama':
        script_meta |= dict(provider='ollama', writer=FREE_SCRIPT_VERSION)
    updated = episode.model_copy(update=dict(
        title=draft.title, hook_promise=draft.hook_promise, sources=facts,
        hook_options=draft.hook_options, hook_pick=0, subject_settings=draft.subject_settings,
        beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)],
        approvals={k: v for k, v in episode.approvals.items() if k != 'script'},
        meta=episode.meta | dict(script=script_meta)))
    return Episode.model_validate(updated.model_dump()), notes
