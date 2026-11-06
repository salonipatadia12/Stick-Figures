"""Build painted base poses from The Empty Chair frames (like its motion edition: original art kept,
clean plates revealed only where a moving part used to be).

For each base:
- plate.png     the original frame; inside the old arms/hands/props it shows the FLUX clean plate, and
                inside the old heads the empty café (plus a neck, so a moving head never comes loose).
- table.png     the round table cut from the plate, laid over a hand that goes under the table.
- front.png     things on the table that sit in front of the arms (the cups).
- occluder.png  the torso around each shoulder, laid over the arm roots so the arms come out from
                behind the torso side, as the Empty Chair sleeves do.
- base.json     anchors in plate pixels: heads, shoulders, rest hands, tabletop, wall spot, light.

Runs in .venvs/mlx (OpenCV):  .venvs/mlx/bin/python scripts/motion_test/build_bases.py [name ...]
FLUX edits come from scripts/motion_test/flux_bases.py (renders/bases/flux).
"""
from __future__ import annotations

import json
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

# Anchors are measured on the plates (see renders/bases/debug-*.jpg). Units: plate pixels (941x1672).
BASES = {
    'lead-at-table': dict(
        source='04-waiting.png', arms='plate-04-arms-s11.png', empty='plate-04-empty-s6.png',
        edit_roi=[(28, 688), (612, 688), (612, 990), (28, 990)],
        keep=[('ellipse', (66, 860, 210, 992))],                      # the cup: original pixels
        front=[('ellipse', (66, 862, 206, 990))],                     # the cup sits in front of the arms
        table=dict(top=(52, 840, 690, 1062), stem=[(352, 1040), (392, 1040), (396, 1452), (348, 1452)],
                   foot=(222, 1448, 534, 1540)),
        wall=(458, 196, 668, 470),                                   # where the calendar hangs
        seats=[dict(name='lead', head_box=(120, 395, 485, 720), facing=1, shade='left',
                    torso_seed=(240, 800),
                    shoulders=dict(l=(212, 734), r=(296, 736)),
                    rest=dict(l=(236, 902), r=(392, 878)))],
        light='right'),
    'together': dict(
        source='03-together.png', arms='plate-03-arms-b-s6.png', empty='plate-03-empty-s7.png',
        edit_roi=[(40, 700), (900, 700), (900, 1010), (40, 1010)],
        keep=[('ellipse', (164, 862, 312, 975)), ('ellipse', (594, 862, 734, 965))],
        front=[('ellipse', (166, 866, 310, 972)), ('ellipse', (596, 866, 732, 962))],
        table=dict(top=(88, 880, 800, 1080), stem=[(410, 1060), (446, 1060), (448, 1480), (408, 1480)],
                   foot=(286, 1440, 560, 1530)),
        wall=None,
        seats=[dict(name='lead', head_box=(90, 430, 410, 730), facing=1, shade='left', torso_seed=(220, 820),
                    shoulders=dict(l=(178, 750), r=(292, 752)), rest=dict(l=(318, 912), r=(468, 900))),
               dict(name='friend', head_box=(480, 400, 830, 720), facing=-1, shade='left', accessory='scarf', head_grow=6,
                    torso_seed=(720, 880), scarf_neck=dict(poly=[(632, 716), (644, 612), (722, 612), (738, 716)], colour_at=(690, 696)),
                    shoulders=dict(l=(598, 752), r=(790, 754)), rest=dict(l=(556, 904), r=(772, 918)))],
        light='right'),
}


def load(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert('RGB'))


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
    """The painted head's outline as an ellipse ((cx, cy), (width, height), angle), fitted to its dark ring.
    Heads are slightly wider than tall in three-quarter view, so a circle would leave the old outline showing."""
    x0, y0, x1, y1 = box
    crop = im[y0:y1, x0:x1].astype(np.float32)
    dark = crop.mean(-1) < 70
    ys, xs = np.nonzero(dark)
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


def ellipse_mask(shape, ell, grow: float = 0.0) -> np.ndarray:
    (cx, cy), (ew, eh), ang = ell
    m = np.zeros(shape[:2], np.uint8)
    cv2.ellipse(m, ((cx, cy), (ew + 2 * grow, eh + 2 * grow), ang), 255, -1)
    return m > 0


def head_sprite(orig: np.ndarray, ell) -> tuple[Image.Image, list]:
    """The painted head with its eyes, brows and mouth painted out (the shading and outline are kept),
    so the face can be animated on top. Returns the sprite and its centre in sprite pixels."""
    (cx, cy), (ew, eh), ang = ell
    inner = ellipse_mask(orig.shape, ell, -3.8)          # the outline's inner edge: brow ends that touch it go too
    outer = ellipse_mask(orig.shape, ell, 7)            # the whole outline, with its warm outer edge
    f = orig.astype(np.float32)
    lum = f.mean(-1)
    sat = f.max(-1) - f.min(-1)
    marks = inner & ((lum < 175) | (sat > 48))
    marks = cv2.dilate(marks.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    marks[~inner] = 0                                   # never touch the outline itself
    # Inpaint from the head's own shading only: outside the interior, show its white, so the dark
    # outline can't bleed into the fill.
    work = orig.copy()
    work[~inner] = np.median(orig[inner & (marks == 0)].reshape(-1, 3), 0).astype(np.uint8)
    clean = cv2.inpaint(work, marks, 7, cv2.INPAINT_TELEA)
    clean = cv2.inpaint(clean, cv2.erode(marks, np.ones((3, 3), np.uint8)), 4, cv2.INPAINT_NS)
    clean[~inner] = orig[~inner]
    alpha = cv2.GaussianBlur(outer.astype(np.float32), (0, 0), 0.9)
    x0, y0 = int(cx - max(ew, eh) / 2 - 12), int(cy - max(ew, eh) / 2 - 12)
    x1, y1 = int(cx + max(ew, eh) / 2 + 13), int(cy + max(ew, eh) / 2 + 13)
    rgba = np.dstack([clean, (alpha * 255).astype(np.uint8)])[y0:y1, x0:x1]
    return Image.fromarray(rgba, 'RGBA'), [round(cx - x0, 2), round(cy - y0, 2)]


def shape_mask(size, shapes, fill=255) -> Image.Image:
    m = Image.new('L', size, 0)
    d = ImageDraw.Draw(m)
    for kind, geo in shapes:
        if kind == 'ellipse':
            d.ellipse(geo, fill=fill)
        else:
            d.polygon(geo, fill=fill)
    return m


def feather(mask: np.ndarray, grow: int, soft: float) -> np.ndarray:
    m = mask.astype(np.uint8) * 255
    if grow:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
    if soft:
        m = cv2.GaussianBlur(m, (0, 0), soft)
    return m.astype(np.float32) / 255


def build(name: str, spec: dict):
    print(name)
    orig = load(EC / spec['source'])
    h, w = orig.shape[:2]
    size = (w, h)
    ells = [fit_head(orig, s['head_box']) for s in spec['seats']]
    heads = [(e[0], (e[1][0] + e[1][1]) / 4) for e in ells]          # centre and mean radius
    for s, e in zip(spec['seats'], ells):
        print(f"    {s['name']}: head centre ({e[0][0]:.1f},{e[0][1]:.1f}) size {e[1][0]:.0f}x{e[1][1]:.0f} angle {e[2]:.0f}")

    # 1. Where the arms, hands and props were: the FLUX plate differs strongly from the original there.
    roi = np.asarray(shape_mask(size, [('polygon', spec['edit_roi'])])) > 0
    head_disc = np.zeros((h, w), bool)
    yy, xx = np.mgrid[0:h, 0:w]
    for s_, e in zip(spec['seats'], ells):
        # the painted head up to the outer edge of its outline (less where a scarf sits right under it)
        head_disc |= ellipse_mask(orig.shape, e, s_.get('head_grow', 12))
    arms = register(orig, load(FLUX / spec['arms']), ~(roi | head_disc))
    diff = cv2.GaussianBlur(np.abs(orig.astype(np.float32) - arms.astype(np.float32)).mean(-1), (0, 0), 2.5)
    edit = (diff > 34) & roi & ~head_disc
    edit = cv2.morphologyEx(edit.astype(np.uint8), cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))) > 0
    n, lab, stats, _ = cv2.connectedComponentsWithStats(edit.astype(np.uint8))
    for i in range(1, n):                       # drop specks of re-painted texture
        if stats[i, cv2.CC_STAT_AREA] < 400:
            edit[lab == i] = False
    keep = np.asarray(shape_mask(size, spec['keep'])) > 0
    edit &= ~keep
    a = feather(edit, 7, 3.0)[..., None]
    plate = orig.astype(np.float32) * (1 - a) + arms.astype(np.float32) * a

    # 2. The heads: the empty café behind, then a neck continuing the torso up under the head.
    empty = register(orig, load(FLUX / spec['empty']), ~(roi | head_disc))
    hm = feather(head_disc, 1, 1.2)[..., None]
    plate = plate * (1 - hm) + empty.astype(np.float32) * hm
    plate_im = Image.fromarray(np.clip(plate, 0, 255).astype(np.uint8))
    navy = (24, 26, 50)
    for s, e, (c, r) in zip(spec['seats'], ells, heads):
        neck = Image.new('RGBA', size, (0, 0, 0, 0))
        d = ImageDraw.Draw(neck)
        lw = max(4, round(0.024 * 2 * r))
        if 'scarf_neck' in s:
            # The scarf wraps the neck: under the head it continues in the scarf's own colours.
            poly, at = s['scarf_neck']['poly'], s['scarf_neck']['colour_at']
            teal = tuple(int(v) for v in np.median(orig[at[1] - 4:at[1] + 5, at[0] - 6:at[0] + 7].reshape(-1, 3), 0))
            d.polygon(poly, fill=teal)
            dark = tuple(int(v * 0.7) for v in teal)
            d.line([poly[0], poly[1]], fill=dark, width=lw)
            d.line([poly[3], poly[2]], fill=dark, width=lw)
            s['neck_fit'] = dict(scarf=poly)
        else:
            # The torso's outer edges just below the head, extended up under it as a neck.
            ff = arms.copy()
            m = np.zeros((h + 2, w + 2), np.uint8)
            cv2.floodFill(ff, m, s['torso_seed'], (255, 0, 255), (58, 58, 58), (16, 16, 16), cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8) | 4)
            clear = (m[1:-1, 1:-1] > 0) & ~ellipse_mask(orig.shape, e, 14)
            rows = []
            for y in range(int(c[1]), h):
                xs_ = np.nonzero(clear[y])[0]
                if len(xs_) and xs_.max() - xs_.min() > 0.25 * 2 * r and xs_.max() - xs_.min() + 1 - len(xs_) < 6:
                    rows.append((y, xs_.min(), xs_.max()))     # a full torso row, clear of the head
                if len(rows) >= 40:
                    break
            edges = np.array(rows, float)
            y_j = edges[:, 0].min()
            kl = np.polyfit(edges[:, 0], edges[:, 1], 1)
            kr = np.polyfit(edges[:, 0], edges[:, 2], 1)
            top, yb = c[1] + 0.15 * 2 * r, y_j + 6
            L = [(np.polyval(kl, yb) - lw / 2, yb), (np.polyval(kl, top) - lw / 2, top)]
            R = [(np.polyval(kr, yb) + lw / 2, yb), (np.polyval(kr, top) + lw / 2, top)]
            white = tuple(int(v) for v in np.median(arms[int(y_j) + 4:int(y_j) + 16, int(np.polyval(kl, y_j)) + 14:int(np.polyval(kr, y_j)) - 14].reshape(-1, 3), 0))
            d.polygon([L[0], L[1], R[1], R[0]], fill=white)
            d.line(L, fill=navy, width=lw)
            d.line(R, fill=navy, width=lw)
            s['neck_fit'] = dict(join_y=round(y_j, 1), left=[[round(v, 1) for v in p] for p in L], right=[[round(v, 1) for v in p] for p in R])
        clip = Image.fromarray((feather(ellipse_mask(orig.shape, e, s.get('head_grow', 12) + 2), 0, 1.0) * 255).astype(np.uint8))
        neck.putalpha(Image.fromarray(np.minimum(np.asarray(neck.getchannel('A')), np.asarray(clip))))
        plate_im = Image.alpha_composite(plate_im.convert('RGBA'), neck).convert('RGB')
    plate = np.asarray(plate_im)

    # 3. Layers cut from the plate: the table, the cups in front, the torso at the shoulders.
    t = spec['table']
    table_m = shape_mask(size, [('ellipse', t['top']), ('polygon', t['stem']), ('ellipse', t['foot'])])
    table = Image.fromarray(plate).convert('RGBA')
    table.putalpha(table_m.filter(ImageFilter.GaussianBlur(1.2)))
    front_m = shape_mask(size, spec['front'])
    front = Image.fromarray(plate).convert('RGBA')
    front.putalpha(front_m.filter(ImageFilter.GaussianBlur(1.0)))

    occ = np.zeros((h, w), np.float32)
    lum = plate.astype(np.float32).mean(-1)
    navyish = (lum < 120) & (plate[..., 2].astype(int) >= plate[..., 0].astype(int) - 6)
    for s, (c, r) in zip(spec['seats'], heads):
        ff = arms.copy()                      # the head is still there, so its outline closes the torso off
        mask = np.zeros((h + 2, w + 2), np.uint8)
        cv2.floodFill(ff, mask, s['torso_seed'], (255, 0, 255), (58, 58, 58), (16, 16, 16), cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8) | 4)
        torso = mask[1:-1, 1:-1] > 0
        torso = cv2.dilate(torso.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))) > 0
        torso &= ((lum > 165) | navyish) & ~head_disc
        for side in ('l', 'r'):
            sx, sy = s['shoulders'][side]
            disc = np.hypot(xx - sx, yy - sy) < 0.15 * 2 * r
            occ = np.maximum(occ, (torso & disc).astype(np.float32))
    occ = cv2.GaussianBlur(occ, (0, 0), 0.8)
    occluder = Image.fromarray(plate).convert('RGBA')
    occluder.putalpha(Image.fromarray((occ * 255).astype(np.uint8)))

    out = OUT / name
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(plate).save(out / 'plate.png', optimize=True)
    for n_, layer in (('table', table), ('front', front), ('occluder', occluder)):
        bbox = layer.getchannel('A').getbbox()
        layer.crop(bbox).save(out / f'{n_}.png', optimize=True)
        spec.setdefault('layers', {})[n_] = list(bbox[:2])
    heads_meta = []
    for s, e in zip(spec['seats'], ells):
        sprite, centre = head_sprite(orig, e)
        sprite.save(out / f"head-{s['name']}.png", optimize=True)
        heads_meta.append(dict(sprite=f"head-{s['name']}.png", centre=centre, size=[round(e[1][0], 1), round(e[1][1], 1)],
                               angle=round(e[2], 1)))
    meta = dict(name=name, source=f'legacy/bespoke/the-empty-chair-source/assets/{spec["source"]}', size=[w, h],
                flux=dict(arms=spec['arms'], empty=spec['empty']), light=spec['light'], wall=spec['wall'],
                table=dict(top=t['top'], surface_y=round((t['top'][1] + t['top'][3]) / 2)), layers=spec['layers'],
                seats=[dict(name=s['name'], head=[round(c[0], 1), round(c[1], 1)], D=round(2 * r, 1), head_art=hm_,
                            facing=s['facing'], shade=s['shade'], accessory=s.get('accessory', ''), shoulders=s['shoulders'],
                            rest=s['rest'], neck=s['neck_fit'])
                       for s, (c, r), hm_ in zip(spec['seats'], heads, heads_meta)])
    (out / 'base.json').write_text(json.dumps(meta, indent=1) + '\n')

    # Debug overlay: masks and anchors on a grid.
    dbg = Image.fromarray(plate).convert('RGBA')
    tint = Image.new('RGBA', size, (255, 0, 80, 0))
    tint.putalpha(Image.fromarray((edit * 90).astype(np.uint8)))
    dbg.alpha_composite(tint)
    tint = Image.new('RGBA', size, (0, 120, 255, 0))
    tint.putalpha(Image.fromarray((occ * 140).astype(np.uint8)))
    dbg.alpha_composite(tint)
    dd = ImageDraw.Draw(dbg)
    for x in range(0, w, 50):
        dd.line([(x, 0), (x, h)], fill=(255, 255, 255, 50))
        dd.text((x + 2, 2), str(x), fill='red')
    for y in range(0, h, 50):
        dd.line([(0, y), (w, y)], fill=(255, 255, 255, 50))
        dd.text((2, y + 2), str(y), fill='red')
    dd.ellipse(t['top'], outline=(0, 255, 0), width=2)
    dd.polygon(t['stem'], outline=(0, 255, 0))
    dd.ellipse(t['foot'], outline=(0, 255, 0), width=2)
    for kind, geo in spec['front']:
        dd.ellipse(geo, outline=(255, 255, 0), width=2)
    if spec['wall']:
        dd.rectangle(spec['wall'], outline=(255, 128, 0), width=3)
    for s, (c, r) in zip(spec['seats'], heads):
        dd.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), outline=(0, 255, 255), width=1)
        for side, col in (('l', (255, 0, 0)), ('r', (0, 0, 255))):
            for key in ('shoulders', 'rest'):
                x, y = s[key][side]
                dd.ellipse((x - 6, y - 6, x + 6, y + 6), fill=col)
    DEBUG.mkdir(parents=True, exist_ok=True)
    dbg.convert('RGB').save(DEBUG / f'debug-{name}.jpg', quality=90)
    print('   ', out.relative_to(ROOT))


if __name__ == '__main__':
    for n in (sys.argv[1:] or list(BASES)):
        build(n, BASES[n])
