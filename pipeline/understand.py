"""Topic understanding (user 2026-11-14), the first step of every episode, before research. Topics are typed casually
on a phone, with typos and claims inside them ("how is crude oil naturally forming in th earth and not comig from the
dinosaurs"). The pipeline reads what is meant and never stops to ask about the topic.

1. `interpret(topic)`: one local-model call (cached): the real question with the typos fixed, the claims inside the
   topic, the misconception a viewer likely holds, the most interesting true angle, a clean title and slug, and the
   picture subjects that show the myth (`myth_subjects`: "dinosaur"). The typed text is never the title or the slug.
2. `verify(interpretation, facts)`: after research (which also searches each claim), each claim is labelled
   supported, mainstream-disputed, fringe/false or unverified from the cited facts, one local-model call (cached).
3. The story adapts: `brief()` goes into the storyline and script prompts. True claims are told straight; a myth or a
   fringe idea is shown as an idea, framed honestly ("some people claim X; here is what geologists found"), never
   stated as fact; `preferred_formats()` steers the storyline pick toward myth_vs_fact, mystery or revelation.
4. Pictures match the story: QA's `story_match` check (pipeline/qa.py) fails a beat that shows a myth subject without
   marking it as the myth, and the self-repair loop adds the MYTH label (pipeline/repair.py).

If the local model is down the step still returns a usable interpretation (`fallback`): the question is the tidied
topic, no claims. The episode goes on.
"""
from __future__ import annotations

import json
import re

UNDERSTAND_VERSION = 'understand-v1'
VERDICTS = ('supported', 'mainstream-disputed', 'fringe/false', 'unverified')
MYTHY = ('mainstream-disputed', 'fringe/false')
PREFERRED = ('myth_vs_fact', 'mystery', 'revelation')

SYSTEM = """You read a video topic typed quickly on a phone and say what it means. The topic may have typos, missing
words and claims inside it. Fix the typos and write the real question being asked as one clear sentence. List every
claim the topic states or assumes, each as a short plain sentence (for "how does X happen and not Y" the claims are
"X happens" and "Y is not the case"; a topic with no claim has none). Say which common belief a viewer most likely
holds that the video should correct (the misconception), and the most interesting TRUE angle for a 45-second
explainer. Give a clean title of at most 60 characters (never the typed text; no clickbait) and the picture subjects
that would show the misconception on screen (one or two nouns, e.g. "dinosaur"). Do not judge whether the claims are
true here."""

SCHEMA = dict(type='object', required=['question', 'title', 'claims', 'misconception', 'angle', 'myth_subjects'],
              properties=dict(question=dict(type='string'), title=dict(type='string'),
                              claims=dict(type='array', items=dict(type='string')),
                              misconception=dict(type='string'), angle=dict(type='string'),
                              myth_subjects=dict(type='array', items=dict(type='string'))))

VERIFY_SYSTEM = """You check claims against research facts. For each claim answer with exactly one verdict:
"supported" (the facts say it is true), "mainstream-disputed" (the facts show scientists or experts disagree, or it
is partly true), "fringe/false" (the facts contradict it, or it is a myth or a fringe idea the facts reject), or
"unverified" (no fact speaks to it). Use only the facts given; cite the fact ids you used; give a one-line reason."""

VERIFY_SCHEMA = dict(type='object', required=['claims'], properties=dict(claims=dict(type='array', items=dict(
    type='object', required=['claim', 'verdict', 'fact_ids', 'why'],
    properties=dict(claim=dict(type='string'), verdict=dict(type='string', enum=list(VERDICTS)),
                    fact_ids=dict(type='array', items=dict(type='string')), why=dict(type='string'))))))

STOP = set('a an the and or but of in on at to for from by with is are was were be been how why what when where who '
           'does do did it its this that these those as into not no th'.split())


def slug_for(text: str, limit: int = 48) -> str:
    """A slug from the clean title: lowercase words, hyphens, at most `limit` characters, cut at a word."""
    words = re.sub(r'[^a-z0-9]+', ' ', str(text or '').lower()).split()
    out = ''
    for w in words:
        nxt = f'{out}-{w}' if out else w
        if len(nxt) > limit:
            break
        out = nxt
    return out or 'episode'


def fallback(topic: str) -> dict:
    """No model: the topic tidied (spaces, a capital, a question mark), no claims. Still never the raw text as slug
    when a title can be made from it."""
    q = ' '.join(str(topic or '').split()).strip(' ?.!')
    q = (q[:1].upper() + q[1:] + '?') if q else 'What is this about?'
    title = q.rstrip('?')[:60]
    return dict(question=q, title=title, slug='', claims=[], misconception='', angle='', myth_subjects=[],
                source='fallback', version=UNDERSTAND_VERSION, topic=topic)      # no slug: the episode keeps its own


def clean(data: dict, topic: str) -> dict:
    """The model's answer checked: strings trimmed, at most 4 claims, a title of at most 60 characters."""
    fb = fallback(topic)
    q = ' '.join(str(data.get('question') or '').split()) or fb['question']
    title = ' '.join(str(data.get('title') or '').split()).strip('"\' ')[:60] or fb['title']
    if title == ' '.join(str(topic).split()):
        title = q.rstrip('?')[:60]                     # the typed text, as typed, is never the title
    claims = [' '.join(str(c).split()) for c in (data.get('claims') or []) if str(c).strip()][:4]
    subjects = [s.strip().lower() for s in (data.get('myth_subjects') or []) if str(s).strip()][:3]
    return dict(question=q if q.endswith('?') else q.rstrip('.') + '?', title=title, slug=slug_for(title), claims=claims,
                misconception=' '.join(str(data.get('misconception') or '').split()),
                angle=' '.join(str(data.get('angle') or '').split()), myth_subjects=subjects,
                source='model', version=UNDERSTAND_VERSION, topic=topic)


def interpret(topic: str, cache=None, ledger=None, generate=None) -> dict:
    """The interpretation of a typed topic (see the module docstring). `generate(system, user, schema)` -> dict is the
    local model (a stand-in in tests); a failure gives `fallback(topic)`."""
    if generate is None:
        from . import config, llm
        if config.script_provider() != 'ollama':      # a free local step only: no paid call for it
            out = fallback(topic)
            out['note'] = 'topic understanding runs on the local model; this episode uses the topic as typed, tidied'
            return out
        generate = lambda s, u, sc: llm.generate_json(s, u, sc, 700, cache=cache, ledger=ledger, step='understand',
                                                      detail='topic understanding', temperature=0.1)
    try:
        data = generate(SYSTEM, f'Typed topic: {topic}', SCHEMA)
        if not isinstance(data, dict) or not str(data.get('question') or '').strip():
            raise ValueError('no question in the answer')
    except Exception as e:                            # the model is down: the episode goes on with the tidied topic
        out = fallback(topic)
        out['note'] = f'the local model did not answer ({type(e).__name__}); the topic is used as typed, tidied'
        return out
    return clean(data, topic)


def verify(interp: dict, facts: list, cache=None, ledger=None, generate=None) -> list:
    """[{claim, verdict, fact_ids, why}] for the interpretation's claims, from the research facts (Source objects)."""
    claims = list(interp.get('claims') or [])
    if not claims:
        return []
    known = {f.id for f in facts}
    if not facts:
        return [dict(claim=c, verdict='unverified', fact_ids=[], why='research found no facts') for c in claims]
    if generate is None:
        from . import config, llm
        if config.script_provider() != 'ollama' or interp.get('source') != 'model':
            return [dict(claim=c, verdict='unverified', fact_ids=[], why='not checked (no local model)') for c in claims]
        generate = lambda s, u, sc: llm.generate_json(s, u, sc, 900, cache=cache, ledger=ledger, step='verify',
                                                      detail='claim verdicts', temperature=0.1)
    user = ('Claims:\n' + '\n'.join(f'- {c}' for c in claims) + '\n\nResearch facts:\n'
            + '\n'.join(f'{f.id}: {f.claim}' for f in facts))
    try:
        data = generate(VERIFY_SYSTEM, user, VERIFY_SCHEMA) or {}
    except Exception as e:
        return [dict(claim=c, verdict='unverified', fact_ids=[], why=f'not checked: the local model did not answer '
                     f'({type(e).__name__})') for c in claims]
    answers = [x for x in data.get('claims') or [] if isinstance(x, dict)]
    got = {' '.join(str(x.get('claim', '')).split()).lower(): x for x in answers}
    out = []
    for k, c in enumerate(claims):
        x = got.get(c.lower()) or (answers[k] if k < len(answers) else {})       # by its text, else by its place
        verdict = x.get('verdict') if x.get('verdict') in VERDICTS else 'unverified'
        ids = [i for i in x.get('fact_ids') or [] if i in known]
        if verdict in ('supported', 'mainstream-disputed', 'fringe/false') and not ids:
            verdict, why = 'unverified', 'no cited fact backs the verdict'
        else:
            why = ' '.join(str(x.get('why') or '').split())[:240]
        out.append(dict(claim=c, verdict=verdict, fact_ids=ids, why=why))
    return out


def has_myth(interp: dict) -> bool:
    """True when the story corrects something: a claim judged disputed or false, or a named misconception."""
    return bool(interp.get('misconception')) or any(v.get('verdict') in MYTHY for v in interp.get('verdicts') or [])


def preferred_formats(interp: dict, allowed: list) -> list:
    """The allowed formats with myth_vs_fact, mystery and revelation first when the topic corrects a myth."""
    if not has_myth(interp):
        return list(allowed)
    return [f for f in PREFERRED if f in allowed] + [f for f in allowed if f not in PREFERRED]


def question(episode) -> str:
    """What research and the writer work on: the interpreted question, else the topic."""
    interp = (episode.meta or {}).get('interpretation') or {}
    return interp.get('question') if interp.get('source') == 'model' and interp.get('question') else episode.topic


def brief(interp: dict) -> str:
    """The interpretation as instructions for the storyline and script writers."""
    if not interp:
        return ''
    lines = [f'What the viewer asked (typed as "{interp.get("topic", "")}"): {interp.get("question", "")}']
    # 2026-11-14: the local model's angle said "marine bacteria (zooplankton)" and "10-25 million years" with no
    # source: the angle and misconception steer the story; only the research facts may be stated
    if interp.get('angle'):
        lines.append(f'Angle to aim for (a direction only, not a source: state nothing from it that no research fact '
                     f'says): {interp["angle"]}')
    if interp.get('misconception'):
        lines.append(f'Likely misconception to correct (as the viewer holds it): {interp["misconception"]}')
    verdicts = interp.get('verdicts') or []
    if verdicts:
        lines.append('Claims inside the topic, checked against the research:')
        lines += [f'- "{v["claim"]}": {v["verdict"]}' + (f' ({", ".join(v["fact_ids"])}; {v["why"]})' if v.get('fact_ids') else
                  f' ({v.get("why", "")})') for v in verdicts]
    if has_myth(interp):
        subjects = ', '.join(interp.get('myth_subjects') or []) or 'the myth'
        lines.append('RULES: tell the true story. Show the myth or fringe idea only AS an idea, framed honestly ("some '
                     'people think X; here is what scientists actually found"). Never state a disputed, false or '
                     'unverified claim as fact in the narration, the captions or any on-screen text. A beat whose '
                     f'picture shows the myth ({subjects}) must mark it on screen: a label "MYTH" or a split labelled '
                     '"Myth" and "Fact".')
    else:
        lines.append('The claims hold: tell it straight.')
    return '\n'.join(lines)


def view(interp: dict) -> dict:
    """For the episode page: question, claims with verdicts, misconception, angle, title."""
    return dict(question=interp.get('question', ''), title=interp.get('title', ''), angle=interp.get('angle', ''),
                misconception=interp.get('misconception', ''),
                claims=[dict(claim=v['claim'], verdict=v['verdict'], why=v.get('why', ''), fact_ids=v.get('fact_ids', []))
                        for v in interp.get('verdicts') or [dict(claim=c, verdict='not checked yet') for c in interp.get('claims') or []]],
                note=interp.get('note', ''))


def topic_text(episode, with_brief: bool = True) -> str:
    """The "Topic:" line of the writers' prompts: the interpreted question and its brief, or the topic as before (an
    episode made before topic understanding gets exactly the old prompt, so its caches still hold)."""
    interp = (episode.meta or {}).get('interpretation')
    if not interp or interp.get('source') != 'model':       # not understood: exactly the old prompt
        return f'Topic: {episode.topic}'
    return f'Topic: {interp.get("question") or episode.topic}' + (f'\n{brief(interp)}' if with_brief else '')


def dumps(interp: dict) -> str:
    return json.dumps(interp, ensure_ascii=False)
