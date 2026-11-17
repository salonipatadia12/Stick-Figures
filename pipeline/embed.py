"""Repeats by meaning (user 2026-11-16: "flag any two narration lines with high similarity; the payoff may restate the
core answer once"), with a local embedding model (config.ollama_embed_model, nomic-embed-text, Apache 2.0) on the same
Ollama.

Two lines of one script are said to repeat when their vectors are close (cosine RAW or more), or fairly close (RAW_LOW)
and still close once the script's shared subject is taken out (the vectors centred on the script's mean: CENTRED). Every
line of a script is about the topic, so the raw cosine of two short lines on different points is high too ("Yawning cools
your brain" / "Your yawn starts in the hypothalamus": 0.91 raw); centring removes that. Tuned 2026-11-16 on the 9b-core
bench scripts and crude oil -8 (repeats such as "plankton gets buried deep and turns to oil" / "heat cooks that buried
plankton into crude oil": 0.80 raw, 0.30 centred; different points 0.40-0.75 raw).
"""
from __future__ import annotations

import math

RAW, RAW_LOW, CENTRED = 0.78, 0.70, 0.20
TIMEOUT_S = 60
_CACHE: dict = {}


def vectors(lines: list) -> list | None:
    """One vector per line, or None when the embedding model cannot be reached or STICK_EMBED=0 (the tests: the
    caller falls back to shared words)."""
    import os
    from . import config, llm
    if os.getenv('STICK_EMBED', '1').strip() == '0':
        return None
    model = config.ollama_embed_model()
    want = [x for x in dict.fromkeys(lines) if (model, x) not in _CACHE]
    if want:
        try:
            r = llm._requests().post(f'{config.ollama_host()}/api/embed', json=dict(model=model, input=want),
                                     timeout=TIMEOUT_S)
            got = r.json().get('embeddings') if r.status_code == 200 else None
        except Exception:
            got = None
        if not got or len(got) != len(want):
            return None
        _CACHE.update({(model, x): v for x, v in zip(want, got)})
    return [_CACHE[(model, x)] for x in lines]


def _cos(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def similar_pairs(lines: list, vecs=None) -> list | None:
    """[(j, i, raw, centred)] for every pair j < i of lines that say the same thing; None without the model."""
    vecs = vecs if vecs is not None else vectors(lines)
    if vecs is None:
        return None
    n = len(vecs)
    if n < 2:
        return []
    mean = [sum(v[d] for v in vecs) / n for d in range(len(vecs[0]))]
    cen = [[x - m for x, m in zip(v, mean)] for v in vecs]
    out = []
    for i in range(n):
        for j in range(i):
            if not lines[i].strip() or not lines[j].strip():
                continue
            raw, c = _cos(vecs[i], vecs[j]), _cos(cen[i], cen[j])
            if raw >= RAW or (raw >= RAW_LOW and c >= CENTRED):
                out.append((j, i, round(raw, 3), round(c, 3)))
    return out
