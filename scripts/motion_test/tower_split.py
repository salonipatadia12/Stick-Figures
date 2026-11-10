"""Pisa b08 before/after pair for a split shot: the split shows each picture's middle half (rows 25%-75%), so the
whole tower is drawn small inside that band, base on the lawn line, with the dashed upright guide from its base.
.venvs/mlx/bin/python scripts/motion_test/tower_split.py <scene> x0 y0 x1 y1 <deg> <out> [<deg> <out> ...]"""
import sys
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import tower_lean as tower  # noqa: E402

src = sys.argv[1]
rect = tuple(int(v) for v in sys.argv[2:6])
pairs = list(zip(sys.argv[6::2], sys.argv[7::2]))
img = cv2.imread(src)
H, W = img.shape[:2]
m = tower.refine(img, tower.segment(img, rect))
deg, pivot, _ = tower.lean(m)
hole = tower.hole_of(m)
bg = tower.smooth_fill(img, None, hole)
G = int(0.735 * H)                                   # the lawn line, near the band's bottom (75%)
shift = int(pivot[1]) - G
if shift > 0:                                        # lift the scene; the lawn below is stretched to the frame's foot
    lawn = cv2.resize(bg[G + shift:], (W, H - G), interpolation=cv2.INTER_CUBIC)
    bg = np.concatenate([bg[shift:G + shift], lawn], 0)
k = (0.42 * H) / (pivot[1] - np.where(m.any(1))[0].min())   # tower height: 42% of the frame (fits the half band)
for target, out in pairs:
    posed, rm = tower.pose(img, m, deg, pivot, float(target))
    ys, xs = np.where(rm > 0)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    crop = cv2.resize(posed[y0:y1 + 1, x0:x1 + 1], None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    a = cv2.resize(rm[y0:y1 + 1, x0:x1 + 1], None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    canvas = bg.copy()
    px, py = int(W / 2 - (pivot[0] - x0) * k), G + 6 - crop.shape[0]
    al = cv2.GaussianBlur(a, (5, 5), 0).astype(np.float32)[..., None] / 255
    reg = canvas[py:py + crop.shape[0], px:px + crop.shape[1]]
    canvas[py:py + crop.shape[0], px:px + crop.shape[1]] = (crop * al + reg * (1 - al)).astype(np.uint8)
    full = np.zeros_like(rm)
    full[py:py + crop.shape[0], px:px + crop.shape[1]] = (a > 127) * 255
    d2, p2, _ = tower.lean(full)
    tower.plumb(canvas, p2, np.where(full.any(1))[0].min())
    cv2.imwrite(out, canvas)
    print(out, 'lean', round(d2, 1), 'tower rows', int(py), '-', G)
