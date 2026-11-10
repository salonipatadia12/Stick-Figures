"""Tower lean tool (used for the Pisa scenes, 2026-11-10; library entries say "edit"): segment the tower in a painted scene, measure its lean, re-pose it at an exact angle.

.venvs/mlx/bin/python scripts/motion_test/tower_lean.py measure <img> x0 y0 x1 y1
.venvs/mlx/bin/python scripts/motion_test/tower_lean.py pose <img> x0 y0 x1 y1 <target_deg> <out> [--mirror] [--plumb] [--ghost DEG]
Angles: positive = top leans to the RIGHT of the image. Rect = a box around the tower (full-res pixels).
"""
import sys
import math
import numpy as np
import cv2


def segment(img, rect):
    mask = np.zeros(img.shape[:2], np.uint8)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    x0, y0, x1, y1 = rect
    cv2.grabCut(img, mask, (x0, y0, x1 - x0, y1 - y0), bgd, fgd, 6, cv2.GC_INIT_WITH_RECT)
    m = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    # keep the biggest component, close small holes
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    if n > 1:
        big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        m = np.where(lab == big, 255, 0).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return m


def refine(img, m):
    """Second pass with a tower-shaped prior: fit the axis and width from the first mask (median, so a blob of sky
    or a shadow on a few rows does not count), then re-cut with everything outside that band marked background."""
    h, w = m.shape
    rows = []
    for y in range(h):
        xs = np.where(m[y] > 0)[0]
        if len(xs) > 5:
            rows.append((y, xs.min(), xs.max()))
    rows = np.array(rows, float)
    width = rows[:, 2] - rows[:, 1]
    med = np.median(width)
    good = (width > 0.55 * med) & (width < 1.25 * med)
    ys, cx, wd = rows[good, 0], (rows[good, 1] + rows[good, 2]) / 2, width[good]
    a, b = np.polyfit(ys, cx, 1)
    wa, wb = np.polyfit(ys, wd, 1)
    # top and base: the first and last rows of tower-like width (a shadow below the base is wider, so it is out)
    top, bot = int(rows[good, 0].min()), int(rows[good, 0].max()) + 12
    init = np.full((h, w), cv2.GC_BGD, np.uint8)
    yy, xx = np.mgrid[0:h, 0:w]
    c = a * yy + b
    half = (wa * yy + wb) / 2
    inside = (np.abs(xx - c) <= 1.12 * half) & (yy >= top - 40) & (yy <= bot)
    spire = (np.abs(xx - c) <= 0.35 * half) & (yy >= top - 170) & (yy < top - 40)   # the thin finial on top
    inside |= spire
    init[inside] = cv2.GC_PR_FGD
    core = (np.abs(xx - c) <= 0.45 * half) & (yy >= top + 30) & (yy <= bot - 10)
    init[core] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(img, init, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    m2 = np.where((init == cv2.GC_FGD) | (init == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m2)
    if n > 1:
        m2 = np.where(lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA])), 255, 0).astype(np.uint8)
    return cv2.morphologyEx(m2, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))


def lean(m):
    ys = np.where(m.any(1))[0]
    top, bot = ys.min(), ys.max()
    h = bot - top
    rows = range(int(top + 0.12 * h), int(top + 0.85 * h), 4)
    pts = []
    for y in rows:
        xs = np.where(m[y] > 0)[0]
        if len(xs) > 5:
            pts.append((y, (xs.min() + xs.max()) / 2))
    pts = np.array(pts)
    a, b = np.polyfit(pts[:, 0], pts[:, 1], 1)       # x = a*y + b
    deg = -math.degrees(math.atan(a))                 # y grows downward: top right => x decreases with y => a<0
    base_y = bot
    base_x = a * base_y + b
    return deg, (float(base_x), float(base_y)), (int(top), int(bot))


def hole_of(m):
    """What to fill once the tower moves: the tower, a margin, and a column above its top (the finial and its halo)."""
    hole = cv2.dilate(m, np.ones((31, 31), np.uint8))
    ys, xs = np.where(m > 0)
    top = ys.min()
    row = np.where(m[top + 8] > 0)[0]
    cx = int((row.min() + row.max()) / 2) if len(row) else int(xs.mean())
    cv2.rectangle(hole, (cx - 70, max(top - 220, 0)), (cx + 70, top + 10), 255, -1)
    return hole


def pose(img, m, cur, pivot, target):
    d = target - cur                                   # rotate by d (positive = more lean to the right)
    h, w = m.shape
    M = cv2.getRotationMatrix2D(pivot, -d, 1.0)        # cv2: positive angle = counter-clockwise
    rimg = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    rm = cv2.warpAffine(m, M, (w, h), flags=cv2.INTER_LINEAR)
    # keep the base on the ground: nothing of the rotated tower below the old base line
    rm[int(pivot[1]) + 2:, :] = 0
    hole = hole_of(m)
    bg = smooth_fill(img, None, hole)
    a = cv2.GaussianBlur(rm, (5, 5), 0).astype(np.float32)[..., None] / 255
    out = (rimg * a + bg * (1 - a)).astype(np.uint8)
    return out, rm


def smooth_fill(img, bg, hole):
    """Fill the hole row by row, blending from the colour on its left to the colour on its right (a sky gradient
    stays a gradient; Telea streaks over a hole this size), soften it, and add back the painting's grain."""
    out = img.astype(np.float32).copy()
    h, w = hole.shape
    for y in range(h):
        xs = np.where(hole[y] > 0)[0]
        if not len(xs):
            continue
        runs = np.split(xs, np.where(np.diff(xs) > 1)[0] + 1)
        for r in runs:
            l, rr = max(r[0] - 3, 0), min(r[-1] + 3, w - 1)
            cl = img[y, max(l - 6, 0):l + 1].astype(np.float32).mean(0)
            cr = img[y, rr:min(rr + 7, w)].astype(np.float32).mean(0)
            n = rr - l + 1
            if (cl.mean() + cr.mean()) / 2 < 140:          # trees or hedges: mirror them in from both sides
                half = n // 2                               # (the seam lands mid-gap, where the tower stands)
                left = img[y, max(l - half, 0):l][::-1]
                right = img[y, rr + 1:min(rr + 1 + n - half, w)][::-1]
                if len(left) == half and len(right) == n - half:
                    out[y, l:l + half] = left
                    out[y, l + half:rr + 1] = right
                    continue
            t = np.linspace(0, 1, n)[:, None]
            out[y, l:rr + 1] = cl * (1 - t) + cr * t
    cloned = (cv2.GaussianBlur(out, (0, 0), 1)[..., 1] < 140)[..., None]     # cloned trees keep their detail
    soft = np.where(cloned, cv2.GaussianBlur(out, (0, 0), 1.2), cv2.GaussianBlur(out, (0, 0), 7))
    k = cv2.GaussianBlur(hole, (0, 0), 5).astype(np.float32)[..., None] / 255
    rng = np.random.default_rng(7)                    # synthetic grain: borrowing it from the painting copies edges
    noise = cv2.GaussianBlur(rng.normal(0, 3.0, out.shape).astype(np.float32), (0, 0), 0.8)
    fill = np.clip(soft + noise, 0, 255)
    return (fill * k + img.astype(np.float32) * (1 - k)).astype(np.uint8)


def plumb(img, base_xy, top_y, color=(46, 87, 228)):
    """A dashed upright line rising from the base centre past the top: how far the top drifts off it is the lean."""
    x, y0, y1 = int(base_xy[0]), int(base_xy[1]), int(top_y) - 60
    for yy in range(y0, y1, -30):
        e = max(yy - 18, y1)
        cv2.line(img, (x, yy), (x, e), (255, 255, 255), 9, cv2.LINE_AA)
        cv2.line(img, (x, yy), (x, e), color, 5, cv2.LINE_AA)
    return img


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    rect = tuple(int(v) for v in sys.argv[3:7])
    img = cv2.imread(path)
    if '--mirror' in sys.argv:
        img = cv2.flip(img, 1)
        w = img.shape[1]
        rect = (w - rect[2], rect[1], w - rect[0], rect[3])
    m = refine(img, segment(img, rect))
    deg, pivot, (top, bot) = lean(m)
    print(f'lean {deg:+.1f} deg  base {pivot[0]:.0f},{pivot[1]:.0f}  height {bot - top}px')
    if cmd == 'measure':
        dbg = img.copy()
        dbg[m > 0] = (dbg[m > 0] * 0.5 + np.array([0, 0, 255]) * 0.5).astype(np.uint8)
        cv2.imwrite(sys.argv[7], dbg)
        return
    target, out_path = float(sys.argv[7]), sys.argv[8]
    out, rm = pose(img, m, deg, pivot, target)
    if '--ghost' in sys.argv:                         # a faint earlier outline at a bigger lean (b04)
        g = float(sys.argv[sys.argv.index('--ghost') + 1])
        gimg, gm = pose(img, m, deg, pivot, g)
        ga = (cv2.GaussianBlur(gm, (5, 5), 0).astype(np.float32)[..., None] / 255) * 0.32
        ga[rm > 0] = 0
        out = (gimg * ga + out * (1 - ga)).astype(np.uint8)
    d2, p2, (t2, b2) = lean(rm)
    print(f'posed lean {d2:+.1f} deg')
    if '--plumb' in sys.argv:
        ys = np.where(rm.any(1))[0]
        plumb(out, p2, ys.min())
    cv2.imwrite(out_path, out)


if __name__ == '__main__':
    main()
