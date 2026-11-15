"""The writing benchmark (user 2026-11-15): "Stop fixing one video at a time ... Every future writing change gets judged
on this, not on one video."

    .venv/bin/python bench/score.py                         # every topic of bench/topics.txt, the current writer
    .venv/bin/python bench/score.py --writer qwen3:14b      # a bigger WRITER (the judge stays OLLAMA_MODEL)
    .venv/bin/python bench/score.py --only crude --label try-x
    .venv/bin/python bench/score.py --compare LABEL_A LABEL_B   # no run: two saved runs side by side

For each topic it runs auto mode up to the approved script (no painting or rendering), the same functions as
`stick make --auto` and the web app's Auto toggle: research and storylines, the first storyline the gate accepts, the
script (fact plan, outline, best-of-N lines, staging, labels from the claims, the fact check), then auto's own fixes
and its script gate (auto.pick_hook). Research and the storyline pick are made once per topic and kept in
bench/work/upstream/<slug> (git-ignored), so every run writes from the same facts and the same storyline: only the
writing differs. Each run writes into its own copy (bench/work/runs/<label>/<slug>), so nothing is overwritten.

Scores per script:
  hook      1-5  the judge's fixed rubric: the gap it opens (minus 2 when it gives the answer away, by the judge
                 or by the code check grounding.gives_away)
  flow      1-5  the judge's fixed rubric: each beat adds something new and follows on (minus 1 per repeated claim the
                 code finds: grounding.same_claim on the split claims; the payoff may restate the twist)
  accuracy  0-1  share of the final lines and on-screen texts that pass the claims check (entail.verdict)
  rewrite   0-1  the fact check's rewrite rate (narration and texts), as logged by the script step
  clean     0/1  auto mode reached an approved script with no manual step (no error, auto.pick_hook found a hook)
  seconds        the script stage's wall time
  overall   0-1  mean of hook/5, flow/5, accuracy, 1 - rewrite, clean

The judge is the local judge model (OLLAMA_MODEL, qwen3.5:9b) at temperature 0 with fixed prompts. Results go to
bench/results/<date>.json (one file per day, a list of runs) and are printed next to the previous run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / 'bench'
WORK = BENCH / 'work'
RESULTS = BENCH / 'results'
UPSTREAM = WORK / 'upstream'
SCORE_VERSION = 'bench-v1'

HOOK_RUBRIC = """You grade the HOOK (the first line) of a narrated 45-second explainer Short with a fixed rubric.
gap (1-5): 5 = the viewer must keep watching to learn something specific the line makes them want; 4 = clear pull;
3 = mildly curious; 2 = weak; 1 = no question opened (a flat statement or a summary).
gives_away: true when the hook already states the answer the video reveals later (given below), even in part.
Judge only the hook line."""
HOOK_SCHEMA = dict(type='object', required=['gap', 'gives_away', 'why'], properties=dict(
    gap=dict(type='integer', minimum=1, maximum=5), gives_away=dict(type='boolean'), why=dict(type='string')))
FLOW_RUBRIC = """You grade the STORY FLOW of a narrated 45-second explainer Short, given its lines in order, with a
fixed rubric. flow (1-5): 5 = every line adds something new and follows naturally from the one before, building to a
payoff that answers the opening; 4 = good with one weak step; 3 = some lines feel disconnected or say little;
2 = several lines repeat each other or jump; 1 = no story. repeated: the numbers of lines that say again what an
earlier line already said ([] when none)."""
FLOW_SCHEMA = dict(type='object', required=['flow', 'repeated', 'why'], properties=dict(
    flow=dict(type='integer', minimum=1, maximum=5), repeated=dict(type='array', items=dict(type='integer')),
    why=dict(type='string')))


def topics(path=BENCH / 'topics.txt') -> list:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            parts = [p.strip() for p in line.split('|')] + ['', '']
            out.append(dict(topic=parts[0], niche=parts[1], audience=parts[2]))
    return out


def commit() -> str:
    try:
        sha = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, text=True).strip()
        dirty = subprocess.call(['git', 'diff', '--quiet', '--', 'pipeline', 'data'], cwd=ROOT) != 0
        return sha + ('+changes' if dirty else '')
    except Exception:
        return ''


# ------------------------------------------------------------------ the runs

def upstream(t) -> tuple[Path, float, str]:
    """(folder, seconds, error): research, storylines and the storyline pick for a topic, made once."""
    from pipeline import auto, cli, store, styles
    from pipeline.models import Episode, Voice
    slug = store.slugify(t['topic'])
    base = UPSTREAM / slug
    if (base / 'episode.json').is_file():
        ep, _ = store.load(base)
        if ep.storyline:
            return base, 0.0, ''
    t0 = time.time()
    fields = cli.BRIEF_DEFAULTS | {k: t[k] for k in ('niche', 'audience') if t[k]}
    styles.load(fields['style'])
    if not (base / 'episode.json').is_file():
        store.save(Episode(slug=slug, topic=t['topic'], **fields, voice=Voice()), base)
    ep, _ = store.load(base)
    from pipeline.story import pitch
    ep, _ = pitch(ep, base, store.ledger(base))
    store.save(ep, base)
    n, why = auto.first_storyline(ep)
    if n is None:
        return base, round(time.time() - t0, 1), f'auto cannot pick a storyline: {why}'
    if cli._approve(slug, 'storyline', story=n, by='auto (bench)') != 0:
        return base, round(time.time() - t0, 1), 'the storyline gate refused the pick'
    return base, round(time.time() - t0, 1), ''


def script_stage(src: Path, dest: Path) -> dict:
    """Auto mode from the picked storyline to the script gate, in a copy. {seconds, clean, problems, error, base}."""
    from pipeline import auto, store, styles
    from pipeline.script import generate
    shutil.copytree(src, dest)
    ep, base = store.load(dest)
    style = styles.load(ep.style)
    t0 = time.time()
    out = dict(base=str(dest), clean=False, problems=[], error='')
    try:
        ep, notes = generate(ep, style, base, store.ledger(base))
        store.save(ep, base)
        out['notes'] = notes
        auto.fix_script(ep)
        auto.resolve_bases(ep)
        store.save(ep, base)
        hook, problems = auto.pick_hook(ep, style)
        out['problems'] = problems
        if hook is not None:
            if hook and ep.hook_options:
                ep.pick_hook(hook)
                store.save(ep, base)
            out['clean'] = True
    except Exception as exc:                          # a failed script is a score, not a crash of the benchmark
        out['error'] = f'{type(exc).__name__}: {str(exc)[:300]}'
        out['trace'] = traceback.format_exc()[-1500:]
    out['seconds'] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------ the scores

def judge_ask(cache):
    from pipeline import llm
    return lambda s, u, sc: llm.generate_json(s, u, sc, 400, cache=cache, step='bench', detail='bench judge',
                                              temperature=0.0)


def score(base: Path) -> dict:
    """The scores of the script in `base` (see the module docstring)."""
    from pipeline import entail, grounding, store
    from pipeline.cache import for_episode
    ep, _ = store.load(base)
    if not ep.beats:
        return dict(hook=1, flow=1, accuracy=0.0, rewrite=1.0, lines=[])
    cache = for_episode(base)
    ask = entail.default_ask(cache)
    judge = judge_ask(cache)
    interp = (ep.meta or {}).get('interpretation') or {}
    plan = ((ep.meta or {}).get('fact_plan') or {}).get('beats') or []
    by = {s.id: s for s in ep.sources}
    twist_id = (interp.get('twist') or {}).get('id') or ((ep.meta or {}).get('fact_plan') or {}).get('twist', '')
    twist_claim = next((b.get('claim') for b in plan if b.get('role') == 'twist' and b.get('claim')), '') \
        or (by[twist_id].claim if twist_id in by else '')
    lines = [b.narration for b in ep.beats]
    hook = lines[0]
    # hook: the judge's gap score, and the code check that it does not state the twist
    code_gives = grounding.gives_away(hook, f'{by[twist_id].claim} {by[twist_id].quote}' if twist_id in by else '',
                                      twist_claim, interp, ep.sources, ep.topic)
    try:
        h = judge(HOOK_RUBRIC, f'Topic: {ep.topic}\nThe answer the video reveals later: {twist_claim or "(unknown)"}\n'
                               f'Hook: {hook}', HOOK_SCHEMA) or {}
    except Exception as exc:
        h = dict(gap=1, gives_away=False, why=f'not judged ({type(exc).__name__})')
    gap = int(h.get('gap') or 1)
    hook_score = max(1, gap - (2 if (h.get('gives_away') or code_gives) else 0))
    # flow: the judge's rubric, and the code check for repeated claims across beats
    ignore = grounding.topic_words(ep)
    claims = [[c['text'] for c in (entail.split_claims(x, ask)[0] or []) if c['kind'] == 'fact'] for x in lines]
    repeats = []
    for k in range(len(lines)):
        for j in range(k):
            rk = plan[k]['role'] if k < len(plan) else ep.beats[k].role
            rj = plan[j]['role'] if j < len(plan) else ep.beats[j].role
            if rk == 'payoff' and rj == 'twist':
                continue
            hit = grounding.repeated_claim(claims[k], [(f'b{j + 1:02d}', claims[j])], ignore)
            if hit:
                repeats.append(f'b{k + 1:02d}: {hit}')
                break
    try:
        f = judge(FLOW_RUBRIC, f'Topic: {ep.topic}\nLines:\n' + '\n'.join(f'{k}. {x}' for k, x in enumerate(lines, 1)),
                  FLOW_SCHEMA) or {}
    except Exception as exc:
        f = dict(flow=1, repeated=[], why=f'not judged ({type(exc).__name__})')
    flow_score = max(1, int(f.get('flow') or 1) - len(repeats))
    # accuracy: the final lines and texts through the claims check
    fails, checked = [], 0
    for b in ep.beats:
        research = entail.research_text(b.source_ids, ep.sources)
        allowed = entail.allowed_groups(ep, b.source_ids)
        ok, why = entail.verdict(b.narration, research, allowed, ask,
                                 entail.CHECK_SYSTEM if b.source_ids else entail.NONFACT_SYSTEM)
        checked += 1
        if ok is False:
            fails.append(f'{b.id} narration "{b.narration}": {why}')
        for field, text, *_ in entail.texts(b):
            checked += 1
            if entail.marks_myth(text, ep) or entail.echoes(text, b.narration, allowed):
                continue
            ok, why = entail.verdict(text, research or entail.NO_RESEARCH, allowed, ask, entail.TEXT_SYSTEM)
            if ok is False:
                fails.append(f'{b.id} {field} "{text}": {why}')
    fc = (ep.meta or {}).get('fact_check') or {}
    lab = (ep.meta or {}).get('label_grounding') or {}
    best = next((e for e in ((ep.meta or {}).get('script') or {}).get('repairs', []) if e.get('stage') == 'best'), {})
    fallbacks = [e['beat'] for e in best.get('entries', []) if not e.get('kept')]
    return dict(hook=hook_score, hook_gap=gap, hook_gives_away=bool(h.get('gives_away')), hook_code_gives_away=code_gives,
                hook_why=' '.join(str(h.get('why') or '').split())[:200],
                flow=flow_score, flow_judge=int(f.get('flow') or 1), flow_judge_repeated=f.get('repeated') or [],
                flow_why=' '.join(str(f.get('why') or '').split())[:200], repeated_claims=repeats,
                accuracy=round(1 - len(fails) / max(1, checked), 3), accuracy_fails=fails,
                rewrite=round(float(fc.get('rate') or 0), 3), narration_rate=fc.get('narration_rate'),
                text_rate=fc.get('text_rate'), labels_rewritten=lab.get('rewritten', 0), labels_dropped=lab.get('dropped', 0),
                fallback_beats=fallbacks,
                lines=[dict(beat=b.id, role=b.role, say=b.narration,
                            texts=[t for _, t, *_ in entail.texts(b)]) for b in ep.beats])


def overall(s: dict, clean: bool) -> float:
    return round((s['hook'] / 5 + s['flow'] / 5 + s['accuracy'] + (1 - min(1.0, s['rewrite'])) + (1 if clean else 0)) / 5, 3)


# ------------------------------------------------------------------ results

def all_runs() -> list:
    runs = []
    for p in sorted(RESULTS.glob('*.json')):
        try:
            runs += json.loads(p.read_text()).get('runs', [])
        except (OSError, ValueError):
            continue
    return sorted(runs, key=lambda r: r.get('finished') or r.get('started') or '')


def save_run(run: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f'{run["started"][:10]}.json'
    data = json.loads(p.read_text()) if p.is_file() else dict(version=SCORE_VERSION, runs=[])
    data['runs'] = [r for r in data['runs'] if r['label'] != run['label']] + [run]
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    return p


METRICS = ('overall', 'hook', 'flow', 'accuracy', 'rewrite', 'clean', 'seconds')


def summary(run: dict) -> dict:
    rows = [t for t in run['topics'] if 'scores' in t]
    if not rows:
        return {}
    avg = lambda f: round(sum(f(t) for t in rows) / len(rows), 3)
    return dict(overall=avg(lambda t: t['overall']), hook=avg(lambda t: t['scores']['hook']),
                flow=avg(lambda t: t['scores']['flow']), accuracy=avg(lambda t: t['scores']['accuracy']),
                rewrite=avg(lambda t: t['scores']['rewrite']), clean=avg(lambda t: 1 if t['clean'] else 0),
                seconds=avg(lambda t: t['seconds']), topics=len(rows))


def compare(a: dict | None, b: dict) -> str:
    """Markdown: run a (previous) next to run b, the averages and each topic."""
    sa, sb = (a or {}).get('summary') or {}, b.get('summary') or {}
    head = f'| metric | {a["label"] if a else "(no previous run)"} | {b["label"]} | change |'
    out = [head, '|---|---|---|---|']
    for m in METRICS:
        x, y = sa.get(m), sb.get(m)
        d = '' if x is None or y is None else f'{y - x:+.3g}'
        out.append(f'| {m} | {"" if x is None else x} | {"" if y is None else y} | {d} |')
    out += ['', '| topic | ' + ' | '.join(f'{r["label"]} hook/flow/acc/rw/clean/s' for r in (a, b) if r) + ' |',
            '|---|' + '---|' * len([r for r in (a, b) if r])]
    cell = lambda t: (f'{t["scores"]["hook"]}/{t["scores"]["flow"]}/{t["scores"]["accuracy"]}/{t["scores"]["rewrite"]}/'
                      f'{"yes" if t["clean"] else "NO"}/{t["seconds"]:.0f}') if t and 'scores' in t else \
        ('failed: ' + (t.get('error') or '')[:60] if t else '')
    names = list(dict.fromkeys([t['topic'] for r in (a, b) if r for t in r['topics']]))
    for name in names:
        ta = next((t for t in (a or {}).get('topics', []) if t['topic'] == name), None)
        tb = next((t for t in b['topics'] if t['topic'] == name), None)
        out.append(f'| {name} | ' + ' | '.join(cell(t) for t, r in ((ta, a), (tb, b)) if r) + ' |')
    return '\n'.join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--writer', help='the writer model (OLLAMA_WRITER_MODEL); default: the judge model')
    ap.add_argument('--label', help='a name for this run (default: date, time and writer)')
    ap.add_argument('--only', help='only topics whose text contains this (comma-separated)')
    ap.add_argument('--compare', nargs=2, metavar=('A', 'B'), help='print two saved runs side by side and stop')
    args = ap.parse_args(argv)
    if args.compare:
        runs = {r['label']: r for r in all_runs()}
        print(compare(runs[args.compare[0]], runs[args.compare[1]]))
        return 0
    os.environ['STICK_EPISODES_DIR'] = str(UPSTREAM)  # the storyline gate's own code acts on the bench's copies only
    if args.writer:
        os.environ['OLLAMA_WRITER_MODEL'] = args.writer
    sys.path.insert(0, str(ROOT))
    from pipeline import config
    config.load_env()
    if args.writer:                                   # .env must not take the writer back
        os.environ['OLLAMA_WRITER_MODEL'] = args.writer
    writer, judge = config.ollama_writer_model(), config.ollama_model()
    started = time.strftime('%Y-%m-%dT%H:%M:%S')
    label = args.label or f'{started[:16].replace(":", "")}-{writer.replace(":", "-")}'
    prev = (all_runs() or [None])[-1]
    run = dict(version=SCORE_VERSION, label=label, writer=writer, judge=judge, commit=commit(), started=started,
               topics=[])
    picked = [t for t in topics() if not args.only or any(o.strip().lower() in t['topic'].lower()
                                                           for o in args.only.split(','))]
    for t in picked:
        print(f'\n=== {t["topic"]} (writer {writer}, judge {judge})', flush=True)
        row = dict(topic=t['topic'])
        try:
            src, up_s, err = upstream(t)
        except Exception as exc:
            src, up_s, err = None, 0.0, f'{type(exc).__name__}: {str(exc)[:300]}'
        row['upstream_seconds'] = up_s
        if err:
            row.update(clean=False, error=f'before the script: {err}', seconds=0.0)
        else:
            res = script_stage(src, WORK / 'runs' / label / src.name)
            row.update(clean=res['clean'], problems=res['problems'], error=res['error'], seconds=res['seconds'],
                       folder=str(Path(res['base']).relative_to(ROOT)))
            if not res['error']:
                row['scores'] = score(Path(res['base']))
                row['overall'] = overall(row['scores'], row['clean'])
        run['topics'].append(row)
        run['summary'] = summary(run)
        run['finished'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        save_run(run)                                 # after every topic: a stopped run keeps what it scored
        s = row.get('scores') or {}
        print(f'  clean={row["clean"]} seconds={row["seconds"]} ' + (
            f'hook={s["hook"]} flow={s["flow"]} accuracy={s["accuracy"]} rewrite={s["rewrite"]} overall={row["overall"]}'
            if s else f'error: {row.get("error")}'), flush=True)
        for line in s.get('lines', []):
            print(f'    {line["beat"]} {line["role"]}: {line["say"]}')
    path = save_run(run)
    print(f'\nSaved {path.relative_to(ROOT)}\n')
    print(compare(prev, run))
    return 0


if __name__ == '__main__':
    sys.exit(main())
