"""Side-by-side style board: an Empty Chair frame next to each engine's version of the same beat.
Usage: board.py OUT_DIR OUT_IMAGE ENGINE_TAG:LABEL [ENGINE_TAG:LABEL ...]
"""
import json, sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'
ROWS = [('b01', 'Hook (b01)', '01-alone'), ('b06', 'Mid beat (b06)', '03-together'), ('b09', 'Ending (b09)', '06-held-doodle')]
OUT, DEST = Path(sys.argv[1]), Path(sys.argv[2])
ENGINES = [a.split(':', 1) for a in sys.argv[3:]]
TW, TH, PAD, HEAD, CAP = 360, 630, 16, 70, 46


def font(size):
    for f in ('/System/Library/Fonts/Supplemental/Arial Bold.ttf', '/System/Library/Fonts/Helvetica.ttc'):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def seconds(tag, beat):
    for name in (f'{tag}-{beat}.json', f'{tag}-timings.json'):
        p = OUT / name
        if p.exists():
            d = json.loads(p.read_text())
            return d.get('generate_s') or d.get('per_image_s', {}).get(beat)
    return None


cols = [('The Empty Chair (reference)', None)] + [(label, tag) for tag, label in ENGINES]
W = PAD + len(cols) * (TW + PAD)
H = HEAD + len(ROWS) * (TH + CAP + PAD) + PAD
board = Image.new('RGB', (W, H), (250, 246, 238))
d = ImageDraw.Draw(board)
for c, (label, _) in enumerate(cols):
    d.text((PAD + c * (TW + PAD), 22), label, font=font(20), fill=(30, 35, 60))
for r, (beat, row_label, ref) in enumerate(ROWS):
    y = HEAD + r * (TH + CAP + PAD)
    for c, (_, tag) in enumerate(cols):
        x = PAD + c * (TW + PAD)
        path = REF / f'{ref}.png' if tag is None else OUT / f'{tag}-{beat}.png'
        if path.exists():
            board.paste(Image.open(path).convert('RGB').resize((TW, TH), Image.LANCZOS), (x, y))
        else:
            d.rectangle([x, y, x + TW, y + TH], outline=(180, 170, 160)); d.text((x + 20, y + 20), 'missing', font=font(18), fill='gray')
        note = f'{row_label} · {ref}' if tag is None else f'{row_label} · {seconds(tag, beat)} s'
        d.text((x, y + TH + 10), note, font=font(17), fill=(60, 60, 60))
board.save(DEST, quality=90)
print(DEST, board.size)
