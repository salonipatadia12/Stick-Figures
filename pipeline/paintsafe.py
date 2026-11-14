"""Safe FLUX output for every paint path: inserts (scenes), props, base candidates and plates.

2026-11-14: "Explain why sunsets are colorful" died at paint with `Error: unknown file extension:`. A split beat
carried an insert with an empty key, the scene went to FLUX as `scene-` and was then saved as `<dir>/.png`, which
PIL reads as a hidden file with no extension. One bad scene stopped the whole episode. From then on:

* every FLUX file name is sanitised and always ends in `.png` (`flux_png`, `png_path`);
* every saved file is opened with PIL before it counts (`is_image`), and a file that does not open is painted
  again once (`save_checked`, used inside the FLUX scripts);
* a scene or prop that will not paint goes through a fallback chain in its step: a new seed, then a simplified
  prompt (`simplify`), then a placeholder (`placeholder_scene`, `placeholder_prop`) flagged `placeholder: true`
  in its library entry, so the video still renders and the QA report names the beat (`pipeline.qa.placeholder_report`).

Standard library and Pillow only: the FLUX scripts load this file by path from the MLX env (`load()`), which has no
pydantic and must not import the rest of the pipeline.
"""
from __future__ import annotations

import hashlib
import re
import textwrap
from pathlib import Path

SAFE = re.compile(r'[^A-Za-z0-9_-]+')
FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'
SCENE_SIZE = (944, 1680)                 # the bases' and inserts' size (flux_scenes.py)


def safe_stem(name, fallback: str = 'unnamed') -> str:
    """A file name stem with no path separators, dots, spaces or other surprises. Kebab-case names are unchanged
    (so cached FLUX output keeps its name); an empty name becomes `<fallback>-<digest>` instead of nothing."""
    stem = SAFE.sub('-', str(name or '')).strip('-_')
    if not stem:
        stem = f'{fallback}-{hashlib.sha1(repr(name).encode()).hexdigest()[:8]}'
    return stem[:150]


def png_path(folder, stem) -> Path:
    """`folder/<safe stem>.png`: a stem that already ends in an image extension loses it first."""
    stem = re.sub(r'\.(png|jpe?g|webp|gif|bmp|tiff?)$', '', str(stem or ''), flags=re.I)
    return Path(folder) / f'{safe_stem(stem)}.png'


def flux_png(folder, job, seed) -> Path:
    """Where FLUX writes job `job` at seed `seed`: the one name the FLUX scripts and their callers both use."""
    return png_path(folder, f'{safe_stem(job)}-s{int(seed)}')


def is_image(path, min_side: int = 16) -> bool:
    """True when the file exists, is not empty, and Pillow can decode the whole of it."""
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size == 0:
            return False
        from PIL import Image
        with Image.open(p) as im:
            im.verify()
        with Image.open(p) as im:                    # verify() leaves the image unusable: open again to decode
            im.load()
            return min(im.size) >= min_side
    except Exception:
        return False


def save_checked(paint, dst, tries: int = 2, log=print) -> bool:
    """Call `paint(dst)` (generate + save) until `dst` is a readable image, at most `tries` times. A FLUX error or a
    file that does not decode is logged and painted again; never raises. True when a good file is on disk."""
    dst = Path(dst)
    for n in range(1, tries + 1):
        try:
            paint(dst)
        except Exception as e:                       # one bad image must not stop the other jobs in the batch
            log(f'{dst.name}: FLUX error on try {n}/{tries}: {type(e).__name__}: {e}')
        if is_image(dst):
            return True
        if dst.exists():
            log(f'{dst.name}: not a readable image on try {n}/{tries}; painting it again' if n < tries
                else f'{dst.name}: not a readable image after {tries} tries; left out')
            dst.unlink(missing_ok=True)
    return False


def load():
    """This module, for a script that runs outside the pipeline's env (the FLUX scripts in .venvs/mlx)."""
    import importlib.util
    import sys
    if 'stick_paintsafe' in sys.modules:
        return sys.modules['stick_paintsafe']
    spec = importlib.util.spec_from_file_location('stick_paintsafe', Path(__file__))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['stick_paintsafe'] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- the simplified prompt (second retry) -----------------------------------------------------------------
DESCRIPTIVE = set('''
a an the very really quite softly gently slightly
beautiful lovely pretty stunning gorgeous dramatic striking vivid vibrant bold rich lush intricate detailed elaborate
ornate subtle delicate faint pale deep dark dim bright brilliant glowing gleaming shimmering sparkling shiny glossy
soft warm cool cold hot cozy cosy gentle calm serene peaceful quiet busy bustling crowded empty lonely
huge giant gigantic enormous massive vast tall tiny small little large big wide narrow thin thick long short
old ancient aged weathered worn rustic modern futuristic new fresh clean dirty dusty rusty muddy
misty hazy foggy smoky cloudy sunny golden silver colorful colourful multicolored multicoloured painted
semi-transparent transparent translucent see-through glowing-red fading swirling floating drifting flowing
red orange yellow green blue purple violet pink crimson scarlet amber teal turquoise navy cream brown grey gray
black white
'''.split())
CLAUSE = re.compile(r'[,;:()]|\s(?:with|while|as|where|that|which|showing|under|beneath|against|and|but)\s', re.I)


def simplify(text: str, max_words: int = 8) -> str:
    """The core of a scene or prop description: its first clause with the describing words taken out
    ("a painted sky with a semi-transparent ozone layer graphic ..." -> "sky"). Never empty: falls back to the
    first few words of the original."""
    text = ' '.join(str(text or '').split())
    head = CLAUSE.split(text, maxsplit=1)[0]
    words = [w for w in head.split() if w.lower().strip('.,!?"\'') not in DESCRIPTIVE]
    core = ' '.join(words[:max_words]).strip(' .,')
    return core or ' '.join(text.split()[:max_words]).strip(' .,') or 'an empty place'


# ---- placeholders (after both retries) ----------------------------------------------------------------------
NIGHT = re.compile(r'\b(night|midnight|dark|darkness|space|outer space|stars?|starry|galaxy|galaxies|moon|planets?|'
                   r'orbit|cave|underground|abyss|deep sea|nebula|cosmos|universe)\b', re.I)
SUNSET = re.compile(r'\b(sunsets?|sunrise|dusk|dawn|twilight|evening glow|fire|flames?|lava|volcano|desert|ember)\b', re.I)
SKY = re.compile(r'\b(sky|skies|clouds?|atmosphere|air|ocean|sea|seas|water|underwater|river|lake|waves?|rain|storm|'
                 r'ice|snow|glacier|arctic|winter|weather|horizon)\b', re.I)
MOODS = {                                # top colour, bottom colour, ink
    'night': ((22, 26, 54), (58, 46, 92), (236, 232, 248)),
    'sunset': ((246, 168, 92), (122, 62, 108), (255, 248, 236)),
    'sky': ((120, 178, 226), (214, 234, 246), (24, 42, 74)),
    'warm': ((246, 222, 180), (206, 146, 102), (52, 34, 26)),        # interiors and everything else
}


def mood(scene: str) -> str:
    """'night', 'sunset', 'sky' or 'warm' (interiors and anything else), read from the scene description."""
    s = str(scene or '')
    for name, rx in (('night', NIGHT), ('sunset', SUNSET), ('sky', SKY)):
        if rx.search(s):
            return name
    return 'warm'


def _font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default(size)


def placeholder_scene(dst, scene: str, size=SCENE_SIZE) -> Path:
    """A vertical gradient in the scene's mood with what the scene would show written on it. Written as a PNG at
    `dst` (forced to .png); the text sits in the middle 70 % so camera pushes and the host do not cut it."""
    from PIL import Image, ImageDraw
    w, h = size
    top, bottom, ink = MOODS[mood(scene)]
    im = Image.new('RGB', (w, h))
    px = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        px.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    d = ImageDraw.Draw(im)
    title, body = _font(40), _font(48)
    lines = textwrap.wrap(' '.join(str(scene or 'a scene').split()), width=24)[:6]
    y = int(h * 0.36)
    d.text((w // 2, y), 'SCENE PLACEHOLDER', font=title, fill=ink, anchor='mm')
    y += 80
    for line in lines:
        d.text((w // 2, y), line, font=body, fill=ink, anchor='mm')
        y += 62
    dst = png_path(Path(dst).parent, Path(dst).name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)
    return dst


def placeholder_prop(name: str, size: int = 512):
    """A cut-out sprite (RGBA) for a prop that will not paint: a cream card with a navy outline and the prop's name."""
    from PIL import Image, ImageDraw
    im = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = size // 10
    d.rounded_rectangle((m, m * 2, size - m, size - m * 2), radius=size // 12, fill=(246, 238, 222, 255),
                        outline=(30, 34, 64, 255), width=max(4, size // 64))
    lines = textwrap.wrap(' '.join(str(name or 'prop').split()), width=12)[:3]
    f = _font(max(28, size // 9))
    y = size // 2 - (len(lines) - 1) * size // 16
    for line in lines:
        d.text((size // 2, y), line, font=f, fill=(30, 34, 64, 255), anchor='mm')
        y += size // 8
    return im
