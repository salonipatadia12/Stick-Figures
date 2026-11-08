"""Derive a build spec for a base from its three images (docs/PLAN.md §7 D): the approved candidate, the FLUX
plate with the arms removed, and the FLUX plate with the character(s) removed.

  .venvs/mlx/bin/python scripts/motion_test/spec_from_plates.py <name> <source.png> <arms.png> <empty.png>
        [--cast lead,friend] [--moving] [--covered] [--build]

What it reads off the plates, per character:
- the character: where the empty plate differs from the source;
- the head: a big white low-saturation blob at the top of the character, fitted to its outline
  (`build_bases.fit_head` + `refine_head`), and which way it faces (the eyes sit on the facing side);
- the arms: navy limb pixels of the source that the arms plate no longer has, inside the character's column and
  outside the head. Each blob's top gives a shoulder, its far end gives the resting hand; the edit region is their
  hull;
- the torso: the white blob below the head without the arms; its centroid seeds the torso cut, its bottom is the
  hip (only used when --moving: a seated torso that leans; a torso at a table or standing stays painted);
- the scarf: teal under a head marks the friend (accessory 'scarf', with the neck polygon the builder paints).

Two characters: the two highest white blobs that are apart are the heads; everything else is split at the
midpoint between them. The one with the scarf is the friend; with no scarf on either, the left one is the lead.

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


def _heads(white: np.ndarray, n: int) -> list:
    """The n head blobs: the highest big white blobs that sit apart from each other."""
    blobs = _largest(white, keep=6, min_area=2000)
    if not blobs:
        raise RuntimeError('no white head blob found')
    info = []
    for m in blobs:
        ys, xs = np.nonzero(m)
        info.append(dict(m=m, y=ys.mean(), x=xs.mean(), w=xs.max() - xs.min(), area=len(ys)))
    info.sort(key=lambda d: d['y'])                                   # highest first
    picked = []
    for d in info:
        if all(abs(d['x'] - p['x']) > 0.6 * max(d['w'], p['w']) for p in picked):
            picked.append(d)
        if len(picked) == n:
            break
    if len(picked) < n:
        raise RuntimeError(f'found {len(picked)} head blob(s), wanted {n}')
    picked.sort(key=lambda d: d['x'])                                  # left to right
    return [d['m'] for d in picked]


def derive(source: Path, arms: Path, empty: Path, cast: list | None = None, moving: bool = False, covered: bool = False) -> dict:
    cast = cast or ['lead']
    orig = BB.load(source, (941, 1672))
    h, w = orig.shape[:2]
    hsv = cv2.cvtColor(orig, cv2.COLOR_RGB2HSV)
    sat, lum = hsv[..., 1].astype(np.float32), orig.astype(np.float32).mean(-1)
    f = orig.astype(np.int32)
    yy = np.mgrid[0:h, 0:w][0]
    # the character(s)
    empty_p = BB.register(orig, BB.load(empty), np.ones((h, w), bool))
    char = _mask_diff(orig, empty_p, 30)
    char = cv2.morphologyEx(char.astype(np.uint8), cv2.MORPH_CLOSE, BB.disk(9)) > 0
    char = np.logical_or.reduce(_largest(char, keep=2 + len(cast), min_area=3000)) if char.any() else char
    ys, xs = np.nonzero(char)
    if not len(ys):
        raise RuntimeError('no character found: the empty plate equals the source')
    # heads: white blobs high in the character
    white = (sat < 45) & (lum > 165) & char
    white = cv2.morphologyEx(white.astype(np.uint8), cv2.MORPH_OPEN, BB.disk(3)) > 0
    head_blobs = _heads(white, len(cast))
    ells = []
    for hb in head_blobs:
        hy, hx = np.nonzero(hb)
        grow = 26
        box = (max(0, hx.min() - grow), max(0, hy.min() - grow), min(w, hx.max() + grow), min(h, hy.max() + grow))
        ells.append(BB.refine_head(orig, BB.fit_head(orig, box)))
    centres = [e[0][0] for e in ells]
    # columns: each character owns the strip around its head, bounded by the midpoints to the neighbours
    bounds = [0] + [int((centres[i] + centres[i + 1]) / 2) for i in range(len(ells) - 1)] + [w]
    all_heads = np.zeros((h, w), bool)
    for e in ells:
        all_heads |= BB.ellipse_mask(orig.shape, e, 10)
    arms_p = BB.register(orig, BB.load(arms), ~all_heads)
    plate_lum = cv2.GaussianBlur(arms_p.astype(np.float32).mean(-1), (0, 0), 1.5)
    navy = (lum < 95) & (sat < 150) & (f[..., 2] > f[..., 0] + 6)              # navy limbs, not dark wood
    teal = (f[..., 1] > f[..., 0] + 12) & (f[..., 2] > f[..., 0] + 4) & (lum > 50) & (lum < 200) & char
    seats, measured, all_arm = [], [], np.zeros((h, w), bool)
    for i, e in enumerate(ells):
        (cx, cy), (ew, eh), ang = e
        D = (ew + eh) / 2
        head_m = BB.ellipse_mask(orig.shape, e, 0)
        marks = head_m & BB.ellipse_mask(orig.shape, e, -16) & (lum < 90)
        my, mx = np.nonzero(marks)
        facing = 1 if (mx.mean() if len(mx) else cx) >= cx else -1
        column = np.zeros((h, w), bool)
        x0, x1 = max(bounds[i], int(cx - 1.6 * D)), min(bounds[i + 1], int(cx + 1.6 * D))
        column[:, x0:x1] = True
        # the scarf: teal just under the head
        sc = teal & column & (yy > cy + 0.3 * D) & (yy < cy + 0.95 * D)
        sc = cv2.morphologyEx(sc.astype(np.uint8), cv2.MORPH_OPEN, BB.disk(2)) > 0
        scarf = sc.sum() > 0.01 * D * D
        # torso: the white below the head in the column (its bottom is the hip; the arms are bounded by it)
        torso = white & ~BB.ellipse_mask(orig.shape, e, 6) & column & (yy < cy + 2.4 * D)
        torso = cv2.morphologyEx(torso.astype(np.uint8), cv2.MORPH_OPEN, BB.disk(5)) > 0
        tb = _largest(torso, keep=3, min_area=1500)
        if tb:
            ty, tx = np.nonzero(np.logical_or.reduce(tb))
            torso_seed = (int(np.median(tx)), int(np.percentile(ty, 35)))
            hip = (int(np.median(tx[ty > np.percentile(ty, 90)])), int(np.percentile(ty, 97)))
            torso_cx = torso_seed[0]
        else:
            torso_seed = (int(cx), int(cy + 0.9 * D))
            hip = (int(cx), int(cy + 1.6 * D))
            torso_cx = int(cx)
        arm_m = navy & (plate_lum > 118) & column & ~BB.ellipse_mask(orig.shape, e, 10) & (yy < hip[1] + 0.35 * D)
        arm_m = cv2.morphologyEx(arm_m.astype(np.uint8), cv2.MORPH_CLOSE, BB.disk(9)) > 0
        arm_blobs = _largest(arm_m, keep=2, min_area=600)
        for m in arm_blobs:
            all_arm |= m
        shoulders, rests = {}, {}
        if len(arm_blobs) == 1:                                   # arms merged into one blob: split at the torso centre
            m = arm_blobs[0]
            left = m.copy(); left[:, torso_cx:] = False
            right = m.copy(); right[:, :torso_cx] = False
            arm_blobs = [b for b in (left, right) if b.sum() > 600]
        for m in arm_blobs:
            ay, ax = np.nonzero(m)
            top = ay < np.percentile(ay, 12)
            sh = (float(ax[top].mean()), float(ay[top].mean()))
            far = np.argmax((ax - sh[0]) ** 2 + (ay - sh[1]) ** 2)
            hand = (float(ax[far]), float(ay[far]))
            side = 'l' if sh[0] < torso_cx else 'r'
            if side in shoulders:
                continue
            shoulders[side] = (round(sh[0]), round(sh[1]))
            rests[side] = (int(min(w - 1, max(0, hand[0] - 0.04 * D * (1 if hand[0] > sh[0] else -1)))), int(min(h - 1, hand[1] - 0.05 * D)))
        for side in ('l', 'r'):                                    # an arm hidden behind the body: mirror the other
            if side not in shoulders:
                other = 'r' if side == 'l' else 'l'
                if other in shoulders:
                    dx = shoulders[other][0] - torso_cx
                    shoulders[side] = (round(torso_cx - dx), shoulders[other][1])
                    rests[side] = (round(torso_cx - (rests[other][0] - torso_cx)), rests[other][1])
                else:
                    shoulders[side] = (round(cx + (-0.45 if side == 'l' else 0.45) * D), round(cy + 0.62 * D))
                    rests[side] = (round(cx + (-0.6 if side == 'l' else 0.6) * D), round(cy + 1.3 * D))
        seat = dict(name=None, head=dict(centre=(round(cx, 1), round(cy, 1)), size=(round(ew, 1), round(eh, 1)), angle=round(ang, 1), refine=False),
                    facing=facing, torso_seed=torso_seed, hip=hip if (moving and not covered) else None, rest=rests)
        if scarf:
            sy_, sx_ = np.nonzero(sc)
            top_y, bot_y = int(np.percentile(sy_, 5)), int(np.percentile(sy_, 95))
            xl, xr = int(np.percentile(sx_, 3)), int(np.percentile(sx_, 97))
            seat.update(accessory='scarf', head_grow=6,
                        scarf_neck=dict(poly=[(xl + 6, bot_y), (xl + 18, top_y), (xr - 18, top_y), (xr - 6, bot_y)],
                                        colour_at=(int(np.median(sx_)), int(np.median(sy_)))))
        if covered:
            seat.update(torso='covered', shoulders=shoulders)
        seats.append(seat)
        measured.append(dict(shoulders=shoulders, D=round(D, 1), scarf=bool(scarf)))
    # names: the scarf marks the friend; otherwise left to right in the order given
    names = list(cast)
    if len(cast) == 2 and 'friend' in cast and 'lead' in cast:
        scarves = [m['scarf'] for m in measured]
        if scarves.count(True) == 1:
            names = ['friend', 'lead'] if scarves[0] else ['lead', 'friend']
        else:
            names = ['lead', 'friend']
    for s, n in zip(seats, names):
        s['name'] = n
    ay, ax = np.nonzero(all_arm) if all_arm.any() else (np.array([int(ells[0][0][1])]), np.array([int(ells[0][0][0])]))
    pad = 40
    roi = [(max(0, int(ax.min()) - pad), max(0, int(ay.min()) - pad)), (min(w, int(ax.max()) + pad), max(0, int(ay.min()) - pad)),
           (min(w, int(ax.max()) + pad), min(h, int(ay.max()) + pad)), (max(0, int(ax.min()) - pad), min(h, int(ay.max()) + pad))]
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
        for side, xy in m['shoulders'].items():
            d.ellipse((xy[0] - 8, xy[1] - 8, xy[0] + 8, xy[1] + 8), fill=(40, 120, 255))
        for side, xy in s['rest'].items():
            d.ellipse((xy[0] - 10, xy[1] - 10, xy[0] + 10, xy[1] + 10), outline=(255, 200, 0), width=4)
            d.text((xy[0] + 12, xy[1]), side, fill=(255, 200, 0))
        d.ellipse((s['torso_seed'][0] - 6, s['torso_seed'][1] - 6, s['torso_seed'][0] + 6, s['torso_seed'][1] + 6), fill=(255, 255, 255))
        if s.get('hip'):
            d.ellipse((s['hip'][0] - 8, s['hip'][1] - 8, s['hip'][0] + 8, s['hip'][1] + 8), fill=(200, 60, 200))
        if s.get('scarf_neck'):
            d.polygon(s['scarf_neck']['poly'], outline=(0, 200, 200), width=2)
        d.text((cx - 30, cy - eh / 2 - 18), f"{s['name']} facing {s['facing']} D {m['D']}", fill=(255, 255, 255))
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
    spec = derive(Path(source), Path(arms), Path(empty), cast=cast, moving='--moving' in argv, covered='--covered' in argv)
    print(json.dumps(spec, indent=1, default=str))
    print('debug:', debug_image(spec, name))
    if '--build' in argv:
        BB.build(name, spec)


if __name__ == '__main__':
    main(sys.argv[1:])
