"""Video 2: "Why you're always tired even after 8 hours of sleep", end to end on the generic episode runner.

  .venv/bin/python scripts/motion_test/episode_sleep.py [--voice am_michael] [--still T ...] [--workers 4] [--no-qa]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from episode_runner import Runner  # noqa: E402

SLUG = 'why-youre-always-tired-after-8-hours-of-sleep'

if __name__ == '__main__':
    Runner(SLUG, 'sleep_beats', extra_code=['scripts/motion_test/sleep_beats.py', 'pipeline/rig/drawn.py']).main()
