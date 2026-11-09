"""Cut the host's painted body (torso and legs) out of a standing base, on a transparent plate the same size as the
base, for compositing the host into insert scenes (script v5 host format). Runs in .venvs/mlx (OpenCV).

  .venvs/mlx/bin/python scripts/motion_test/host_cutout.py [base] [--preview out.jpg]

The head and arms are puppet sprites, so the plate holds only the torso and legs. The body is found where the plate
differs from the base's empty plate AND has the character's colours (white body, navy limbs and outline), near the
known head; small holes are filled (not the gap between the legs), thin cracks are opened away, and the painted
hands left at the rest points are erased (the puppet draws its own hands: otherwise a ghost hand stays on the hip).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def cut(base: str) -> tuple[Image.Image, dict]:
    d = ROOT / 'styles/cozy/bases' / base
    m = json.loads((d / 'base.json').read_text())
    seat = next(s for s in m['seats'] if s['name'] == 'lead')
    p = np.asarray(Image.open(d / 'plate.png').convert('RGB')).astype(np.int32)
    e = np.asarray(Image.open(d / 'empty.png').convert('RGB')).astype(np.int32)
    h, w = p.shape[:2]
    hx, hy = seat['head']
    D = seat['D']
    diff = np.abs(p - e).max(-1) > 35
    lum, sat = p.mean(-1), p.max(-1) - p.min(-1)
    white = (lum > 185) & (sat < 45)
    navy = (lum < 95) & (p[..., 2] >= p[..., 0] - 4) & (sat > 8)
    roi = np.zeros((h, w), bool)
    roi[int(hy - 0.65 * D):, max(0, int(hx - 0.95 * D)):min(w, int(hx + 0.95 * D))] = True
    # 1. the torso: the biggest white blob below the head, every hole filled, plus its navy outline band
    wb = ((white & diff & roi).astype(np.uint8) * 255)
    wb = cv2.morphologyEx(wb, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(wb)
    torso = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8) * 255
    cs, _ = cv2.findContours(torso, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    torso = cv2.drawContours(np.zeros_like(torso), cs, -1, 255, -1)
    band = cv2.dilate(torso, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(0.05 * D) | 1,) * 2))
    mask = torso | (band & (navy.astype(np.uint8) * 255))
    # 2. the legs and shoes: navy below the torso's widest point, under the torso's width
    tx0, ty0, tw, th = cv2.boundingRect(torso)
    under = np.zeros((h, w), bool)
    under[ty0 + int(0.75 * th):, max(0, tx0 - int(0.12 * D)):min(w, tx0 + tw + int(0.12 * D))] = True
    legs = ((navy & under & (lum < 64)).astype(np.uint8) * 255)   # the shoes' own navy, not the pavement shadow (lum 66-97)
    legs = cv2.morphologyEx(legs, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    legs = cv2.morphologyEx(legs, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(legs)
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] > 0.04 * D * D:
            mask[lab == k] = 255
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)      # one piece: drop anything not joined to the body
    mask = (lab == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8) * 255
    inv = cv2.bitwise_not(mask)                                 # small enclosed holes (not the open gap between the legs)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(inv)
    for k in range(1, n):
        x, y, bw, bh, area = stats[k]
        if area < 0.1 * D * D and x > 0 and y > 0 and x + bw < w and y + bh < h:
            mask[lab == k] = 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    mask = cv2.GaussianBlur(mask, (3, 3), 0)
    rgba = np.dstack([p.astype(np.uint8), mask])
    ys, xs = np.where(mask > 128)
    meta = dict(base=base, size=[w, h], head=seat['head'], D=D, feet_y=int(ys.max()), body_box=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())])
    return Image.fromarray(rgba, 'RGBA'), meta


if __name__ == '__main__':
    base = next((a for a in sys.argv[1:] if not a.startswith('--') and not a.endswith('.jpg')), 'lead-standing-street')
    body, meta = cut(base)
    out = ROOT / 'styles/cozy/host' / 'lead-standing'
    out.mkdir(parents=True, exist_ok=True)
    body.save(out / 'body.png')
    (out / 'host.json').write_text(json.dumps(meta, indent=1))
    print(out, meta)
    if '--preview' in sys.argv:
        dst = sys.argv[sys.argv.index('--preview') + 1]
        x0, y0, x1, y1 = meta['body_box']
        c = body.crop((x0 - 20, y0 - 20, x1 + 20, y1 + 20))
        bg = Image.new('RGBA', c.size, (90, 160, 90, 255)); bg.alpha_composite(c)
        bg.convert('RGB').save(dst)
