"""The speaking rate the script writer plans with, measured from the videos Kokoro actually voiced (user 2026-11-14:
"The crude oil video ... 37.0 s vs 45 target"). The cozy style says 2.7 words per second; the finished videos run
at about 2.45 (90 words took 37.0 s), so every script came in short. The writer's word target now uses the median rate
of the latest renders (words / seconds, pauses included), so a short video raises the next script's word target.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from . import config

SAMPLES = config.ROOT / 'renders/sample'
LATEST = 8                     # the newest renders count; older voices and pacing rules age out
MIN_SAMPLES = 3
LOW, HIGH = 2.1, 2.9           # a rate outside this is a broken render, not a voice


def measured_rate(samples: Path | None = None) -> float | None:
    """Median words per second over the newest renders' QA reports, or None with too few of them."""
    folder = SAMPLES if samples is None else Path(samples)
    reports = sorted(folder.glob('*/qa.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:LATEST * 2]
    rates = []
    for p in reports:
        try:
            r = json.loads(p.read_text())
            words, sec = int(r['words']['script_words']), float(r['duration']['seconds'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if words >= 40 and sec >= 15 and LOW <= words / sec <= HIGH:
            rates.append(words / sec)
        if len(rates) >= LATEST:
            break
    return round(statistics.median(rates), 2) if len(rates) >= MIN_SAMPLES else None


def writer_rate(style, samples: Path | None = None) -> float:
    """The rate the writer's word target uses: the measured one when there is enough history, else the style's."""
    m = measured_rate(samples)
    return m if m is not None else style.words_per_second
