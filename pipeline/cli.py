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

from . import config, store, styles
from .cost import BudgetExceeded, PriceUnknown, usd
from .models import Episode, Voice, episode_json_schema
from .review import script_view

EXIT_ERROR, EXIT_CHECKS, EXIT_GATE, EXIT_BUDGET = 1, 2, 3, 4
GATES = ('script',)  # Phase 2 adds 'style'


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
    print(f'\nScript approved ({episode.approvals["script"][:12]}). '
          'Next stages (style board and images, voice, captions, compositing) arrive in Phases 2-4.')
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
    _, issues = check(episode)
    if errors_of(issues):
        for issue in errors_of(issues):
            print(issue)
        print('Fix these errors before approving.')
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
    m.add_argument('--voice-id', help='local narrator voice, e.g. a Kokoro voice such as af_heart (default: the style default)')
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
    a.set_defaults(func=cmd_approve)

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
