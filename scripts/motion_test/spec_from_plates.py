"""Derive a build spec for a base from its three images (docs/PLAN.md §7 D): the approved candidate, the FLUX
plate with the arms removed, and the FLUX plate with the character(s) removed.

  .venvs/mlx/bin/python scripts/motion_test/spec_from_plates.py <name> <source.png> <arms.png> <empty.png>
        [--cast lead,friend] [--poses lead=standing,friend=seated-sofa] [--heads lead=cx,cy,r;friend=cx,cy,r]
        [--moving] [--covered] [--build]

What it reads off the plates, per character:
- the character: where the empty plate differs from the source;
- the head: a big circle in the navy outlines, white inside and outlined round nearly its whole circumference
  (a round torso bottom is outlined only below), fitted to its outline
  (`build_bases.fit_head` + `refine_head`), and which way it faces (the eyes sit on the facing side);
- the torso: the white flooded under the head on the arms plate (as the builder cuts it); its bottom is the hip
  (used when --moving: a seated torso that leans; a torso at a table or standing stays painted);
- the resting hands: from the pose (hands at the sides, on the table, on the knees, on the wheel), in head
  diameters from the torso centre; the painted arms are removed, so the drawn arms define the look. The edit
  region covers everything from the shoulders to below the hands. Shoulders come from the torso outline in the
  builder;
- the scarf: teal under a head marks the friend (accessory 'scarf', with the neck polygon the builder paints).

Two characters: the two strongest head circles; everything below is split at the midpoint between them. The one with the scarf is the friend; with no scarf on either, the left one is the lead.

When the head search picks the wrong circle, `--heads` (or `head_hints` in the library entry, written by hand from
the picture) places the heads and the rest is derived as usual.

It prints the spec, writes a debug overlay to renders/bases/debug/<name>-spec.jpg, and with --build runs
`build_bases.build` with it. A hand-written spec in build_bases.BASES always wins; this is for the pack bases.

Checked (2026-11-08): on `lead-standing` (arms at the sides) the head, torso seed, hip and resting hands match
the hand-written spec within a few pixels; on `together` (two seated at the café table) both heads, torso seeds
and rests land within ~15 px of the hand-written spec; on `friend-at-table` (arms crossed in front) the hands are
not told apart, so a base with crossed or overlapping arms still needs its `rest` points written by hand. Always
look at the debug overlay before trusting a build.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bases as BB  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DEBUG = ROOT / 'renders/bases/debug'


def _mask_diff(a: np.ndarray, b: np.ndarray, thresh: float = 34, blur: float = 2.5) -> np.ndarray:
    d = cv2.GaussianBlur(np.abs(a.astype(np.float32) - b.astype(np.float32)).mean(-1), (0, 0), blur)
    return d > thresh


def _largest(mask: np.ndarray, keep: int = 1, min_area: int = 400) -> list:
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
    comps = sorted([(stats[i, cv2.CC_STAT_AREA], i) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] >= min_area], reverse=True)
    return [(lab == i) for _, i in comps[:keep]]


def _ring_coverage(navy: np.ndarray, cx: float, cy: float, r: float, bins: int = 36) -> float:
    """How much of a circle's circumference has navy outline on it (a head: nearly all; a round torso bottom: half)."""
    h, w = navy.shape
    hits = 0
    for k in range(bins):
        a = 2 * np.pi * k / bins
        found = False
        for rr in np.linspace(r * 0.85, r * 1.22, 9):                 # heads are ellipses: the ring is a band
            x, y = int(cx + rr * np.cos(a)), int(cy + rr * np.sin(a))
            if 0 <= x < w and 0 <= y < h and navy[y, x]:
                found = True
                break
        hits += found
    return hits / bins


def _head_circles(orig: np.ndarray, n: int, lum: np.ndarray, sat: np.ndarray, char: np.ndarray) -> list:
    """The n heads as (cx, cy, r): circles in the navy outlines with a white inside and an outline around (nearly)
    the whole circumference, which a round torso bottom never has; two heads must be alike in size and height."""
    h, w = orig.shape[:2]
    f = orig.astype(np.int32)
    navy = (lum < 110) & (f[..., 2] > f[..., 0] - 10)
    navy_d = cv2.dilate(navy.astype(np.uint8), BB.disk(3)) > 0
    edges = cv2.GaussianBlur((navy.astype(np.uint8) * 255), (0, 0), 2.0)
    white = ((sat < 60) & (lum > 150)).astype(np.float32)
    raw = []
    for p2 in (40, 30, 22, 16, 12):
        circles = cv2.HoughCircles(edges, cv2.HOUGH_GRADIENT, dp=2, minDist=60, param1=120, param2=p2, minRadius=90, maxRadius=270)
        if circles is None:
            continue
        for cx, cy, r in circles[0]:
            if cy > 0.7 * h or cy < r * 0.7 or any(np.hypot(cx - c[0], cy - c[1]) < 0.3 * r for c in raw):
                continue
            m = np.zeros((h, w), np.uint8)
            cv2.circle(m, (int(cx), int(cy)), int(r * 0.75), 255, -1)
            sel = m > 0
            if not sel.any():
                continue
            raw.append((float(cx), float(cy), float(r), float(white[sel].mean()), _ring_coverage(navy_d, cx, cy, r)))
    def torso_like(c):                                                   # another candidate sits right above it: a torso
        return any(abs(q[0] - c[0]) < 0.7 * c[2] and 0.8 * c[2] < c[1] - q[1] < 3.2 * c[2] and q[4] > 0.45 for q in raw)
    raw = [c for c in raw if not torso_like(c)]
    picked, cands = [], []
    for min_cov, min_in in ((0.8, 0.5), (0.65, 0.45), (0.5, 0.4)):                 # strict first, then looser
        cands = [(cx, cy, r, cov * inside * r) for cx, cy, r, inside, cov in raw if inside > min_in and cov > min_cov]
        cands.sort(key=lambda c: -c[3])
        if n == 1:
            picked = cands[:1]
        else:
            picked = []
            for i, c in enumerate(cands):
                for q in cands[i + 1:]:
                    if (abs(c[0] - q[0]) > 0.9 * (c[2] + q[2]) and max(c[2], q[2]) / min(c[2], q[2]) < 1.45
                            and abs(c[1] - q[1]) < 1.2 * max(c[2], q[2])):
                        picked = [c, q]
                        break
                if picked:
                    break
        if len(picked) == n:
            break
    if len(picked) < n:
        raise RuntimeError(f'found {len(picked)} head circle(s), wanted {n} (candidates: {[(round(c[0]), round(c[1]), round(c[2])) for c in cands[:6]]})')
    picked.sort(key=lambda c: c[0])
    return [(c[0], c[1], c[2]) for c in picked]


# resting hands per pose, in head diameters from the head centre (x outward from the body's centre, y down)
RESTS = {                                   # measured on the hand-written bases: hands at the sides ±0.38 D, on the table ±0.27 D
    'standing': dict(dx=0.4, dy=1.42), 'seated-at-table': dict(dx=0.28, dy=1.1), 'seated-desk': dict(dx=0.28, dy=1.1),
    'seated-sofa': dict(dx=0.32, dy=1.5), 'seated-bench': dict(dx=0.3, dy=1.5), 'driving': dict(dx=0.45, dy=0.95),
    'passenger': dict(dx=0.25, dy=1.45), 'bed-edge': dict(dx=0.45, dy=1.45),
}


def derive(source: Path, arms: Path, empty: Path, cast: list | None = None, moving: bool = False, covered: bool = False,
           poses: dict | None = None, head_hints: dict | None = None) -> dict:
    cast = cast or ['lead']
    head_hints = head_hints or {}
    poses = poses or {c: 'standing' for c in cast}
    orig = BB.load(source, (941, 1672))
    h, w = orig.shape[:2]
    hsv = cv2.cvtColor(orig, cv2.COLOR_RGB2HSV)
    sat, lum = hsv[..., 1].astype(np.float32), orig.astype(np.float32).mean(-1)
    f = orig.astype(np.int32)
    yy = np.mgrid[0:h, 0:w][0]
    # the character(s): where the empty plate differs from the source (FLUX also redraws some background, so
    # this is only a hint for the head search)
    empty_p = BB.register(orig, BB.load(empty), np.ones((h, w), bool))
    char = _mask_diff(orig, empty_p, 30)
    char = cv2.morphologyEx(char.astype(np.uint8), cv2.MORPH_CLOSE, BB.disk(9)) > 0
    char = np.logical_or.reduce(_largest(char, keep=2 + len(cast), min_area=3000)) if char.any() else char
    ys, xs = np.nonzero(char)
    if not len(ys):
        raise RuntimeError('no character found: the empty plate equals the source')
    ells = []
    if len(head_hints) == len(cast):                                   # the user placed the heads (cx, cy, r), left to right
        circles = sorted((tuple(v) for v in head_hints.values()), key=lambda c: c[0])
    else:
        circles = _head_circles(orig, len(cast), lum, sat, char)
    for cx0, cy0, r0 in circles:
        g = 0.12 * r0
        box = (int(max(0, cx0 - r0 - g)), int(max(0, cy0 - r0 - g)), int(min(w, cx0 + r0 + g)), int(min(h, cy0 + r0 + g)))
        ells.append(BB.refine_head(orig, BB.fit_head(orig, box)))
    centres = [e[0][0] for e in ells]
    bounds = [0] + [int((centres[i] + centres[i + 1]) / 2) for i in range(len(ells) - 1)] + [w]
    all_heads = np.zeros((h, w), bool)
    for e in ells:
        all_heads |= BB.ellipse_mask(orig.shape, e, 10)
    arms_p = BB.register(orig, BB.load(arms), ~all_heads)
    teal = (f[..., 1] > f[..., 0] + 12) & (f[..., 2] > f[..., 0] + 4) & (lum > 50) & (lum < 200)
    white = (sat < 45) & (lum > 165)
    # names first (the scarf marks the friend), so each seat knows its pose
    scarves = []
    for e in ells:
        (cx, cy), (ew, eh), _ = e
        D = (ew + eh) / 2
        band = teal & (yy > cy + 0.3 * D) & (yy < cy + 0.95 * D)
        band[:, :max(0, int(cx - 0.7 * D))] = False
        band[:, min(w, int(cx + 0.7 * D)):] = False
        scarves.append(band.sum() > 0.01 * D * D)
    names = list(cast)
    if len(cast) == 2 and 'friend' in cast and 'lead' in cast:
        names = (['friend', 'lead'] if scarves[0] else ['lead', 'friend']) if scarves.count(True) == 1 else ['lead', 'friend']
    seats, measured, rois = [], [], []
    for i, (e, name, scarf) in enumerate(zip(ells, names, scarves)):
        (cx, cy), (ew, eh), ang = e
        D = (ew + eh) / 2
        pose = poses.get(name, 'standing')
        head_m = BB.ellipse_mask(orig.shape, e, 0)
        marks = head_m & BB.ellipse_mask(orig.shape, e, -16) & (lum < 90)
        my, mx = np.nonzero(marks)
        facing = 1 if (mx.mean() if len(mx) else cx) >= cx else -1
        # the torso: flood the white under the head on the arms plate (the builder cuts it the same way)
        x0, x1 = max(bounds[i], int(cx - 1.3 * D)), min(bounds[i + 1], int(cx + 1.3 * D))
        seed = None
        for dy in (0.85, 0.75, 1.0, 1.15, 0.65):
            yq = int(cy + dy * D)
            if yq >= h:
                continue
            row = np.nonzero(white[yq, x0:x1] & ~all_heads[yq, x0:x1])[0]
            if len(row) > 0.1 * D:
                seed = (int(x0 + np.median(row)), yq)
                break
        if seed is None:
            seed = (int(cx), int(cy + 0.85 * D))
        try:
            body = BB.torso_mask(arms_p, seed, scarf) & ~all_heads
        except cv2.error:
            body = np.zeros((h, w), bool)
        if body.sum() > 0.3 * D * D:
            by, bx = np.nonzero(body)
            hip = (int(np.median(bx[by > np.percentile(by, 92)])), int(np.percentile(by, 98)))
            torso_cx = int(np.median(bx))
        else:
            hip = (int(cx), int(cy + 1.65 * D))
            torso_cx = int(cx)
        r = RESTS.get(pose, RESTS['standing'])
        rests = dict(l=(int(torso_cx - r['dx'] * D), int(cy + r['dy'] * D)), r=(int(torso_cx + r['dx'] * D), int(cy + r['dy'] * D)))
        if pose == 'driving':                                           # both hands forward, on the wheel
            rests = dict(l=(int(torso_cx + 0.55 * D * facing), int(cy + 0.9 * D)), r=(int(torso_cx + 0.75 * D * facing), int(cy + 1.0 * D)))
        for k, (rx, ry) in rests.items():
            rests[k] = (min(w - 1, max(0, rx)), min(h - 1, max(0, ry)))
        seat = dict(name=name, head=dict(centre=(round(cx, 1), round(cy, 1)), size=(round(ew, 1), round(eh, 1)), angle=round(ang, 1), refine=False),
                    facing=facing, torso_seed=seed, hip=hip if (moving and not covered) else None, rest=rests)
        if scarf:
            band = teal & (yy > cy + 0.3 * D) & (yy < cy + 0.95 * D)
            band[:, :max(0, int(cx - 0.7 * D))] = False
            band[:, min(w, int(cx + 0.7 * D)):] = False
            sy_, sx_ = np.nonzero(band)
            top_y, bot_y = int(np.percentile(sy_, 5)), int(np.percentile(sy_, 95))
            xl, xr = int(np.percentile(sx_, 3)), int(np.percentile(sx_, 97))
            seat.update(accessory='scarf', head_grow=6,
                        scarf_neck=dict(poly=[(xl + 6, bot_y), (xl + 18, top_y), (xr - 18, top_y), (xr - 6, bot_y)],
                                        colour_at=(int(np.median(sx_)), int(np.median(sy_)))))
        if covered:
            seat.update(torso='covered')
        seats.append(seat)
        measured.append(dict(D=round(D, 1), scarf=bool(scarf), pose=pose, body_px=int(body.sum())))
        # the edit region: everything the arms could have covered, from the shoulders down past the hands
        rois.append((max(0, int(torso_cx - 1.25 * D)), int(cy + 0.45 * D), min(w, int(torso_cx + 1.25 * D)), min(h, int(cy + r['dy'] * D + 0.4 * D))))
    rx0, ry0 = min(r_[0] for r_ in rois), min(r_[1] for r_ in rois)
    rx1, ry1 = max(r_[2] for r_ in rois), max(r_[3] for r_ in rois)
    roi = [(rx0, ry0), (rx1, ry0), (rx1, ry1), (rx0, ry1)]
    spec = dict(source=str(source), arms=str(arms), empty=str(empty), edit_roi=roi, seats=seats, light='right', derived=True, inserts=True,
                measured=dict(seats=measured, character_box=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]))
    return spec


def debug_image(spec: dict, name: str) -> Path:
    from PIL import Image, ImageDraw
    im = Image.open(spec['source']).convert('RGB').resize((941, 1672))
    d = ImageDraw.Draw(im)
    d.polygon(spec['edit_roi'], outline=(40, 200, 90), width=3)
    for s, m in zip(spec['seats'], spec['measured']['seats']):
        (cx, cy), (ew, eh) = s['head']['centre'], s['head']['size']
        d.ellipse((cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2), outline=(255, 40, 40), width=3)
        for side, xy in s['rest'].items():
            d.ellipse((xy[0] - 10, xy[1] - 10, xy[0] + 10, xy[1] + 10), outline=(255, 200, 0), width=4)
            d.text((xy[0] + 12, xy[1]), side, fill=(255, 200, 0))
        d.ellipse((s['torso_seed'][0] - 6, s['torso_seed'][1] - 6, s['torso_seed'][0] + 6, s['torso_seed'][1] + 6), fill=(255, 255, 255))
        if s.get('hip'):
            d.ellipse((s['hip'][0] - 8, s['hip'][1] - 8, s['hip'][0] + 8, s['hip'][1] + 8), fill=(200, 60, 200))
        if s.get('scarf_neck'):
            d.polygon(s['scarf_neck']['poly'], outline=(0, 200, 200), width=2)
        d.text((cx - 30, cy - eh / 2 - 18), f"{s['name']} {m['pose']} facing {s['facing']} D {m['D']}", fill=(255, 255, 255))
    DEBUG.mkdir(parents=True, exist_ok=True)
    out = DEBUG / f'{name}-spec.jpg'
    im.save(out, quality=88)
    return out


def main(argv):
    if len(argv) < 4:
        sys.exit(__doc__)
    name, source, arms, empty = argv[:4]
    cast = ['lead']
    if '--cast' in argv:
        cast = argv[argv.index('--cast') + 1].split(',')
    poses = None
    if '--poses' in argv:
        poses = dict(kv.split('=') for kv in argv[argv.index('--poses') + 1].split(','))
    hints = None
    if '--heads' in argv:                                              # --heads lead=315,590,150
        hints = {kv.split('=')[0]: tuple(float(x) for x in kv.split('=')[1].split(',')) for kv in argv[argv.index('--heads') + 1].split(';')}
    spec = derive(Path(source), Path(arms), Path(empty), cast=cast, moving='--moving' in argv, covered='--covered' in argv, poses=poses,
                  head_hints=hints)
    print(json.dumps(spec, indent=1, default=str))
    print('debug:', debug_image(spec, name))
    if '--build' in argv:
        BB.build(name, spec)


if __name__ == '__main__':
    main(sys.argv[1:])
