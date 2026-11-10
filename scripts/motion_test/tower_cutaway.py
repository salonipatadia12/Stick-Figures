"""The Pisa b07 cut-away: the standing tower (5.5 deg) over a painted soil cross-section, pipes under its north side.
.venvs/mlx/bin/python scripts/motion_test/tower_cutaway.py <tower scene> <soil scene> <out> <soil grass row>"""
import sys
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
import tower_lean as tower  # noqa: E402
src, soil_p, out_p = sys.argv[1], sys.argv[2], sys.argv[3]
img = cv2.imread(src)
m = tower.refine(img, tower.segment(img, (250, 200, 760, 1420)))
deg, pivot, (top, bot) = tower.lean(m)
posed, rm = tower.pose(img, m, deg, pivot, 5.5)
hole = tower.hole_of(m)
bg = tower.smooth_fill(img, None, hole)
W, H, G = 944, 1680, 1000                       # ground line of the composite
by = int(pivot[1])
upper = bg[by - G: by + 30].copy()           # sky, trees and the lawn edge, base row at y=G
k = 0.8                                         # the tower a little smaller so its top clears the frame
ys, xs = np.where(rm > 0)
y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
crop, a = posed[y0:y1 + 1, x0:x1 + 1], rm[y0:y1 + 1, x0:x1 + 1]
crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
a = cv2.resize(a, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
canvas = np.zeros((H, W, 3), np.uint8)
canvas[:G + 30] = upper
soil = cv2.imread(soil_p)
gy = int(sys.argv[4])                            # the soil image's grass/soil boundary row
band = soil[gy - 30:]                            # the whole soil picture, squeezed: pipe tips and soil heaps in view
band = cv2.resize(band, (W, H - G + 30))
f = np.linspace(0, 1, 40)[:, None, None]         # feather the lawn into the soil picture's grass strip
canvas[G - 10:G + 30] = upper[G - 10:G + 30] * (1 - f) + band[:40] * f
canvas[G + 30:] = band[40:40 + H - G - 30]
# the tower: base centre at the middle of the ground line (base row sits slightly into the grass)
bx_rel = (pivot[0] - x0) * k
h_t = crop.shape[0]
px, py = int(W / 2 - bx_rel), G + 14 - h_t
al = cv2.GaussianBlur(a, (5, 5), 0).astype(np.float32)[..., None] / 255
region = canvas[py:py + h_t, px:px + crop.shape[1]]
canvas[py:py + h_t, px:px + crop.shape[1]] = (crop * al + region * (1 - al)).astype(np.uint8)
cv2.imwrite(out_p, canvas)
d2, _, _ = tower.lean(np.pad((a > 127).astype(np.uint8) * 255, 10))
print('cutaway tower lean', round(d2, 1), 'top y', py)
