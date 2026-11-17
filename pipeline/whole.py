"""Whole-script best-of-N (user 2026-11-16): "The line-by-line pipeline is over-constrained: good lines fail nitpicks,
then we fall back to copied research sentences or 'Now you know.' Simplify instead of adding checks."

The fact-first outline stays (pipeline/grounding.py make_plan: code picks the roles, the core answer, the twist and the
one fact each beat says, and names the single point each beat makes). From it the writer writes N_SCRIPTS complete
scripts, one call each (a different hook shape and seed per script). Every line of every script goes through the same
checks (`problems`: code first, the judge only for a line the code passes), plus repeats by meaning across the script
(pipeline/embed.py; the payoff may restate the core answer once). A script with no failing line survives; the
survivors are scored (`score`: the judge's hook gap and the flow of each pair of lines, plus code: the length) and the
best one is kept. When no script survives, the one with the fewest failing lines is kept and only those lines are
repaired (`fix_line`: best-of-N for that one line, kept only if it passes); a weak pair of lines in the winner gets its
second line written again, kept only if the pair scores higher. No raw fallback (user 2026-11-16): a line is never
replaced by a research sentence or a stock line; a line no rewrite fixes is dropped with its beat when the video stays
above MIN_SECONDS and the story keeps its shape, else it stays and is reported.
"""
from __future__ import annotations

import re

WHOLE_VERSION = 'whole-v1'
N_SCRIPTS = 4
TEMPERATURE = 0.7
SEED = 101
MIN_SECONDS = 30                 # a beat is dropped only when the video stays above this
FLOW_FIX = 2                     # a pair of lines the flow judge scores this or lower is weak
FLOW_REWRITES = 2                # at most this many lines of the winner are written again for flow
MAX_TOKENS = 1400

CANNED = re.compile(r"^\s*(now you know|but there(?:'s| is) a catch|here(?:'s| is) the (?:surprise|clue)|so what really "
                    r"happened|now it gets strange|wait\. it gets better|what is really going on here)\W*$", re.I)
BELIEF = re.compile(r"\b(?:(?:many|most|some) (?:people )?|people |others |everyone |folks )(?:still )?(?:think|believe|"
                    r"claim|say|swear|assume)s?\b|\b(?:you(?:'ve| have) been|we(?:'re| are)) told\b", re.I)

WRITE_SYSTEM = """You write the whole narration of a narrated 30-50 second science Short from its outline: one line per
beat, in order, in the host's story voice: talking to a friend, vivid, with rhythm when read aloud, a little
dramatic, contractions welcome.
- Each line makes ONLY its own beat's point, so no two lines say the same thing; only the payoff restates the core
answer.
- Each line follows from the one before it: it explains it, draws what it leads to, or turns it. The link may sit
inside the line; at most two lines open with a link word such as "But" or "So", never two in a row.
- A fact beat says what its fact says in plain words of your own: simpler is fine, leaving details out is fine, but
never add a cause, number, place, time or kind of living thing the fact does not state, and never copy the fact's
sentence.
- A beat with no fact states no fact about the world.
- The hook opens a question the video answers later and never gives the core answer away, not even in part.
- No stock lines ("Now you know.", "But there is a catch.") and no slang ("critters", "stuff", "goo")."""

LINES_SCHEMA = lambda n: dict(type='object', required=['lines'], properties=dict(lines=dict(
    type='array', items=dict(type='string'), minItems=n, maxItems=n)))

SCORE_SYSTEM = """You grade the HOOK (the first line) of a narrated explainer Short: how strongly it makes a viewer
keep watching to learn something specific. 5 = they must keep watching; 4 = clear pull; 3 = mildly curious; 2 = weak;
1 = no question opened (a flat statement or a summary). Judge only the hook line."""
SCORE_SCHEMA = dict(type='object', required=['gap', 'why'], properties=dict(
    gap=dict(type='integer', minimum=1, maximum=5), why=dict(type='string')))

BEAT_JOB = {
    'question': 'ask the question the next beat answers; do not answer it; state no fact',
    'transition': 'move the story on in a few words of your own; explain nothing; state no fact',
    'reaction': 'the host reacts with surprise or doubt to the line before, in a few words; state no fact',
    'callback': 'repeat a word of the hook with its new meaning; state no fact',
    'myth': 'say what many people believe (the misconception), clearly as a belief ("Many think...")',
}
ROLE_POINT = {'hook': 'open the question', 'setup': 'the everyday picture or the myth', 'problem': 'break the belief',
              'stakes': 'raise the stakes', 'clue': 'a clue', 'reason': 'a reason', 'reveal': 'show how it works',
              'twist': 'THE TWIST: the surprise that changes how the viewer sees it', 'payoff': 'answer the hook',
              'loop': 'tie back to the hook'}


# ------------------------------------------------------------------ the context of one episode's scripts

def context(episode, facts, plan, words_total) -> dict:
    """Everything the checks need, worked out once: per beat its plan entry (the payoff with the core answer and the
    core answer's facts), fact ids, research, allowed groups of living things and word range."""
    from . import entail, grounding
    from .understand import core as core_of
    episode = episode.model_copy(update=dict(sources=list(facts)))
    interp = (episode.meta or {}).get('interpretation') or {}
    by = {f.id: f for f in facts}
    twist = (interp.get('twist') or {}).get('id') or plan.get('twist', '')
    core = core_of(interp)
    core_text = core.get('text', '')
    pbs = [dict(pb) for pb in plan['beats']]
    n = len(pbs)
    # without a core answer (an older episode) the payoff answers the hook as the gate checks it: the twist and the
    # storyline's payoff
    twist_claim = next((b.get('claim') for b in pbs if b['role'] == 'twist' and b.get('claim')), '') \
        or (by[twist].claim if twist in by else '')
    promise = core_text or ' '.join(x for x in [twist_claim, episode.storyline.payoff if episode.storyline else ''] if x)
    for pb in pbs:
        if pb['role'] == 'payoff':
            more = list(core.get('fact_ids') or []) + ([twist] if twist else [])
            pb.update(core=core_text, answer_to=promise,
                      fact_ids=list(dict.fromkeys(list(pb['fact_ids']) + [i for i in more if i in by])))
    hook_fact = plan.get('hook_fact') or twist
    ids = []
    for k, pb in enumerate(pbs):
        if k == 0:
            ids.append([hook_fact] if hook_fact in by else [])
        elif k == n - 1:
            ids.append([])
        else:
            ids.append(list(pb['fact_ids']))
    # word ranges: the fact beats share what the hook, the loop and the no-fact beats leave
    n_fact = sum(1 for k, pb in enumerate(pbs) if 0 < k < n - 1 and pb['kind'] == 'fact')
    other = sum(6 for k, pb in enumerate(pbs) if 0 < k < n - 1 and pb['kind'] != 'fact')
    mid = max(9, min(22, round((words_total - 6 - 8 - other) / max(1, n_fact))))
    words = []
    for k, pb in enumerate(pbs):
        if k == 0:
            words.append((3, 8, 3, 8))                 # the gate's limit (bestof.HOOK_MAX_WORDS)
        elif k == n - 1:
            words.append((4, 12, 3, 16))
        elif pb['kind'] != 'fact':
            words.append((3, 10, 2, 14) if pb['kind'] != 'myth' else (6, 16, 4, 20))
        else:
            words.append((max(6, mid - 5), mid + 4, max(5, mid - 8), mid + 7))   # asked lo-hi, accepted lo2-hi2
    twist_k = next((k for k, pb in enumerate(pbs) if pb['role'] == 'twist'), None)
    return dict(episode=episode, facts=facts, by=by, interp=interp, pbs=pbs, ids=ids, words=words, core=core_text,
                core_ids=list(core.get('fact_ids') or []), twist=twist, twist_k=twist_k, hook_fact=hook_fact,
                answer_words=list(plan.get('answer_words') or []), words_total=words_total,
                research=[entail.research_text(i, facts) for i in ids],
                allowed=[entail.allowed_groups(episode, i) for i in ids],
                topic=grounding.topic_words(episode))


# ------------------------------------------------------------------ writing

def outline_text(ctx, shape=None) -> str:
    """The outline the writer works from: the question, the core answer, the myth and one entry per beat with the one
    point it makes and the fact it stands on."""
    from . import bestof
    interp, pbs, by = ctx['interp'], ctx['pbs'], ctx['by']
    misc = interp.get('misconception', '')
    ep = ctx['episode']
    out = [f'Topic: {ep.topic}', f'Question: {interp.get("question") or ep.topic}']
    if ctx['core']:
        out.append(f'Core answer (the payoff says it; no line before the reveal gives it away): {ctx["core"]}')
    if misc:
        out.append(f'What many people believe (the myth the video corrects): {misc}')
    out.append(f'\nBeats ({len(pbs)} lines, in this order):')
    for k, pb in enumerate(pbs):
        lo, hi = ctx['words'][k][:2]
        role = pb['role'].upper()
        if k == 0:
            hf = by.get(ctx['hook_fact'])
            line = f'{k + 1}. HOOK, {lo}-{hi} words: open a question the viewer must see answered.'
            if shape:
                line += f' Shape: {shape["shape"]}: {shape["how"]} (shape only; on another topic: "{shape["example"]}").'
            if hf is not None:
                line += f' Tease this surprising fact without saying it: {hf.claim}'
            subject = ', '.join(sorted(ctx['topic'])[:6])
            if subject:
                line += f' Name the subject (a word such as: {subject}).'
            if ctx['answer_words']:
                line += ' Never use these words: ' + ', '.join(ctx['answer_words'][:20]) + '.'
        elif k == len(pbs) - 1:
            line = (f'{k + 1}. LOOP, {lo}-{hi} words: {BEAT_JOB["callback"]}. It must reuse one word of your line 1 '
                    '(the hook).')
        elif pb['kind'] != 'fact':
            job = BEAT_JOB.get(pb['kind'], BEAT_JOB['transition'])
            line = f'{k + 1}. {role} ({ROLE_POINT.get(pb["role"], "")}), {lo}-{hi} words: {job}.'
            if pb['kind'] == 'myth' and misc:
                line += f' The myth: {misc}'
                subjects = [x for x in interp.get('myth_subjects') or [] if x]
                if subjects:
                    line += f' Name what it is about in these words: {", ".join(subjects)}.'
        else:
            facts = '; '.join(f'{by[i].claim}' for i in ctx['ids'][k] if i in by)
            point = pb.get('core') if pb['role'] == 'payoff' and pb.get('core') else (pb.get('claim') or facts)
            line = (f'{k + 1}. {role} ({ROLE_POINT.get(pb["role"], "")}), {lo}-{hi} words. Its one point: {point}'
                    + (f'\n   Its fact: {facts}' if facts and facts != point else ''))
            if pb['role'] == 'payoff' and pb.get('core'):
                from . import grounding
                own = sorted(grounding.core_words(pb['core'], ep)[1])[:8]
                line += ('\n   This line must say the core answer, in plain words of your own'
                         + (f', using at least two of: {", ".join(own)}.' if own else '.'))
            caution = '; '.join(c for c in (bestof_caution(by[i]) for i in ctx['ids'][k] if i in by) if c)
            if caution:
                line += f'\n   NOTE: {caution}'
        out.append(line)
    return '\n'.join(out)


def bestof_caution(s) -> str:
    from . import grounding
    return grounding.caution(s)


def draft(ctx, k, write) -> list:
    """Script k: the writer's lines (one call), cleaned; [] when the answer is unusable."""
    from . import bestof
    shapes = bestof.shapes('hook', ctx['episode'].slug)
    shape = shapes[k % len(shapes)] if shapes else None
    n = len(ctx['pbs'])
    try:
        a = write(WRITE_SYSTEM, outline_text(ctx, shape) + f'\n\nAnswer with JSON: {{"lines": [{n} strings]}}',
                  LINES_SCHEMA(n), k) or {}
    except Exception:
        return []
    lines = [bestof._clean(x) for x in (a.get('lines') if isinstance(a.get('lines'), list) else [])]
    return lines if len(lines) == n and all(lines) else []


# ------------------------------------------------------------------ the checks (one list for every line)

def code_problem(c, k, lines, ctx) -> str:
    """The first problem code finds with line k (c) of a script, else ''."""
    from . import bestof, entail, grounding
    from .script import copies
    pbs, by, interp, ep = ctx['pbs'], ctx['by'], ctx['interp'], ctx['episode']
    pb, n = pbs[k], len(pbs)
    kind = 'hook' if k == 0 else 'loop' if k == n - 1 else 'beat'
    research = ctx['research'][k]
    earlier = [x for x in lines[:k] if x]
    prev = lines[k - 1] if k else ''
    w = len(c.split())
    lo2, hi2 = ctx['words'][k][2:]
    twist = ctx['twist']
    if not lo2 <= w <= hi2:
        return f'{w} words (wanted {ctx["words"][k][0]}-{ctx["words"][k][1]})'
    if bestof.UNFINISHED.search(c) or set(re.findall(r"[a-z']+", c.lower())) & bestof.SLANG:
        return 'slang or an unfinished line'
    if not re.match(r'["\'(]?[A-Z0-9]', c):
        return 'starts mid-sentence'
    if CANNED.match(c):
        return 'a stock line'
    if grounding.narrowed(c, research):
        return grounding.narrowed(c, research)
    if set(entail.NUMBER.findall(c)) - set(entail.NUMBER.findall(research)):
        return 'a number its fact does not have'
    fact_lines = [by[i].claim for i in ctx['ids'][k] if i in by] + [by[i].quote for i in ctx['ids'][k]
                                                                   if i in by and by[i].quote]
    if copies(c, fact_lines):
        return 'copies its fact sentence'
    if bestof.opener(c) and bestof.opener(c) in {bestof.opener(x) for x in earlier}:
        return f'opens like an earlier line ("{bestof.opener(c)}")'
    lw = bestof.link_word(c)
    if lw and (lw == bestof.link_word(prev) or sum(1 for x in earlier if bestof.link_word(x) == lw) >= bestof.LINK_OPENS):
        return f'opens with "{lw}" like too many lines already'
    if k == ctx['twist_k'] and twist in by:
        tg = grounding.groups(f'{by[twist].claim} {by[twist].quote}') - grounding.myth_groups(interp, ctx['facts'])
        if tg and not grounding.groups(c) & (grounding.allowed_for([f'{by[twist].claim} {by[twist].quote}'])
                                             - grounding.myth_groups(interp, ctx['facts'])):
            return f'the twist must say what its fact names ({", ".join(sorted(tg))})'
    if kind == 'hook':
        tw = f'{by[twist].claim} {by[twist].quote}' if twist in by else ''
        twist_claim = pbs[ctx['twist_k']].get('claim', '') if ctx['twist_k'] is not None else ''
        gave = grounding.gives_away(c, tw, twist_claim, interp, ctx['facts'], ep.topic)
        if gave:
            return f'gives the twist away: {gave}'
        straw = bestof.strawman(c, interp, by[twist].claim if twist in by else '', ep.topic)
        if straw:
            return f'argues against something nobody claims ({straw})'
    if kind == 'loop' and lines[0] and not bestof.shares_stem(c, lines[0]):
        return 'does not repeat a word of the hook'
    if pb['role'] == 'payoff' and pb.get('core') and not grounding.says_core(c, pb['core'], ep):
        return f'does not say the core answer ("{pb["core"][:100]}")'
    if pb['role'] == 'payoff' and not pb.get('core') and pb.get('answer_to') and not bestof.answers(c, pb['answer_to']):
        return f'does not answer the hook (say what it promised: "{pb["answer_to"][:100]}")'
    if pb['kind'] == 'myth' and not bestof.names_myth(c, interp):
        return 'does not name what the myth is about'
    if pb['kind'] != 'myth' and kind != 'hook' and BELIEF.search(c):
        return 'reports a belief; only the myth beat says what people believe'
    return ''


def judge_problem(c, k, lines, ctx, ask) -> tuple[str, list]:
    """(the first problem the judge finds with line k, else '', and the fact ids the line stands on)."""
    from . import bestof, entail, grounding
    pbs, ep, interp, by = ctx['pbs'], ctx['episode'], ctx['interp'], ctx['by']
    pb, n = pbs[k], len(pbs)
    kind = 'hook' if k == 0 else 'loop' if k == n - 1 else 'beat'
    ids = ctx['ids'][k]
    if kind == 'hook':
        if not grounding.points_to_subject(c, ep, by.get(ids[0]) if ids else None, ctx['answer_words'], ask):
            return 'says nothing about the topic (it could open any video)', ids
        why = grounding.reveals(c, ctx['core'] or (by[ctx['twist']].claim if ctx['twist'] in by else ''), ask)
        if why:
            return f'gives the answer away ({why})', ids
    if kind in ('hook', 'loop'):
        myth = bestof.asserts_myth(c, interp, ask)
        if myth:
            return f'says the myth as a fact ({myth})', ids
    if pb['kind'] == 'myth':
        ok, why = bestof.real_belief(c, interp, ask)
        if not ok:
            return f'not the belief people hold ({why})', ids
    if pb['role'] == 'payoff' and pb.get('core') and not grounding.states_core(c, pb['core'], ep, ask):
        return f'the judge says it does not state the core answer ("{pb["core"][:100]}")', ids
    system = entail.CHECK_SYSTEM if ids else entail.NONFACT_SYSTEM
    ok, why = entail.verdict(c, ctx['research'][k], ctx['allowed'][k], ask, system)
    if ok is False and ids and kind in ('hook', 'loop'):   # a hook or a loop may also state no fact at all
        ok2, _ = entail.verdict(c, '', entail.allowed_groups(ep, ()), ask, entail.NONFACT_SYSTEM)
        if ok2 is not False:
            return '', []
    if ok is False:
        return why, ids
    if kind == 'hook':                                 # the hook cites its fact only when it states one
        claims = entail.split_claims(c, ask)[0] or []
        return '', ids if any(x['kind'] == 'fact' for x in claims) else []
    return '', ids if pb['kind'] == 'fact' else []


def repeats(lines, ctx) -> dict:
    """{line index: why} for lines that say what another line says (by meaning, pipeline/embed.py); the payoff may
    restate one line (the core answer, once), the loop calls back to the hook and the payoff, the myth is the opposite of
    the truth, and the hook asks what the payoff answers: those pairs are not repeats. Of a pair, the line that is not
    the payoff is flagged (the payoff must say the core answer), else the later one. Shared words stand in when the
    embedding model is down."""
    from . import embed, entail
    pbs = ctx['pbs']
    n = len(lines)
    pay = next((k for k, p in enumerate(pbs) if p['role'] == 'payoff'), None)
    skip = lambda j, i: (pbs[i]['kind'] == 'myth' or pbs[j]['kind'] == 'myth' or (j == 0 and i in (n - 1, pay))
                         or (i == n - 1 and j == pay))
    pairs = embed.similar_pairs(lines)
    if pairs is None:
        pairs = [(j, i, 0.0, 0.0) for i in range(n) for j in range(i) if entail.repeats(lines[i], [lines[j]])]
    out, restated = {}, False
    for j, i, raw, cen in sorted(pairs, key=lambda p: -p[2]):
        if skip(j, i):
            continue
        if pay in (i, j) and not restated:
            restated = True
            continue
        bad = j if i == pay else i
        out.setdefault(bad, f'says the same as b{(i if bad == j else j) + 1:02d} '
                            f'("{lines[i if bad == j else j][:60]}", similarity {raw:.2f})')
    return out


def code_check(lines, ctx) -> dict:
    """{index: why} from code alone: each line's code checks and the repeats across the script by meaning."""
    problems = {}
    for k, c in enumerate(lines):
        why = code_problem(c, k, lines, ctx)
        if why:
            problems[k] = why
    for k, why in repeats(lines, ctx).items():
        problems.setdefault(k, why)
    return problems


def check(lines, ctx, ask, limit=None) -> dict:
    """{problems: {index: why}, ids: {index: fact ids}, stopped} for a whole script: code checks on every line, the
    judge on the lines code passes, repeats across the script by meaning. With `limit`, the judge stops once the
    script has more failing lines than that (it can no longer win: another script has at most `limit`)."""
    problems, ids = code_check(lines, ctx), {}
    stopped = False
    for k, c in enumerate(lines):
        if k in problems:
            continue
        if limit is not None and len(problems) > limit:
            stopped = True
            break
        why, used = judge_problem(c, k, lines, ctx, ask)
        ids[k] = used
        if why:
            problems[k] = why
    return dict(problems=problems, ids=ids, stopped=stopped)


# ------------------------------------------------------------------ scoring

def pair_scores(lines, ask) -> dict:
    """{index of the second line: (score 1-5, why)} for every adjacent pair, from one call of the flow judge."""
    from . import grounding
    if ask is None or len(lines) < 2:
        return {}
    try:
        got = (ask(grounding.FLOW_SYSTEM, 'Lines:\n' + '\n'.join(f'{k}. {x}' for k, x in enumerate(lines, 1)),
                   grounding.FLOW_SCHEMA) or {}).get('pairs') or []
    except Exception:
        return {}
    out = {}
    for x in got:
        try:
            k, sc = int(x.get('second')) - 1, max(1, min(5, int(x.get('score'))))
        except (TypeError, ValueError):
            continue
        if 1 <= k < len(lines):
            out.setdefault(k, (sc, ' '.join(str(x.get('why') or '').split())[:160]))
    return out


def score(lines, ctx, ask) -> dict:
    """{total, hook, flow, length, pairs}: the judge's hook gap (1-5), the mean flow score of the pairs (1-5) and,
    from code, how close the script is to its length (0 to 1)."""
    hook = 3
    try:
        a = ask(SCORE_SYSTEM, f'Topic: {ctx["episode"].topic}\nHook: {lines[0]}', SCORE_SCHEMA) or {}
        hook = max(1, min(5, int(a.get('gap') or 3)))
    except Exception:
        pass
    pairs = pair_scores(lines, ask)
    flow = round(sum(s for s, _ in pairs.values()) / len(pairs), 2) if pairs else 3.0
    words = sum(len(x.split()) for x in lines)
    length = round(max(0.0, 1 - abs(words - ctx['words_total']) / max(1, ctx['words_total'])), 2)
    return dict(total=round(hook + flow + length, 2), hook=hook, flow=flow, length=length, words=words,
                pairs={k: list(v) for k, v in pairs.items()})


# ------------------------------------------------------------------ repairing one line

def fix_line(ctx, lines, k, why, ask, write, n=4, avoid=()) -> tuple[str, list, list]:
    """(line, fact ids, tried) for line k written again by best-of-N with the rest of the script fixed: the
    candidates go through the same checks (in the script) and the ranker picks among those that pass; ('', [], tried)
    when none passes."""
    from . import bestof
    pb = dict(ctx['pbs'][k])
    nb = len(ctx['pbs'])
    kind = 'hook' if k == 0 else 'loop' if k == nb - 1 else 'beat'
    if kind == 'beat' and k >= 2:
        pb['link'] = True
    if kind == 'hook':
        pb.update(answer=ctx['core'], answer_words=ctx['answer_words'],
                  hook_fact_claim=ctx['by'][ctx['hook_fact']].claim if ctx['hook_fact'] in ctx['by'] else '')
    lo, hi = ctx['words'][k][:2]
    rows = bestof.shapes(kind, ctx['episode'].slug) if kind in ('hook', 'loop') else ()
    prev, nxt = (lines[k - 1] if k else ''), (lines[k + 1] if k + 1 < nb else '')
    caution = '; '.join(c for c in (bestof_caution(ctx['by'][i]) for i in ctx['ids'][k] if i in ctx['by']) if c)
    cands = bestof.candidates(kind, pb, ctx['research'][k], prev, nxt, lo, hi, n if kind == 'beat' else bestof.N_HOOK,
                              write or ask, ctx['interp'], rows, tuple(avoid), caution,
                              f'- "{lines[k]}": {why}\n(no stock line; say only what its fact says, in plain words)')
    ok, used = [], {}
    for c in cands:
        trial = lines[:k] + [c] + lines[k + 1:]
        if any(a.lower() in c.lower() for a in avoid) or code_problem(c, k, trial, ctx) or k in repeats(trial, ctx):
            continue
        bad, ids = judge_problem(c, k, trial, ctx, ask)
        if not bad:
            ok.append(c)
            used[c] = ids
    if not ok:
        return '', [], cands
    best, _ = bestof.rank(kind, ok, prev, nxt, ask)
    return ok[best], used[ok[best]], cands


def rewrite(episode, beat_id: str, avoid=(), ask=None, why='') -> str:
    """One beat of a finished script written again (the self-repair: a word the voice does not say clearly), through the
    same checks as the whole script, with the other lines fixed. The chosen line, or '' when none passes (the line then
    stays: no stock line, no research sentence)."""
    from . import bestof, entail
    write = None if ask is not None else bestof.write_ask()   # a given ask (a stand-in) also writes
    ask = ask or entail.default_ask()
    if ask is None:
        return ''
    k = next((i for i, b in enumerate(episode.beats) if b.id == beat_id), None)
    if k is None:
        return ''
    fp = (episode.meta or {}).get('fact_plan') or {}
    plan = list(fp.get('beats') or [])
    if len(plan) != len(episode.beats):
        plan = [dict(role=b.role, kind='fact' if b.source_ids else 'transition', fact_ids=list(b.source_ids), claim='')
                for b in episode.beats]
    plan = [dict(p, fact_ids=list(b.source_ids) if p.get('kind') == 'fact' else p.get('fact_ids', []))
            for p, b in zip(plan, episode.beats)]
    lines = [b.narration for b in episode.beats]
    ctx = context(episode, episode.sources, dict(fp, beats=plan), len(' '.join(lines).split()))
    if k == 0 and episode.beats[0].source_ids:
        ctx['ids'][0] = list(episode.beats[0].source_ids)
    new, _, _ = fix_line(ctx, lines, k, why or 'written again: ' + (', '.join(avoid) or 'the line'), ask, write,
                         avoid=avoid)
    return new


def droppable(ctx, lines, k, rate) -> bool:
    """True when beat k may go: a middle beat that is not the problem, the twist or the payoff, the story keeps a
    rising beat after the problem (two for a mystery or countdown) and a twist or reveal, and the video stays above
    MIN_SECONDS."""
    roles = [p['role'] for p in ctx['pbs']]
    if k in (0, len(roles) - 1) or roles[k] in ('problem', 'twist', 'payoff'):
        return False
    rest = roles[:k] + roles[k + 1:]
    need = 2 if ctx['episode'].story_format in ('mystery', 'countdown') else 1
    if sum(1 for r in rest if r in ('stakes', 'clue', 'reason')) < need or not any(r in ('twist', 'reveal') for r in rest):
        return False
    if len(rest) < 7:
        return False
    words = sum(len(x.split()) for j, x in enumerate(lines) if j != k)
    return words / max(0.1, rate) >= MIN_SECONDS


# ------------------------------------------------------------------ the whole step

def default_write(cache=None, ledger=None):
    """The writer's call for one whole script (the writer model, a seed and a temperature per script)."""
    from . import config, llm
    if config.script_provider() != 'ollama':
        return None
    model = config.ollama_writer_model()
    return lambda s, u, sc, k: llm.generate_json(s, u, sc, MAX_TOKENS, cache=cache, ledger=ledger, step='script',
                                                 detail=f'whole script {k + 1}', temperature=TEMPERATURE,
                                                 seed=SEED + k, model=model)


def write(episode, facts, o, plan, words_total, ask, rate, write_script=None, write_line=None, log=None) -> dict:
    """The narration of every beat (in place on the outline o, and plan['beats'] when a beat is dropped). Returns the
    log entry: {scripts: [{lines, problems, score}], winner, repairs, flow, dropped, unresolved, hooks}."""
    ctx = context(episode, facts, plan, words_total)
    write_script = write_script or (lambda s, u, sc, k: ask(s, u, sc))
    scripts = []
    for k in range(N_SCRIPTS):
        lines = draft(ctx, k, write_script)
        scripts.append(dict(script=k + 1, lines=lines, problems={} if lines else {-1: 'the writer gave no usable script'},
                            ids={}, score=None, stopped=False))
    # the judge goes through the scripts with the fewest code failures first, each only while it can still win
    best = None
    for row in sorted((r for r in scripts if r['lines']), key=lambda r: (len(code_check(r['lines'], ctx)), r['script'])):
        got = check(row['lines'], ctx, ask, limit=best)
        row.update(problems=got['problems'], ids=got['ids'], stopped=got['stopped'])
        if not got['stopped']:
            best = len(got['problems']) if best is None else min(best, len(got['problems']))
    usable = [r for r in scripts if r['lines']]
    if not usable:
        raise RuntimeError('the writer gave no usable script in %d tries' % N_SCRIPTS)
    fewest = min(len(r['problems']) for r in usable if not r['stopped'])
    contenders = [r for r in usable if len(r['problems']) == fewest and not r['stopped']]
    for r in contenders:
        r['score'] = score(r['lines'], ctx, ask)
    win = max(contenders, key=lambda r: (r['score']['total'], -r['script']))
    lines, ids = list(win['lines']), dict(win['ids'])
    entry = dict(stage='whole', version=WHOLE_VERSION, scripts=scripts, winner=win['script'],
                 survived=fewest == 0, repairs=[], flow=[], dropped=[], unresolved=[])
    # 1. the winner's failing lines, one at a time: the same line of the next-best scripts (user 2026-11-16: "next-best
    # whole script -> rewrite just that line -> drop the beat"), then a rewrite; kept only if it passes in this script
    others = sorted((r for r in scripts if r is not win and r['lines']),
                    key=lambda r: (len(r['problems']), -((r['score'] or {}).get('total') or 0), r['script']))
    for k in sorted(win['problems']):
        if k < 0:
            continue
        new, used, tried = '', [], []
        for r in others:
            c = r['lines'][k]
            if c == lines[k] or c in tried:
                continue
            tried.append(c)
            trial = lines[:k] + [c] + lines[k + 1:]
            if code_problem(c, k, trial, ctx) or k in repeats(trial, ctx):
                continue
            bad, got = judge_problem(c, k, trial, ctx, ask)
            if not bad:
                new, used = c, got
                break
        if not new:
            new, used, more = fix_line(ctx, lines, k, win['problems'][k], ask, write_line)
            tried += more
        entry['repairs'].append(dict(beat=f'b{k + 1:02d}', was=lines[k], why=win['problems'][k], now=new or lines[k],
                                     fixed=bool(new), tried=tried))
        if new:
            lines[k], ids[k] = new, used
    # 2. weak pairs of the winner: the second line again, kept only if it passes and the pair scores higher
    pairs = (win['score'] or {}).get('pairs') or {}
    weak = sorted(((int(k), v[0], v[1]) for k, v in pairs.items() if v[0] <= FLOW_FIX and int(k) < len(lines) - 1),
                  key=lambda t: (t[1], t[0]))[:FLOW_REWRITES]
    from . import grounding
    for k, sc, why in weak:
        if any(r['beat'] == f'b{k + 1:02d}' and not r['fixed'] for r in entry['repairs']):
            continue
        new, used, _ = fix_line(ctx, lines, k, f'does not follow from the line before ("{lines[k - 1]}"): {why}', ask,
                                write_line)
        better = grounding.pair_score(lines[k - 1], new, ask) if new and new != lines[k] else 0
        entry['flow'].append(dict(beat=f'b{k + 1:02d}', was=lines[k], score=sc, why=why, now=new if better > sc else lines[k],
                                  new_score=better))
        if better > sc:
            lines[k], ids[k] = new, used
    # 3. what no rewrite fixed: the beat goes when it may, else the line stays and is reported
    still = [k for k in sorted(win['problems']) if k >= 0
             and not any(r['beat'] == f'b{k + 1:02d}' and r['fixed'] for r in entry['repairs'])]
    beats = o.get('beats') or []
    for k in sorted(still, reverse=True):
        if droppable(ctx, lines, k, rate):
            entry['dropped'].append(dict(beat=f'b{k + 1:02d}', role=ctx['pbs'][k]['role'], line=lines[k],
                                         why=win['problems'][k]))
            for seq in (lines, ctx['pbs'], ctx['ids'], ctx['words'], ctx['research'], ctx['allowed'], plan['beats'],
                        beats):
                if k < len(seq):
                    seq.pop(k)
            ids = {(j if j < k else j - 1): v for j, v in ids.items() if j != k}
        else:
            entry['unresolved'].append(dict(beat=f'b{k + 1:02d}', line=lines[k], why=win['problems'][k]))
    for k, ob in enumerate(beats[:len(lines)]):
        ob['narration'] = lines[k]
        ob['source_ids'] = list(ids.get(k, ctx['ids'][k] if ctx['pbs'][k]['kind'] == 'fact' else []))
    # every hook option is a checked hook: the winner's, then the other scripts' hooks that passed
    hooks = [lines[0]] + [r['lines'][0] for r in scripts if r is not win and r['lines'] and 0 not in r['problems']
                          and 0 in r['ids'] and r['lines'][0] != lines[0]]   # judged and passed
    hooks = list(dict.fromkeys(hooks))
    entry['hooks'] = hooks
    for k, opt in enumerate(o.get('hook_options') or []):
        opt['line'] = hooks[k] if k < len(hooks) else hooks[0]
        opt['loop_line'] = lines[-1]
        opt['answer'] = ctx['core'] or opt.get('answer', '')
    entry['lines'] = list(lines)
    if log is not None:
        log.append(entry)
    return entry


def view(entry) -> list:
    """The step as lines for the script log."""
    if not entry:
        return []
    out = []
    for r in entry.get('scripts') or []:
        s = r.get('score') or {}
        out.append(f'script {r["script"]}: {len(r["problems"])} failing line(s)' + (' (judging stopped: it could not win)'
                                                                                    if r.get('stopped') else '')
                   + (f'; score {s["total"]} (hook {s["hook"]}, flow {s["flow"]}, length {s["length"]})' if s else '')
                   + (' <- kept' if r['script'] == entry.get('winner') else ''))
        for k, why in sorted(r['problems'].items()):
            out.append(f'  b{k + 1:02d}: {why}' if k >= 0 else f'  {why}')
    for r in entry.get('repairs') or []:
        out.append(f'repair {r["beat"]}: ' + (f'"{r["was"]}" -> "{r["now"]}"' if r['fixed'] else
                                              f'no rewrite passed ({r["why"]})'))
    for r in entry.get('flow') or []:
        out.append(f'flow {r["beat"]}: pair scored {r["score"]}' + (f' -> written again, scored {r["new_score"]}: '
                                                                    f'"{r["now"]}"' if r['now'] != r['was'] else
                                                                    f' -> kept (the rewrite scored {r["new_score"]})'))
    for r in entry.get('dropped') or []:
        out.append(f'dropped {r["beat"]} ({r["role"]}): "{r["line"]}" ({r["why"]})')
    for r in entry.get('unresolved') or []:
        out.append(f'unresolved {r["beat"]}: "{r["line"]}" ({r["why"]})')
    return out
