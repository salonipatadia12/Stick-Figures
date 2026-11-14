"""Fact entailment (user 2026-11-14): before the script gate, every narrated sentence of a beat that cites a fact is
checked against that fact's quote. One short yes/no call to the local model per sentence (cached like every model
call). 2026-11-14: the crude-oil script said "Modern geochemistry finds oil starts with tiny marine bacteria" citing
s3, which says only "thermal conversion of sedimentary organic matter derived from living organisms".

A true sentence that cites the wrong fact is re-cited to the fact that says it. Otherwise a sentence the facts do not
support is rewritten to say only what the research says (its cited facts, or another fact it then cites), then
checked again. If the rewrite
still does not pass, the claim is dropped and the beat kept: the sentence is removed when the beat has another
sentence; a beat of one sentence gets a short line that states no fact in its place (a question or a transition),
or, failing that, the cited fact as research wrote it if no other beat says it already.
Every rewrite and drop is logged (`episode.meta['fact_check']`, the job notes). A question or a line that states no
fact passes as it is; beats that cite nothing are not checked here (the writer may not state facts there).
"""
from __future__ import annotations

import re

ENTAIL_VERSION = 'entail-v1'

CHECK_SYSTEM = """You check one sentence of a narrated science video against the research it cites. First say whether
the sentence states a fact at all (a question, a transition, a joke or "here is why" states none). If it does, list
every word or phrase OF THE SENTENCE that says something the cited research does not say: a different or more
specific cause, organism, material, place, number, date, field of science or certainty (in "oil starts with tiny
marine bacteria", against research about "living organisms": "tiny marine bacteria"). A plainer or simpler wording of
the research is fine, and so is leaving its details out: list only what the sentence ADDS. supported=true when the
list is empty."""

CHECK_SCHEMA = dict(type='object', required=['states_a_fact', 'adds', 'supported', 'why'],
                    properties=dict(states_a_fact=dict(type='boolean'), adds=dict(type='array', items=dict(type='string')),
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

SENTENCE = re.compile(r'(?<=[.!?])\s+(?=["\'A-Z0-9])')
NUMBER = re.compile(r'\d[\d,.]*')


def sentences(text: str) -> list:
    return [s for s in SENTENCE.split(' '.join(str(text or '').split())) if s.strip()]


def research_text(fact_ids, sources) -> str:
    by = {s.id: s for s in sources}
    rows = []
    for i in fact_ids:
        s = by.get(i)
        if s is not None:
            rows.append(f'{s.id}: {s.claim}' + (f'\n  quote: "{s.quote}"' if s.quote else ''))
    return '\n'.join(rows)


def supported(sentence: str, research: str, ask) -> tuple[bool | None, str]:
    """(True/False, why); None when the model gave no usable answer (the sentence is then kept, noted)."""
    if sentence.rstrip().endswith('?'):
        return True, 'a question states no fact'
    try:
        a = ask(CHECK_SYSTEM, f'Sentence: {sentence}\n\nCited research:\n{research}', CHECK_SCHEMA) or {}
    except Exception as e:                            # the model is down: never stop the episode for it
        return None, f'not checked: the local model did not answer ({type(e).__name__})'
    if not isinstance(a, dict) or not isinstance(a.get('supported'), bool):
        return None, 'not checked: the answer had no verdict'
    why = ' '.join(str(a.get('why') or '').split())[:200]
    if a.get('states_a_fact') is False:
        return True, why or 'states no fact'
    adds = [str(x) for x in a.get('adds') or [] if str(x).strip()]
    real = [x for x in adds if added_words(x, sentence, research)]
    if a['supported'] or not real:
        # 2026-11-14: the 9B judge failed "over millions of years" for leaving out kerogen; an "addition" whose words
        # are all in the research (or not in the sentence) is an omission, which is fine
        return True, why
    return False, f'adds {", ".join(repr(x) for x in real)}: {why}'


def other_facts(sentence: str, episode, cited, ask) -> list:
    """The ids of research facts the beat does not cite that, with its cited facts, support the sentence ([] when
    none do): a true line that cites the wrong fact is re-cited, not rewritten. 2026-11-14: "Heat and pressure turn
    this biomass into energy-dense hydrocarbons" cited s2 (deoxygenation); EIA's "heat and pressure ... turned the
    remains into crude oil" was another fact. The candidates are picked in code (the facts that hold the words the
    sentence adds to its cited facts, at most two); the model then makes the same short check as for any sentence."""
    from .qa import content_words
    mine = research_text(cited, episode.sources)
    extra = content_words(sentence) - content_words(mine)
    if not extra:
        return []
    others = [x for x in episode.sources if x.id not in cited]
    cover = lambda x: extra & content_words(f'{x.claim} {x.quote}')
    picked, left = [], set(extra)
    for x in sorted(others, key=lambda x: -len(cover(x))):
        if len(picked) < 2 and cover(x) & left:
            picked.append(x.id)
            left -= cover(x)
    if not picked or len(left) > len(extra) / 2:      # most of what it adds is in no fact
        return []
    ok, _ = supported(sentence, research_text(list(cited) + picked, episode.sources), ask)
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
             said=(), others: str = '') -> list:
    """Up to two versions of the sentence rewritten to the research (or, bridge=True, lines in its place that state
    no fact); a version with a new number, a bad length, or that repeats another line is left out."""
    n = len(sentence.split())
    user = (f'Previous line: {before or "(none: this opens the video)"}\nSentence to fix: {sentence}\n'
            f'Next line: {after or "(none: this ends the video)"}\n'
            + (f'What is wrong with it: {why}\n' if why else '')
            + (('Other lines of the video (do not repeat them):\n' + '\n'.join(f'- {x}' for x in said) + '\n') if said else '')
            + f'\nCited research:\n{research}\n'
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
        if repeats(new, said) or new in out:
            continue                                  # another beat says it already
        out.append(new if new[-1] in '.!?' else new + '.')
    return out


def rewrite(sentence: str, research: str, before: str, after: str, ask, **kw) -> str:
    """The first usable version (`rewrites`), or ''."""
    got = rewrites(sentence, research, before, after, ask, **kw)
    return got[0] if got else ''


def default_ask(cache=None, ledger=None):
    """The local model, or None when this run has none (the paid provider: nothing is checked, it is noted)."""
    from . import config, llm
    if config.script_provider() != 'ollama':
        return None
    return lambda s, u, sc: llm.generate_json(s, u, sc, 300, cache=cache, ledger=ledger, step='fact-check',
                                              detail='sentence vs its cited fact', temperature=0.0)


def check(episode, cache=None, ledger=None, ask=None) -> dict:
    """Check every sentence of every beat that cites a fact; rewrite or drop what the facts do not support (in place).
    Returns {version, checked, changed: [{beat, was, now, action, fact_ids, why}], notes}."""
    ask = ask or default_ask(cache, ledger)
    out = dict(version=ENTAIL_VERSION, checked=0, changed=[], notes=[])
    if ask is None:
        out['notes'].append('fact check: not run (it uses the local model)')
        return out
    beats = episode.beats
    original = [b.narration for b in beats]           # the rewrites read the lines as written, not each other's fixes
    for k, b in enumerate(beats):
        research = research_text(b.source_ids, episode.sources)
        if not research:
            continue
        lines = sentences(b.narration)
        kept, changed = [], False
        for j, s in enumerate(lines):
            out['checked'] += 1
            ok, why = supported(s, research, ask)
            if ok is None:
                out['notes'].append(f'{b.id}: {why}')
            if ok is not False:
                kept.append(s)
                continue
            more = other_facts(s, episode, b.source_ids, ask)
            if more:                                  # true, but it cited the wrong fact: cite the right one
                kept.append(s)
                new_ids = list(dict.fromkeys(list(b.source_ids) + more))
                out['changed'].append(dict(beat=b.id, was=s, now=s, fact_ids=new_ids, why=why,
                                           action=f'kept; now cites {", ".join(more)}'))
                b.source_ids = new_ids
                research = research_text(b.source_ids, episode.sources)
                continue
            before = (lines[j - 1] if j else (original[k - 1] if k else ''))
            after = (lines[j + 1] if j + 1 < len(lines) else (original[k + 1] if k + 1 < len(beats) else ''))
            said = [x.narration for x in beats if x is not b] + [x for x in lines if x != s]
            others = research_text([x.id for x in episode.sources if x.id not in b.source_ids], episode.sources)
            new, ok2, recite = '', False, []
            for cand in rewrites(s, research, before, after, ask, why, said=said, others=others):
                new, ok2 = cand, supported(cand, research, ask)[0]
                recite = other_facts(cand, episode, b.source_ids, ask) if ok2 is False else []
                if ok2 or recite:
                    break
            if recite:                                # the rewrite says another fact of the research: cite that one
                ok2 = True
                b.source_ids = list(dict.fromkeys(list(b.source_ids) + recite))
                research = research_text(b.source_ids, episode.sources)
            entry = dict(beat=b.id, was=s, fact_ids=list(b.source_ids), why=why)
            failed = '; the rewrite still said more than the fact' if new else '; no usable rewrite'
            if new and ok2:
                kept.append(new)
                entry.update(action='rewritten' + (f'; now cites {", ".join(recite)}' if recite else ''), now=new,
                             fact_ids=list(b.source_ids))
            elif len(lines) > 1:
                entry.update(action='dropped', now='', why=why + failed)
            else:                                     # the only sentence: the claim goes, the beat stays
                bridge = rewrite(s, research, before, after, ask, why=why, bridge=True, said=said)
                said = ' '.join(x.narration for x in beats if x is not b).casefold()
                facts = sorted((x for x in episode.sources if x.id in b.source_ids), key=lambda x: len(x.claim.split()))
                unsaid = [x for x in facts if x.claim.casefold().rstrip('.') not in said
                          and not repeats(x.claim, [y.narration for y in beats if y is not b])]
                if bridge and supported(bridge, research, ask)[0] is not False:
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
    if out['changed']:
        from . import auto                            # an anchor word that is no longer said: re-anchored
        out['notes'] += auto.fix_script(episode)
    return out


def view(result: dict) -> list:
    """Plain lines for logs and the episode page."""
    lines = []
    for c in (result or {}).get('changed') or []:
        if c['action'] == 'dropped':
            lines.append(f'{c["beat"]}: dropped "{c["was"]}" ({", ".join(c["fact_ids"])} does not say it)')
        elif c['now'] == c['was']:
            lines.append(f'{c["beat"]}: "{c["was"]}" {c["action"]}')
        else:
            lines.append(f'{c["beat"]}: "{c["was"]}" -> "{c["now"]}" ({c["action"]}; {", ".join(c["fact_ids"])})')
    return lines
