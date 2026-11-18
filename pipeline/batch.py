"""Many checks in one Claude Code call (user 2026-11-18: "under claude-code, batch the fact-check: one call per script
that checks every line (narration, on-screen text, labels) against its facts and returns a verdict per line, instead of
one call per line. Same for the claims split ... Target under 25 calls per video").

Every check keeps its own prompt, its own answer shape and its own code: a check runs once with a recorder that
notes each question it would ask (and answers none, so the check takes its "the model did not answer" path and goes
on to the next question), the questions go out together in ONE call (`many`: each question names its rules, the
rules are written once), and the check then runs again on those answers. A question the first pass could not see
(one that depends on an answer, such as the rewrite of a line that failed) is asked on its own, as before.

    memo = Memo(ask)                  # an ask that answers from what was fetched, else asks
    memo.collect(lambda a: ..., what) # one call for every question the function asks
    run(fn, ask, record=None, what)   # fn(memo) after memo.collect(record or fn)

Only under the Claude Code route (pipeline/llm.py claude_route) and only for an ask that can batch (`asker`): on
Ollama the 9B answers one question at a time exactly as before (one long prompt is what it reads worst).
"""
from __future__ import annotations

import threading

MANY_SYSTEM = """You answer several separate questions in one reply. Each question says which RULES it follows (R1, R2
...), written once below. Answer each question on its own, exactly as you would if it were the only one you were
asked: never let one question's answer change another's. Put each answer under its key (q1, q2 ...) in the shape the
schema gives for it."""
MAX_PER_CALL = 40                 # questions in one call (a script's lines and texts are ~15-35)


class Recorded(Exception):
    """Raised by the recorder: the question was noted, not answered."""


def many(questions: list, *, cache=None, ledger=None, step='fact-check', detail='', temperature=0.0, seed=7,
         max_tokens=4000) -> list:
    """[(system, user, schema)] -> [answer dict, or None when there is none], one call per MAX_PER_CALL questions."""
    from . import llm
    if not questions:
        return []
    chunks = [questions[k:k + MAX_PER_CALL] for k in range(0, len(questions), MAX_PER_CALL)]

    def one(chunk):
        rules = list(dict.fromkeys(s for s, _, _ in chunk))
        system = MANY_SYSTEM + '\n\n' + '\n\n'.join(f'=== RULES R{k}\n{s}' for k, s in enumerate(rules, 1))
        user = '\n\n'.join(f'=== q{k} (rules R{rules.index(s) + 1})\n{u}' for k, (s, u, _) in enumerate(chunk, 1))
        schema = dict(type='object', required=[f'q{k}' for k in range(1, len(chunk) + 1)],
                      properties={f'q{k}': sc for k, (_, _, sc) in enumerate(chunk, 1)})
        got = llm.generate_json(system, user, schema, max_tokens, cache=cache, ledger=ledger, step=step,
                                detail=f'{detail or step}: {len(chunk)} checks in one call', temperature=temperature,
                                seed=seed)
        return [got.get(f'q{k}') if isinstance(got.get(f'q{k}'), dict) else None for k in range(1, len(chunk) + 1)]
    out = []
    for chunk, got in zip(chunks, llm.run_parallel(one, chunks)):
        out += got if isinstance(got, list) else [None] * len(chunk)
    return out[:len(questions)]


def asker(cache=None, ledger=None, step='fact-check', detail='', max_tokens=500, temperature=0.0, seed=7, model=None):
    """The judge's ask(system, user, schema) -> dict, with `.many` for one call of many questions."""
    from . import llm

    def ask(system, user, schema):
        return llm.generate_json(system, user, schema, max_tokens, cache=cache, ledger=ledger, step=step,
                                 detail=detail, temperature=temperature, seed=seed,
                                 **(dict(model=model) if model else {}))
    ask.many = lambda qs, what='': many(qs, cache=cache, ledger=ledger, step=step, detail=what or detail,
                                        temperature=temperature, seed=seed)
    return ask


def on(ask) -> bool:
    """True when this ask's questions go out together: the Claude Code route, and an ask that can batch."""
    from . import llm
    return getattr(ask, 'many', None) is not None and llm.active_route() is not None


class Memo:
    """An ask that answers from the questions fetched together (`collect`), and asks the others one at a time."""

    def __init__(self, ask):
        self.ask, self.table = ask, {}
        self.on = ask is not None and on(ask)
        self.many = getattr(ask, 'many', None)
        self.fetched = self.missed = 0
        self._lock = threading.Lock()

    def __call__(self, system, user, schema):
        hit = self.table.get((system, user))
        if hit is not None:
            return hit
        if self.on:
            self.missed += 1
        return self.ask(system, user, schema)

    def collect(self, fn, what: str = '') -> int:
        """Run fn(recorder) and fetch every new question it asked in one call; returns how many were fetched."""
        if not self.on:
            return 0
        asked = []

        def record(system, user, schema):
            if (system, user) not in self.table and all((system, user) != (s, u) for s, u, _ in asked):
                asked.append((system, user, schema))
            raise Recorded()
        try:
            fn(record)
        except Exception:                               # noqa: BLE001 - what was noted before it stopped is enough
            pass
        if not asked:
            return 0
        try:
            got = self.ask.many(asked, what)
        except Exception:                               # noqa: BLE001 - each question is asked on its own instead
            got = []
        with self._lock:
            for (s, u, _), a in zip(asked, got):
                if isinstance(a, dict):
                    self.table[(s, u)] = a
            self.fetched += len(asked)
        return len(asked)


def run(fn, ask, record=None, what: str = ''):
    """fn(ask), with every question it asks fetched in one call first (`record`: the same check on a copy, for a
    function that changes what it checks)."""
    memo = ask if isinstance(ask, Memo) else Memo(ask)
    if not memo.on:
        return fn(ask)
    memo.collect(record or fn, what)
    return fn(memo)
