"""Derive a build spec for a base from its three images (docs/PLAN.md §7 D): the approved candidate, the FLUX
plate with the arms removed, and the FLUX plate with the character removed.

  .venvs/mlx/bin/python scripts/motion_test/spec_from_plates.py <name> <source.png> <arms.png> <empty.png> [--build]

What it reads off the plates:
- the character: where the empty plate differs from the source;
- the head: the big white low-saturation blob at the top of the character, fitted to its outline
  (`build_bases.fit_head` + `refine_head`), and which way it faces (the eyes sit on the facing side);
- the arms: where the arms plate differs from the source, inside the character and outside the head. Each
  blob's top gives a shoulder, its far end gives the resting hand; the edit region is their hull;
- the torso: the white blob below the head without the arms; its centroid seeds the torso cut, its bottom is
  the hip (a seated or standing character; for a covered torso pass --covered).

It prints the spec, writes a debug overlay to renders/bases/debug/<name>-spec.jpg, and with --build runs
`build_bases.build` with it. A hand-written spec in build_bases.SPECS always wins; this is for the pack bases.

Checked against two approved bases (2026-11-08): on `lead-standing` (arms at the sides) the head, torso seed,
hip and resting hands match the hand-written spec within a few pixels; on `friend-at-table` (arms crossed in
front) the head and torso are right but the hands are not told apart, so a base with crossed or overlapping
arms still needs its `rest` points written by hand. Always look at the debug overlay before building.
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


def derive(source: Path, arms: Path, empty: Path, covered: bool = False) -> dict:
    orig = BB.load(source, (941, 1672))
    h, w = orig.shape[:2]
    hsv = cv2.cvtColor(orig, cv2.COLOR_RGB2HSV) if orig.shape[2] == 3 else None
    sat, lum = hsv[..., 1].astype(np.float32), orig.astype(np.float32).mean(-1)
    # the character
    empty_p = BB.register(orig, BB.load(empty), np.ones((h, w), bool))
    char = _mask_diff(orig, empty_p, 30)
    char = cv2.morphologyEx(char.astype(np.uint8), cv2.MORPH_CLOSE, BB.disk(9)) > 0
    char = np.logical_or.reduce(_largest(char, keep=2, min_area=3000)) if char.any() else char
    ys, xs = np.nonzero(char)
    if not len(ys):
        raise RuntimeError('no character found: the empty plate equals the source')
    # the head: white blob high in the character
    white = (sat < 45) & (lum > 165) & char
    white = cv2.morphologyEx(white.astype(np.uint8), cv2.MORPH_OPEN, BB.disk(3)) > 0
    blobs = _largest(white, keep=3, min_area=2000)
    if not blobs:
        raise RuntimeError('no white head blob found')
    head_blob = min(blobs, key=lambda m: np.nonzero(m)[0].mean())          # the highest one
    hy, hx = np.nonzero(head_blob)
    grow = 26
    box = (max(0, hx.min() - grow), max(0, hy.min() - grow), min(w, hx.max() + grow), min(h, hy.max() + grow))
    ell = BB.refine_head(orig, BB.fit_head(orig, box))
    (cx, cy), (ew, eh), ang = ell
    D = (ew + eh) / 2
    head_m = BB.ellipse_mask(orig.shape, ell, 0)
    # facing: the eyes (dark marks inside the head) sit on the facing side
    marks = head_m & BB.ellipse_mask(orig.shape, ell, -16) & (lum < 90)
    my, mx = np.nonzero(marks)
    facing = 1 if (mx.mean() if len(mx) else cx) >= cx else -1
    # the arms: navy limb pixels in the source that the arms plate no longer has (FLUX redraws everything a
    # little, so the colour test keeps the mask to the limbs themselves)
    arms_p = BB.register(orig, BB.load(arms), ~BB.ellipse_mask(orig.shape, ell, 20))
    navy = (lum < 95) & (sat < 150) & (orig[..., 2].astype(int) > orig[..., 0].astype(int) + 6)   # navy limbs, not dark wood
    column = np.zeros((h, w), bool)
    column[:, max(0, int(cx - 1.6 * D)):min(w, int(cx + 1.6 * D))] = True
    plate_lum = cv2.GaussianBlur(arms_p.astype(np.float32).mean(-1), (0, 0), 1.5)
    arm_m = navy & (plate_lum > 118) & column & ~BB.ellipse_mask(orig.shape, ell, 10)   # navy here, not navy on the plate
    arm_m &= (yy_ := np.mgrid[0:h, 0:w][0]) < cy + 2.2 * D                     # not the legs
    arm_m = cv2.morphologyEx(arm_m.astype(np.uint8), cv2.MORPH_CLOSE, BB.disk(9)) > 0
    arm_blobs = _largest(arm_m, keep=2, min_area=600)
    # torso: the white below the head in the character's column, down to the seat, arms excluded
    torso = white & ~BB.ellipse_mask(orig.shape, ell, 6) & ~arm_m & column & (yy_ < cy + 2.4 * D)
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
    # shoulders and resting hands from the arm blobs
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
        if side in shoulders:                                  # two blobs on one side: keep the larger
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
    ay, ax = np.nonzero(arm_m) if arm_m.any() else (np.array([int(cy)]), np.array([int(cx)]))
    pad = 40
    roi = [(max(0, int(ax.min()) - pad), max(0, int(ay.min()) - pad)), (min(w, int(ax.max()) + pad), max(0, int(ay.min()) - pad)),
           (min(w, int(ax.max()) + pad), min(h, int(ay.max()) + pad)), (max(0, int(ax.min()) - pad), min(h, int(ay.max()) + pad))]
    seat = dict(name='lead', head=dict(centre=(round(cx, 1), round(cy, 1)), size=(round(ew, 1), round(eh, 1)), angle=round(ang, 1), refine=False),
                facing=facing, torso_seed=torso_seed, hip=None if covered else hip, rest=rests)
    if covered:
        seat.update(torso='covered', shoulders=shoulders)
    spec = dict(source=str(source), arms=str(arms), empty=str(empty), edit_roi=roi, seats=[seat], light='right', derived=True,
                measured=dict(shoulders=shoulders, D=round(D, 1), character_box=[int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]))
    return spec


def debug_image(spec: dict, name: str) -> Path:
    from PIL import Image, ImageDraw
    im = Image.open(spec['source']).convert('RGB').resize((941, 1672))
    d = ImageDraw.Draw(im)
    s = spec['seats'][0]
    (cx, cy), (ew, eh) = s['head']['centre'], s['head']['size']
    d.ellipse((cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2), outline=(255, 40, 40), width=3)
    d.polygon(spec['edit_roi'], outline=(40, 200, 90), width=3)
    for side, xy in spec['measured']['shoulders'].items():
        d.ellipse((xy[0] - 8, xy[1] - 8, xy[0] + 8, xy[1] + 8), fill=(40, 120, 255))
    for side, xy in s['rest'].items():
        d.ellipse((xy[0] - 10, xy[1] - 10, xy[0] + 10, xy[1] + 10), outline=(255, 200, 0), width=4)
        d.text((xy[0] + 12, xy[1]), side, fill=(255, 200, 0))
    d.ellipse((s['torso_seed'][0] - 6, s['torso_seed'][1] - 6, s['torso_seed'][0] + 6, s['torso_seed'][1] + 6), fill=(255, 255, 255))
    if s.get('hip'):
        d.ellipse((s['hip'][0] - 8, s['hip'][1] - 8, s['hip'][0] + 8, s['hip'][1] + 8), fill=(200, 60, 200))
    d.text((12, 12), f"{name}: facing {s['facing']}, D {spec['measured']['D']}", fill=(255, 255, 255))
    DEBUG.mkdir(parents=True, exist_ok=True)
    out = DEBUG / f'{name}-spec.jpg'
    im.save(out, quality=88)
    return out


def main(argv):
    if len(argv) < 4:
        sys.exit(__doc__)
    name, source, arms, empty = argv[:4]
    covered = '--covered' in argv
    spec = derive(Path(source), Path(arms), Path(empty), covered)
    print(json.dumps(spec, indent=1, default=str))
    print('debug:', debug_image(spec, name))
    if '--build' in argv:
        BB.build(name, spec)


if __name__ == '__main__':
    main(sys.argv[1:])
