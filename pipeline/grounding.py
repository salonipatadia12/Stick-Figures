"""Fact-first writing (user 2026-11-14): the facts are chosen before any narration is written, so the fact check
after writing confirms instead of rewrites.

1. The twist fact is picked in code (`twist_fact`): the fact that names what the thing really is, preferring the
   facts the topic's claim was judged on (2026-11-14: "oil formed from the remains of animals and plants (diatoms) ... before dinosaurs existed"
   IS the twist of the crude-oil video). The storylines are pitched on it and their twist is checked against it.
2. The plan (`make_plan`), one local-model call before the outline: every beat gets its role, its KIND and, for a
   fact beat, the fact ids it may use and its CLAIM, one plain line drawn from those facts. A beat of any other kind
   (question, transition, reaction, callback, myth) states no fact and cites nothing. The roles (`skeleton`) and the
   fact of every fact beat (`assign_facts`) are chosen in code; the model writes the claims. Each claim is checked (word
   precision in code, then the yes/no judge against the fact's quote); a claim that still fails after the repairs
   becomes the fact's own line from research. The narration is then written from the claims (pipeline/script.py).
3. Word precision (`imprecise`), in code, for the plan's claims, the narration and every on-screen text: when a fact
   names a group of living things ("animals and plants (diatoms)", "organisms"), a line uses that group's own words
   or a direct plain-English equivalent ("plankton", "tiny sea plants and animals", "microscopic"), never a broader
   or different category ("bacteria", "microbes", "bugs"). The equivalents are a fixed table (`SAME`, `GLOSS`); the
   gloss also goes to the judge with the research, so a plain equivalent is not read as an addition.
"""
from __future__ import annotations

import re

GROUNDING_VERSION = 'ground-v1'

# words that name a group of living things -> the group
_GROUP_WORDS = {
    'bacteria': ('bacteria', 'bacterium', 'bacterial'),
    'microbes': ('microbe', 'microbes', 'microbial', 'microorganism', 'microorganisms', 'germ', 'germs'),
    'bugs': ('bug', 'bugs'),
    'insects': ('insect', 'insects'),
    'plankton': ('plankton', 'phytoplankton', 'zooplankton', 'planktonic'),
    'diatoms': ('diatom', 'diatoms'),
    'algae': ('alga', 'algae', 'algal', 'seaweed', 'seaweeds'),
    'plants': ('plant', 'plants'),
    'animals': ('animal', 'animals'),
    'creatures': ('creature', 'creatures', 'beast', 'beasts'),
    'organisms': ('organism', 'organisms'),
    'dinosaurs': ('dinosaur', 'dinosaurs'),
    'reptiles': ('reptile', 'reptiles', 'lizard', 'lizards'),
    'vertebrates': ('vertebrate', 'vertebrates'),
    'mammals': ('mammal', 'mammals'),
    'fish': ('fish', 'fishes'),
    'fungi': ('fungus', 'fungi', 'fungal'),
    'viruses': ('virus', 'viruses', 'viral'),
    'trees': ('tree', 'trees'),
}
GROUP_OF = {w: g for g, words in _GROUP_WORDS.items() for w in words}
# a group a fact names -> the groups a line may use for it: its own words or a direct plain-English equivalent
SAME = {
    'diatoms': {'diatoms', 'plankton', 'algae', 'plants'},     # diatoms are microscopic algae of the plankton
    'plankton': {'plankton', 'plants', 'animals', 'algae'},     # "tiny sea plants and animals"
    'algae': {'algae', 'plants'},
    'animals': {'animals', 'creatures'},
    'creatures': {'creatures', 'animals'},
}
# plain-English equivalents shown to the judge next to the research (keyed by a word the research uses)
GLOSS = {
    'diatom': 'diatoms are microscopic algae, part of the plankton: tiny sea plants',
    'plankton': 'plankton are tiny, often microscopic, plants and animals that drift in the sea',
    'marine': '"marine" means of the sea or the ocean',
    'organism': '"organisms" means living things, life',
    'million': '"millions of years ago" means ancient, long ago, prehistoric',
}
WORD = re.compile(r"[a-z]+")


def groups(text: str) -> set:
    """The groups of living things a text names."""
    return {GROUP_OF[w] for w in WORD.findall(str(text or '').lower()) if w in GROUP_OF}


def allowed_for(texts) -> set:
    """The groups a line may name when it stands on these texts (facts)."""
    out = set()
    for g in groups(' '.join(texts)):
        out |= SAME.get(g, {g})
    return out


def gloss(text: str) -> str:
    """Glossary lines for the words of `text` that have a plain-English equivalent ('' when none)."""
    low = str(text or '').lower()
    rows = [v for k, v in GLOSS.items() if re.search(rf'\b{k}', low)]
    return ('Plain words for it: ' + '; '.join(rows) + '.') if rows else ''


def fact_texts(sources, ids=None) -> list:
    return [f'{s.claim} {s.quote}' for s in sources if ids is None or s.id in ids]


def myth_groups(interp: dict, sources) -> set:
    """Groups a line may name as the myth: the myth subjects and the claims' groups, plus the misconception's groups
    that are not what the facts say it really is (2026-11-14: the misconception said "dinosaurs and plankton";
    plankton is the truth)."""
    interp = interp or {}
    subject = groups(' '.join(list(interp.get('myth_subjects') or []) + list(interp.get('claims') or [])))
    truth = allowed_for(fact_texts(sources)) - subject
    return subject | (groups(interp.get('misconception', '')) - truth)


TIME_WORDS = {'age', 'era', 'eras', 'time', 'times', 'days', 'reign', 'dawn', 'rise', 'period', 'world'}
BEFORE_WORDS = {'before', 'after', 'since', 'until', 'till', 'when', 'while', 'older', 'earlier', 'later'}
FILLER = {'the', 'a', 'any', 'first', 'last', 'early', 'giant', 'big', 'great', 'mighty', 'ancient', 'even', 'very',
          'all', 'those', 'these', 'of'}


def time_phrase(words: list, i: int) -> bool:
    """True when words[i] (a group word) only marks a time: "the age of reptiles", "before dinosaurs existed",
    "older than the first dinosaur" (user 2026-11-15: the word rule fires only when a group names a source)."""
    j = i - 1
    while j >= 0 and words[j] in FILLER - {'of'}:
        j -= 1
    if j >= 1 and words[j] == 'of' and words[j - 1] in TIME_WORDS:
        return True
    if j >= 0 and (words[j] in BEFORE_WORDS or (words[j] == 'than' and j >= 1 and words[j - 1] in BEFORE_WORDS)):
        return True
    return False


def imprecise(text: str, allowed: set) -> list:
    """The words of `text` naming a group of living things that is not in `allowed`, in order (a group word that
    only marks a time is not counted)."""
    out = []
    words = WORD.findall(str(text or '').lower())
    for i, w in enumerate(words):
        if w in GROUP_OF and GROUP_OF[w] not in allowed and w not in out and not time_phrase(words, i):
            out.append(w)
    return out


def precision_why(words, allowed) -> str:
    named = ', '.join(sorted(allowed)) or 'no group of living things'
    return (f'says {", ".join(repr(w) for w in words)}, a group its facts do not name (they name: {named}); use the '
            "facts' own words for it or a direct plain equivalent, never a broader or different category")


# ------------------------------------------------------------------ the twist fact

def quality(s) -> int:
    """+2 a scientific source, 0 a general government or university page, -3 a page simplified for kids or students
    (user 2026-11-15: prefer the specific scientific source when both exist)."""
    from .script import QUALITY_SCORE, source_quality
    return QUALITY_SCORE[source_quality(s.url)]


def caution(s) -> str:
    """The phrasing note for a fact that stands only on a simplified page ('' otherwise)."""
    from .script import source_quality
    return ('from a page simplified for students: say it with matching caution ("much of it", "most", "scientists '
            'think"), never more certain or more exact than the page') if source_quality(s.url) == 'simplified' else ''


def twist_fact(interp: dict, sources) -> str:
    """The id of the strongest fact for the twist, picked in code: among the facts the topic's claims were judged on
    (all facts when there are none), the one that names most of what the thing really is (groups of living things,
    not the myth's), then the one closest to the question and the claims. '' when there are no facts."""
    from .qa import content_words
    if not sources:
        return ''
    interp = interp or {}
    ids = [i for v in interp.get('verdicts') or [] if v.get('verdict') == 'supported' for i in v.get('fact_ids') or []]
    myth = myth_groups(interp, sources)
    ask = content_words(' '.join([interp.get('question', '')] + list(interp.get('claims') or [])))

    def score(s):
        # 2026-11-14: the verdict cited only "formed before dinosaurs existed"; the fact that names the plants and
        # animals was another fact of the same research, so every fact is scored and the verdict's facts get a bonus
        text = f'{s.claim} {s.quote}'
        return (2 * len(groups(text) - myth) + len(content_words(text) & ask) + (1 if s.quote else 0)
                + (2 if s.id in ids else 0), -int(s.id[1:]) if s.id[1:].isdigit() else 0)
    top = max(sources, key=score)
    # a near-duplicate from a better source says the same thing: that one is told (a kids page only when it is alone)
    return max([s for s in sources if s is top or _same(s, top)], key=lambda s: (quality(s), score(s))).id


# ------------------------------------------------------------------ the plan

KINDS = ['fact', 'question', 'transition', 'reaction', 'callback', 'myth']
NO_FACT = ('question', 'transition', 'reaction', 'callback', 'myth')
PLAN_REPAIRS = 2
PLAN_MAX_TOKENS = 1200
CLAIM_MAX_WORDS = 18
RELEVANT = 2                      # a fact shares at least this many content words with the topic to be told
DUPLICATE = 0.5                   # facts sharing this much of their content words say the same thing (EIA said it twice)

PLAN_SYSTEM = """You plan an explainer Short beat by beat BEFORE any narration is written: 30-60 s vertical videos,
a host who talks to the viewer. The beats, their story roles and the research fact of every fact beat are given, in
order. For each beat give its kind and its claim.

FACT BEATS (their kind is "fact"): claim = ONE plain sentence of at most 16 words that says what the beat's fact says,
for a viewer of 12: simpler is fine, adding is not. Nothing the fact does not say: no other cause, group, number,
place or time.
OTHER BEATS state NO fact about the world; claim "". Their kind is one of: "question" (asks what a later beat
answers), "transition" (moves the story on: "But there's a catch."), "reaction" (the host reacts: "Wait, really?"),
"callback" (repeats words of the hook or an earlier beat, nothing new), "myth" (what many people believe, said as a
belief: "Many think..."; only the misconception given).

WORDS. When a fact names a group of things ("animals and plants (diatoms)", "marine", "living organisms"), the claim
uses that group's own words or a direct plain-English equivalent ("tiny sea plants and animals", "plankton",
"microscopic sea life"), never a broader or different category: "bacteria", "microbes" or "bugs" are wrong for
plankton or diatoms.

STORY. The hook opens a question; the problem breaks what the viewer believes; the middle beats build toward the
TWIST, the surprise of the whole video; the payoff answers the hook; the loop ties back to the hook. No two claims
alike."""

FACT_ROLES = ('stakes', 'clue', 'reason', 'twist', 'reveal', 'payoff')   # these stand on a fact while facts remain
ROLE_GUIDE = {'hook': 'opens the question', 'setup': 'the myth, or the everyday picture', 'problem': 'breaks the belief',
              'stakes': 'raises the stakes', 'clue': 'a clue toward the twist', 'reason': 'a reason',
              'twist': 'THE TWIST, the surprise', 'reveal': 'shows how it works', 'payoff': 'answers the hook',
              'loop': 'ties back to the hook'}
DEFAULT_KIND = {'hook': 'question', 'setup': 'myth', 'problem': 'transition', 'loop': 'callback'}


def skeleton(fmt: str, n: int) -> list:
    """The story roles, in order, for `n` beats, picked in code (the 9B planner, left to choose them, ended a plan on
    the payoff with no loop, 2026-11-14). Matches the gate's story check: a problem by beat 3, rising beats after it
    (two for a mystery or countdown), a twist before the payoff, then the payoff and the loop."""
    need = 2 if fmt in ('mystery', 'countdown') else 1
    setup, reveal = n >= 8, n >= 9
    rising = max(need, n - 5 - setup - reveal)
    if fmt == 'countdown':
        mid = ['reason'] * rising
    elif fmt in ('mystery', 'revelation'):
        mid = ['clue'] * rising
    else:
        mid = ['stakes'] + ['clue'] * (rising - 1)
    return ['hook'] + (['setup'] if setup else []) + ['problem'] + mid + ['twist'] + (['reveal'] if reveal else []) \
        + ['payoff', 'loop']


def _words(s) -> set:
    from .qa import content_words
    return content_words(f'{s.claim} {s.quote}')


def _same(a, b) -> bool:
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= DUPLICATE


def assign_facts(roles, sources, twist, interp, storyline=None) -> dict:
    """{beat index: fact id}, chosen in code (user 2026-11-14: "the facts are chosen in code before writing"). The
    twist beat says the twist fact; for a topic that corrects a myth, the problem beat says the fact that breaks it
    (another fact the claims were judged on); the payoff the fact closest to the question; the reveal the fact of how it
    works (a cause: heat, pressure, a process); the other middle beats the most relevant facts left. A fact is used
    once, a near-duplicate of a used fact is skipped, and a fact that shares nothing with the topic is never used: a
    beat left without one states no fact."""
    from .qa import content_words
    from .understand import has_myth
    by = {s.id: s for s in sources}
    interp = interp or {}
    topic = content_words(' '.join([interp.get('question', '')] + list(interp.get('claims') or [])
                                   + ([by[twist].claim] if twist in by else [])
                                   + ([storyline.hook, storyline.payoff] if storyline else [])))
    used, out = [], {}

    def free(s):
        return s.id not in [u.id for u in used] and not any(_same(s, u) for u in used)

    def take(k, s):
        if s is not None:
            out[k] = s.id
            used.append(s)

    def best(score, pool=None):
        cands = [s for s in (pool if pool is not None else sources) if free(s) and len(_words(s) & topic) >= RELEVANT]
        # of two near-duplicates the scientific source is told, the simplified page only when it is alone
        cands = [s for s in cands if not any(_same(s, o) and quality(o) > quality(s) for o in cands if o is not s)]
        return max(cands, key=lambda s: (score(s), quality(s), -int(s.id[1:]) if s.id[1:].isdigit() else 0), default=None)
    rel = lambda s: len(_words(s) & topic)
    t = next((k for k, r in enumerate(roles) if r == 'twist'), -1)
    if twist in by and t >= 0:
        take(t, by[twist])
    p = next((k for k, r in enumerate(roles) if r == 'problem'), -1)
    if p >= 0 and has_myth(interp):
        # the facts of the claim about the myth ("Crude oil does not come from dinosaurs": s5, s7), not of the others
        subj = myth_groups(interp, sources) | content_words(' '.join(interp.get('myth_subjects') or []))
        about = [v for v in interp.get('verdicts') or [] if v.get('verdict') == 'supported'
                 and (groups(v.get('claim', '')) & subj or content_words(v.get('claim', '')) & subj)]
        judged = [by[i] for v in about for i in v.get('fact_ids') or [] if i in by]
        claims = content_words(' '.join(v['claim'] for v in about))
        take(p, best(lambda s: (len(_words(s) & claims), rel(s)), judged))
    q = content_words(interp.get('question', '') + (f' {storyline.hook} {storyline.payoff}' if storyline else ''))
    pay = next((k for k, r in enumerate(roles) if r == 'payoff'), -1)
    if pay >= 0:
        take(pay, best(lambda s: (len(_words(s) & q), rel(s))))
    how = content_words('heat pressure process turns turned transform transforms conversion converts layers buried')
    for k, r in enumerate(roles):
        if r == 'reveal':
            take(k, best(lambda s: (len(_words(s) & how), rel(s))))
    for k, r in enumerate(roles):
        if r in FACT_ROLES and k not in out:
            take(k, best(rel))
    return out


def plan_schema(n) -> dict:
    from .script import _arr, _enum, _obj, _s
    return _obj(dict(beats=_arr(_obj(dict(kind=_enum(KINDS), claim=_s())), n, n)))


def fact_lines(sources) -> str:
    rows = []
    for s in sources:
        q = ' '.join(str(s.quote or '').split())
        rows.append(f'{s.id}: {s.claim}' + (f'\n  quote: "{q[:240]}"' if q else ''))
    g = gloss(' '.join(fact_texts(sources)))
    return '\n'.join(rows) + (f'\n{g}' if g else '')


def plan_user(episode, sources, roles: list, facts: dict, misconception: str) -> str:
    from .models import FORMATS
    from .understand import topic_text
    sl = episode.storyline
    by = {s.id: s for s in sources}
    beats = []
    for k, r in enumerate(roles):
        if k in facts:
            s = by[facts[k]]
            q = ' '.join(str(s.quote or '').split())
            g = gloss(f'{s.claim} {s.quote}')
            beats.append(f'b{k + 1:02d} {r} ({ROLE_GUIDE[r]}): FACT BEAT on {s.id}: {s.claim}'
                         + (f'\n    quote: "{q[:240]}"' if q else '') + (f'\n    {g}' if g else '')
                         + (f'\n    NOTE: {caution(s)}' if caution(s) else ''))
        else:
            beats.append(f'b{k + 1:02d} {r} ({ROLE_GUIDE[r]}): no fact; pick its kind')
    lines = [topic_text(episode), f'Length: {episode.target_length} s.', '',
             f'THE BEATS ({len(roles)}, fixed, in order; give one answer per beat):', *beats, '']
    if misconception:
        lines += [f'The misconception (a "myth" beat may say it, as a belief): {misconception}', '']
    if sl:
        lines += [f'PICKED STORYLINE (format {sl.format}: {FORMATS[sl.format]}):', f'Hook: {sl.hook}',
                  f'Pitch: {sl.pitch}', f'Twist: {sl.twist}', f'Payoff: {sl.payoff}']
    return '\n'.join(lines).strip()


def twist_index(beats) -> int:
    i = next((k for k, b in enumerate(beats) if b['role'] == 'twist'), None)
    if i is None:
        i = next((k for k, b in enumerate(beats) if b['role'] == 'reveal'), None)
    return -1 if i is None else i


def normalise(plan: dict, roles, facts: dict) -> dict:
    """The model's answers on the fixed roles and facts, by position: a fact beat cites its assigned fact (whatever the
    model said its kind was); any other beat states no fact (a short or odd answer gets the role's default kind)."""
    got = list((plan or {}).get('beats') or [])
    beats = []
    for k, role in enumerate(roles):
        b = got[k] if k < len(got) and isinstance(got[k], dict) else {}
        claim = ' '.join(str(b.get('claim') or '').split()).strip('"\' ')
        if claim and claim[-1] not in '.!?':
            claim += '.'
        if k in facts:
            beats.append(dict(role=role, kind='fact', fact_ids=[facts[k]], claim=claim))
        else:
            kind = b.get('kind') if b.get('kind') in NO_FACT else DEFAULT_KIND.get(role, 'transition')
            beats.append(dict(role=role, kind=kind, fact_ids=[], claim=''))
    return dict(beats=beats)


def claim_problem(b, sources, myth, ask) -> str:
    """'' when a fact beat's claim is fine, else what is wrong with it (precision in code, then the judge)."""
    from . import entail
    if not b['claim']:
        return 'a fact beat needs its claim'
    own = {s.claim for s in sources if s.id in b['fact_ids']}
    if not b.get('from_research') and len(b['claim'].split()) > CLAIM_MAX_WORDS:
        return (f'the claim is {len(b["claim"].split())} words; at most 16, in plain words of your own (not the '
                'research line copied)')
    if not b.get('from_research'):
        from .script import copies
        mine = [s for s in sources if s.id in b['fact_ids']]
        if copies(b['claim'], [s.claim for s in mine] + [s.quote for s in mine]):
            return ('it copies the research line; say it in plain words of your own, for a viewer of 12 '
                    '("animal and plant remains in a marine environment" -> "tiny sea plants and animals")')
    allowed = allowed_for(fact_texts(sources, b['fact_ids'])) | myth
    bad = imprecise(b['claim'], allowed)
    if bad:
        return precision_why(bad, allowed)
    if ask is None or b.get('from_research'):
        return ''
    ok, why = entail.supported(b['claim'], entail.research_text(b['fact_ids'], sources), ask)
    return '' if ok is not False else f'its fact does not say all of it ({why})'


def plan_issues(plan, sources, myth, ask) -> tuple[list, dict]:
    """(messages for a repair, {beat index: what is wrong with its claim})."""
    from . import entail
    msgs, claims, seen = [], {}, []
    for k, b in enumerate(plan['beats']):
        if b['kind'] != 'fact':
            continue
        p = claim_problem(b, sources, myth, ask)
        if not p and entail.repeats(b['claim'], seen):
            p = 'says what an earlier beat says; say this beat\'s own fact'
        if p:
            claims[k] = p
            msgs.append(f'b{k + 1:02d} claim "{b["claim"]}": {p}')
        seen.append(b['claim'])
    return msgs, claims


def _fallback(plan, sources, claims, notes):
    """After the repairs: a claim that still fails becomes its fact's own line from research (research checked it
    against the quote)."""
    by = {s.id: s for s in sources}
    for k in sorted(claims):
        b = plan['beats'][k]
        own = by[b['fact_ids'][0]]
        notes.append(f'b{k + 1:02d}: claim "{b["claim"]}" ({claims[k]}) -> the fact as research wrote it: "{own.claim}"')
        b['claim'], b['from_research'] = own.claim, True


def make_plan(episode, sources, aim, cache=None, ledger=None, ask=None, generate=None) -> dict:
    """The fact-first plan: {version, twist, beats: [{role, kind, fact_ids, claim}], attempts, notes, remaining}.
    Roles and facts are chosen in code; the local model writes each fact beat's claim in plain words and picks the
    kind of the others. `generate(system, user, schema, attempt)` is the model (a stand-in in tests); `ask` the judge."""
    from . import entail
    from .script import ScriptError, shape
    interp = (episode.meta or {}).get('interpretation') or {}
    twist = (interp.get('twist') or {}).get('id') or twist_fact(interp, sources)
    myth = myth_groups(interp, sources)
    ask = ask if ask is not None else entail.default_ask(cache, ledger)
    roles = skeleton(episode.story_format, (aim[0] + aim[1] + 1) // 2)
    facts = assign_facts(roles, sources, twist, interp, episode.storyline)
    schema = plan_schema(len(roles))
    if generate is None:
        from . import llm

        def generate(system, user, sc, attempt):
            return llm.generate_json(system, user, sc, PLAN_MAX_TOKENS, temperature=0.3 if attempt == 0 else 0.2,
                                     seed=51 + attempt, cache=cache, ledger=ledger, step='script',
                                     detail='fact plan' + (f' (repair {attempt})' if attempt else ''))
    user = plan_user(episode, sources, roles, facts, interp.get('misconception', ''))
    plan, msgs, claims, attempts = None, [], {}, []
    for attempt in range(PLAN_REPAIRS + 1):
        ask_user = user + '\n\nAnswer with JSON only: ' + shape(schema)
        if plan is not None:
            import json
            ask_user += ('\n\nYOUR PREVIOUS PLAN:\n' + json.dumps(dict(beats=[dict(kind=b['kind'], claim=b['claim'])
                                                                         for b in plan['beats']]),
                                                               ensure_ascii=False, separators=(',', ':'))
                         + '\n\nTHE CHECKER FOUND THESE PROBLEMS. Fix every one and keep everything else:\n'
                         + '\n'.join(f'- {m}' for m in msgs))
        try:
            got = normalise(generate(PLAN_SYSTEM, ask_user, schema, attempt), roles, facts)
        except Exception as exc:                      # the model is down or the answer is unusable
            if plan is None:
                raise ScriptError(f'The local model could not plan the beats: {exc}') from None
            break
        if plan is not None:                          # a repair keeps the claims that already passed
            for k, b in enumerate(got['beats']):
                if b['kind'] == 'fact' and k not in claims:
                    b['claim'] = plan['beats'][k]['claim']
        plan = got
        msgs, claims = plan_issues(plan, sources, myth, ask)
        attempts.append(dict(attempt=attempt, issues=list(msgs)))
        if not msgs:
            break
    notes = []
    if msgs:
        _fallback(plan, sources, claims, notes)
    left = plan_issues(plan, sources, myth, None)[0]
    return dict(version=GROUNDING_VERSION, twist=twist, beats=plan['beats'], attempts=attempts, notes=notes,
                remaining=left)


def plan_view(plan: dict) -> list:
    """The plan as lines: 'b05 clue [fact s7]: claim'."""
    out = []
    for k, b in enumerate((plan or {}).get('beats') or [], 1):
        tag = f'fact {", ".join(b["fact_ids"])}' if b['kind'] == 'fact' else b['kind']
        out.append(f'b{k:02d} {b["role"]} [{tag}]' + (f': {b["claim"]}' if b['claim'] else ''))
    return out


def kinds(episode) -> dict:
    """{beat id: kind} from the episode's plan ({} for an episode written before the plan)."""
    plan = (episode.meta or {}).get('fact_plan') or {}
    return {f'b{k:02d}': b.get('kind', '') for k, b in enumerate(plan.get('beats') or [], 1)}
