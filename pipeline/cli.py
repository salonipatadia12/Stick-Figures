"""`stick`: make a narrated, captioned stick-figure Short from a topic.

Exit codes: 0 done, 1 error, 2 the episode fails editorial checks, 3 waiting at an approval
gate, 4 the budget cap would be exceeded (nothing was sent).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import config, store, styles
from .cost import BudgetExceeded, PriceUnknown, usd
from .models import Episode, Voice, episode_json_schema
from .review import script_view

EXIT_ERROR, EXIT_CHECKS, EXIT_GATE, EXIT_BUDGET = 1, 2, 3, 4
GATES = ('script', 'bases')


def errors_of(issues):
    return [i for i in issues if i.severity == 'error']


def check(episode):
    style = styles.load(episode.style)
    return style, episode.check(style.cast_ids, style.words_per_second)


def record_approval(episode, base, gate, by):
    episode.approvals[gate] = episode.script_digest()
    episode.meta.setdefault('approval_log', []).append(dict(gate=gate, by=by, at=time.strftime('%Y-%m-%dT%H:%M:%S'),
                                                           digest=episode.approvals[gate][:12]))
    store.save(episode, base)


def interactive_gate(episode, base):
    """Terminal approval: [a]pprove, [e]dit episode.json in $EDITOR, [q]uit."""
    editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or 'vi'
    while True:
        style, issues = check(episode)
        print(script_view(episode, style, issues))
        blocked = bool(errors_of(issues))
        choice = input('\n[e]dit, [q]uit' + ('' if blocked else ', [a]pprove') + ' > ').strip().lower()
        if choice == 'a' and not blocked:
            record_approval(episode, base, 'script', 'interactive')
            return True
        if choice == 'e':
            subprocess.call([editor, str(base / 'episode.json')])
            try:
                episode, _ = store.load(base)
            except ValueError as exc:
                print(f'episode.json is not valid after editing:\n{exc}\nOpen it again to fix it.')
        elif choice == 'q':
            return False


BRIEF_DEFAULTS = dict(niche='', audience='', style=config.DEFAULT_STYLE, aspect=config.DEFAULT_ASPECT,
                      target_length=config.DEFAULT_LENGTH)


def brief_from(args):
    """Brief fields the user passed explicitly on this command line."""
    given = dict(niche=args.niche, audience=args.audience, style=args.style, aspect=args.aspect,
                 target_length=args.length)
    return {k: v for k, v in given.items() if v is not None}


def apply_brief(episode, base, brief, regenerate, voice_id):
    """Update an episode's brief. Allowed freely before a script exists; afterwards only when the
    script is being regenerated, so a changed brief never sits next to a script written for the old one."""
    changed = {k: v for k, v in brief.items() if getattr(episode, k) != v}
    if changed and episode.beats and not (regenerate & {'script', 'research'}):
        listing = ', '.join(f'{k}: "{getattr(episode, k)}" -> "{v}"' for k, v in changed.items())
        raise ValueError(f'This episode already has a script written for a different brief ({listing}). '
                         'Pass --regenerate script to rewrite it with the new brief (a new paid call), '
                         'or start a separate episode with --slug.')
    update = dict(changed)
    if voice_id and voice_id != episode.voice.voice_id:
        update['voice'] = episode.voice.model_copy(update=dict(voice_id=voice_id))
    if not update:
        return episode
    if 'style' in update:
        styles.load(update['style'])
    episode = Episode.model_validate(episode.model_copy(update=update).model_dump())
    store.save(episode, base)
    for k, v in changed.items():
        print(f'  brief: {k} = "{v}"')
    return episode


def cmd_make(args):
    config.load_env()
    regenerate = set(args.regenerate or ())
    if args.episode:
        episode, base = store.load(args.episode)
    elif args.topic:
        slug = args.slug or store.slugify(args.topic)
        if store.exists(slug):
            episode, base = store.load(slug)
            if episode.topic != args.topic:
                raise ValueError(f'Episode "{slug}" already exists for topic "{episode.topic}". Pass --slug to start a new one.')
        else:
            fields = BRIEF_DEFAULTS | brief_from(args)
            styles.load(fields['style'])  # fail early on an unknown style
            episode = Episode(slug=slug, topic=args.topic, **fields,
                              voice=Voice(voice_id=args.voice_id) if args.voice_id else Voice())
            base = store.folder(slug)
            store.save(episode, base)
    else:
        raise ValueError('Give a topic, or --episode SLUG to continue an existing episode.')
    episode = apply_brief(episode, base, brief_from(args), regenerate, args.voice_id)
    style = styles.load(episode.style)
    ledger = store.ledger(base, args.budget)
    print(f'Episode {episode.slug}  ({base})')
    print(f'Audience: {episode.audience or "general"} · niche: {episode.niche or "general"} · '
          f'{episode.style} · {episode.aspect} · {episode.target_length}s')
    print(f'Budget ${usd(ledger.budget):.2f}, spent so far ${usd(ledger.spent()):.4f}')

    needs_script = not episode.beats or bool(regenerate & {'script', 'research'})
    if args.dry_run:
        from .script import plan
        client = None
        if config.has_anthropic_key():
            from .script import make_client
            client = make_client()
        print('\nDry run: nothing paid is sent.')
        total = 0
        for step, tokens, how, estimate in plan(episode, style, client, not args.no_research):
            total += estimate
            print(f'  {step:9} input {tokens:>7,} tokens ({how}); reserve up to ${usd(estimate):.4f}')
        print(f'  worst case for the script stage: ${usd(total):.4f} of ${usd(ledger.remaining()):.4f} remaining'
              + ('' if needs_script else ' (script already exists; nothing would be sent)'))
        return 0

    if needs_script:
        from .script import generate
        episode, notes = generate(episode, style, base, ledger, use_research=not args.no_research, regenerate=regenerate)
        store.save(episode, base)
        for note in notes:
            print('  ' + note)
        print(f'  ledger: spent ${usd(ledger.spent()):.4f} of ${usd(ledger.budget):.2f}')

    gates = () if args.auto else tuple(g for g in (args.approve or 'script').split(',') if g and g != 'none')
    style, issues = check(episode)
    if not episode.is_approved('script'):
        if args.auto:
            if errors_of(issues):
                print(script_view(episode, style, issues))
                print('\n--auto will not approve a script that fails the checks. Fix episode.json, then rerun.')
                return EXIT_CHECKS
            record_approval(episode, base, 'script', 'auto')
        elif 'script' in gates:
            if sys.stdin.isatty() and sys.stdout.isatty():
                if not interactive_gate(episode, base):
                    return EXIT_GATE
                episode, _ = store.load(base)
            else:
                print(script_view(episode, style, issues))
                print(f'\nWAITING AT GATE: script. Review the script above, edit {base / "episode.json"} if needed, then run:\n'
                      f'  stick approve {episode.slug}\n  stick make --episode {episode.slug}')
                return EXIT_GATE
    print(f'\nScript approved ({episode.approvals["script"][:12]}).')
    # The base plan: every beat needs an approved painted base pose. Missing ones stop here.
    from . import bases
    rows = bases.plan(episode)
    print('\n' + bases.plan_view(rows))
    miss = bases.missing(rows)
    if miss and not episode.is_approved('bases'):
        print(f'\nWAITING AT GATE: bases. Make candidates with\n  stick bases {episode.slug} --candidates\n'
              f'then pick one per base:\n  stick approve {episode.slug} --gate bases --pick <base>=<candidate.png> ...')
        return EXIT_GATE
    print('\nBases ready. Next: build any newly approved base into layers (scripts/motion_test/build_bases.py), '
          'then render the episode (see docs/RIG.md).')
    return 0


def cmd_bases(args):
    """Print the base plan; with --candidates, render FLUX candidates for the missing bases and a sheet."""
    from . import bases
    episode, base = store.load(args.episode)
    rows = bases.plan(episode)
    print(bases.plan_view(rows))
    miss = bases.missing(rows)
    if not args.candidates or not miss:
        return EXIT_GATE if miss and not episode.is_approved('bases') else 0
    meta = episode.meta.get('bases') or {}
    direction = dict(d.split('=', 1) for d in (args.direction or []))
    unknown = [w for w in direction if w not in miss]
    if unknown:
        print(f'--direction names bases that are not missing: {unknown}; missing: {sorted(miss)}')
        return EXIT_ERROR
    from . import basepack
    pack_state = basepack.load_state('starter')
    lib = bases.library()
    covered = {}
    for w, d in list(miss.items()):
        name = basepack.covers(d)
        if name and (name in pack_state.get('candidates', {}) or lib.get(name)) and w not in direction:
            covered[w] = name
            del miss[w]
    if covered:
        print('\nIn the starter pack (pick there, not here):')
        for w, name in covered.items():
            st = lib.get(name, {}).get('status') or 'candidates ready: stick pack starter --pick'
            print(f'  {w:40} <- {name} ({st})')
    inserts = [w for w, d in miss.items() if d['pose'] == 'insert']
    for w in inserts:
        print(f'  {w}: an empty plate of an approved {miss[w]["setting"]} base serves it; nothing to render')
        del miss[w]
    if not miss:
        return EXIT_GATE
    jobs = bases.candidate_jobs(miss, rejected=meta.get('rejected'), direction=direction)
    rnd = max(j['round'] for j in jobs.values())
    print(f'\nRendering {sum(len(j["seeds"]) for j in jobs.values())} candidates with FLUX.2 klein (local, free), round {rnd}...')
    for w, j in jobs.items():
        print(f'  {w}: {j["job"]} seeds {j["seeds"]}, references {j["images"]}')
    found = bases.render_candidates(jobs, episode.slug)
    out = bases.sheet(found, episode.slug, rnd)
    meta.update(plan=[dict(beat=r['beat'], base=r['base'], wanted=r['wanted']) for r in rows], candidates=found,
                sheet=str(out.relative_to(config.ROOT)), made=time.strftime('%Y-%m-%dT%H:%M:%S'), round=rnd)
    if direction:
        meta.setdefault('direction', {}).update(direction)
    episode.meta['bases'] = meta
    store.save(episode, base)
    print(f'Candidate sheet: {out}')
    for w, files in found.items():
        print(f'  {w}: ' + ', '.join(Path(f).name for f in files))
    print(f'\nWAITING AT GATE: bases. Pick one candidate per base:\n  stick approve {episode.slug} --gate bases '
          + ' '.join(f'--pick {w}=<file>' for w in found))
    return EXIT_GATE


def cmd_props(args):
    """List the script props no library has; with --generate, make them (FLUX text-to-image, cut, QA) and ask
    only about the ones that fail."""
    from . import propgen
    episode, _ = store.load(args.episode)
    result = propgen.run(episode, generate=args.generate)
    print(propgen.view(result))
    if result['failed']:
        print(f'\nWAITING: {len(result["failed"])} prop(s) failed QA on both seeds. Tell me how each should look, name a '
              'stand-in, or drop it from the script.')
        return EXIT_GATE
    if result['needed'] and not args.generate:
        print('\nRun with --generate to make them (local FLUX, about 25 s per candidate).')
    return 0


def cmd_inserts(args):
    """List the insert scenes no library has; with --generate, paint them (local FLUX), check them, ask only on failure."""
    from . import inserts
    episode, _ = store.load(args.episode)
    result = inserts.run(episode, generate=args.generate)
    print(inserts.view(result))
    if result['failed']:
        print(f'\nWAITING: {len(result["failed"])} scene(s) failed QA on both seeds. Tell me how each should look.')
        return EXIT_GATE
    if result['needed'] and not args.generate:
        print('\nRun with --generate to paint them (local FLUX, about 80 s per candidate).')
    return 0


def cmd_pack(args):
    """The starter base pack: render candidates per setting and lay out one sheet per setting; record the user's
    picks and rejections."""
    from . import basepack
    want = basepack.wanted(args.settings.split(',') if args.settings else None)
    state = basepack.load_state(args.pack)
    picks = dict(p.split('=', 1) for p in (args.pick or []))
    rejects = dict(r.split('=', 1) if '=' in r else (r, '') for r in (args.reject or []))
    unknown = [n for n in list(picks) + list(rejects) if n not in want]
    if unknown:
        print(f'Unknown pack bases: {unknown}')
        return EXIT_ERROR
    if picks or rejects:
        for line in basepack.approve(state, picks, rejects, args.by, want):
            print(line)
    if args.candidates:
        direction = dict(d.split('=', 1) for d in (args.direction or []))
        from . import bases
        lib = bases.library()
        todo = {n: d for n, d in want.items() if lib.get(n, {}).get('status') not in ('approved', 'built')}
        job_map = basepack.jobs(todo, state, direction)
        print(f'Rendering {sum(len(j["seeds"]) for j in job_map.values())} candidates for {len(job_map)} bases with FLUX.2 klein (local, free)...')
        for n, j in job_map.items():
            print(f'  {n}: {j["job"]} seeds {j["seeds"]}')
        found = basepack.render(job_map, args.pack)
        sheets = basepack.sheets(found, job_map, args.pack)
        state['candidates'].update(found)
        state['sheets'] = sheets
        state['made'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        if direction:
            state.setdefault('direction', {}).update(direction)
        basepack.save_state(state)
        print('\nSheets (one per setting):')
        for setting, path in sheets.items():
            print(f'  {setting}: {path}')
        print(f'\nWAITING: pick one candidate per base:\n  stick pack {args.pack} ' + ' '.join(f'--pick {n}=<file>' for n in list(found)[:3]) + ' ...')
        return EXIT_GATE
    print(basepack.status(state, want))
    return 0


def cmd_build_bases(args):
    """Build every approved base (FLUX plates, derived spec, layers); with names, only those."""
    from . import basebuild
    lib = __import__('pipeline.bases', fromlist=['library']).library()
    names = args.names or sorted(basebuild.to_build(lib))
    if args.episode:
        from . import bases
        episode, _ = store.load(args.episode)
        used = {r['base'] for r in bases.plan(episode, lib) if r['base']}
        names = [n for n in names if any(n == u or n in u.split(' + ') for u in used)]
    names = [n for n in names if lib.get(n, {}).get('status') == 'approved']
    if not names:
        print('Nothing to build: every base named is built already (or not approved).')
        return 0
    print(f'Building {len(names)} base(s): ' + ', '.join(names))
    for line in basebuild.build(names, lib):
        print(line)
    lib = __import__('pipeline.bases', fromlist=['library']).library()
    left = [n for n in names if lib.get(n, {}).get('status') != 'built']
    if left:
        print(f'Not built: {left}')
        return EXIT_ERROR
    return 0


def cmd_show(args):
    episode, _ = store.load(args.episode)
    style, issues = check(episode)
    print(script_view(episode, style, issues))
    return 0


def cmd_validate(args):
    episode, _ = store.load(args.episode)
    _, issues = check(episode)
    for issue in issues:
        print(issue)
    errors = errors_of(issues)
    print(f'{len(errors)} error(s), {len(issues) - len(errors)} warning(s)')
    return EXIT_CHECKS if errors else 0


def cmd_approve(args):
    episode, base = store.load(args.episode)
    if args.gate == 'bases':
        from . import bases
        rows = bases.plan(episode)
        miss = bases.missing(rows)
        picks = dict(p.split('=', 1) for p in (args.pick or []))
        rejects = dict(r.split('=', 1) if '=' in r else (r, '') for r in (args.reject or []))
        unknown = [w for w in list(picks) + list(rejects) if w not in miss]
        if unknown or not (picks or rejects) or set(picks) & set(rejects):
            print(f'Each --pick/--reject names one missing base {sorted(miss)} (not both for the same base); got '
                  f'picks {sorted(picks)}, rejects {sorted(rejects)}.')
            return EXIT_ERROR
        meta = episode.meta.setdefault('bases', {})
        for wanted, reason in rejects.items():
            files = (meta.get('candidates') or {}).get(wanted, [])
            meta.setdefault('rejected', {}).setdefault(wanted, []).extend(f for f in files if f not in meta.get('rejected', {}).get(wanted, []))
            meta.setdefault('rejections', []).append(dict(base=wanted, files=files, reason=reason, by=args.by,
                                                          at=time.strftime('%Y-%m-%dT%H:%M:%S')))
            print(f'  {wanted}: rejected {", ".join(Path(f).name for f in files) or "(no candidates on record)"}'
                  + (f' ({reason})' if reason else ''))
        lib = bases.library()
        for wanted, cand in picks.items():
            path = Path(cand)
            if not path.is_absolute():
                path = config.ROOT / (cand if '/' in cand else f'renders/bases/flux/{cand}')
            if not path.exists():
                print(f'No such candidate: {path}')
                return EXIT_ERROR
            bases.approve(lib, wanted, str(path.relative_to(config.ROOT)), miss[wanted], args.by)
            print(f'  {wanted} <- {path.name} (approved, to build)')
        if picks:
            bases.save_library(lib)
            episode.meta.setdefault('approval_log', []).append(dict(gate='bases', by=args.by, at=time.strftime('%Y-%m-%dT%H:%M:%S'),
                                                                   picks=picks, rejects=sorted(rejects)))
        still = bases.missing(bases.plan(episode, lib))
        if still:
            episode.approvals.pop('bases', None)
            store.save(episode, base)
            print(f'Still missing: {sorted(still)}. Make new candidates with\n  stick bases {episode.slug} --candidates'
                  + ''.join(f' --direction {w}="<what the base should show>"' for w in sorted(still) if w in rejects))
            return EXIT_GATE
        episode.approvals['bases'] = bases.plan_digest(rows)
        episode.meta['approval_log'][-1]['digest'] = episode.approvals['bases'][:12]
        store.save(episode, base)
        print(f'Approved bases for {episode.slug}. Build them with scripts/motion_test/build_bases.py, then render.')
        return 0
    if args.gate == 'script' and episode.hook_options:
        if not args.hook:
            print(f'{episode.slug} has {len(episode.hook_options)} hook options: approve with --hook N (1-{len(episode.hook_options)}).')
            return EXIT_GATE
        episode.pick_hook(args.hook)
        print(f'Hook {args.hook}: "{episode.hook_options[args.hook - 1].line}"')
    _, issues = check(episode)
    if errors_of(issues):
        for issue in errors_of(issues):
            print(issue)
        print('Fix these errors before approving.')
        store.save(episode, base)
        return EXIT_CHECKS
    record_approval(episode, base, args.gate, args.by)
    print(f'Approved {args.gate} for {episode.slug} ({episode.approvals[args.gate][:12]}).')
    return 0


def cmd_cost(args):
    _, base = store.load(args.episode)
    print(store.ledger(base).summary())
    return 0


def cmd_schema(args):
    path = args.output or config.SCHEMA_PATH
    path = config.ROOT / path if not os.path.isabs(path) else path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(episode_json_schema(), indent=2) + '\n')
    print(path)
    return 0


def cmd_list(args):
    for slug in store.list_episodes():
        episode, base = store.load(slug)
        state = 'script approved' if episode.is_approved('script') else ('script drafted' if episode.beats else 'new')
        print(f'{slug:50} {state:16} ${usd(store.ledger(base).spent()):.4f}  {episode.topic}')
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog='stick', description='Make a narrated, captioned stick-figure Short from a topic.')
    sub = p.add_subparsers(dest='command', required=True)

    m = sub.add_parser('make', help='Create or continue an episode, stopping at approval gates')
    m.add_argument('topic', nargs='?')
    m.add_argument('--episode', help='continue an existing episode by slug')
    m.add_argument('--slug', help='slug for a new episode (default: from the topic)')
    # Brief options default to None so an explicit value can update an episode that has no script yet.
    m.add_argument('--niche')
    m.add_argument('--audience', help='who it is for, e.g. "US salaried workers"')
    m.add_argument('--style', choices=styles.available() or [config.DEFAULT_STYLE],
                   help=f'visual style (default {config.DEFAULT_STYLE})')
    m.add_argument('--length', type=int, help=f'target seconds (default {config.DEFAULT_LENGTH})')
    m.add_argument('--aspect', choices=['9:16', '1:1', '16:9'], help=f'(default {config.DEFAULT_ASPECT})')
    m.add_argument('--budget', type=float, help=f'per-video cap in USD (default {config.DEFAULT_BUDGET_USD:.2f})')
    m.add_argument('--voice-id', help='Kokoro narrator voice (default: the style default, am_michael; alternate: af_heart)')
    m.add_argument('--approve', help='comma-separated gates to stop at (default "script"; "none" to skip)')
    m.add_argument('--auto', action='store_true', help='skip all gates (still refuses scripts that fail checks)')
    m.add_argument('--no-research', action='store_true', help='skip web research; the script may then use no statistics')
    m.add_argument('--regenerate', action='append', choices=['research', 'script'],
                   help='ignore the cached result for this step (a new paid call)')
    m.add_argument('--dry-run', action='store_true', help='show the cost plan without sending paid requests')
    m.set_defaults(func=cmd_make)

    for name, func, text in (('show', cmd_show, 'Print the script review'),
                             ('validate', cmd_validate, 'Run the editorial checks'),
                             ('cost', cmd_cost, 'Show the cost ledger')):
        c = sub.add_parser(name, help=text)
        c.add_argument('episode')
        c.set_defaults(func=func)

    a = sub.add_parser('approve', help='Approve a gate for the current content')
    a.add_argument('episode')
    a.add_argument('--gate', default='script', choices=GATES)
    a.add_argument('--by', default='cli', help='who approved (recorded in the approval log)')
    a.add_argument('--hook', type=int, help='script gate, host format: the hook option to use (1-3)')
    a.add_argument('--pick', action='append', help='bases gate: <wanted base>=<candidate png>; bases may be picked over several calls')
    a.add_argument('--reject', action='append', help='bases gate: <wanted base>[=reason]; its candidates are rejected and the next '
                                                     '`stick bases --candidates` renders new ones')
    a.set_defaults(func=cmd_approve)

    bs = sub.add_parser('bases', help='Show which painted base each beat needs; make FLUX candidates for missing ones')
    bs.add_argument('episode')
    bs.add_argument('--candidates', action='store_true', help='render candidates for the missing bases and a review sheet')
    bs.add_argument('--direction', action='append', help='<wanted base>="<what the base should show>": the user\'s direction for a '
                                                         'redo round, used in place of the pose template')
    bs.set_defaults(func=cmd_bases)

    pr = sub.add_parser('props', help='Make the props a script needs that no library has (FLUX, cut-out, QA); asks only on failure')
    pr.add_argument('episode')
    pr.add_argument('--generate', action='store_true', help='render, cut and check the missing props (local FLUX)')
    pr.set_defaults(func=cmd_props)

    ins = sub.add_parser('inserts', help='Paint the explainer insert scenes an episode needs (local FLUX, QA)')
    ins.add_argument('episode')
    ins.add_argument('--generate', action='store_true')
    ins.set_defaults(func=cmd_inserts)

    pk = sub.add_parser('pack', help='The starter base pack: candidates per setting, one sheet each, then picks')
    pk.add_argument('pack', nargs='?', default='starter')
    pk.add_argument('--candidates', action='store_true', help='render candidates for every base not yet approved')
    pk.add_argument('--settings', help='comma-separated subset of settings')
    pk.add_argument('--pick', action='append', help='<base>=<candidate png>')
    pk.add_argument('--reject', action='append', help='<base>[=reason]')
    pk.add_argument('--direction', action='append', help='<base>="<what the base should show>" for a redo round')
    pk.add_argument('--by', default='cli')
    pk.set_defaults(func=cmd_pack)

    bb = sub.add_parser('build-bases', help='Build approved bases into layers (FLUX plates + derived spec)')
    bb.add_argument('names', nargs='*')
    bb.add_argument('--episode', help='only the bases this episode\'s plan uses')
    bb.set_defaults(func=cmd_build_bases)

    s = sub.add_parser('schema', help='Write the episode.json JSON schema')
    s.add_argument('--output')
    s.set_defaults(func=cmd_schema)

    sub.add_parser('list', help='List episodes').set_defaults(func=cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BudgetExceeded as exc:
        print(f'BUDGET: {exc}', file=sys.stderr)
        return EXIT_BUDGET
    except (ValueError, PriceUnknown, RuntimeError, OSError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return EXIT_ERROR
