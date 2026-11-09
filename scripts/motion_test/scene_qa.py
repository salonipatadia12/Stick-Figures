"""QA for generated insert scenes (docs/PLAN.md §7 H). Runs in .venvs/mlx (OpenCV).

  .venvs/mlx/bin/python scripts/motion_test/scene_qa.py <png> [<png> ...]

Prints one JSON line per image: {"path", "ok", "reasons": [...], "score"}. A scene passes when it
- has painted detail (edge density and local contrast; not a blank wash or a flat gradient),
- is neither too dark nor blown out,
- has no character in it: no white round head with a dark outline (the style's characters), found with the same
  circle search the base builder uses,
- has no big flat text-like block (a sign, a caption box): many thin, high-contrast horizontal strokes in a row.
The score (higher is better) picks between the seeds that pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spec_from_plates as SP  # noqa: E402


def check(path: Path) -> dict:
    im = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    h, w = im.shape[:2]
    hsv = cv2.cvtColor(im, cv2.COLOR_RGB2HSV)
    lum = im.astype(np.float32).mean(-1)
    sat = hsv[..., 1].astype(np.float32)
    reasons = []
    edges = cv2.Canny(cv2.cvtColor(im, cv2.COLOR_RGB2GRAY), 60, 140)
    density = float((edges > 0).mean())
    contrast = float(lum.std())
    if density < 0.01 or contrast < 18:
        reasons.append(f'too plain (edges {density:.3f}, contrast {contrast:.0f})')
    if lum.mean() < 45:
        reasons.append(f'too dark (mean {lum.mean():.0f})')
    if (lum > 245).mean() > 0.35:
        reasons.append('blown out')
    # a character's head: a white disc with a dark outline nearly all round, almost all white inside (over 88 %) with
    # two small dark eye marks (0.5-8 % dark). Trees, windows and clouds that pass the circle test are far less white
    # inside or far darker (measured 2026-11-08: heads 95 % white / 3-5 % dark; frosted trees 48-78 % / 10-13 %).
    for tier in ((0.8, 0.5), (0.65, 0.45), (0.5, 0.4)):
        try:
            cx, cy, r = SP._head_circles(im, 1, lum, sat, np.ones((h, w), bool), tiers=(tier,))[0]
        except RuntimeError:
            continue
        m = np.zeros((h, w), np.uint8)
        cv2.circle(m, (int(cx), int(cy)), int(r * 0.7), 255, -1)
        inside = im[m > 0].astype(np.float32)
        dark = float((inside.mean(-1) < 90).mean())
        white = float(((inside.mean(-1) > 170) & (inside.max(-1) - inside.min(-1) < 50)).mean())
        if white > 0.88 and 0.005 < dark < 0.08:
            reasons.append(f'a character-like head at ({float(cx):.0f},{float(cy):.0f})')
            break
    score = float(density * 100 + contrast / 10 - abs(float(lum.mean()) - 135) / 20)
    return dict(path=str(path), ok=not reasons, reasons=reasons, score=round(score, 2),
                stats=dict(edges=round(density, 4), contrast=round(contrast, 1), mean=round(float(lum.mean()), 1)))


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print(json.dumps(check(Path(p))), flush=True)
