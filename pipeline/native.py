"""Claude-native writing (user 2026-11-19, SCRIPT_PROVIDER=claude-code only; the 9B path is unchanged): "Give Claude the
verified facts, the core answer and the story format, and let IT plan the outline: which facts to use, the order, the
twist and the hook. Drop the code-picked twist and one-fact-per-beat assignment for this path."

    plan(...)   one call: Claude picks the number of beats (the story format's roles for that count stay the gate's),
                the facts of each beat, their order, the twist, the hook's fact and how it opens a gap, and says which
                facts it leaves out. The plan's claims get the same claim checks as the code plan (one call).
    write(...)  N_DRAFTS whole scripts from the plan at once; every line of each goes through the hard rules in one
                call per script; the best is kept; Claude revises only the failing lines (all of them in one call,
                REVISE_ROUNDS at most) and trims to the word budget in the same loop; a side beat may be cut.

The hard rules (code first, then the batched judge): the core answer is said in the payoff; every line that states a
fact passes the fact check against its beat's facts (a beat with none states none); no two lines say the same thing
(nomic-embed-text, pipeline/embed.py); no staging direction; the hook opens a gap (a question, a surprising contrast or
a stake), names the subject, never restates the title or the topic ("Picture this: the real origin of crude." fails),
never gives the answer away; every middle line moves the story toward the core answer (a side fact is cut); the word
budget: the narration is at most the target seconds times the measured speaking rate, within BUDGET_TOL.
"""
from __future__ import annotations

import json
import re

NATIVE_VERSION = 'native-v1'
N_DRAFTS = 4
REVISE_ROUNDS = 2
TRIM_ROUNDS = 2
BUDGET_TOL = 0.10                 # the narration stays within +-10 % of the word budget (user 2026-11-19)
GATE_OVER = 0.20                  # and the gate stops a script more than 20 % over it
MIN_BEATS, MAX_BEATS = 7, 11
MAX_LINE_WORDS = 26
HOOK_WORDS = (3, 10)
KINDS = ('fact', 'question', 'transition', 'reaction', 'callback', 'myth')
ANGLES = ('a sharp question the viewer cannot answer yet', 'a surprising contrast between what people think and what is so',
          'a stake: why the answer matters to the viewer', 'the strongest gap you can find in the facts')

PLAN_SYSTEM = """You plan a narrated vertical explainer Short (about 45 seconds, a host talking to the viewer) before any
narration is written. You get the video's question, its core answer, the misconception it corrects (if any), its story
format with the roles of its beats for each possible number of beats, the word budget and the verified research facts.

Decide:
- how many beats (one of the options given) and, for each beat in order, its kind and the facts it stands on;
- which facts to use and in what order, so every beat moves the story one step closer to the core answer: what the
  viewer believes, why that breaks, the clues that build, the twist, then the answer. Leave out a fact that is true but
  does not move the story toward the answer (a side fact), and say why;
- the TWIST: the fact that most changes how the viewer sees the answer; it goes on the twist beat;
- the HOOK: the fact it teases and how it opens a gap (a question, a surprising contrast, or a stake). The hook never
  restates the title or the topic, and never gives the answer away.

Kinds: "fact" (the beat says what its facts say), "question" (asks what a later beat answers), "transition" (moves the
story on), "reaction" (the host reacts to the line before), "callback" (the loop: reuses a word of the hook), "myth"
(what many people believe, said as a belief). The hook is a "question" (it may tease its fact), the loop a "callback",
the twist and the payoff are "fact" beats, and the payoff's facts back the core answer. A fact beat's point is ONE plain
sentence of at most 16 words saying only what its facts say, for a viewer of 12: never a group, cause, number, place
or time its facts do not state, and never "X" where the facts say "some X". No two points alike. Use only fact ids that
are given."""


def plan_schema():
    beat = dict(type='object', required=['kind', 'fact_ids', 'point'], properties=dict(
        kind=dict(type='string', enum=list(KINDS)), fact_ids=dict(type='array', items=dict(type='string'), maxItems=3),
        point=dict(type='string')))
    return dict(type='object', required=['beats', 'twist_fact', 'hook_fact', 'hook_gap', 'left_out'], properties=dict(
        beats=dict(type='array', items=beat, minItems=MIN_BEATS, maxItems=MAX_BEATS),
        twist_fact=dict(type='string'), hook_fact=dict(type='string'), hook_gap=dict(type='string'),
        left_out=dict(type='array', items=dict(type='object', required=['id', 'why'], properties=dict(
            id=dict(type='string'), why=dict(type='string'))))))


WRITE_SYSTEM = """You write the whole narration of a narrated vertical explainer Short from its plan: one line per beat,
in order, in the host's voice: talking to a friend, vivid, with rhythm when read aloud, contractions welcome.
- THE HOOK (line 1, 3-10 words) opens a gap the viewer needs closed: a sharp question, a surprising contrast or a
  stake. It names the subject, but never restates the title or the topic (not "Picture this: the real origin of X",
  not "Let's talk about X"), and never gives the answer away, not even in part.
- Every line moves the story one step closer to the core answer and follows from the line before (it explains it,
  draws what it leads to, or turns it). No line repeats another; only the payoff states the core answer.
- A fact line says ONLY what its facts say, in plain words of your own: simpler is fine, leaving details out is fine;
  never add a cause, number, place, time or kind of thing the facts do not state, never say "X" where they say "some
  X", and never copy a fact's sentence. A line with no facts states no fact about the world.
- The payoff says the core answer in plain words, only the parts its facts back. The loop reuses a word of the hook.
- Stay within the word budget: count your words. No stock lines ("Now you know."), no slang, no stage directions, no
  quotes around words."""

REVISE_SYSTEM = WRITE_SYSTEM + """

You are given the script and what failed. Write a new version of every line marked FIX so that it passes, keep every
other line word for word (unless the word budget needs it shorter), and keep the story flowing. A line marked SIDE (it
does not move the story toward the answer) may be cut: give "" for it. Give all the lines, in order."""

LINES_SCHEMA = lambda n: dict(type='object', required=['lines'], properties=dict(lines=dict(
    type='array', items=dict(type='string'), minItems=n, maxItems=n)))

HOOK_SYSTEM = """You judge the first line (the hook) of a short explainer video. Answer:
opens_gap: does it open a gap the viewer needs closed: a question they cannot answer yet, a surprising contrast, or
a stake? A flat statement, a summary, or a line that only names the topic ("Picture this: the real origin of crude.")
opens none.
restates_topic: does it only restate the video's title or topic in other words, with nothing to make the viewer
wonder?"""
HOOK_SCHEMA = dict(type='object', required=['opens_gap', 'restates_topic', 'why'], properties=dict(
    opens_gap=dict(type='boolean'), restates_topic=dict(type='boolean'), why=dict(type='string')))
ADVANCES_SYSTEM = """You judge one line in the middle of a short explainer video whose job is to lead the viewer to its
core answer. Answer whether the line moves the story toward that answer: it sets up the belief the video corrects,
breaks it, gives a clue or a step the answer needs, or turns the story. A line that tells a true but side fact the
answer does not need (a detail of drilling, of measurement, of history) does not."""
ADVANCES_SCHEMA = dict(type='object', required=['advances', 'why'], properties=dict(advances=dict(type='boolean'),
                                                                                    why=dict(type='string')))
OPENER = re.compile(r"^\s*(picture this|imagine this|let'?s talk about|today,? we|here'?s the (?:real )?(?:story|origin|truth) of"
                    r"|this is the (?:real )?(?:story|origin) of|the (?:real )?(?:story|origin|truth) of)\b", re.I)


# ------------------------------------------------------------------ the budget

def budget(seconds: float, rate: float) -> int:
    """The narration's words: the target seconds times the measured speaking rate (pipeline/pacing.py)."""
    return max(20, round(seconds * rate))


def words(lines) -> int:
    return sum(len(str(x).split()) for x in lines)


def over_budget(n_words: int, budget_words: int, tol: float = BUDGET_TOL) -> bool:
    return n_words > budget_words * (1 + tol)


# ------------------------------------------------------------------ the plan

def options(fmt: str) -> dict:
    from .grounding import skeleton
    return {n: skeleton(fmt, n) for n in range(MIN_BEATS, MAX_BEATS + 1)}


def plan_user(episode, facts, budget_words) -> str:
    from .understand import core as core_of
    interp = (episode.meta or {}).get('interpretation') or {}
    core = core_of(interp)
    opts = options(episode.story_format)
    rows = [f'Question: {interp.get("question") or episode.topic}', f'Title/topic: {episode.title or episode.topic}',
            f'Core answer: {core.get("text") or "(none: answer the question from the facts)"}'
            + (f' (backed by {", ".join(core.get("fact_ids") or [])})' if core.get('fact_ids') else ''),
            f'Misconception: {interp.get("misconception") or "(none)"}',
            f'Story format: {episode.story_format}',
            f'Word budget: {budget_words} words in all (about {round(budget_words / 9)} beats of 8-12 words reads best)',
            'Beat options (number of beats: their roles in order):']
    rows += [f'  {n}: {", ".join(r)}' for n, r in opts.items()]
    rows.append('\nVerified facts:')
    for f in facts:
        rows.append(f'{f.id}: {f.claim}' + (f'\n   quote: "{f.quote[:240]}"' if f.quote else ''))
    return '\n'.join(rows)


def normalise(raw: dict, episode, facts) -> tuple[dict | None, list]:
    """(plan, problems): the answer as a plan the rest of the script step reads, or None and what is wrong."""
    from .understand import core as core_of
    known = {f.id for f in facts}
    beats = [b for b in (raw or {}).get('beats') or [] if isinstance(b, dict)]
    roles = options(episode.story_format).get(len(beats))
    if roles is None:
        return None, [f'{len(beats)} beats; use one of the options ({MIN_BEATS}-{MAX_BEATS})']
    problems, out = [], []
    core_ids = [i for i in core_of((episode.meta or {}).get('interpretation') or {}).get('fact_ids') or [] if i in known]
    twist = str((raw or {}).get('twist_fact') or '')
    hook_fact = str((raw or {}).get('hook_fact') or '')
    for k, (role, b) in enumerate(zip(roles, beats)):
        kind = b.get('kind') if b.get('kind') in KINDS else 'transition'
        ids = [i for i in dict.fromkeys(str(x) for x in b.get('fact_ids') or []) if i in known]
        unknown = [str(x) for x in b.get('fact_ids') or [] if str(x) not in known]
        if unknown:
            problems.append(f'b{k + 1:02d}: unknown fact ids {", ".join(unknown)}')
        if role == 'hook':
            kind, ids = 'question', []
        elif role == 'loop':
            kind, ids = 'callback', []
        elif role in ('twist', 'payoff') and (kind != 'fact' or not ids):
            problems.append(f'b{k + 1:02d} ({role}) must be a fact beat with its facts')
        if kind != 'fact':
            ids = []
        if role == 'twist' and twist in known and twist not in ids:
            ids = [twist] + ids
        if role == 'payoff':
            ids = list(dict.fromkeys(ids + core_ids))
        point = ' '.join(str(b.get('point') or '').split()) if kind == 'fact' else ''
        if kind == 'fact' and not point:
            problems.append(f'b{k + 1:02d}: a fact beat needs its point')
        out.append(dict(role=role, kind=kind, fact_ids=ids, claim=point))
    if twist not in known:
        twist = next((b['fact_ids'][0] for b in out if b['role'] == 'twist' and b['fact_ids']), '')
    if hook_fact not in known:
        hook_fact = twist
    plan = dict(beats=out, twist=twist, hook_fact=hook_fact, hook_gap=' '.join(str(raw.get('hook_gap') or '').split()),
                left_out=[dict(id=str(x.get('id')), why=' '.join(str(x.get('why') or '').split())[:200])
                          for x in raw.get('left_out') or [] if isinstance(x, dict)])
    return plan, problems


def plan(episode, facts, budget_words, ask, write, cache=None, ledger=None) -> dict:
    """The plan Claude makes from the facts, checked; raises ValueError when no usable plan comes back."""
    from . import batch, grounding
    from .script import shape
    user = plan_user(episode, facts, budget_words)
    schema = plan_schema()
    got, problems, attempts = None, [], []
    myth = grounding.myth_groups((episode.meta or {}).get('interpretation') or {}, facts)
    for attempt in range(2):
        ask_user = user + '\n\nAnswer with JSON only: ' + shape(schema)
        if got is not None:
            ask_user += ('\n\nYOUR PREVIOUS PLAN:\n' + json.dumps(raw, ensure_ascii=False) +
                         '\n\nTHESE PROBLEMS WERE FOUND. Fix every one and keep the rest:\n'
                         + '\n'.join(f'- {m}' for m in problems))
        raw = write(PLAN_SYSTEM, ask_user, schema, f'plan (repair {attempt})' if attempt else 'plan') or {}
        got, problems = normalise(raw, episode, facts)
        if got is not None:
            msgs, _ = batch.run(lambda a: grounding.plan_issues(got, facts, myth, a), ask, what='plan claims')
            problems += msgs
        attempts.append(dict(attempt=attempt, issues=list(problems)))
        if got is not None and not problems:
            break
    if got is None:
        raise ValueError('Claude Code gave no usable plan: ' + '; '.join(problems))
    by = {f.id: f for f in facts}
    from .understand import core as core_of
    got.update(version=NATIVE_VERSION, native=True, attempts=attempts, notes=[], remaining=list(problems),
               answer_words=grounding.answer_words(by.get(got['twist']), episode),
               core=core_of((episode.meta or {}).get('interpretation') or {}).get('text', ''),
               surprise=[got['hook_fact']] if got['hook_fact'] else [], chain=[''] * len(got['beats']),
               fact_beats=sum(1 for b in got['beats'] if b['kind'] == 'fact'),
               usable_facts=len({i for b in got['beats'] for i in b['fact_ids']}), word_budget=budget_words)
    return got


def take_twist(episode, plan_: dict, facts):
    """The plan's twist becomes the episode's (the code-picked one is kept beside it)."""
    by = {f.id: f for f in facts}
    interp = dict((episode.meta or {}).get('interpretation') or {})
    old = interp.get('twist') or {}
    if plan_.get('twist') in by:
        interp['twist'] = dict(id=plan_['twist'], claim=by[plan_['twist']].claim, how='planned by Claude Code',
                               code_pick=old.get('id', ''))
        episode.meta['interpretation'] = interp


# ------------------------------------------------------------------ the checks

def restates_topic(hook: str, episode) -> str:
    """'' or why the hook only restates the title or the topic (in code)."""
    from .qa import content_words
    if OPENER.search(hook or ''):
        return f'opens like a title card ("{OPENER.search(hook).group(0).strip()}")'
    interp = (episode.meta or {}).get('interpretation') or {}
    own = content_words(hook)
    topic = content_words(' '.join([episode.topic, episode.title or '', interp.get('question', '')]))
    fold = lambda ws: {w[:-1] if w.endswith('s') and len(w) > 4 else w for w in ws}
    if own and len(fold(own) - fold(topic)) <= 1 and not hook.rstrip().endswith('?'):
        return 'only restates the title or the topic'
    return ''


def code_problem(c, k, lines, ctx) -> str:
    """The first hard rule line k breaks, in code ('' when none)."""
    from . import bestof, entail, grounding
    from .models import staging_leak
    from .script import copies
    from .whole import BELIEF, CANNED
    pbs, by, interp, ep = ctx['pbs'], ctx['by'], ctx['interp'], ctx['episode']
    pb, n = pbs[k], len(pbs)
    w = len(c.split())
    if not c.strip():
        return 'empty'
    if k == 0 and not HOOK_WORDS[0] <= w <= HOOK_WORDS[1]:
        return f'the hook is {w} words (3-10)'
    if w > MAX_LINE_WORDS:
        return f'{w} words: at most {MAX_LINE_WORDS} a line'
    if bestof.UNFINISHED.search(c) or set(re.findall(r"[a-z']+", c.lower())) & bestof.SLANG:
        return 'slang or an unfinished line'
    if CANNED.match(c):
        return 'a stock line'
    if staging_leak(c) and not c.rstrip().endswith('?'):
        return f'a staging direction ({staging_leak(c)})'
    research = ctx['research'][k]
    if set(entail.NUMBER.findall(c)) - set(entail.NUMBER.findall(research)):
        return 'a number its facts do not have'
    if grounding.narrowed(c, research):
        return grounding.narrowed(c, research)
    if copies(c, [by[i].claim for i in ctx['ids'][k] if i in by] + [by[i].quote for i in ctx['ids'][k]
                                                                    if i in by and by[i].quote]):
        return 'copies its fact sentence'
    if k == 0:
        why = restates_topic(c, ep)
        if why:
            return f'the hook {why}'
    if k == n - 1 and lines[0] and not bestof.shares_stem(c, lines[0]):
        return 'the loop does not reuse a word of the hook'
    if pb['role'] == 'payoff' and ctx['core'] and not grounding.says_core(c, ctx['core'], ep):
        return f'does not say the core answer ("{ctx["core"][:100]}")'
    if pb['kind'] == 'myth' and not bestof.names_myth(c, interp):
        return 'does not name what the myth is about'
    if pb['kind'] != 'myth' and k != 0 and BELIEF.search(c):
        return 'reports a belief; only the myth beat says what people believe'
    return ''


def judge_problem(c, k, lines, ctx, ask) -> str:
    """The first hard rule line k breaks by the judge ('' when none); each is one question, asked together."""
    from . import bestof, entail, grounding
    pbs, ep, interp = ctx['pbs'], ctx['episode'], ctx['interp']
    pb, n = pbs[k], len(pbs)
    if k == 0:
        try:
            a = ask(HOOK_SYSTEM, f'Title/topic: {ep.title or ep.topic}\nHook: {c}', HOOK_SCHEMA) or {}
        except Exception:
            a = {}
        if a.get('opens_gap') is False or a.get('restates_topic') is True:
            return 'the hook opens no gap' + (f' ({" ".join(str(a.get("why") or "").split())[:140]})' if a.get('why') else '')
        why = grounding.reveals(c, ctx['core'], ask)
        if why:
            return f'the hook gives the answer away ({why})'
    if k in (0, n - 1):
        myth = bestof.asserts_myth(c, interp, ask)
        if myth:
            return f'says the myth as a fact ({myth})'
    if pb['kind'] == 'myth':
        ok, why = bestof.real_belief(c, interp, ask)
        if not ok:
            return f'not the belief people hold ({why})'
    if pb['role'] == 'payoff' and ctx['core'] and not grounding.states_core(c, ctx['core'], ep, ask):
        return f'the judge says it does not state the core answer ("{ctx["core"][:100]}")'
    ids = ctx['ids'][k]
    ok, why = entail.verdict(c, ctx['research'][k], ctx['allowed'][k], ask,
                             entail.CHECK_SYSTEM if ids else entail.NONFACT_SYSTEM)
    if ok is False and ids and k in (0, n - 1):         # a hook or a loop may also state no fact at all
        if entail.verdict(c, '', entail.allowed_groups(ep, ()), ask, entail.NONFACT_SYSTEM)[0] is not False:
            ok = True
    if ok is False:
        return f'fact check: {why}'
    if 0 < k < n - 1 and pb['role'] not in ('payoff', 'problem') and pb['kind'] not in ('myth', 'reaction', 'question'):
        try:
            a = ask(ADVANCES_SYSTEM, f'Core answer: {ctx["core"] or ctx["episode"].topic}\nLine before: {lines[k - 1]}\n'
                                     f'Line: {c}', ADVANCES_SCHEMA) or {}
        except Exception:
            a = {}
        if a.get('advances') is False:
            return 'SIDE: does not move the story toward the answer (' + ' '.join(str(a.get('why') or '').split())[:140] + ')'
    return ''


def every_question(lines, ctx, ask):
    for k, c in enumerate(lines):
        judge_problem(c, k, lines, ctx, ask)
    from .whole import score
    score(lines, ctx, ask)


def check(lines, ctx, ask) -> dict:
    """{index: why} for every line that breaks a hard rule, the repeats across the script by meaning included."""
    from .whole import repeats
    out = {}
    for k, c in enumerate(lines):
        why = code_problem(c, k, lines, ctx) or judge_problem(c, k, lines, ctx, ask)
        if why:
            out[k] = why
    for k, why in repeats(lines, ctx).items():
        out.setdefault(k, why)
    return out


# ------------------------------------------------------------------ writing

def outline_text(ctx, budget_words, angle) -> str:
    interp, by = ctx['interp'], ctx['by']
    ep = ctx['episode']
    out = [f'Title/topic: {ep.title or ep.topic}', f'Question: {interp.get("question") or ep.topic}',
           f'Core answer (only the payoff states it): {ctx["core"] or "(answer the question from the facts)"}']
    if interp.get('misconception'):
        out.append(f'What many people believe (the myth the video corrects): {interp["misconception"]}')
    out.append(f'Word budget: at most {budget_words} words in all ({len(ctx["pbs"])} lines).')
    hf = by.get(ctx['hook_fact'])
    out.append(f'The hook opens its gap with {angle}' + (f', teasing this fact without saying it: {hf.claim}' if hf else '')
               + (f' (the plan: {ctx["hook_gap"]})' if ctx.get('hook_gap') else '') + '.')
    out.append('\nThe plan, one line per beat:')
    for k, pb in enumerate(ctx['pbs']):
        facts = '; '.join(by[i].claim for i in ctx['ids'][k] if i in by and pb['kind'] == 'fact')
        row = f'{k + 1}. {pb["role"].upper()} ({pb["kind"]})'
        if pb['kind'] == 'fact':
            row += f': {pb.get("claim") or facts}' + (f'\n   its facts: {facts}' if facts and facts != pb.get('claim') else '')
        out.append(row)
    return '\n'.join(out)


def _ctx(episode, facts, plan_, budget_words):
    from . import whole
    ctx = whole.context(episode, facts, plan_, budget_words)
    ctx['hook_gap'] = plan_.get('hook_gap', '')
    return ctx


def draft(ctx, budget_words, k, write) -> list:
    from . import bestof
    n = len(ctx['pbs'])
    try:
        a = write(WRITE_SYSTEM, outline_text(ctx, budget_words, ANGLES[k % len(ANGLES)])
                  + f'\n\nAnswer with JSON: {{"lines": [{n} strings]}}', LINES_SCHEMA(n), f'draft {k + 1}') or {}
    except Exception:
        return []
    lines = [bestof._clean(x) for x in (a.get('lines') if isinstance(a.get('lines'), list) else [])]
    return lines if len(lines) == n and all(lines) else []


def revise(ctx, lines, problems, budget_words, write, weak=(), what='revise') -> list:
    """Claude writes the failing lines again, in one call; [] when the answer is unusable."""
    from . import bestof
    n = len(lines)
    rows = []
    for k, c in enumerate(lines):
        tag = ('SIDE ' if str(problems.get(k, '')).startswith('SIDE') else '') + ('FIX' if k in problems or k in weak else 'KEEP')
        why = problems.get(k) or (f'does not follow from the line before: {weak[k]}' if k in weak else '')
        rows.append(f'{k + 1}. [{tag}] {c}' + (f'\n   failed: {why}' if why else ''))
    n_words = words(lines)
    user = (outline_text(ctx, budget_words, ANGLES[-1]) + '\n\nThe script now (' + f'{n_words} words; budget {budget_words}):\n'
            + '\n'.join(rows) + f'\n\nAnswer with JSON: {{"lines": [{n} strings]}}')
    try:
        a = write(REVISE_SYSTEM, user, LINES_SCHEMA(n), what) or {}
    except Exception:
        return []
    got = [bestof._clean(x) if str(x).strip() else '' for x in (a.get('lines') if isinstance(a.get('lines'), list) else [])]
    return got if len(got) == n else []


def _drop(seqs, k):
    for seq in seqs:
        if k < len(seq):
            seq.pop(k)


def write(episode, facts, o, plan_, budget_words, ask, rate, write_call, log=None) -> dict:
    """The narration of every beat (in place on the outline o; plan_['beats'] loses a cut beat). Returns the log
    entry: {scripts, winner, revisions, trims, dropped, unresolved, words, budget, hooks, lines}."""
    from . import batch
    from .llm import run_parallel
    from .whole import droppable, score
    ctx = _ctx(episode, facts, plan_, budget_words)
    memo = batch.Memo(ask)
    ask = memo if memo.on else ask
    drafts = run_parallel(lambda k: draft(ctx, budget_words, k, write_call), list(range(N_DRAFTS)))
    scripts = [dict(script=k + 1, lines=d if isinstance(d, list) else [], problems={}, score=None)
               for k, d in enumerate(drafts)]
    usable = [r for r in scripts if r['lines']]
    if not usable:
        raise RuntimeError(f'Claude Code gave no usable script in {N_DRAFTS} drafts')
    if memo.on:                                   # one call per draft for every check of every line
        run_parallel(lambda r: memo.collect(lambda a: every_question(r['lines'], ctx, a), f'checks of script {r["script"]}'),
                     usable)
    for r in usable:
        r['problems'] = check(r['lines'], ctx, ask)
        r['score'] = score(r['lines'], ctx, ask)
        r['words'] = words(r['lines'])
    # fewest hard-rule failures, then the score (hook, flow), then nearest the budget
    win = min(usable, key=lambda r: (len(r['problems']), -r['score']['total'],
                                     abs(r['words'] - budget_words) / budget_words, r['script']))
    lines, problems = list(win['lines']), dict(win['problems'])
    entry = dict(stage='native', version=NATIVE_VERSION, scripts=[dict(r, problems={str(k): v for k, v in r['problems'].items()})
                                                                  for r in scripts],
                 winner=win['script'], budget=budget_words, revisions=[], trims=[], dropped=[], unresolved=[])
    pairs = (win['score'] or {}).get('pairs') or {}
    weak = {int(k): v[1] for k, v in pairs.items() if v[0] <= 2 and 0 < int(k) < len(lines) - 1}

    def accept(new, old_lines, old_problems, label):
        """The revised script, line by line: a changed line is kept only if it passes (and breaks no other line)."""
        if not new:
            return old_lines, old_problems
        cut = [k for k, x in enumerate(new) if not x]
        trial = [x if x else old_lines[k] for k, x in enumerate(new)]
        if memo.on:
            memo.collect(lambda a: every_question(trial, ctx, a), label)
        got = check(trial, ctx, ask)
        merged = list(old_lines)
        for k, x in enumerate(trial):
            if x != old_lines[k] and k not in got:
                merged[k] = x
        final = check(merged, ctx, ask) if merged != old_lines else old_problems
        for k in cut:                              # a side beat Claude cut: it goes if the story keeps its shape
            final[k] = final.get(k) or 'SIDE: cut by the revision'
        return merged, final

    for rnd in range(REVISE_ROUNDS):
        if not problems and not weak and not over_budget(words(lines), budget_words):
            break
        new = revise(ctx, lines, problems, budget_words, write_call, weak, f'revise {rnd + 1}')
        before = dict(problems)
        lines, problems = accept(new, lines, problems, f'checks of revision {rnd + 1}')
        entry['revisions'].append(dict(round=rnd + 1, failing=before, now={str(k): v for k, v in problems.items()},
                                       lines=list(lines)))
        weak = {}
    # side beats go, the story keeping its shape
    for k in sorted([k for k, why in problems.items() if str(why).startswith('SIDE')], reverse=True):
        if droppable(ctx, lines, k, rate):
            entry['dropped'].append(dict(beat=f'b{k + 1:02d}', role=ctx['pbs'][k]['role'], line=lines[k], why=problems[k]))
            _drop([lines, ctx['pbs'], ctx['ids'], ctx['words'], ctx['research'], ctx['allowed'], plan_['beats'],
                   o.get('beats') or []], k)
            problems = {(j if j < k else j - 1): v for j, v in problems.items() if j != k}
    # the word budget: trimmed by Claude in the same loop, before anything is voiced
    for rnd in range(TRIM_ROUNDS):
        if not over_budget(words(lines), budget_words):
            break
        long = {k: f'too long: the script is {words(lines)} words, the budget {budget_words}; say it in fewer words'
                for k, c in enumerate(lines) if len(c.split()) > 9}
        new = revise(ctx, lines, {**long, **problems}, budget_words, write_call, (), f'trim {rnd + 1}')
        before = words(lines)
        if new and words([x for x in new if x]) < before:
            lines, problems = accept(new, lines, problems, f'checks of trim {rnd + 1}')
        entry['trims'].append(dict(round=rnd + 1, words_before=before, words_after=words(lines)))
    for k, why in sorted(problems.items()):
        entry['unresolved'].append(dict(beat=f'b{k + 1:02d}', line=lines[k], why=why))
    # the beats: narration, and the facts a line stands on (a hook or a loop cites its fact only when it states one)
    from . import entail
    beats = o.get('beats') or []
    for k, ob in enumerate(beats[:len(lines)]):
        ob['narration'] = lines[k]
        ids = ctx['ids'][k]
        if ids and k in (0, len(lines) - 1):
            ok, _ = entail.verdict(lines[k], '', entail.allowed_groups(ctx['episode'], ()), ask, entail.NONFACT_SYSTEM)
            ids = [] if ok is not False else ids
        ob['source_ids'] = list(ids if ctx['pbs'][k]['kind'] == 'fact' or k in (0, len(lines) - 1) else [])
    hooks = [lines[0]] + [r['lines'][0] for r in usable if r is not win and 0 not in r['problems']]
    hooks = list(dict.fromkeys(hooks))
    for k, opt in enumerate(o.get('hook_options') or []):
        opt['line'] = hooks[k] if k < len(hooks) else hooks[0]
        opt['loop_line'] = lines[-1]
        opt['answer'] = ctx['core'] or opt.get('answer', '')
    entry.update(lines=list(lines), words=words(lines), hooks=hooks,
                 batched=dict(fetched=memo.fetched, asked_alone=memo.missed) if memo.on else None)
    if log is not None:
        log.append(entry)
    return entry


def view(entry) -> list:
    if not entry:
        return []
    out = []
    for r in entry.get('scripts') or []:
        s = r.get('score') or {}
        out.append(f'draft {r["script"]}: {len(r["problems"])} failing line(s), {r.get("words", "?")} words'
                   + (f'; hook {s.get("hook")}, flow {s.get("flow")}' if s else '')
                   + (' <- kept' if r['script'] == entry.get('winner') else ''))
        for k, why in sorted(r['problems'].items(), key=lambda kv: int(kv[0])):
            out.append(f'  b{int(k) + 1:02d}: {why}')
    for r in entry.get('revisions') or []:
        out.append(f'revision {r["round"]}: {len(r["failing"])} failing -> {len(r["now"])}')
    for r in entry.get('trims') or []:
        out.append(f'trim {r["round"]}: {r["words_before"]} -> {r["words_after"]} words (budget {entry.get("budget")})')
    for r in entry.get('dropped') or []:
        out.append(f'cut {r["beat"]} ({r["role"]}): "{r["line"]}" ({r["why"]})')
    for r in entry.get('unresolved') or []:
        out.append(f'unresolved {r["beat"]}: "{r["line"]}" ({r["why"]})')
    out.append(f'{entry.get("words")} words, budget {entry.get("budget")}')
    return out
