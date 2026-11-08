"""Render any episode from its script alone: the beats are composed from `shot.staging` and the motion recipes
(scripts/motion_test/compose_beats.py), on the generic runner.

  .venv/bin/python scripts/motion_test/episode_compose.py <slug> [--voice am_michael] [--still T ...] [--workers N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from episode_runner import Runner  # noqa: E402

if __name__ == '__main__':
    args = sys.argv[1:]
    if not args or args[0].startswith('-'):
        sys.exit('usage: episode_compose.py <slug> [runner options]')
    slug, rest = args[0], args[1:]
    Runner(slug, 'compose_beats', extra_code=['scripts/motion_test/compose_beats.py', 'pipeline/rig/verbs.py', 'pipeline/rig/drawn.py']).main(rest)
