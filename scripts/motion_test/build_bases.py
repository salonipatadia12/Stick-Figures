"""Build painted base poses from The Empty Chair frames (like its motion edition: original art kept,
clean plates revealed only where a moving part used to be).

For each base:
- plate.png       the frame with everything that moves taken out: arms, hands, props, heads, and (for
                  seated bases) the torso, which becomes its own layer. FLUX clean plates fill in
                  only where those parts were; everything else is the original painting.
- body-<seat>.png the painted torso (with the friend's scarf), a neck continuing its outline under the
                  head, and a little extra below the tabletop, so it can lean and squash about the hip.
- head-<seat>.png the frame's own head, cut at the outer edge of its outline, face painted out.
- occluder-<seat>.png  the torso around each shoulder, laid back over the arm roots.
- table.png       the round table (laid over the body and over a hand that reaches under it).
- front.png       cups that stay on the table and sit in front of the arms.
- cup-<n>.png     cups that can be lifted (where a no-cup plate exists); their saucers stay painted.
- base.json       anchors in plate pixels.

Runs in .venvs/mlx (OpenCV):  .venvs/mlx/bin/python scripts/motion_test/build_bases.py [name ...]
FLUX edits come from scripts/motion_test/flux_bases.py (renders/bases/flux).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
EC = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'
FLUX = ROOT / 'renders/bases/flux'
OUT = ROOT / 'styles/cozy/bases'
DEBUG = ROOT / 'renders/bases'
NAVY = (24, 26, 50)

# Anchors are measured on the plates (see renders/bases/debug-*.jpg). Units: plate pixels (941x1672).
# Shoulders sit on the torso outline (measured), so a resting arm covers the outline as in the paintings.
BASES = {
    'lead-at-table': dict(
        source='04-waiting.png', arms='plate-04-nocup-b-s97.png', empty='plate-04-empty-s6.png', inserts=True,
        edit_roi=[(28, 688), (612, 688), (612, 995), (28, 995)],
        lift_cups=[dict(rim=(150, 885.5, 52, 8.5), base=(110, 192, 950, 961), handle=(-1, 75, 102, 892, 937, 83, 100, 900, 927),
                        saucer=(148.5, 961, 74, 19.5))],                              # the cup can be picked up; its saucer stays
        table=dict(top=(52, 840, 690, 1062), stem=[(352, 1040), (392, 1040), (396, 1452), (348, 1452)],
                   foot=(222, 1448, 534, 1540)),
        wall=(458, 196, 668, 470),
        seats=[dict(name='lead', head_box=(120, 395, 485, 720), facing=1, torso_seed=(240, 800), hip=(205, 1080),
                    rest=dict(l=(246, 902), r=(392, 878)))],
        light='right'),
    'together': dict(
        source='03-together.png', arms='plate-03-nocups2-s95.png', empty='plate-03-empty-s7.png',
        edit_roi=[(40, 700), (900, 700), (900, 1010), (40, 1010)],
        lift_cups=[dict(rim=(240, 878.3, 47, 7), base=(205, 273, 943, 951), handle=(-1, 171, 197, 883, 922, 182, 195, 892, 915),
                        saucer=(239, 944, 68, 16.5)),
                   dict(rim=(664, 875.5, 46.5, 7.5), base=(627, 700, 935, 942), handle=(1, 708, 729, 881, 918, 712, 723, 888, 910),
                        saucer=(666.7, 938, 70.5, 18.5))],
        table=dict(top=(88, 880, 800, 1080), stem=[(410, 1060), (446, 1060), (448, 1480), (408, 1480)],
                   foot=(286, 1440, 560, 1530)),
        wall=(546, 330, 690, 486),
        seats=[dict(name='lead', head_box=(90, 430, 410, 730), facing=1, torso_seed=(220, 820), hip=(185, 1085),
                    rest=dict(l=(245, 905), r=(468, 900))),
               dict(name='friend', head_box=(480, 400, 830, 720), facing=-1, accessory='scarf', head_grow=6,
                    torso_seed=(720, 880), hip=(760, 1090),
                    scarf_neck=dict(poly=[(632, 716), (644, 612), (722, 612), (738, 716)], colour_at=(690, 696)),
                    rest=dict(l=(556, 904), r=(772, 918)))],
        light='right'),
    'friend-at-table': dict(
        source='cand-friend-seated-s52.png', arms='plate-friend-nocup-s75.png', empty='plate-friend-empty-s73.png',
        edit_roi=[(480, 700), (900, 700), (900, 1000), (480, 1000)],
        front_cups=[(170, 890, 310, 990), (598, 884, 734, 982)],
        table=dict(top=(92, 905, 802, 1110), stem=[(404, 1090), (446, 1090), (448, 1560), (404, 1560)],
                   foot=(300, 1520, 560, 1600)),
        wall=None,
        seats=[dict(name='friend', head_box=(500, 390, 830, 730), facing=-1, accessory='scarf', head_grow=6,
                    torso_seed=(700, 900), hip=(770, 1100),
                    scarf_neck=dict(poly=[(632, 720), (644, 620), (726, 620), (740, 720)], colour_at=(690, 712)),
                    rest=dict(l=(588, 902), r=(772, 1010)))],
        light='right'),
    'lead-standing': dict(
        source='cand-lead-standing-b-s62.png', arms='plate-standing-arms-s82.png', empty='plate-standing-empty-s83.png',
        edit_roi=[(470, 560), (920, 560), (920, 900), (470, 900)],
        front_cups=[],
        table=dict(top=(-60, 880, 690, 1085), stem=[(330, 1060), (372, 1060), (374, 1600), (330, 1600)],
                   foot=(200, 1560, 500, 1650)),
        wall=None,
        seats=[dict(name='lead', head_box=(470, 300, 820, 640), facing=-1, torso_seed=(620, 720), hip=None,
                    rest=dict(l=(528, 862), r=(744, 866)))],
        light='right'),
    # ---- bedroom bases approved on 2026-11-08 (video 2). The character lies under the quilt: the quilt is the
    #      'cover' layer (drawn where a table would be), the torso is covered, only head, arms and hands animate.
    #      Clocks come out of every plate; one brass clock sprite (cut from s322) stands in all three. ----
    'lead-bed-edge-bedroom': dict(
        source='edit-bed-edge-clean-s161.png', arms='plate-bed-edge-arms-s171.png', empty='plate-bed-edge-empty-s173.png',
        edit_roi=[(215, 720), (620, 720), (620, 830), (760, 830), (760, 930), (640, 930), (640, 1090), (215, 1100)],
        props=[dict(name='clock', box=(660, 830, 760, 925))],
        seats=[dict(name='lead', head_box=(225, 370, 615, 735), facing=1, torso_seed=(350, 850), hip=(350, 1045),
                    rest=dict(l=(395, 1045), r=(590, 1035)))],
        places=dict(clock=dict(xy=(712, 916), width=0.33)),
        light='right'),
    'lead-bed-asleep-top-bedroom': dict(
        source='cand-bed-asleep-top-s112.png', arms='plate-bed-top-hand-noclock-s145.png', empty='plate-bed-top-empty-noclock-s147.png',
        edit_roi=[(540, 500), (850, 200), (941, 200), (941, 420), (720, 660), (540, 660)],
        cover=[(10, 700), (30, 640), (150, 590), (330, 565), (500, 555), (650, 575), (800, 600), (880, 650), (930, 760),
               (930, 1672), (10, 1672)],
        seats=[dict(name='lead', head_box=(230, 190, 620, 585), facing=1, torso='covered',
                    shoulders=dict(l=(270, 612), r=(580, 612)), hip=None, rest=dict(l=(300, 665), r=(622, 592)))],
        places=dict(clock=dict(xy=(885, 335), width=0.33)),
        light='right'),
    'lead-bed-awake-bedroom': dict(
        source='cand-bed-awake-r3-s322.png', arms='plate-bed-awake-arms-s151.png', empty='plate-bed-awake-empty-s153.png',
        edit_roi=[(200, 740), (640, 700), (650, 540), (790, 540), (790, 700), (620, 830), (480, 980), (200, 980)],
        cover=[(0, 935), (130, 892), (250, 860), (330, 832), (450, 792), (560, 770), (650, 760), (720, 770), (800, 800),
               (860, 840), (941, 900), (941, 1672), (0, 1672)],
        props=[dict(name='clock', box=(658, 560, 748, 688))],
        seats=[dict(name='lead', head=dict(centre=(302, 655), size=(363, 358), refine=False), face_lum=100, face_sat=130,
                    facing=1, torso='covered',
                    shoulders=dict(l=(268, 862), r=(548, 782)), hip=None, rest=dict(l=(455, 1095), r=(705, 1035)))],   # arms lying on the quilt (user: the higher hands read as a 'W')
        places=dict(clock=dict(xy=(690, 650), width=0.33)),
        light='right'),
}


def src(name: str) -> Path:
    return EC / name if (EC / name).exists() else FLUX / name


def load(path: Path, size=None) -> np.ndarray:
    im = Image.open(path).convert('RGB')
    if size and im.size != size:
        im = im.resize(size, Image.LANCZOS)
    return np.asarray(im)


def register(orig: np.ndarray, gen: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """FLUX redraws the whole picture a few pixels off; align it to the original (affine, ECC)."""
    h, w = orig.shape[:2]
    gen = cv2.resize(gen, (w, h), interpolation=cv2.INTER_AREA)
    g0 = cv2.cvtColor(orig, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    g1 = cv2.cvtColor(gen, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 400, 1e-7)
    cc, warp = cv2.findTransformECC(g0, g1, warp, cv2.MOTION_AFFINE, crit, valid.astype(np.uint8) * 255, 5)
    aligned = cv2.warpAffine(gen, warp, (w, h), flags=cv2.INTER_LANCZOS4 + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
    print(f'    registered: ecc={cc:.3f} shift=({warp[0, 2]:+.1f},{warp[1, 2]:+.1f}) scale=({warp[0, 0]:.4f},{warp[1, 1]:.4f})')
    return aligned


def fit_head(im: np.ndarray, box) -> tuple:
    """The painted head's outline as an ellipse ((cx, cy), (width, height), angle), fitted to its dark ring."""
    x0, y0, x1, y1 = box
    crop = im[y0:y1, x0:x1].astype(np.float32)
    ys, xs = np.nonzero(crop.mean(-1) < 70)
    cx, cy, r = (x1 - x0) / 2, (y1 - y0) / 2 - 10, (x1 - x0) * 0.42
    for _ in range(6):                      # circle first, to pick the ring out from the face marks
        d = np.hypot(xs - cx, ys - cy)
        sel = (d > r * 0.86) & (d < r * 1.12)
        X, Y = xs[sel].astype(np.float64), ys[sel].astype(np.float64)
        A = np.c_[2 * X, 2 * Y, np.ones_like(X)]
        (cx, cy, c), *_ = np.linalg.lstsq(A, X * X + Y * Y, rcond=None)
        r = float(np.sqrt(c + cx * cx + cy * cy))
    d = np.hypot(xs - cx, ys - cy)
    sel = (d > r * 0.88) & (d < r * 1.12)
    (ex, ey), (ew, eh), ang = cv2.fitEllipse(np.c_[xs[sel], ys[sel]].astype(np.float32))
    return ((float(ex + x0), float(ey + y0)), (float(ew), float(eh)), float(ang))


def outline_ring(im: np.ndarray, ell, inner: float = -12, outer: float = 14, min_area: int = 300) -> np.ndarray:
    """The head's own dark outline: the dark runs in a band around the ellipse, specks and short spurs
    of the torso outline dropped. Thin, light spots can split the ring, so every sizeable piece is kept."""
    lum = im.astype(np.float32).mean(-1)
    band = ellipse_mask(im.shape, ell, outer) & ~ellipse_mask(im.shape, ell, inner)
    stroke = band & (lum < 135)
    stroke = cv2.morphologyEx(stroke.astype(np.uint8), cv2.MORPH_OPEN, disk(1)) > 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats(stroke.astype(np.uint8))
    keep = np.zeros_like(stroke)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep |= lab == i
    return keep if keep.any() else stroke


def refine_head(im: np.ndarray, ell) -> tuple:
    """Re-fit the ellipse to the OUTER edge of the outline stroke. The first fit uses every dark pixel near
    the head, so a dark door frame or a shadow beside it can pull the ellipse a few pixels off the painted
    outline, and the backing ring then showed outside the head (seen on the standing lead).
    Along rays from the centre, the dark run nearest the first ellipse is the stroke; runs much thicker than
    the stroke (a window frame, the torso outline merging in) are skipped. The ellipse is fitted to the
    stroke's outer ends with outliers dropped, and kept only if it stays close to the first fit."""
    h, w = im.shape[:2]
    lum = im.astype(np.float32).mean(-1)
    (cx, cy), (ew, eh), ang = ell
    A, Bx, th = ew / 2, eh / 2, np.radians(ang)
    runs = []
    for k in range(0, 360, 2):
        a = np.radians(k)
        phi = a - th
        R = A * Bx / np.hypot(Bx * np.cos(phi), A * np.sin(phi))
        r = np.arange(R - 16, R + 16, 0.5)
        xs = np.clip((cx + np.cos(a) * r).astype(int), 0, w - 1)
        ys = np.clip((cy + np.sin(a) * r).astype(int), 0, h - 1)
        dark = lum[ys, xs] < 90
        if not dark.any():
            continue
        idx = np.nonzero(dark)[0]
        pieces = np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1)
        best = min(pieces, key=lambda p: abs(r[p].mean() - R))
        runs.append((a, r[best[-1]] + 0.5, r[best[-1]] - r[best[0]]))
    if len(runs) < 20:
        return ell
    thick = np.median([t for _, _, t in runs])
    pts = np.array([(cx + np.cos(a) * ro, cy + np.sin(a) * ro) for a, ro, t in runs if t <= 1.8 * thick + 1], np.float32)
    for _ in range(4):
        if len(pts) < 12:
            return ell
        (ex, ey), (ew2, eh2), ang2 = cv2.fitEllipse(pts)
        t2 = np.radians(ang2)
        dx, dy = pts[:, 0] - ex, pts[:, 1] - ey
        xr = dx * np.cos(t2) + dy * np.sin(t2)
        yr = -dx * np.sin(t2) + dy * np.cos(t2)
        res = (np.sqrt((xr / (ew2 / 2)) ** 2 + (yr / (eh2 / 2)) ** 2) - 1) * (ew2 + eh2) / 4
        good = np.abs(res) < 3
        if good.all():
            break
        pts = pts[good]
    moved = np.hypot(ex - cx, ey - cy)
    if moved > 14 or abs(ew2 - ew) > 0.08 * ew or abs(eh2 - eh) > 0.08 * eh:
        print(f'    head refit rejected (moved {moved:.1f}px, axes {ew:.0f}x{eh:.0f} -> {ew2:.0f}x{eh2:.0f})')
        return ell
    return ((float(ex), float(ey)), (float(ew2), float(eh2)), float(ang2))


def ellipse_mask(shape, ell, grow: float = 0.0) -> np.ndarray:
    (cx, cy), (ew, eh), ang = ell
    m = np.zeros(shape[:2], np.uint8)
    cv2.ellipse(m, ((cx, cy), (max(1, ew + 2 * grow), max(1, eh + 2 * grow)), ang), 255, -1)
    return m > 0


def disk(r: int):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def fill_holes(m: np.ndarray) -> np.ndarray:
    h, w = m.shape
    ff = m.astype(np.uint8) * 255
    canvas = np.zeros((h + 2, w + 2), np.uint8)
    flood = ff.copy()
    cv2.floodFill(flood, canvas, (0, 0), 255)
    return m | (flood == 0)


def head_sprite(orig: np.ndarray, ell, face_lum: float = 175, face_sat: float = 48) -> tuple[Image.Image, list]:
    """The painted head, cut exactly at the outer edge of its own dark outline (so no ring of the old
    background or torso comes with it), with the eyes, brows and mouth painted out."""
    (cx, cy), (ew, eh), ang = ell
    f = orig.astype(np.float32)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    inner = ellipse_mask(orig.shape, ell, -3.8)
    # the stroke ring (every sizeable piece of it: light spots can split it), not specks or short spurs
    stroke = outline_ring(orig, ell, -7, 9)
    region = ellipse_mask(orig.shape, ell, -4) | stroke
    # the ellipse is fitted to the stroke's outer edge, so the cut can hug it: dark background touching the
    # outline (foliage, a shadow) stays out
    region = fill_holes(region) & ellipse_mask(orig.shape, ell, 2.5)
    # spurs: anything sticking out more than the stroke's own width is the torso outline, not the head
    region = cv2.morphologyEx(region.astype(np.uint8), cv2.MORPH_OPEN, disk(3)) > 0
    marks = inner & ((lum < face_lum) | (sat > face_sat))
    marks &= ellipse_mask(orig.shape, ell, -16)        # the face only: never the inner half of the outline stroke
    marks = cv2.dilate(marks.astype(np.uint8) * 255, disk(4))
    marks[~inner] = 0
    work = orig.copy()
    work[~inner] = np.median(orig[inner & (marks == 0)].reshape(-1, 3), 0).astype(np.uint8)
    clean = cv2.inpaint(work, marks, 7, cv2.INPAINT_TELEA)
    clean = cv2.inpaint(clean, cv2.erode(marks, np.ones((3, 3), np.uint8)), 4, cv2.INPAINT_NS)
    clean[~inner] = orig[~inner]
    # a one-pixel soft edge in the outline's own colour, over whatever is behind the head
    ring = cv2.dilate(region.astype(np.uint8), disk(1)) > 0
    edge = ring & ~region
    core = stroke & (lum < 70)
    stroke_col = np.median(orig[core].reshape(-1, 3), 0) if core.any() else np.array(NAVY)
    clean[edge] = stroke_col.astype(np.uint8)
    alpha = cv2.GaussianBlur((region * 1.0 + edge * 0.55).astype(np.float32), (0, 0), 0.6)
    # an unbroken outline at the painting's own stroke weight: along rays from the centre, the stroke is the
    # dark run nearest the ellipse; where a ray finds none (the old torso outline used to join there), that
    # angle is painted at the median stroke width, just inside the cut, so nothing shows outside the head.
    hh, ww = orig.shape[:2]
    A, Bx, th = ew / 2, eh / 2, np.radians(ang)
    thick, missing = [], []
    for k in range(0, 360, 2):
        a = np.radians(k)
        phi = a - th
        R = A * Bx / np.hypot(Bx * np.cos(phi), A * np.sin(phi))
        r = np.arange(R - 14, R + 4, 0.5)
        xs = np.clip((cx + np.cos(a) * r).astype(int), 0, ww - 1)
        ys = np.clip((cy + np.sin(a) * r).astype(int), 0, hh - 1)
        dark = lum[ys, xs] < 120
        if dark.any():
            idx = np.nonzero(dark)[0]
            pieces = np.split(idx, np.nonzero(np.diff(idx) > 1)[0] + 1)
            best = max(pieces, key=len)
            thick.append(r[best[-1]] - r[best[0]] + 0.5)
        else:
            missing.append(k)
    sw = int(round(np.median(thick))) if thick else 5
    sw = max(4, min(sw, 12))
    if missing:
        wedge = np.zeros((hh, ww), np.uint8)
        for k in missing:
            a = np.radians(k)
            cv2.line(wedge, (int(cx), int(cy)), (int(cx + np.cos(a) * (max(ew, eh) / 2 + 8)), int(cy + np.sin(a) * (max(ew, eh) / 2 + 8))),
                     255, max(3, int(0.04 * max(ew, eh))))
        fill = (wedge > 0) & ellipse_mask(orig.shape, ell, 0) & ~ellipse_mask(orig.shape, ell, -sw)
        fill_a = cv2.GaussianBlur(fill.astype(np.float32), (0, 0), 0.8)[..., None]
        clean = np.clip(clean.astype(np.float32) * (1 - fill_a) + np.array(stroke_col, np.float32) * fill_a, 0, 255).astype(np.uint8)
    alpha = np.clip(alpha, 0, 1)
    r = max(ew, eh) / 2
    x0, y0, x1, y1 = int(cx - r - 14), int(cy - r - 14), int(cx + r + 15), int(cy + r + 15)
    rgba = np.dstack([clean, (np.clip(alpha, 0, 1) * 255).astype(np.uint8)])[y0:y1, x0:x1]
    return Image.fromarray(rgba, 'RGBA'), [round(cx - x0, 2), round(cy - y0, 2)]


def torso_mask(img: np.ndarray, seed, scarf: bool) -> np.ndarray:
    """The painted torso: its white, its navy outline and (for the friend) the scarf on it."""
    h, w = img.shape[:2]
    m = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(img.copy(), m, seed, (255, 0, 255), (58, 58, 58), (16, 16, 16),
                  cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8) | 4)
    white = m[1:-1, 1:-1] > 0
    f = img.astype(np.int32)
    lum = f.mean(-1)
    navy = (lum < 120) & (f[..., 2] >= f[..., 0] - 6)
    teal = (f[..., 1] > f[..., 0] + 12) & (f[..., 2] > f[..., 0] + 4) & (lum > 50) & (lum < 200)
    near = cv2.dilate(white.astype(np.uint8), disk(9)) > 0
    body = white | (near & navy)
    if scarf:
        sc = teal & (cv2.dilate(white.astype(np.uint8), disk(30)) > 0)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(sc.astype(np.uint8))
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] > 400:
                body |= lab == i
        body |= near & navy
    body = fill_holes(body)
    return cv2.morphologyEx(body.astype(np.uint8), cv2.MORPH_OPEN, disk(1)) > 0


def stroke_width(img: np.ndarray, y: int, x_edge: int, step: int) -> int:
    lum = img[y].astype(np.float32).mean(-1)
    n, x = 0, x_edge
    while 0 <= x < len(lum) and lum[x] < 120 and n < 20:
        n, x = n + 1, x + step
    return max(4, n)


def neck_layer(img: np.ndarray, body: np.ndarray, ell, D: float, size) -> tuple[Image.Image, dict]:
    """A neck that continues the torso's own curved outline up under the head, with the torso's shading
    copied from its top row and the same outline weight, so a nodding head never shows a seam."""
    (cx, cy), _, _ = ell
    clear = body & ~ellipse_mask(img.shape, ell, 14)
    rows = []
    for y in range(int(cy), img.shape[0]):
        xs = np.nonzero(clear[y])[0]
        if len(xs) and xs.max() - xs.min() > 0.25 * D and xs.max() - xs.min() + 1 - len(xs) < 6:
            rows.append((y, xs.min(), xs.max()))
        if len(rows) >= 50:
            break
    e = np.array(rows, float)
    y_j = e[0, 0]
    kl = np.polyfit(e[:, 0] - y_j, e[:, 1], 2)
    kr = np.polyfit(e[:, 0] - y_j, e[:, 2], 2)
    # extrapolate the curves gently: limit the quadratic term so the neck narrows, never flares
    kl[0], kr[0] = min(kl[0], 0.0) * 0.5, max(kr[0], 0.0) * 0.5
    wl = stroke_width(img, int(y_j + 6), int(np.polyval(kl, 6)), 1)
    wr = stroke_width(img, int(y_j + 6), int(np.polyval(kr, 6)), -1)
    src_y = int(y_j + 8)
    xl0, xr0 = np.polyval(kl, 8), np.polyval(kr, 8)
    profile = img[src_y, int(xl0):int(xr0) + 1].astype(np.float32)
    top = cy + 0.08 * D
    layer = np.zeros((size[1], size[0], 4), np.uint8)
    min_w = 0.26 * D                         # a neck, never a point
    for y in range(int(top), int(y_j + 12)):
        xl, xr = np.polyval(kl, y - y_j), np.polyval(kr, y - y_j)
        if xr - xl < min_w:
            mid = (xl + xr) / 2
            xl, xr = mid - min_w / 2, mid + min_w / 2
        xs = np.arange(int(np.floor(xl)), int(np.ceil(xr)) + 1)
        u = np.clip((xs - xl) / (xr - xl), 0, 1)
        cols = profile[np.clip((u * (len(profile) - 1)).astype(int), 0, len(profile) - 1)]
        layer[y, xs, :3] = cols.astype(np.uint8)
        layer[y, xs, 3] = 255
    return Image.fromarray(layer, 'RGBA'), dict(join_y=round(y_j, 1), stroke=[wl, wr], left=[round(v, 4) for v in kl],
                                                right=[round(v, 4) for v in kr])


def clean_torso(img: np.ndarray, body: np.ndarray, scarf: bool) -> np.ndarray:
    """Paint out navy strokes inside a torso (the outlines of arms FLUX turned into white sleeves), keeping
    the torso's own outline and any scarf."""
    f = img.astype(np.int32)
    lum = f.mean(-1)
    navy = (lum < 140) & (f[..., 2] >= f[..., 0] - 6)
    interior = cv2.erode(body.astype(np.uint8), disk(11)) > 0
    stray = interior & navy
    if scarf:
        teal = (f[..., 1] > f[..., 0] + 12) & (f[..., 2] > f[..., 0] + 4)
        stray &= ~(cv2.dilate(teal.astype(np.uint8), disk(10)) > 0)
    if not stray.any():
        return img
    m = cv2.dilate(stray.astype(np.uint8) * 255, disk(3))
    return cv2.inpaint(img, m, 6, cv2.INPAINT_TELEA)


def shoulders_from(body: np.ndarray, img: np.ndarray, ell, D: float) -> dict:
    """Shoulders on the torso outline, a little below where it clears the head."""
    (cx, cy), _, _ = ell
    clear = body & ~ellipse_mask(img.shape, ell, 14)
    for y in range(int(cy), img.shape[0]):
        xs = np.nonzero(clear[y])[0]
        if len(xs) and xs.max() - xs.min() > 0.25 * D:
            break
    y = int(y + 0.05 * D)
    xs = np.nonzero(body[y])[0]
    xl, xr = xs.min(), xs.max()
    wl, wr = stroke_width(img, y, xl, 1), stroke_width(img, y, xr, -1)
    return dict(l=[round(xl + wl / 2, 1), y], r=[round(xr - wr / 2, 1), y])


def shape_mask(size, shapes) -> Image.Image:
    m = Image.new('L', size, 0)
    d = ImageDraw.Draw(m)
    for kind, geo in shapes:
        (d.ellipse if kind == 'ellipse' else d.polygon)(geo, fill=255)
    return m


def feather(mask: np.ndarray, grow: int, soft: float) -> np.ndarray:
    m = mask.astype(np.uint8) * 255
    if grow:
        m = cv2.dilate(m, disk(grow))
    if soft:
        m = cv2.GaussianBlur(m, (0, 0), soft)
    return m.astype(np.float32) / 255


def cup_matte(img: np.ndarray, box) -> np.ndarray:
    """A painted green cup (and saucer) on the wooden table: its own pixels only, no table around it."""
    x0, y0, x1, y1 = box
    f = img.astype(np.int32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    lum = f.mean(-1)
    green = (g >= r - 6) & (g > b) & (lum > 45)
    pale = (lum > 150) & (np.abs(r - g) < 40) & (np.abs(g - b) < 60)      # crema, rim highlights
    m = np.zeros(img.shape[:2], bool)
    m[y0:y1, x0:x1] = True
    cup = m & (green | (pale & (cv2.dilate((m & green).astype(np.uint8), disk(10)) > 0)))
    cup = cv2.morphologyEx(cup.astype(np.uint8), cv2.MORPH_CLOSE, disk(4)) > 0
    cup = fill_holes(cup)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cup.astype(np.uint8))
    if n > 1:
        cup = lab == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))
    return cup


def cup_shapes(size, g) -> tuple[np.ndarray, np.ndarray]:
    """(cup, saucer) masks for a painted cup from its measured geometry: the rim ellipse, a bowl tapering to its
    base, a handle ring on one side, and the saucer ellipse."""
    w, h = size
    cup = Image.new('L', size, 0)
    d = ImageDraw.Draw(cup)
    cx, cy, rx, ry = g['rim']
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    bl, br, by, bottom = g['base']
    left, right = [], []
    for i in range(13):
        t = i / 12
        y = cy + (by - cy) * t
        left.append((cx - rx + (bl - (cx - rx)) * t * t, y))
        right.append((cx + rx + (br - (cx + rx)) * t * t, y))
    arc = [((bl + br) / 2 + (br - bl) / 2 * math.cos(math.pi * i / 12), by + (bottom - by) * math.sin(math.pi * i / 12)) for i in range(13)]
    d.polygon(left + arc[::-1] + right[::-1], fill=255)
    _, ox0, ox1, oy0, oy1, ix0, ix1, iy0, iy1 = g['handle']
    d.ellipse((ox0, oy0, ox1, oy1), fill=255)
    d.ellipse((ix0, iy0, ix1, iy1), fill=0)
    m = np.asarray(cup) > 0
    # the handle's hole is cut, but the bowl over it stays
    bowl = Image.new('L', size, 0)
    db = ImageDraw.Draw(bowl)
    db.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=255)
    db.polygon(left + arc[::-1] + right[::-1], fill=255)
    m |= np.asarray(bowl) > 0
    sc = Image.new('L', size, 0)
    sx, sy, srx, sry = g['saucer']
    ImageDraw.Draw(sc).ellipse((sx - srx, sy - sry, sx + srx, sy + sry), fill=255)
    return cv2.dilate(m.astype(np.uint8), disk(1)) > 0, np.asarray(sc) > 0


def cut(img: np.ndarray, mask: np.ndarray, soft: float = 0.7) -> tuple[Image.Image, list]:
    a = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), soft) if soft else mask.astype(np.float32)
    ys, xs = np.nonzero(a > 0.01)
    x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    rgba = np.dstack([img, (np.clip(a, 0, 1) * 255).astype(np.uint8)])[y0:y1, x0:x1]
    return Image.fromarray(rgba, 'RGBA'), [int(x0), int(y0)]


def build(name: str, spec: dict):
    print(name)
    orig = load(src(spec['source']), (941, 1672))
    h, w = orig.shape[:2]
    size = (w, h)
    yy, xx = np.mgrid[0:h, 0:w]
    def given(hd):
        e = (tuple(hd['centre']), tuple(hd['size']), hd.get('angle', 0.0))
        return refine_head(orig, e) if hd.get('refine', True) else e
    ells = [given(s['head']) if s.get('head') else refine_head(orig, fit_head(orig, s['head_box'])) for s in spec['seats']]
    Ds = [(e[1][0] + e[1][1]) / 2 for e in ells]
    for s, e in zip(spec['seats'], ells):
        print(f"    {s['name']}: head centre ({e[0][0]:.1f},{e[0][1]:.1f}) size {e[1][0]:.0f}x{e[1][1]:.0f}")

    roi = np.asarray(shape_mask(size, [('polygon', spec['edit_roi'])])) > 0
    head_disc = np.zeros((h, w), bool)
    for s_, e in zip(spec['seats'], ells):
        head_disc |= ellipse_mask(orig.shape, e, s_.get('head_grow', 12))
    t = spec.get('table')
    if t:
        table_m = np.asarray(shape_mask(size, [('ellipse', t['top']), ('polygon', t['stem']), ('ellipse', t['foot'])])) > 0
    elif spec.get('cover'):
        # a quilt (or anything the character lies under): drawn where the table would be, so arms sent
        # 'under' disappear beneath it and the others lie on top of it
        table_m = np.asarray(shape_mask(size, [('polygon', spec['cover'])])) > 0
    else:
        table_m = np.zeros((h, w), bool)

    # 1. Where the arms, hands and lifted cups were: the FLUX plate differs strongly from the original there.
    arms = register(orig, load(FLUX / spec['arms']), ~(roi | head_disc))
    diff = cv2.GaussianBlur(np.abs(orig.astype(np.float32) - arms.astype(np.float32)).mean(-1), (0, 0), 2.5)
    edit = (diff > 34) & roi & ~head_disc
    # Painted legs never turn into background: below a seat's torso (legs_from, set by derived specs), a pixel that is
    # navy in the original but not on the arms plate is FLUX erasing a thigh, not an arm (2026-11-08, sofa bases).
    navy_o = (orig.astype(np.int32).mean(-1) < 120) & (orig[..., 2].astype(np.int32) >= orig[..., 0].astype(np.int32) - 6)
    navy_a = (arms.astype(np.int32).mean(-1) < 120) & (arms[..., 2].astype(np.int32) >= arms[..., 0].astype(np.int32) - 6)
    for s_, e_ in zip(spec['seats'], ells):
        if s_.get('legs_from') is not None:
            D_ = (e_[1][0] + e_[1][1]) / 2
            col = np.zeros((h, w), bool)
            col[:, max(0, int(e_[0][0] - 1.3 * D_)):min(w, int(e_[0][0] + 1.3 * D_))] = True
            edit &= ~(navy_o & ~navy_a & col & (yy > s_['legs_from'] - 0.05 * D_))
    edit = cv2.morphologyEx(edit.astype(np.uint8), cv2.MORPH_CLOSE, disk(7)) > 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats(edit.astype(np.uint8))
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 400:
            edit[lab == i] = False
    # cups that stay painted: their own pixels come from the original (tight matte, no table around them)
    front_cups = [cup_matte(orig, b) for b in spec.get('front_cups', [])]
    keep = np.zeros((h, w), bool)
    for m in front_cups:
        keep |= m
    edit &= ~keep
    a = feather(edit, 7, 3.0)[..., None]
    plate = orig.astype(np.float32) * (1 - a) + arms.astype(np.float32) * a
    plate = plate.astype(np.uint8)

    # 2. Liftable cups: cut from the original and split from their saucers by shape (the saucer is the wide
    #    flat part). The saucer goes back into the plate with its hidden centre filled from its own rim,
    #    whatever FLUX did to it; the cup becomes a sprite that can be picked up.
    cups_meta = []
    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    for k, g in enumerate(spec.get('lift_cups', [])):
        cup, saucer = cup_shapes(size, g)
        # the plate: no cup (the FLUX pixels where it stood) and the original painted saucer, its hidden
        # centre filled from the saucer's own colours
        a = feather(cv2.dilate(cup.astype(np.uint8), disk(4)) > 0, 0, 1.0)[..., None]
        plate = (plate.astype(np.float32) * (1 - a) + arms.astype(np.float32) * a).astype(np.uint8)
        visible = saucer & ~cup
        hidden = saucer & cup
        work = orig.copy()
        work[~saucer] = np.median(orig[visible].reshape(-1, 3), 0).astype(np.uint8)
        filled = cv2.inpaint(work, (hidden * 255).astype(np.uint8), 6, cv2.INPAINT_TELEA)
        filled[~saucer] = orig[~saucer]
        # the original table around the saucer too, so a saucer FLUX left a few pixels off can't show twice
        ring = cv2.dilate(saucer.astype(np.uint8), disk(6)) > 0
        region = ring & ~(cup & ~saucer)
        a = feather(region, 0, 1.2)[..., None]
        plate = (plate.astype(np.float32) * (1 - a) + filled.astype(np.float32) * a).astype(np.uint8)
        sprite, xy = cut(orig, cup, 0.8)
        sprite.save(out / f'cup-{k}.png', optimize=True)
        cups_meta.append(dict(sprite=f'cup-{k}.png', xy=xy, size=list(sprite.size), saucer=list(g['saucer'][:2])))

    # 2b. Props the rig will move (a clock that rings): their pixels come from the original where the arms
    #     plate painted them out; the plate keeps the FLUX pixels (already blended in through `edit`).
    props_meta = {}
    for pr in spec.get('props', []):
        bx0, by0, bx1, by1 = pr['box']
        box = np.zeros((h, w), bool)
        box[by0:by1, bx0:bx1] = True
        m = (diff > 28) & box
        m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, disk(5)) > 0
        n_, lab_, st_, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8))
        if n_ > 1:
            m = lab_ == (1 + int(np.argmax(st_[1:, cv2.CC_STAT_AREA])))
        m = fill_holes(m)
        m = cv2.erode(m.astype(np.uint8), disk(1)) > 0 if pr.get('tight', True) else m
        sprite, xy = cut(orig, m, 0.7)
        sprite.save(out / f"prop-{pr['name']}.png", optimize=True)
        ys_, xs_ = np.nonzero(m)
        props_meta[pr['name']] = dict(sprite=f"prop-{pr['name']}.png", xy=xy, size=list(sprite.size),
                                      foot=[int(xs_.mean()), int(ys_.max())])
        # the plate under the prop is the arms plate's
        a = feather(cv2.dilate(m.astype(np.uint8), disk(3)) > 0, 0, 1.0)[..., None]
        plate = (plate.astype(np.float32) * (1 - a) + arms.astype(np.float32) * a).astype(np.uint8)

    # 3. Heads out (the empty café behind them); 4. seated torsos out, into their own layers.
    empty = register(orig, load(FLUX / spec['empty']), ~(roi | head_disc))
    seats_meta, bodies = [], []
    for s, e, D in zip(spec['seats'], ells, Ds):
        if s.get('torso') == 'covered':
            body = np.zeros((h, w), bool)
        else:
            body = torso_mask(arms, s['torso_seed'], s.get('accessory') == 'scarf')
            body &= ~ellipse_mask(orig.shape, e, s.get('head_grow', 12))
        shoulders = s.get('shoulders') or shoulders_from(body, arms, e, D)
        meta = dict(name=s['name'], head=[round(e[0][0], 1), round(e[0][1], 1)], D=round(D, 1), facing=s['facing'],
                    accessory=s.get('accessory', ''), shoulders=shoulders, rest=s['rest'], hip=s['hip'])
        bodies.append((s, e, D, body, meta))
        seats_meta.append(meta)
    cup_zone = np.zeros((h, w), bool)        # the empty-café plate keeps its cups: never take pixels from there
    for b in spec.get('front_cups', []):
        cup_zone |= cv2.dilate(cup_matte(orig, b).astype(np.uint8), disk(8)) > 0
    for g in spec.get('lift_cups', []):
        c_, s_ = cup_shapes(size, g)
        cup_zone |= cv2.dilate((c_ | s_).astype(np.uint8), disk(8)) > 0
    hm = feather(head_disc, 1, 1.2)[..., None]
    plate = (plate.astype(np.float32) * (1 - hm) + empty.astype(np.float32) * hm).astype(np.uint8)
    for s, e, D, body, meta in bodies:
        if body.any():
            tm = feather(cv2.erode(body.astype(np.uint8), disk(2)) > 0, 0, 1.5)[..., None]
            plate = (plate.astype(np.float32) * (1 - tm) + arms.astype(np.float32) * tm).astype(np.uint8)
            plate = clean_torso(plate, body, s.get('accessory') == 'scarf')
    for s, e, D, body, meta in bodies:
        if s.get('torso') == 'covered':
            # the body is under the cover: the only painted part that stays is the cover itself, laid back
            # over the arm roots at the shoulders so an arm comes out from under it
            occ = np.zeros((h, w), np.float32)
            for side in ('l', 'r'):
                sx, sy = meta['shoulders'][side]
                disc_ = np.hypot(xx - sx, yy - sy) < 0.17 * D
                occ = np.maximum(occ, (table_m & disc_).astype(np.float32))
            occ = cv2.GaussianBlur(occ, (0, 0), 2.0) * table_m
            osprite, oxy = cut(plate, occ, 0)
            osprite.save(out / f"occluder-{s['name']}.png", optimize=True)
            meta['occluder'] = dict(sprite=f"occluder-{s['name']}.png", xy=oxy)
            meta['neck'] = None
            continue
        moving = s['hip'] is not None
        # the torso itself (from the arms-free plate), extended under the tabletop where the table hides it,
        # so it can rise a little without opening a gap at the table edge
        if moving:
            ext = np.zeros((h, w, 4), np.uint8)
            for x in np.nonzero(body.any(0))[0]:
                y = np.nonzero(body[:, x])[0].max()
                if y + 2 < h and table_m[y + 2, x]:
                    ys_ = np.arange(y, min(h, y + 60))
                    ys_ = ys_[table_m[ys_, x]]                   # only where the table layer will cover it
                    ext[ys_, x, :3] = plate[y - 2, x]
                    ext[ys_, x, 3] = 255
        torso_rgba = np.dstack([plate, (body * 255).astype(np.uint8)])
        layer = Image.fromarray(torso_rgba, 'RGBA')
        if moving:
            layer.alpha_composite(Image.fromarray(ext, 'RGBA'))
        # the neck, continuing the torso's outline under the head (or the scarf's colours, for the friend)
        if 'scarf_neck' in s:
            nk = Image.new('RGBA', size, (0, 0, 0, 0))
            dn = ImageDraw.Draw(nk)
            poly, at = s['scarf_neck']['poly'], s['scarf_neck']['colour_at']
            teal = tuple(int(v) for v in np.median(orig[at[1] - 4:at[1] + 5, at[0] - 6:at[0] + 7].reshape(-1, 3), 0))
            dn.polygon(poly, fill=teal + (255,))
            lw = max(4, round(0.024 * D))
            dark = tuple(int(v * 0.7) for v in teal) + (255,)
            dn.line([poly[0], poly[1]], fill=dark, width=lw)
            dn.line([poly[3], poly[2]], fill=dark, width=lw)
            meta['neck'] = dict(scarf=poly)
        else:
            nk, meta['neck'] = neck_layer(plate, body, e, D, size)
            # outline strokes along the curves, at the torso outline's own weight
            dn = ImageDraw.Draw(nk)
            (cx, cy), _, _ = e
            nf = meta['neck']
            y_j = nf['join_y']
            na = np.asarray(nk.getchannel('A')) > 0
            for k, wdt in ((0, nf['stroke'][0]), (1, nf['stroke'][1])):
                pts = []
                for y in range(int(cy + 0.08 * D), int(y_j + 12), 2):
                    xs_ = np.nonzero(na[y])[0]
                    if len(xs_):
                        pts.append((float(xs_.min() + wdt / 2) if k == 0 else float(xs_.max() - wdt / 2), y))
                if len(pts) > 1:
                    dn.line(pts, fill=NAVY + (255,), width=int(wdt), joint='curve')
        clip = (feather(ellipse_mask(orig.shape, e, s.get('head_grow', 12) + 2), 0, 1.0) * 255).astype(np.uint8)
        nk.putalpha(Image.fromarray(np.minimum(np.asarray(nk.getchannel('A')), clip)))
        layer.alpha_composite(nk)
        full_alpha = np.asarray(layer.getchannel('A')) > 0
        if moving:
            # the body moves, so the café behind it comes from the empty plate (never over the table)
            fa = arms.astype(np.int32)
            legs = (((fa.mean(-1) < 120) & (fa[..., 2] >= fa[..., 0] - 6)) | navy_o) & ~(cv2.dilate(body.astype(np.uint8), disk(2)) > 0)
            behind = (cv2.dilate(full_alpha.astype(np.uint8), disk(16)) > 0) & ~table_m & ~cup_zone & ~legs
            bm = feather(behind, 0, 1.5)[..., None]
            plate = (plate.astype(np.float32) * (1 - bm) + empty.astype(np.float32) * bm).astype(np.uint8)
        else:
            # a standing body stays painted in the plate; the neck goes into the plate under the head
            pl = Image.fromarray(plate).convert('RGBA')
            pl.alpha_composite(nk)
            plate = np.asarray(pl.convert('RGB'))
        sprite, xy = cut(np.asarray(layer.convert('RGB')), np.asarray(layer.getchannel('A')) / 255.0, 0)
        if moving:
            sprite.save(out / f"body-{s['name']}.png", optimize=True)
            meta['body'] = dict(sprite=f"body-{s['name']}.png", xy=xy)
        # the torso around each shoulder, laid back over the arm roots (soft-edged, so no hard cut)
        occ = np.zeros((h, w), np.float32)
        for side in ('l', 'r'):
            sx, sy = meta['shoulders'][side]
            disc = np.hypot(xx - sx, yy - sy) < 0.15 * D
            occ = np.maximum(occ, (body & disc).astype(np.float32))
        occ = cv2.GaussianBlur(occ, (0, 0), 2.0) * body
        src_img = np.asarray(layer.convert('RGB')) if moving else plate
        osprite, oxy = cut(src_img, occ, 0)
        osprite.save(out / f"occluder-{s['name']}.png", optimize=True)
        meta['occluder'] = dict(sprite=f"occluder-{s['name']}.png", xy=oxy)

    # 5. Layers cut from the final plate: the table (never any torso: the drawn ellipse is only roughly the
    #    tabletop, the torso's own lower edge is the true one), cups that stay painted in front of the arms.
    table_layer = table_m.copy()
    for s, e, D, body, meta in bodies:
        table_layer &= ~(cv2.dilate(body.astype(np.uint8), disk(1)) > 0)
    table_layer = cv2.erode(table_layer.astype(np.uint8), disk(1)) > 0
    table = Image.fromarray(plate).convert('RGBA')
    table.putalpha(Image.fromarray(table_layer.astype(np.uint8) * 255).filter(ImageFilter.GaussianBlur(0.7)))
    front = np.zeros((h, w), bool)
    for m in front_cups:
        front |= m
    Image.fromarray(plate).save(out / 'plate.png', optimize=True)
    # the café with nobody in it, aligned to the plate: for inserts that show only the table and props
    if spec.get('inserts'):
        ea = Image.fromarray(empty).convert('RGBA')
        ea.putalpha(Image.fromarray((~table_m).astype(np.uint8) * 255))   # the table itself stays painted
        em = Image.fromarray(plate).convert('RGBA')
        em.alpha_composite(ea)
        em.convert('RGB').save(out / 'empty.png', optimize=True)
    layers = {}
    if table_layer.any():
        tb_sprite, txy = cut(np.asarray(table.convert('RGB')), np.asarray(table.getchannel('A')) / 255.0, 0)
        tb_sprite.save(out / 'table.png', optimize=True)
        layers['table'] = txy
    elif (out / 'table.png').exists():
        (out / 'table.png').unlink()
    if front.any():
        fs, fxy = cut(plate, front, 0.7)
        fs.save(out / 'front.png', optimize=True)
        layers['front'] = fxy
    elif (out / 'front.png').exists():
        (out / 'front.png').unlink()
    for old in ('occluder.png',):
        if (out / old).exists():
            (out / old).unlink()
    for s, e, meta in zip(spec['seats'], ells, seats_meta):
        sprite, centre = head_sprite(orig, e, s.get('face_lum', 175), s.get('face_sat', 48))
        sprite.save(out / f"head-{s['name']}.png", optimize=True)
        meta['head_art'] = dict(sprite=f"head-{s['name']}.png", centre=centre, size=[round(e[1][0], 1), round(e[1][1], 1)],
                                angle=round(e[2], 1))
    meta = dict(name=name, source=str(src(spec['source']).relative_to(ROOT)), size=[w, h],
                flux=dict(arms=spec['arms'], empty=spec['empty']), light=spec['light'], wall=spec.get('wall'),
                table=dict(top=t['top']) if t else None, cover=spec.get('cover'), layers=layers, cups=cups_meta,
                props=props_meta, seats=seats_meta, places=spec.get('places', {}))
    (out / 'base.json').write_text(json.dumps(meta, indent=1) + '\n')

    # Debug overlay: the edit mask, anchors and the body layers' outlines, on a grid.
    dbg = Image.fromarray(plate).convert('RGBA')
    for s, e, D, body, m in bodies:
        if 'body' in m:
            bl = Image.open(out / m['body']['sprite'])
            dbg.alpha_composite(bl, tuple(m['body']['xy']))
        hs = Image.open(out / m['head_art']['sprite'])
        c = m['head_art']['centre']
        dbg.alpha_composite(hs, (round(m['head'][0] - c[0]), round(m['head'][1] - c[1])))
    for c in cups_meta:
        dbg.alpha_composite(Image.open(out / c['sprite']), tuple(c['xy']))
    tint = Image.new('RGBA', size, (255, 0, 80, 0))
    tint.putalpha(Image.fromarray((edit * 70).astype(np.uint8)))
    dbg.alpha_composite(tint)
    dd = ImageDraw.Draw(dbg)
    for x in range(0, w, 50):
        dd.line([(x, 0), (x, h)], fill=(255, 255, 255, 40))
        dd.text((x + 2, 2), str(x), fill='red')
    for y in range(0, h, 50):
        dd.line([(0, y), (w, y)], fill=(255, 255, 255, 40))
        dd.text((2, y + 2), str(y), fill='red')
    for m in seats_meta:
        for side, col in (('l', (255, 0, 0)), ('r', (0, 0, 255))):
            for key in ('shoulders', 'rest'):
                x, y = m[key][side]
                dd.ellipse((x - 5, y - 5, x + 5, y + 5), fill=col)
        if m['hip']:
            x, y = m['hip']
            dd.rectangle((x - 6, y - 6, x + 6, y + 6), outline=(0, 255, 0), width=3)
    if spec.get('wall'):
        dd.rectangle(spec['wall'], outline=(255, 128, 0), width=3)
    for pm in props_meta.values():
        dbg.alpha_composite(Image.open(out / pm['sprite']), tuple(pm['xy']))
    DEBUG.mkdir(parents=True, exist_ok=True)
    dbg.convert('RGB').save(DEBUG / f'debug-{name}.jpg', quality=90)
    print('   ', out.relative_to(ROOT), [m['shoulders'] for m in seats_meta])


if __name__ == '__main__':
    for n in (sys.argv[1:] or list(BASES)):
        build(n, BASES[n])
