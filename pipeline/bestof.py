"""Best-of-N writing (user 2026-11-15): "accurate now, but flat". Each beat's narration is chosen from several
candidates instead of being written once and patched.

For every beat, in order: one local-model call writes 4 candidate lines in story voice (6 for the hook and the
loop) from the beat's plan (its claim and research, or its no-fact kind). Code drops a candidate whose claims fail the
fact check (pipeline/entail.py `verdict`: claims, not colour), that copies its research line, repeats another line,
breaks the word rule, is the wrong length, or, for a hook, argues against something nobody claims. One more call
ranks the survivors for vividness, rhythm and fit with the previous line; the top one is kept. A beat with no
survivor keeps its planned claim (or, for a no-fact beat, a short line that states none).

Hooks and loops fill shapes from data/hooks.json (proven shapes: "you've been told X", "older than", an impossible
number, a direct question with stakes, before/after ...). The loop repeats a word of the hook.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

BEST_VERSION = 'best-v2'   # v2: myth lines must be the belief people hold; questions are split into claims too
BANK = Path(__file__).resolve().parent.parent / 'data' / 'hooks.json'
N_BEAT, N_HOOK = 4, 6
HOOK_MAX_WORDS = 8

WRITE_SYSTEM = """You write ONE beat of a narrated 45-second science Short in the host's story voice: talking to a
friend, vivid, with rhythm when read aloud, a little dramatic, contractions welcome. Colour is welcome ("black liquid
gold", "millions of years in the dark"), but every claim about the world must be one the beat's research makes, and
every group of living things keeps the research's words or a plain equivalent. Never mix up categories (dinosaurs
were land animals, not sea creatures). Do not copy the research sentence. Give the number of different versions
asked for, each in the length asked for."""

RANK_SYSTEM = """You pick the best line for one beat of a narrated science Short: the most vivid, the best rhythm read
aloud, the most natural follow-on from the previous line, and a good set-up for the next beat. For a hook: the one a
viewer cannot scroll past, never one that argues against something nobody believes. Answer with the number of the
best line."""

BELIEF_SYSTEM = """A line of a science video says what people believe (the myth the video corrects). Answer whether it
reports a belief people really hold, as the misconception given states it, without inventing a sillier or different
version of it ("oil is dinosaur poop" is a strawman of "oil comes from dinosaurs") and without mixing up categories
("dinosaurs in the sea"). The common form of the myth, in other words ("oil comes from dinosaur bones"), is a real
belief."""
BELIEF_SCHEMA = dict(type='object', required=['real_belief', 'why'], properties=dict(real_belief=dict(type='boolean'),
                                                                                     why=dict(type='string')))
LINES_SCHEMA = dict(type='object', required=['lines'], properties=dict(lines=dict(
    type='array', items=dict(type='string'), minItems=1, maxItems=N_HOOK)))
RANK_SCHEMA = dict(type='object', required=['best', 'why'], properties=dict(best=dict(type='integer'),
                                                                             why=dict(type='string')))

KIND_GUIDE = {
    'question': 'ask the question the next beat answers; do not answer it; state no fact',
    'transition': 'move the story on ("But there is a catch."); do not explain anything; state no fact',
    'reaction': 'the host reacts with surprise or doubt to the previous line in a few words ("Hold on. Is that really '
                'true?"); do not explain or correct it (the next beat does that) and state no fact',
    'callback': 'repeat words of the hook with the new meaning; state no fact',
    'myth': 'say what many people believe (the misconception below), clearly as a belief ("Many think..."); keep the '
            'belief itself sensible (no category mix-ups)',
}
STRAWMAN_STOP = set('call calling called think thought told believe believed everyone people anyone nobody really quite '
                    'actually true truth story whole real again myth wrong stop forget never about maybe sure exactly '
                    'just even thing things what where come comes came made make'.split())
SLANG = set('goo gooey gunk gloop glop muck stuff junk thingy poop dino dinos yucky gross ick icky sludge critters '
            'gazillion'.split())
UNFINISHED = re.compile(r"(,|;|:|-|\b(and|or|but|if|so|the|a|of|to|that|it's|its))\s*[.!?]$", re.I)
ARGUE = re.compile(r"\b(if you think|you think|you've been told|you were told|they told you|not|isn't|aren't|wasn't|weren't|don't|doesn't|didn't|stop|no|never|wrong|myth|lie|lies|forget|"
                   r"quit|nope)\b", re.I)


def bank() -> dict:
    return json.loads(BANK.read_text())


def shapes(kind: str, slug: str, n: int = N_HOOK) -> list:
    """n shapes of the bank, rotated by the episode so videos do not all open the same way."""
    rows = bank()['hooks' if kind == 'hook' else 'loops']
    start = sum(map(ord, slug or '')) % len(rows)
    return [rows[(start + k * 3) % len(rows)] for k in range(min(n, len(rows)))]


def strawman(hook: str, interp: dict, twist_claim: str = '', topic: str = '') -> str:
    """'' when the hook is fair; else the words it argues against that nobody claims (user 2026-11-15: "Stop calling it
    dinosaur poop"). Only for a topic that corrects a myth: the words a hook argues against (after "not", "stop
    calling it", "wrong", "myth" ...) may only be what the topic's misconception, claims, myth subjects, question or
    twist fact name. A plain counter-reveal of the viewer's assumption ("Your salary didn't shrink") is fair."""
    from .qa import content_words
    from .understand import has_myth
    if not has_myth(interp or {}):
        return ''
    known = content_words(' '.join([interp.get('misconception', ''), interp.get('question', ''), interp.get('topic', ''),
                                    twist_claim, topic] + list(interp.get('claims') or []) + list(interp.get('myth_subjects') or [])))
    extra = set()
    for sent in re.split(r'(?<=[.!?])\s+', hook or ''):
        m = ARGUE.search(sent)
        if m:
            extra |= content_words(sent[m.end():]) - known - STRAWMAN_STOP
    return ', '.join(sorted(extra))


def real_belief(line: str, interp: dict, ask) -> tuple[bool, str]:
    """(True, why) when a myth line reports the misconception people really hold (user 2026-11-15: "oil's dinosaur
    poop" is no one's belief). A model that does not answer lets the line through to the other checks."""
    misc = (interp or {}).get('misconception', '')
    if not misc:
        return True, 'no misconception to compare with'
    try:
        claims = '; '.join(interp.get('claims') or [])
        subjects = ', '.join(interp.get('myth_subjects') or [])
        a = ask(BELIEF_SYSTEM, f'Line: {line}\nThe misconception: {misc}'
                + (f'\nThe topic says: {claims} (so the myth is the opposite)' if claims else '')
                + (f'\nWhat the myth is about: {subjects}' if subjects else ''), BELIEF_SCHEMA) or {}
    except Exception:
        return True, 'not checked'
    return (a.get('real_belief') is not False), ' '.join(str(a.get('why') or '').split())[:160]


def shares_stem(a: str, b: str) -> bool:
    """True when two lines share a content word, plurals folded ("dinosaurs" / "dinosaur")."""
    from .qa import content_words
    fold = lambda t: {w[:-1] if w.endswith('s') and len(w) > 4 else w for w in content_words(t)}
    return bool(fold(a) & fold(b))


def _clean(line: str) -> str:
    line = ' '.join(str(line or '').split()).strip('"\' ')
    line = re.sub(r'^\s*\d+[.)]\s*', '', line)
    return line if not line or line[-1] in '.!?' else line + '.'


def candidates(kind, beat, research, prev, nxt, lo, hi, n, ask, interp, shape_rows=(), avoid=(), caution='',
               retried='') -> list:
    """The model's n candidate lines for one beat."""
    misc = interp.get('misconception', '')
    lines = [f'BEAT: {beat["role"]}' + (f' (kind: {beat["kind"]})' if beat['kind'] != 'fact' else ''),
             f'Previous line: {prev or "(none: this opens the video)"}', f'Next beat: {nxt or "(none: this ends it)"}']
    if beat['kind'] == 'fact' or kind in ('hook', 'loop'):
        if beat.get('claim'):
            lines.append(f'What it says (its claim): {beat["claim"]}')
        lines.append(f'Its research (the only claims allowed):\n{research or "(none: state no fact)"}')
        if caution:
            lines.append(f'NOTE: {caution}')
    else:
        lines.append(f'What it does: {KIND_GUIDE.get(beat["kind"], KIND_GUIDE["transition"])}')
    if misc and (beat['kind'] == 'myth' or kind == 'hook'):
        lines.append(f'The misconception viewers hold: {misc}')
    if shape_rows:
        lines.append('Write ONE line per shape, in this order:\n' + '\n'.join(
            f'{k}. {r["shape"]}: {r["how"]} (shape only, e.g. on another topic: "{r["example"]}")'
            for k, r in enumerate(shape_rows, 1)))
    if avoid:
        lines.append(f'Do not use the word(s): {", ".join(avoid)}')
    if retried:
        lines.append('EARLIER LINES FAILED THE CHECKS (say only what the research says, in plain words):\n' + retried)
    lines.append(f'Give {n} different versions, each {lo}-{hi} words.')
    try:
        a = ask(WRITE_SYSTEM, '\n'.join(lines), LINES_SCHEMA) or {}
    except Exception:
        return []
    out = []
    for x in (a.get('lines') if isinstance(a.get('lines'), list) else [])[:n]:
        x = _clean(x)
        if x and x not in out:
            out.append(x)
    return out


def rank(kind, survivors, prev, nxt, ask) -> tuple[int, str]:
    if len(survivors) == 1:
        return 0, 'the only line that passed'
    user = (f'Beat: {kind}\nPrevious line: {prev or "(none)"}\nNext beat: {nxt or "(none)"}\n\nLines:\n'
            + '\n'.join(f'{k}. {x}' for k, x in enumerate(survivors, 1)))
    try:
        a = ask(RANK_SYSTEM, user, RANK_SCHEMA) or {}
        k = int(a.get('best')) - 1
    except Exception:
        return 0, 'not ranked (the model did not answer); the first line that passed'
    return (k, ' '.join(str(a.get('why') or '').split())[:200]) if 0 <= k < len(survivors) else (0, 'out of range')


def write_beat(episode, facts, plan_beats, k, chosen, nxt, words, ask, avoid=(), log=None, retried='') -> dict:
    """Best-of-N for beat k: {line, ids, tried, kept, why}. `chosen` are the lines chosen so far (earlier beats)."""
    from . import entail, grounding
    from .script import copies
    interp = (episode.meta or {}).get('interpretation') or {}
    episode = episode.model_copy(update=dict(sources=list(facts)))   # the research of this script (not yet on it)
    pb = plan_beats[k]
    role = pb['role']
    twist = (interp.get('twist') or {}).get('id') or (episode.meta.get('fact_plan') or {}).get('twist', '')
    kind = 'hook' if k == 0 else 'loop' if k == len(plan_beats) - 1 else 'beat'
    ids = list(pb['fact_ids']) or ([twist] if kind in ('hook', 'loop') and twist else [])
    by = {f.id: f for f in facts}
    research = entail.research_text(ids, facts)
    allowed = entail.allowed_groups(episode, ids)
    lo, hi = words if kind == 'beat' else ((3, HOOK_MAX_WORDS) if kind == 'hook' else (4, 14))
    n = N_HOOK if kind in ('hook', 'loop') else N_BEAT
    rows = shapes(kind, episode.slug) if kind in ('hook', 'loop') else ()
    caution = '; '.join(c for c in (grounding.caution(by[i]) for i in ids if i in by) if c)
    prev = chosen[-1] if chosen else ''
    cands = candidates(kind, pb, research, prev, nxt, lo, hi, n, ask, interp, rows, avoid, caution, retried)
    hook = chosen[0] if chosen else ''
    fact_lines = [by[i].claim for i in ids if i in by] + [by[i].quote for i in ids if i in by and by[i].quote]
    out, why_not = [], {}
    twist_groups = (grounding.groups(f'{by[twist].claim} {by[twist].quote}') - grounding.myth_groups(interp, facts)
                    if role == 'twist' and twist in by else set())
    for c in cands:
        w = len(c.split())
        if not lo <= w <= hi + 2:
            why_not[c] = f'{w} words'
        elif UNFINISHED.search(c) or set(re.findall(r"[a-z']+", c.lower())) & SLANG:
            why_not[c] = 'slang or an unfinished line'
        elif twist_groups and not grounding.groups(c) & (grounding.allowed_for([f'{by[twist].claim} {by[twist].quote}'])
                                                          - grounding.myth_groups(interp, facts)):
            why_not[c] = f'the twist must say what its fact names ({", ".join(sorted(twist_groups))})'
        elif any(a.lower() in c.lower() for a in avoid):
            why_not[c] = 'uses an avoided word'
        elif copies(c, fact_lines):
            why_not[c] = 'copies its research line'
        elif entail.repeats(c, chosen):
            why_not[c] = 'repeats an earlier line'
        elif set(entail.NUMBER.findall(c)) - set(entail.NUMBER.findall(research)):
            why_not[c] = 'a number the research does not have'
        elif kind == 'hook' and strawman(c, interp, by[twist].claim if twist in by else '', episode.topic):
            why_not[c] = ('argues against something nobody claims ('
                          + strawman(c, interp, by[twist].claim if twist in by else '', episode.topic) + ')')
        elif kind == 'loop' and hook and not shares_stem(c, hook):
            why_not[c] = 'does not repeat a word of the hook'
        elif pb['kind'] == 'myth' and not real_belief(c, interp, ask)[0]:
            why_not[c] = f'not the belief people hold ({real_belief(c, interp, ask)[1]})'
        else:
            system = entail.CHECK_SYSTEM if ids else entail.NONFACT_SYSTEM
            ok, why = entail.verdict(c, research, allowed, ask, system)
            if ok is False and ids and kind in ('hook', 'loop'):   # a hook or loop may also state no fact at all
                ok2, _ = entail.verdict(c, '', entail.allowed_groups(episode, ()), ask, entail.NONFACT_SYSTEM)
                ok = True if ok2 is not False else ok
            if ok is False:
                why_not[c] = why
            else:
                out.append(c)
    if not out and cands and not retried:            # one more call that sees why each line failed
        why = '\n'.join(f'- "{c}": {r}' for c, r in why_not.items())
        more = write_beat(episode, facts, plan_beats, k, chosen, nxt, words, ask, avoid=avoid, retried=why)
        more['tried'] = cands + more['tried']
        more['rejected'] = {**why_not, **more['rejected']}
        if log is not None:
            log.append(more)
        return more
    entry = dict(beat=f'b{k + 1:02d}', role=role, kind=pb['kind'], tried=cands, rejected=why_not, kept=out)
    if out:
        best, why = rank(kind, out, prev, nxt, ask)
        entry.update(line=out[best], why=why)
    else:                                          # nothing passed: the plan's checked claim, or a plain no-fact line
        fallback = pb.get('claim') or {'hook': 'What is really in your tank?',
                                        'loop': f'{hook} Now you know.' if hook else ''}.get(kind, '')
        entry.update(line=fallback or 'Here is the surprise.',
                     why='no candidate passed: ' + ('the planned claim' if pb.get('claim') else 'a plain line'))
    claims_facts = entail.split_claims(entry['line'], ask)[0] or []
    entry['ids'] = ids if (pb['fact_ids'] or any(c['kind'] == 'fact' for c in claims_facts)) else []
    if log is not None:
        log.append(entry)
    return entry


def per_beat_words(ctx_words: int, n_beats: int) -> tuple[int, int]:
    """The word range of a middle beat so the video meets its length: what is left after the hook and the loop,
    shared by the middle beats: from 5 under (a short punchy line is welcome) to 4 over."""
    mid = max(6, round((ctx_words - 6 - 9) / max(1, n_beats - 2)))
    return max(4, mid - 5), mid + 4


def write_all(episode, facts, o, plan, words_total, ask, log=None) -> list:
    """Every beat's narration by best-of-N, in order (in place on the outline o). Returns the log entries."""
    log = log if log is not None else []
    beats = o.get('beats') or []
    pbs = plan['beats']
    words = per_beat_words(words_total, len(pbs))
    chosen = []
    for k, ob in enumerate(beats[:len(pbs)]):
        nb = pbs[k + 1] if k + 1 < len(pbs) else None
        nxt = (f'{nb["role"]}: {nb["claim"]}' if nb and nb.get('claim') else f'{nb["role"]} ({nb["kind"]})') if nb else ''
        e = write_beat(episode, facts, pbs, k, chosen, nxt, words, ask, log=log)
        ob['narration'] = e['line']
        ob['source_ids'] = e['ids']
        chosen.append(e['line'])
    if o.get('hook_options') and beats:
        o['hook_options'][0]['line'] = beats[0]['narration']
        o['hook_options'][0]['loop_line'] = beats[-1]['narration']
    return log


def rewrite_beat(episode, beat_id: str, avoid=(), ask=None) -> str:
    """Best-of-N for one beat of a finished script (the self-repair: a word Whisper does not hear and that has no clean
    plainer swap). The beat's plan line (meta.fact_plan) or, for an older episode, its cited facts. Returns the chosen
    line, or '' when no candidate passed (the line then stays)."""
    from . import entail
    ask = ask or entail.default_ask()
    if ask is None:
        return ''
    k = next((i for i, b in enumerate(episode.beats) if b.id == beat_id), None)
    if k is None:
        return ''
    plan = list(((episode.meta or {}).get('fact_plan') or {}).get('beats') or [])
    if len(plan) != len(episode.beats):
        plan = [dict(role=b.role, kind='fact' if b.source_ids else 'transition', fact_ids=list(b.source_ids), claim='')
                for b in episode.beats]
    plan = [dict(p, fact_ids=list(b.source_ids) if p.get('kind') == 'fact' else p.get('fact_ids', []))
            for p, b in zip(plan, episode.beats)]
    nb = plan[k + 1] if k + 1 < len(plan) else None
    nxt = episode.beats[k + 1].narration if nb else ''
    e = write_beat(episode, episode.sources, plan, k, [b.narration for b in episode.beats[:k]], nxt,
                   per_beat_words(len(' '.join(b.narration for b in episode.beats).split()), len(plan)), ask, avoid=avoid)
    return e['line'] if e['kept'] else ''


def view(log) -> list:
    out = []
    for e in log or []:
        out.append(f'{e["beat"]} {e["role"]}: {len(e["kept"])} of {len(e["tried"])} passed -> "{e["line"]}"'
                   + (f' ({e["why"]})' if e.get('why') else ''))
    return out
