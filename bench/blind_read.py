"""A blind read of benchmark scripts (user 2026-11-17: "show the three scripts ... labelled only A, B, C in a shuffled
order per topic, narration only, no scores. Put the key ... at the very bottom").

    .venv/bin/python bench/blind_read.py --run 9B=9b-whole-fix,9b-whole --run "Claude Code=claude-code" \
        --run "Ministral 3 14B=ministral-14b" --out bench/results/blind-read.md

Each --run is NAME=LABEL[,LABEL...]: a topic's script comes from the first of the labels that scored it. The order of
the letters is shuffled per topic (seeded, so the file is the same each time it is written).
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import all_runs, topics  # noqa: E402


def lines_for(runs: dict, labels: list, topic: str):
    for label in labels:
        run = runs.get(label)
        for t in (run or {}).get('topics', []):
            if t['topic'] == topic and 'scores' in t:
                return [l['say'] for l in t['scores']['lines']], label
    return None, None


def orders(spec: list, names: list, seed: int) -> dict:
    """{topic: the writers' order}, seeded (the same file each time), drawn again until the order is not the same on
    every topic: a letter that always means one writer would give the key away (2026-11-19: two writers came out
    A/B on all five topics, twice)."""
    for k in range(100):
        rng = random.Random(seed + k)
        got = {}
        for name in names:
            order = list(range(len(spec)))
            rng.shuffle(order)
            got[name] = order
        if len(names) < 2 or len(spec) < 2 or len({tuple(o) for o in got.values()}) > 1:
            return got
    return got


def build(spec: list, seed: int = 20261117) -> str:
    runs = {r['label']: r for r in all_runs()}
    letters = 'ABCDEFG'
    out = ['# Blind read', '', 'Each topic has one script per writer, in a shuffled order. Narration only, no scores. '
           'Pick the best per topic before you read the key at the bottom.', '']
    key = []
    names = [t['topic'] for t in topics()]
    order = orders(spec, names, seed)
    for name in names:
        got = [(spec[i][0], *lines_for(runs, spec[i][1], name)) for i in order[name]]
        got = [g for g in got if g[1]]
        out += [f'## {name}', '']
        row = []
        for letter, (who, lines, label) in zip(letters, got):
            out += [f'### {letter}', '']
            out += [f'{k}. {x}' for k, x in enumerate(lines, 1)] + ['']
            row.append(f'{letter} = {who} ({label})')
        missing = [who for who, labels in spec if who not in [g[0] for g in got]]
        if missing:
            row.append('no script: ' + ', '.join(missing))
        key.append(f'- {name}: ' + '; '.join(row))
    out += ['', '', '---', '', '*(Spoilers below: the key.)*', '', '', '', '---', '', '## Key', ''] + key + ['']
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='append', required=True, help='NAME=LABEL[,LABEL...]')
    ap.add_argument('--out', default=str(Path(__file__).resolve().parent / 'results' / 'blind-read.md'))
    args = ap.parse_args(argv)
    spec = [(r.split('=', 1)[0], r.split('=', 1)[1].split(',')) for r in args.run]
    Path(args.out).write_text(build(spec), encoding='utf-8')
    print(args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
