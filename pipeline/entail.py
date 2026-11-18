"""Fact entailment (user 2026-11-14): before the script gate, every narrated sentence and every text shown on screen
is checked against the facts its beat cites. One short yes/no call to the local model per line (cached like every
model call). 2026-11-14: the crude-oil script said "Modern geochemistry finds oil starts with tiny marine bacteria"
citing s3, which says only "thermal conversion of sedimentary organic matter derived from living organisms".

Since the fact-first plan (pipeline/grounding.py) the facts are chosen before writing, so this check should confirm
and rarely rewrite: the rewrite rate (changed lines / checked lines) is logged per episode, and above 20 % means the
plan step failed (`RATE_LIMIT`).

* A beat that cites facts: each sentence must say only what they say (word precision in code first, then the judge).
  A true sentence that cites the wrong fact is re-cited to the fact that says it. Otherwise it is rewritten to the
  research, then checked again; if the rewrite still fails, the claim is dropped and the beat kept: the sentence is
  removed when the beat has another; a beat of one sentence gets a short line that states no fact in its place, or,
  failing that, the cited fact as research wrote it if no other beat says it already.
* A beat that cites nothing may only hold lines that state no fact (a question, a transition, a reaction, a callback,
  the myth said as a belief). A line there that states a fact is cited to the fact that says it, else replaced by a
  line that states none, else dropped (when the beat has another sentence).
* Every text on screen (on-screen text, split labels, diagram items, label and pin effects, drawn text recipes) gets
  the same check against its beat's facts: never a claim the narration could not make. A text that fails is
  rewritten short and checked again, else removed.
Every change is logged (`episode.meta['fact_check']`, the job notes, the episode page).
"""
from __future__ import annotations

import re

ENTAIL_VERSION = 'entail-v5'      # v5: over-generalising fails, staging directions on screen fail, one call per script under Claude Code; v4: only what adds a claim or contradicts fails; v2: word precision, uncited beats, on-screen text, the rewrite rate; v3: ranges
RATE_LIMIT = 0.2                  # above this share of rewritten lines, the fact-first plan failed (user 2026-11-14)

CHECK_SYSTEM = """You check one claim of a narrated science video against the research it cites. The video simplifies
for a general audience, so judge what the claim MEANS, not its wording. First say whether it states a fact at all (a
question, a transition, a joke or "here is why" states none). If it does, list:
adds: only a claim the research does not make: a new cause, effect, thing, material, kind of living thing, place,
time, number or certainty that the research does not state ("hormones" for research about neurotransmitters; "within
seconds" when the research gives no time; a step or a substance the research never names).
contradicts: what says the opposite of the research ("lets more light through" for research that says it scatters
more; "away from you" for research that says "toward you"), and what OVER-GENERALISES: a claim about all of a thing
where the research says only some of it ("some X" or "certain X" said as "X": "mushrooms are poisonous" for research
that says some mushrooms are; "it starts in clay, rich in iron" for research that says only some clays are
iron-rich; "bats carry rabies" for "a few bats"), or "always" where the research says often or can.
Never list: plainer or everyday words for the research's own things ("cash" for "money", "the heart's own wiring" for
"the sinoatrial node", "way down" for "below 200 meters"), leaving details or places out, and vivid verbs or
metaphors for the same process ("cooks", "snowballs", "an engine"). supported=true when both lists are empty."""

NONFACT_SYSTEM = """You check one line of a narrated science video that cites no research. Say whether it states a fact
about the world: that something is or was so, a cause, what a thing is made of or where it comes from, a number or a
date. A question, a transition ("But there's a catch."), a reaction ("Wait, really?"), a line that repeats the hook,
or a line that says what many people wrongly believe ("Many think oil comes from dinosaurs") states none; what
scientists or research found does state one. adds: the words that state a fact. supported=true when it states none."""

TEXT_SYSTEM = """You check one short text shown on screen in a narrated science video (a caption, a label on a picture
or a diagram label) against the research its beat cites. First say whether it states a fact at all: a label that only
names a thing, a place or a step that is shown ("Ocean floor", "Rock layers", "Heat"), or marks the myth ("MYTH:
Dinosaurs?"), states none; a text that says what something is, is made of, comes from or does states one ("Fact:
Microbes" says the answer is microbes). If it states one, list every word OF THE TEXT that says something the research
does not say: a different or more specific group, material, place, number or cause, or all of a thing where the
research says only some of it. A plainer wording of the research is fine. supported=true when the list is empty."""

CHECK_SCHEMA = dict(type='object', required=['states_a_fact', 'adds', 'supported', 'why'],
                    properties=dict(states_a_fact=dict(type='boolean'), adds=dict(type='array', items=dict(type='string')),
                                    supported=dict(type='boolean'), why=dict(type='string')))
# the claims check (user 2026-11-16: reject only what ADDS a claim or CONTRADICTS the fact; simpler is fine)
CLAIM_SCHEMA = dict(type='object', required=['states_a_fact', 'adds', 'contradicts', 'supported', 'why'],
                    properties=dict(states_a_fact=dict(type='boolean'), adds=dict(type='array', items=dict(type='string')),
                                    contradicts=dict(type='array', items=dict(type='string')),
                                    supported=dict(type='boolean'), why=dict(type='string')))

REWRITE_SYSTEM = """You fix one sentence of a narrated science video that says more than its research supports.
Rewrite it so it says only what the cited research says (use the research's own words where you can), in plain spoken
English for a 45-second video, about the same length. Leave out what is wrong rather than replacing it with a guess. It must still follow the previous line and lead into the next one. No facts, numbers, organisms or
names that the research does not state; no quotes."""

REWRITE_SCHEMA = dict(type='object', required=['sentences'], properties=dict(sentences=dict(
    type='array', items=dict(type='string'), minItems=1, maxItems=2)))
TWO = ('\nGive two different versions: first one close to the original wording with only the unsupported part taken '
       'out; then one that makes the same point as the original line with the fact of the research (cited or other) '
       'that comes closest to it, in its words.')

BRIDGE_SYSTEM = """A line of a narrated science video states something its research does not support, and it must
go. Write a short line in its place that states NO fact: a question or a transition that follows the previous line
and leads into the next one (e.g. "So where does it really come from?"). At most 10 words, plain spoken English."""

TEXT_REWRITE_SYSTEM = """A short text shown on screen in a narrated science video says more than the research of its
beat. Give two short replacements: each names what the research or the narration says, in their words, or names what
the picture shows. No group, material, number or cause the research does not state. Keep the same kind of text (a
label stays a label; "Myth:" or "Fact:" may stay)."""

SENTENCE = re.compile(r'(?<=[.!?])\s+(?=["\'A-Z0-9])')
NUMBER = re.compile(r'\d[\d,.]*')
NO_RESEARCH = '(this beat cites no research: a text that states a fact is not supported here)'


def sentences(text: str) -> list:
    return [s for s in SENTENCE.split(' '.join(str(text or '').split())) if s.strip()]


def research_text(fact_ids, sources) -> str:
    """The cited facts with their quotes, and the plain-English equivalents of the groups they name."""
    from .grounding import gloss
    by = {s.id: s for s in sources}
    rows = []
    for i in fact_ids:
        s = by.get(i)
        if s is not None:
            rows.append(f'{s.id}: {s.claim}' + (f'\n  quote: "{s.quote}"' if s.quote else ''))
    g = gloss(' '.join(rows))
    return '\n'.join(rows) + (f'\n{g}' if rows and g else '')


def supported(sentence: str, research: str, ask, system: str = CHECK_SYSTEM) -> tuple[bool | None, str]:
    """(True/False, why); None when the model gave no usable answer (the sentence is then kept, noted)."""
    if sentence.rstrip().endswith('?') and system != TEXT_SYSTEM:
        return True, 'a question states no fact'
    try:
        label = 'Text' if system == TEXT_SYSTEM else 'Sentence'
        a = ask(system, f'{label}: {sentence}\n\n' + (f'Cited research:\n{research}' if system != NONFACT_SYSTEM else
                                                    'It cites no research.'),
                CLAIM_SCHEMA if system == CHECK_SYSTEM else CHECK_SCHEMA) or {}
    except Exception as e:                            # the model is down: never stop the episode for it
        return None, f'not checked: the local model did not answer ({type(e).__name__})'
    if not isinstance(a, dict) or not isinstance(a.get('supported'), bool):
        return None, 'not checked: the answer had no verdict'
    why = ' '.join(str(a.get('why') or '').split())[:200]
    if a.get('states_a_fact') is False:
        return True, why or 'states no fact'
    adds = [str(x) for x in a.get('adds') or [] if str(x).strip()]
    if system == NONFACT_SYSTEM:                      # any fact at all fails here: nothing is cited
        if a['supported'] and not adds:
            return True, why
        return False, ('states a fact but the beat cites none' + (f' ({", ".join(repr(x) for x in adds)})' if adds else '')
                       + (f': {why}' if why else ''))
    against = [str(x) for x in a.get('contradicts') or [] if str(x).strip()] if system == CHECK_SYSTEM else []
    if against:
        return False, f'contradicts the research ({", ".join(repr(x) for x in against)}): {why}'
    real = [x for x in adds if added_words(x, sentence, research)]
    if a['supported'] or not real:
        # 2026-11-14: the 9B judge failed "over millions of years" for leaving out kerogen; an "addition" whose words
        # are all in the research (or not in the sentence) is an omission, which is fine
        return True, why
    return False, f'adds {", ".join(repr(x) for x in real)}: {why}'


SPLIT_SYSTEM = """You split ONE line of a narrated science video into its atomic claims: the separate things it
asserts about the world, each a short sentence in the line's OWN words (do not explain, describe or add words). Mark
each claim "fact" (it says something is or was so, including what scientists or research found) or "belief" (it
only reports what people believe or used to think: "many think oil comes from dinosaurs"; the whole belief is one
belief claim). Metaphor, tone, rhythm and vivid wording assert nothing beyond their plain meaning: "black liquid
gold" asserts only that it is oil; "millions of years in the dark" asserts only that it took millions of years;
questions, jokes, reactions and transitions assert nothing. A line that asserts nothing has no claims. Then list in
"nonsense" every claim that mixes up categories or contradicts basic common knowledge, such as "dinosaurs were sea
creatures" or "sea creatures like dinosaurs". A belief being false is NOT nonsense (that is what a myth is); [] when
none."""
SPLIT_SCHEMA = dict(type='object', required=['claims', 'nonsense'], properties=dict(
    claims=dict(type='array', maxItems=5, items=dict(type='object', required=['text', 'kind'], properties=dict(
        text=dict(type='string'), kind=dict(type='string', enum=['fact', 'belief'])))),
    nonsense=dict(type='array', maxItems=3, items=dict(type='string'))))


# the split and the check in one question (Claude Code, user 2026-11-18: "batch ... the claims split"): the same rules
LINE_SYSTEM = ("You check one line of a narrated science video in two steps.\n\nSTEP 1, THE CLAIMS. " + SPLIT_SYSTEM
               + "\n\nSTEP 2, EACH FACT CLAIM against the research the line cites (when it cites none, leave the lists "
                 "empty: any fact claim fails there). " + CHECK_SYSTEM.replace(
                   'You check one claim of a narrated science video against the research it cites. ', '')
               .replace('First say whether it states a fact at all (a\nquestion, a transition, a joke or "here is why" '
                        'states none). If it does, list:', 'For each fact claim list:')
               .replace('supported=true when both lists are empty.', 'A claim is supported when both lists are empty.'))
LINE_SCHEMA = dict(type='object', required=['claims', 'nonsense', 'why'], properties=dict(
    claims=dict(type='array', maxItems=5, items=dict(type='object', required=['text', 'kind', 'adds', 'contradicts'],
                                                     properties=dict(text=dict(type='string'),
                                                                     kind=dict(type='string', enum=['fact', 'belief']),
                                                                     adds=dict(type='array', items=dict(type='string')),
                                                                     contradicts=dict(type='array',
                                                                                      items=dict(type='string'))))),
    nonsense=dict(type='array', maxItems=3, items=dict(type='string')), why=dict(type='string')))


def line_verdict(line: str, research: str, allowed: set, ask, system: str) -> tuple[bool | None, str]:
    """verdict() as ONE question (the split and the check of each claim together), for Claude Code: the same code
    rules on its answer (nonsense, group words, ranges, a cited-nothing beat holds no fact, adds that only leave things
    out are not additions)."""
    from .grounding import imprecise, narrowed, precision_why
    cites = system == CHECK_SYSTEM
    try:
        a = ask(LINE_SYSTEM, f'Line: {line}\n\n' + (f'Cited research:\n{research}' if cites else 'It cites no research.'),
                LINE_SCHEMA) or {}
    except Exception as e:                            # the model is down: never stop the episode for it
        return None, f'not checked: the model did not answer ({type(e).__name__})'
    if not isinstance(a, dict) or not isinstance(a.get('claims'), list):
        return None, 'not checked: the answer had no claims'
    claims = [c for c in a['claims'] if isinstance(c, dict) and str(c.get('text') or '').strip()]
    nonsense = [' '.join(str(x).split()) for x in a.get('nonsense') or [] if str(x).strip()]
    why = ' '.join(str(a.get('why') or '').split())[:200]
    if nonsense:
        return False, f'does not make sense: {"; ".join(nonsense)}'
    said = set(re.findall(r'[a-z]+', ' '.join(str(c['text']) for c in claims).lower()))
    bad = [w for w in imprecise(line, allowed) if w in said]
    if bad:
        return False, precision_why(bad, allowed)
    facts = [c for c in claims if c.get('kind') != 'belief']
    if facts and cites and narrowed(line, research):
        return False, narrowed(line, research)
    if not cites:
        return (True, 'states no fact') if not facts else (
            False, 'states a fact but the beat cites none (' + '; '.join(repr(str(c['text'])) for c in facts) + ')')
    for c in facts:
        text = ' '.join(str(c['text']).split())
        against = [str(x) for x in c.get('contradicts') or [] if str(x).strip()]
        if against:
            return False, f'the claim "{text}" contradicts or over-generalises the research ' \
                          f'({", ".join(repr(x) for x in against)}): {why}'
        real = [str(x) for x in c.get('adds') or [] if str(x).strip() and added_words(str(x), text, research)]
        if real:
            return False, f'the claim "{text}" adds {", ".join(repr(x) for x in real)}: {why}'
    return True, f'{len(facts)} claim(s) supported' if facts else 'asserts no fact'


def split_claims(line: str, ask):
    """([{text, kind}], [nonsense]) of a line, or (None, None) when the model gave no usable answer."""
    try:
        a = ask(SPLIT_SYSTEM, f'Line: {line}', SPLIT_SCHEMA) or {}
    except Exception:
        return None, None
    if not isinstance(a, dict) or not isinstance(a.get('claims'), list):
        return None, None
    claims = [dict(text=' '.join(str(c.get('text') or '').split()), kind=c.get('kind') if c.get('kind') in ('fact', 'belief')
                   else 'fact') for c in a['claims'] if isinstance(c, dict) and str(c.get('text') or '').strip()]
    nonsense = [' '.join(str(x).split()) for x in a.get('nonsense') or [] if str(x).strip()]
    return claims, nonsense


def verdict(line: str, research: str, allowed: set, ask, system: str = CHECK_SYSTEM) -> tuple[bool | None, str]:
    """Claims, not colour (user 2026-11-15): the line is split into its atomic claims (one short call) and only those
    are checked; metaphor and tone that assert nothing pass. A claim that makes no sense ("dinosaurs were sea
    creatures") fails; word precision runs in code on the claims; each fact claim gets the yes/no judge against the
    research (a beat citing nothing may hold no fact claim at all). On-screen texts keep the whole-text check, and a
    line the splitter cannot read falls back to it too. A number or range must be as wide as the research says it
    (grounding.narrowed: "thousands to millions" never becomes "thousands")."""
    from .grounding import imprecise, narrowed, precision_why
    from .llm import active_route
    if system != TEXT_SYSTEM and active_route() is not None:   # Claude Code: the split and the check in one question
        return line_verdict(line, research, allowed, ask, system)
    if system == TEXT_SYSTEM:
        bad = imprecise(line, allowed)
        if bad:
            return False, precision_why(bad, allowed)
        if narrowed(line, research):
            return False, narrowed(line, research)
        return supported(line, research, ask, system)
    claims, nonsense = split_claims(line, ask)
    if claims is None:
        bad = imprecise(line, allowed)
        if bad:
            return False, precision_why(bad, allowed)
        if system == CHECK_SYSTEM and narrowed(line, research):
            return False, narrowed(line, research)
        return supported(line, research, ask, system)
    if nonsense:
        return False, f'does not make sense: {"; ".join(nonsense)}'
    said = set(re.findall(r'[a-z]+', ' '.join(c['text'] for c in claims).lower()))
    bad = [w for w in imprecise(line, allowed) if w in said]   # the line's own group words that it asserts
    if bad:
        return False, precision_why(bad, allowed)
    facts = [c['text'] for c in claims if c['kind'] == 'fact']
    if facts and system == CHECK_SYSTEM and narrowed(line, research):   # ranges kept as wide as the fact (in code)
        return False, narrowed(line, research)
    if system == NONFACT_SYSTEM:
        return (True, 'states no fact') if not facts else (
            False, 'states a fact but the beat cites none (' + '; '.join(repr(x) for x in facts) + ')')
    for c in facts:
        ok, why = supported(c, research, ask)
        if ok is False:
            return False, f'the claim "{c}" {why}'
        if ok is None:
            return None, why
    return True, f'{len(facts)} claim(s) supported' if facts else 'asserts no fact'


def allowed_groups(episode, ids) -> set:
    """The groups of living things a line of a beat citing `ids` may name (any fact's, for a beat citing none)."""
    from .grounding import allowed_for, fact_texts, myth_groups
    interp = (episode.meta or {}).get('interpretation') or {}
    return allowed_for(fact_texts(episode.sources, set(ids) if ids else None)) | myth_groups(interp, episode.sources)


def other_facts(sentence: str, episode, cited, ask) -> list:
    """The ids of research facts the beat does not cite that, with its cited facts, support the sentence ([] when
    none do): a true line that cites the wrong fact is re-cited, not rewritten. 2026-11-14: "Heat and pressure turn
    this biomass into energy-dense hydrocarbons" cited s2 (deoxygenation); EIA's "heat and pressure ... turned the
    remains into crude oil" was another fact. The candidates are picked in code (the facts that hold the words the
    sentence adds to its cited facts, at most two); the model then makes the same short check as for any sentence."""
    from .grounding import gloss
    from .qa import content_words
    mine = research_text(cited, episode.sources)
    extra = content_words(sentence) - content_words(mine)
    if not extra:
        return []
    others = [x for x in episode.sources if x.id not in cited]
    cover = lambda x: extra & content_words(f'{x.claim} {x.quote} {gloss(x.claim + " " + x.quote)}')
    picked, left = [], set(extra)
    for x in sorted(others, key=lambda x: -len(cover(x))):
        if len(picked) < 2 and cover(x) & left:
            picked.append(x.id)
            left -= cover(x)
    if not picked or len(left) > len(extra) / 2:      # most of what it adds is in no fact
        return []
    ids = list(cited) + picked
    ok, _ = verdict(sentence, research_text(ids, episode.sources), allowed_groups(episode, ids), ask)
    return picked if ok else []


def added_words(phrase: str, sentence: str, research: str) -> set:
    """The content words of `phrase` that the sentence says and the research does not."""
    from .qa import content_words
    return (content_words(phrase) & content_words(sentence)) - content_words(research)


def repeats(line: str, others) -> bool:
    """True when `line` says nearly what another line of the script says (2026-11-14: two beats both became "...
    comes from living organisms")."""
    import difflib
    a = ' '.join(re.findall(r'[a-z0-9]+', line.lower()))
    return any(difflib.SequenceMatcher(None, a, ' '.join(re.findall(r'[a-z0-9]+', o.lower()))).ratio() > 0.7
               for o in others if o)


def rewrites(sentence: str, research: str, before: str, after: str, ask, why: str = '', bridge: bool = False,
             said=(), others: str = '', allowed=None) -> list:
    """Up to two versions of the sentence rewritten to the research (or, bridge=True, lines in its place that state
    no fact); a version with a new number, a bad length, a group of living things the facts do not name, or that
    repeats another line is left out."""
    from .grounding import imprecise
    n = len(sentence.split())
    user = (f'Previous line: {before or "(none: this opens the video)"}\nSentence to fix: {sentence}\n'
            f'Next line: {after or "(none: this ends the video)"}\n'
            + (f'What is wrong with it: {why}\n' if why else '')
            + (('Other lines of the video (do not repeat them):\n' + '\n'.join(f'- {x}' for x in said) + '\n') if said else '')
            + f'\nCited research:\n{research or "(none)"}\n'
            + (f'\nOther facts of the same research (the line may say one of these instead):\n{others}\n'
               if others and not bridge else '')
            + '\n' + ('At most 10 words each.' if bridge else f'About {n} words each.'))
    try:
        a = ask(BRIDGE_SYSTEM if bridge else REWRITE_SYSTEM + TWO, user, REWRITE_SCHEMA) or {}
    except Exception:
        return []
    got = a.get('sentences') if isinstance(a.get('sentences'), list) else [a.get('sentence')]
    out = []
    for x in got[:2]:
        new = ' '.join(str(x or '').split()).strip('"\' ')
        if not new or not 3 <= len(new.split()) <= (10 if bridge else max(n + 6, 14)) or new.casefold() == sentence.casefold():
            continue
        if set(NUMBER.findall(new)) - set(NUMBER.findall(research + ' ' + others + ' ' + sentence)):
            continue                                  # a number the research does not have
        if allowed is not None and imprecise(new, allowed):
            continue                                  # "bacteria" for plankton again
        if repeats(new, said) or new in out:
            continue                                  # another beat says it already
        new = new if new[-1] in '.!?' else new + '.'
        from .bestof import UNFINISHED
        if UNFINISHED.search(new) or not re.match(r'["\'(]?[A-Z0-9]', new):
            continue                                  # 2026-11-15 bench: "Let time work for you:." (a cut clause)
        out.append(new)
    return out


def rewrite(sentence: str, research: str, before: str, after: str, ask, **kw) -> str:
    """The first usable version (`rewrites`), or ''."""
    got = rewrites(sentence, research, before, after, ask, **kw)
    return got[0] if got else ''


def default_ask(cache=None, ledger=None):
    """The local model, or None when this run has none (the paid provider: nothing is checked, it is noted)."""
    from . import batch, config
    if config.script_provider() != 'ollama':
        return None
    return batch.asker(cache, ledger, step='fact-check', detail='line vs its cited fact', max_tokens=500)


# ------------------------------------------------------------------ texts on screen

def texts(b) -> list:
    """[(field, text, max words, set(new), drop())] for every text the beat shows on screen."""
    sh = b.shot
    out = []
    if b.on_screen_text.strip():
        out.append(('on-screen text', b.on_screen_text, 5, lambda v: setattr(b, 'on_screen_text', v),
                    lambda: setattr(b, 'on_screen_text', '')))
    if sh.split:
        for k, lab in enumerate(sh.split.labels or []):
            if lab.strip():
                out.append(('split label', lab, 3, lambda v, k=k: sh.split.labels.__setitem__(k, v),
                            lambda k=k: sh.split.labels.__setitem__(k, '')))
    if sh.graphic:
        for k, item in enumerate(sh.graphic.items):
            out.append(('diagram label', item, 3, lambda v, item=item: _set_item(b, item, v),
                        lambda item=item: _drop_item(b, item)))
    for e in (sh.insert.effects if sh.insert else []):
        if e.kind in ('label', 'pin') and e.detail.strip():
            out.append((f'{e.kind} on the picture', e.detail, 3, lambda v, e=e: setattr(e, 'detail', v),
                        lambda e=e: sh.insert.effects.remove(e) if e in sh.insert.effects else None))
    for m in sh.motion:
        if m.recipe == 'text_slam' and m.detail.strip() and not b.on_screen_text.strip():
            out.append(('slammed text', m.detail, 5, lambda v, m=m: setattr(m, 'detail', v),
                        lambda m=m: setattr(m, 'detail', '')))
        elif m.recipe in ('checklist_tick', 'split_compare') and m.detail.strip():
            for part in [p.strip() for p in m.detail.split('|') if p.strip()]:
                out.append(('drawn text', part, 4, lambda v, m=m, part=part: setattr(m, 'detail', m.detail.replace(part, v)),
                            lambda m=m: _plain_move(m)))
    return out


def _plain_move(m):
    m.recipe, m.anchor, m.detail = 'push_in', '', ''


def _set_item(b, item, new):
    g = b.shot.graphic
    if item in g.items:
        g.items[g.items.index(item)] = new


def _drop_item(b, item):
    """A diagram label that cannot stay: removed with its anchor; the last one becomes a name of what is said."""
    g = b.shot.graphic
    if item not in g.items:
        return
    k = g.items.index(item)
    if len(g.items) > 1:
        g.items.pop(k)
        if len(g.anchors) > k:
            g.anchors.pop(k)
        return
    from .qa import content_words
    said = [w for w in re.findall(r"[A-Za-z]+", b.narration) if content_words(w)]
    g.items[0] = ' '.join(w.capitalize() for w in said[-2:]) or 'Here'


def text_rewrites(text: str, research: str, narration: str, max_words: int, ask, allowed) -> list:
    from .grounding import imprecise
    user = (f'Text: {text}\nAt most {max_words} words each.\nThe beat says: {narration}\n\n'
            f'Cited research:\n{research or "(none: name only what the picture shows)"}')
    try:
        a = ask(TEXT_REWRITE_SYSTEM, user, REWRITE_SCHEMA) or {}
    except Exception:
        return []
    out = []
    for x in (a.get('sentences') if isinstance(a.get('sentences'), list) else [])[:2]:
        new = ' '.join(str(x or '').split()).strip('"\' .')
        from .bestof import SLANG
        from .models import staging_leak
        if not new or len(new.split()) > max_words or new.casefold() == text.casefold() or imprecise(new, allowed) \
                or set(re.findall(r"[a-z']+", new.lower())) & SLANG or staging_leak(new):
            continue
        if set(NUMBER.findall(new)) - set(NUMBER.findall(research + ' ' + narration)):
            continue
        out.append(new)
    return out


MYTH_MARK = re.compile(r"^\s*(myth|old belief|false|not true|wrong|the myth)\b\s*[:!?.-]?\s*", re.I)


def marks_myth(text: str, episode) -> bool:
    """True for a text that only marks the myth: "MYTH", or "Myth: Dinosaurs" whose other words name only the myth's
    subjects (2026-11-14: the judge rewrote the label "MYTH" into "Oil predates dinosaurs", undoing the myth mark)."""
    from .grounding import groups, myth_groups
    from .qa import content_words
    m = MYTH_MARK.match(text or '')
    if not m:
        return False
    from .grounding import GROUP_OF
    rest = text[m.end():]
    interp = (episode.meta or {}).get('interpretation') or {}
    subjects = content_words(' '.join(interp.get('myth_subjects') or []))
    other = {w for w in content_words(rest) if w not in GROUP_OF and w not in subjects}
    return groups(rest) <= myth_groups(interp, episode.sources) and not other


def echoes(text: str, narration: str, allowed) -> bool:
    """True when every content word of the text is a word the beat's narration says (and no group word is off):
    such a text cannot say more than the narration, which is checked itself."""
    from .grounding import imprecise
    from .qa import content_words
    words = content_words(text)
    return bool(words) and words <= content_words(narration) and not imprecise(text, allowed) \
        and not set(NUMBER.findall(text)) - set(NUMBER.findall(narration))


def check_texts(b, episode, ask, out):
    """The texts the beat shows, against the beat's facts (after its narration was checked)."""
    research = research_text(b.source_ids, episode.sources)
    allowed = allowed_groups(episode, b.source_ids)
    for field, text, most, put, drop in texts(b):
        out['text_checked'] += 1
        said = b.narration + ('' if field == 'on-screen text' else ' ' + b.on_screen_text)   # the gate's own rule
        stray = {n.strip('.,') for n in NUMBER.findall(text)} - {n.strip('.,') for n in NUMBER.findall(said)}
        from .bestof import SLANG
        slang = set(re.findall(r"[a-z']+", text.lower())) & SLANG
        from .models import staging_leak, unstaged
        leak = staging_leak(text)
        if leak:                                      # 2026-11-17: "Slam 'BLUE SCATTERS ...' at the bottom of the frame"
            ok, why = False, f'a staging direction, not a text to show ({leak})'
            inner = unstaged(text)                    # the words it quotes, when they pass as a text of their own
            if inner and len(inner.split()) <= most and verdict(inner, research or NO_RESEARCH, allowed, ask,
                                                                 TEXT_SYSTEM)[0] is not False:
                put(inner)
                out['changed'].append(dict(beat=b.id, field=field, was=text, now=inner, fact_ids=list(b.source_ids),
                                           why=why, action='text rewritten (the words it quoted)'))
                continue
        elif stray:                                   # 2026-11-15: "DINOSAURS (65 MYA)" on a beat that says no number
            ok, why = False, f'a number the beat never says ({", ".join(sorted(stray))})'
        elif slang:                                   # 2026-11-15: "MYTH: Dinosaur Poop" on the hook
            ok, why = False, f'slang ({", ".join(sorted(slang))})'
        elif marks_myth(text, episode):
            continue                                  # "MYTH", "Myth: Dinosaurs": marks the myth, states no fact
        elif echoes(text, b.narration, allowed):
            continue                                  # says only words the (checked) narration says
        else:
            ok, why = verdict(text, research or NO_RESEARCH, allowed, ask, TEXT_SYSTEM)
        if ok is None:
            out['notes'].append(f'{b.id}: {field} "{text}": {why}')
        if ok is not False:
            continue
        new = ''
        for cand in text_rewrites(text, research, b.narration, most, ask, allowed):
            if verdict(cand, research or NO_RESEARCH, allowed, ask, TEXT_SYSTEM)[0] is not False:
                new = cand
                break
        entry = dict(beat=b.id, field=field, was=text, fact_ids=list(b.source_ids), why=why)
        if new:
            put(new)
            entry.update(action='text rewritten', now=new)
        else:
            drop()
            entry.update(action='text removed', now='')
        out['changed'].append(entry)


# ------------------------------------------------------------------ the check

def check(episode, cache=None, ledger=None, ask=None) -> dict:
    """Check every sentence and every on-screen text of every beat; rewrite or drop what the facts do not support (in
    place). Returns {version, checked, text_checked, changed: [{beat, field, was, now, action, fact_ids, why}], rate,
    plan_failed, notes}."""
    from . import batch
    ask = ask or default_ask(cache, ledger)
    if ask is None:
        return dict(version=ENTAIL_VERSION, checked=0, text_checked=0, changed=[], notes=[
            'fact check: not run (it uses the local model)'], rate=0.0, plan_failed=False)
    # Claude Code: every line and every text of the script in one call (the check runs first on a copy to see them)
    memo = batch.Memo(ask)
    out = batch.run(lambda a: _check(episode, a), memo, record=lambda a: _check(episode.model_copy(deep=True), a),
                    what='fact check: every line and text')
    if memo.on:
        out['batched'] = dict(fetched=memo.fetched, asked_alone=memo.missed)
    return out


def _check(episode, ask) -> dict:
    from . import grounding
    out = dict(version=ENTAIL_VERSION, checked=0, text_checked=0, changed=[], notes=[])
    beats = episode.beats
    kinds = grounding.kinds(episode)
    claims = {f'b{k:02d}': (b.get('claim') or '') for k, b in enumerate(((episode.meta or {}).get('fact_plan') or {})
                                                                         .get('beats') or [], 1)}
    original = [b.narration for b in beats]           # the rewrites read the lines as written, not each other's fixes
    for k, b in enumerate(beats):
        lines = sentences(b.narration)
        claim = claims.get(b.id, '') if b.source_ids else ''
        kept, changed = [], False
        for j, s in enumerate(lines):
            out['checked'] += 1
            cited = bool(b.source_ids)
            research = research_text(b.source_ids, episode.sources)
            allowed = allowed_groups(episode, b.source_ids)
            ok, why = verdict(s, research, allowed, ask, CHECK_SYSTEM if cited else NONFACT_SYSTEM)
            if ok is None:
                out['notes'].append(f'{b.id}: {why}')
            if ok is not False:
                kept.append(s)
                continue
            more = other_facts(s, episode, b.source_ids, ask)
            if more:                                  # true, but it cited the wrong fact (or none): cite the right one
                kept.append(s)
                new_ids = list(dict.fromkeys(list(b.source_ids) + more))
                out['changed'].append(dict(beat=b.id, field='narration', was=s, now=s, fact_ids=new_ids, why=why,
                                           action=f'kept; now cites {", ".join(more)}'))
                b.source_ids = new_ids
                continue
            before = (lines[j - 1] if j else (original[k - 1] if k else ''))
            after = (lines[j + 1] if j + 1 < len(lines) else (original[k + 1] if k + 1 < len(beats) else ''))
            said = [x.narration for x in beats if x is not b] + [x for x in lines if x != s]
            entry = dict(beat=b.id, field='narration', was=s, fact_ids=list(b.source_ids), why=why)
            new, ok2, recite = '', False, []
            if cited:
                others = research_text([x.id for x in episode.sources if x.id not in b.source_ids], episode.sources)
                for cand in rewrites(s, research, before, after, ask, why, said=said, others=others,
                                     allowed=allowed_groups(episode, ())):
                    new, ok2 = cand, verdict(cand, research, allowed, ask)[0]
                    recite = other_facts(cand, episode, b.source_ids, ask) if ok2 is False else []
                    if ok2 or recite:
                        break
                if recite:                            # the rewrite says another fact of the research: cite that one
                    ok2 = True
                    b.source_ids = list(dict.fromkeys(list(b.source_ids) + recite))
                    research = research_text(b.source_ids, episode.sources)
            failed = '; the rewrite still said more than the fact' if new else '; no usable rewrite'
            if new and ok2:
                kept.append(new)
                entry.update(action='rewritten' + (f'; now cites {", ".join(recite)}' if recite else ''), now=new,
                             fact_ids=list(b.source_ids))
            elif not cited:                           # a beat with no fact may hold only a line that states none
                bridge = [x for x in rewrites(s, '', before, after, ask, why=why, bridge=True, said=said, allowed=allowed)
                          if verdict(x, '', allowed, ask, NONFACT_SYSTEM)[0] is not False]
                if bridge:
                    kept.append(bridge[0])
                    entry.update(action='claim dropped; a line with no fact in its place', now=bridge[0])
                elif len(lines) > 1:
                    entry.update(action='dropped', now='', why=why + '; no line without a fact could replace it')
                else:
                    kept.append(s)
                    entry.update(action='not fixed (look at it)', now=s)
                    out['notes'].append(f'{b.id}: "{s}" states a fact but the beat cites none, and it could not be fixed')
            elif claim and not repeats(claim, said) and verdict(claim, research, allowed, ask)[0] is not False:
                kept.append(claim)                    # the plan's claim, checked before writing: the beat keeps its fact
                entry.update(action="said the beat's planned claim", now=claim, why=why + failed)
            elif len(lines) > 1:
                entry.update(action='dropped', now='', why=why + failed)
            else:                                     # the only sentence: the claim goes, the beat stays
                bridge = rewrite(s, research, before, after, ask, why=why, bridge=True, said=said, allowed=allowed)
                said_all = ' '.join(x.narration for x in beats if x is not b).casefold()
                facts = sorted((x for x in episode.sources if x.id in b.source_ids), key=lambda x: len(x.claim.split()))
                unsaid = [x for x in facts if x.claim.casefold().rstrip('.') not in said_all
                          and not repeats(x.claim, [y.narration for y in beats if y is not b])]
                if bridge and b.role not in grounding.FACT_ROLES and supported(bridge, research, ask)[0] is not False:
                    kept.append(bridge)
                    entry.update(action='claim dropped; a line with no fact in its place', now=bridge, why=why + failed)
                elif unsaid:                          # research checked this claim against its quote
                    kept.append(unsaid[0].claim)
                    entry.update(action='said the fact as written', now=unsaid[0].claim, why=why + failed)
                else:                                 # nothing usable: kept, and said so for the gate
                    kept.append(s)
                    entry.update(action='not fixed (look at it)', now=s, why=why + failed)
                    out['notes'].append(f'{b.id}: "{s}" is not supported by {", ".join(b.source_ids)} and could not be fixed')
            out['changed'].append(entry)
            changed = changed or entry['now'] != s
        if changed:
            b.narration = ' '.join(kept)
        if kinds.get(b.id) in grounding.NO_FACT and b.source_ids:
            out['notes'].append(f'{b.id}: planned as a {kinds[b.id]} line, but it states a fact; it now cites '
                                f'{", ".join(b.source_ids)}')
        check_texts(b, episode, ask, out)
    rewritten = [c for c in out['changed'] if c['now'] != c['was']]
    total = out['checked'] + out['text_checked']
    out['rate'] = round(len(rewritten) / total, 3) if total else 0.0
    out['plan_failed'] = out['rate'] > RATE_LIMIT
    said = [c for c in rewritten if c.get('field', 'narration') == 'narration']
    out['narration_rate'] = round(len(said) / out['checked'], 3) if out['checked'] else 0.0
    out['text_rate'] = round((len(rewritten) - len(said)) / out['text_checked'], 3) if out['text_checked'] else 0.0
    out['notes'].append(f'rewrite rate {round(100 * out["rate"])} % ({len(rewritten)} of {total} lines: '
                        f'narration {len(said)} of {out["checked"]}, texts {len(rewritten) - len(said)} of '
                        f'{out["text_checked"]})'
                        + (f'; above {round(100 * RATE_LIMIT)} %: the fact-first plan failed' if out['plan_failed'] else ''))
    if out['changed']:
        from . import auto                            # an anchor word that is no longer said: re-anchored
        out['notes'] += auto.fix_script(episode)
    return out


def view(result: dict) -> list:
    """Plain lines for logs and the episode page."""
    lines = []
    for c in (result or {}).get('changed') or []:
        what = '' if c.get('field', 'narration') == 'narration' else f'{c["field"]} '
        if c['action'] == 'text removed':
            lines.append(f'{c["beat"]}: {what}removed "{c["was"]}" ({c["why"]})')
        elif c['action'] == 'dropped':
            lines.append(f'{c["beat"]}: dropped "{c["was"]}" ({", ".join(c["fact_ids"]) or "no fact"} does not say it)')
        elif c['now'] == c['was']:
            lines.append(f'{c["beat"]}: {what}"{c["was"]}" {c["action"]}')
        else:
            lines.append(f'{c["beat"]}: {what}"{c["was"]}" -> "{c["now"]}" ({c["action"]}; '
                         f'{", ".join(c["fact_ids"]) or "no fact"})')
    return lines
