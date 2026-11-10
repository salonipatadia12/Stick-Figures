"""Turn the pipeline's console output into (stage, pct, message) progress.

Render (scripts/motion_test/episode_runner.py):
  duration 45.12s, 14 shots, 31 caption lines
  cached: 3 to render: ['b01', 'b02', ...]
    b08: 207 frames  452s
  /…/renders/sample/<slug>/<slug>.mp4 45.12s 300s
  { QA summary json ... }

Paint (`stick inserts|props --generate`, through flux_scenes.py / flux_props.py):
  scene model loaded in 12s
  scene-<key>-s151.png: 80s      | cached scene-<key>-s151.png
  prop-<key>-s300.png: 25s       | cached prop-<key>-s300.png
"""
from __future__ import annotations

import ast
import re

DURATION = re.compile(r'^duration ([\d.]+)s, (\d+) shots')
CACHED = re.compile(r'^cached: (\d+) to render: (\[.*\])\s*$')
SHOT = re.compile(r'^\s+([A-Za-z0-9_-]+): (\d+) frames\s+(\d+)s\s*$')
FINAL = re.compile(r'^(\S+\.mp4) ([\d.]+)s (\d+)s\s*$')
QA_LINE = re.compile(r'^(library|subject on screen|character on screen|hook|QA NOTE)')
VOICE_CHANGED = re.compile(r'^(\S+): narration changed')


class RenderProgress:
    """Feed lines; `update(line)` returns (stage, pct, message) when the progress moved, else None."""

    def __init__(self):
        self.stage, self.pct, self.message = 'voice', 2.0, 'narration and timeline'
        self.total_shots = self.to_render = self.rendered = 0
        self.duration = None
        self.final_path = None

    def update(self, line: str):
        line = line.rstrip('\n')
        if m := DURATION.match(line):
            self.duration = float(m.group(1))
            self.total_shots = int(m.group(2))
            return self._set('shots', 10, f'timeline ready: {self.duration:.1f}s, {self.total_shots} shots')
        if m := CACHED.match(line):
            try:
                self.to_render = len(ast.literal_eval(m.group(2)))
            except (ValueError, SyntaxError):
                self.to_render = max(0, self.total_shots - int(m.group(1)))
            if self.to_render == 0:
                return self._set('mix', 80, 'all shots cached; mixing')
            return self._set('shots', 12, f'rendering {self.to_render} shots ({m.group(1)} cached)')
        if m := SHOT.match(line):
            self.rendered += 1
            n = max(self.to_render, self.rendered)
            return self._set('shots', 12 + 68 * self.rendered / n, f'shot {m.group(1)} done ({self.rendered}/{n})')
        if m := FINAL.match(line):
            self.final_path = m.group(1)
            self.duration = float(m.group(2))
            return self._set('qa', 85, f'video {self.duration:.1f}s made in {m.group(3)}s; running QA')
        if m := VOICE_CHANGED.match(line):
            return self._set('voice', self.pct, f'voicing {m.group(1)} again (narration changed)')
        if QA_LINE.match(line) and self.final_path:
            return self._set('qa', 95, 'QA done')
        return None

    def _set(self, stage, pct, message):
        self.stage, self.pct, self.message = stage, round(max(self.pct, float(pct)), 1), message   # never backwards
        return self.stage, self.pct, self.message


FLUX_FILE = re.compile(r'^(?:cached )?((scene|prop)-(.+?)(?:-r\d+)?-s\d+\.png)(?::\s*\d+s)?\s*$')
MODEL_LOADED = re.compile(r'model loaded in (\d+)s')


class PaintProgress:
    """Per-candidate progress inside one `stick inserts` / `stick props` run, mapped onto [lo, hi] percent.
    `expected` is the number of candidates (two seeds per missing key)."""

    def __init__(self, expected: int, lo: float, hi: float, what: str):
        self.expected, self.lo, self.hi, self.what = max(1, expected), lo, hi, what
        self.seen = set()

    def update(self, line: str):
        line = line.strip()
        if m := FLUX_FILE.match(line):
            self.seen.add(m.group(1))
            n = len(self.seen)
            pct = self.lo + (self.hi - self.lo) * min(1.0, n / self.expected)
            return 'paint', round(pct, 1), f'{self.what}: {m.group(3)} candidate {min(n, self.expected)}/{self.expected}'
        if m := MODEL_LOADED.search(line):
            return 'paint', self.lo, f'{self.what}: FLUX loaded ({m.group(1)}s)'
        return None


FAILED_LINE = re.compile(r'^\s*!\s+([a-z0-9_-]+)(?: \([^)]*\))?:\s*(.+)$')


def failed_reasons(output: str) -> dict:
    """key -> why, from the `  ! key: failed QA ...` lines of inserts.view / propgen.view."""
    return {m.group(1): m.group(2).strip() for line in output.splitlines() if (m := FAILED_LINE.match(line))}
