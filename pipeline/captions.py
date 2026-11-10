"""Word-synced captions: one line at a time, grouped by meaning, the current word highlighted.
Burned into the picture and written as an .srt file.

Safe area (YouTube Shorts): nothing in the bottom quarter or the right edge, where the title and the
buttons sit. Captions sit just above the bottom quarter, centred in the left part of the frame.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'
W, H = 1080, 1920
SAFE = dict(left=60, right=W - 150, bottom=int(H * 0.75), top=int(H * 0.12))
SIZE = 64
CENTRE_Y = SAFE['bottom'] - 92          # the caption's centre line
WHITE, HIGHLIGHT, STROKE = (255, 255, 255), (255, 222, 112), (22, 22, 46)


@dataclass
class Word:
    text: str          # as shown (the script's spelling, e.g. "$1,500")
    start: float
    end: float
    stop: str = ''     # punctuation that followed it in the script


@dataclass
class Line:
    words: list = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0

    @property
    def text(self) -> str:
        return ' '.join(w.text for w in self.words)


def align(script: str, timed: list, offset: float = 0.0) -> list:
    """Attach the script's own spelling and punctuation to the TTS word timings ([{word, start, end}])."""
    tokens = re.findall(r"\S+", script)
    norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
    out, j = [], 0
    for tok in tokens:
        core = norm(tok)
        if not core:
            continue
        # the TTS may split or join tokens ("$1,500" -> "1,500"); advance until the letters match
        k = j
        while k < len(timed) and norm(timed[k]['word']) not in (core, core.rstrip('s')) and not core.startswith(norm(timed[k]['word'])):
            k += 1
        if k >= len(timed):
            k = min(j, len(timed) - 1)
        w = timed[k]
        stop = re.sub(r'[\w$%\']', '', tok[-1:]) if not tok[-1].isalnum() else ''
        out.append(Word(tok.rstrip('.,!?;:'), offset + w['start'], offset + w['end'], stop))
        j = k + 1
    return out


GOOD_START = {'in', 'at', 'for', 'until', 'till', 'because', 'then', 'but', 'to', 'every', 'across', 'when', 'with',
              'like', 'is', 'take', 'empty', 'feels', 'lands', 'whispers'}   # phrase starts (and verbs)
WEAK_END = {'the', 'a', 'an', 'and', 'of', 'to', 'in', 'at', 'on', 'for', 'my', 'your', 'their'}


def _split(sentence: list, max_chars: int, max_words: int) -> list:
    """Best split of one sentence into lines: balanced, no one-word orphans, breaks before phrase starts."""
    n = len(sentence)
    best = {0: (0.0, [])}
    for i in range(n):
        if i not in best:
            continue
        for j in range(i + 1, min(n, i + max_words) + 1):
            chunk = sentence[i:j]
            chars = len(' '.join(w.text for w in chunk))
            if chars > max_chars and j > i + 1:
                break
            cost = best[i][0] + 4 + max(0, chars - 20) ** 2 * 0.15         # fewer lines; long ones cost more
            if j - i == 1 and n > 1:
                cost += 40                                   # a lone word on a line
            if j < n and sentence[j].text.lower() in GOOD_START:
                cost -= 5                                    # the next line starts a phrase
            if j < n and sentence[j - 1].text.lower() in WEAK_END:
                cost += 8                                    # never end a line on 'the', 'in', ...
            if j not in best or cost < best[j][0]:
                best[j] = (cost, best[i][1] + [(i, j)])
    return [sentence[a:b] for a, b in best[n][1]]


def group(words: list, max_chars: int = 24, max_words: int = 5) -> list:
    """Lines that follow the sense: one sentence or clause at a time (split at its punctuation), long
    ones split where a phrase starts, and never a one-word orphan."""
    sentences, cur = [], []
    for w in words:
        cur.append(w)
        if w.stop:
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)
    # a lone-word clause ("Coffee.") is fine on its own line; very short clauses join the next one
    lines = []
    for sent in sentences:
        for chunk in _split(sent, max_chars, max_words):
            lines.append(Line(list(chunk), chunk[0].start, chunk[-1].end))
    for a, b in zip(lines, lines[1:]):       # a line stays up until the next one (unless there is a long pause)
        a.end = b.start if b.start - a.end < 0.7 else a.end + 0.35
    if lines:
        lines[-1].end += 0.5
    return lines


@lru_cache(maxsize=4)
def _font(size: int):
    return ImageFont.truetype(FONT, size)


def layout(line: Line, size: int = SIZE):
    """(word boxes, total width, height) for one caption line, shrinking the font if it would leave the safe area."""
    width = SAFE['right'] - SAFE['left']
    while True:
        font = _font(size)
        space = font.getlength(' ')
        widths = [font.getlength(w.text) for w in line.words]
        total = sum(widths) + space * (len(widths) - 1)
        if total <= width - 40 or size <= 40:
            return font, widths, space, total
        size -= 4


CLEAR = 18            # px kept between a caption and the host
_placed: dict = {}    # (line start, text) -> (x, y, size): a line keeps its spot while it stays clear of the host


def _metrics(line: Line, size: int):
    font = _font(size)
    space = font.getlength(' ')
    widths = [font.getlength(w.text) for w in line.words]
    asc, desc = font.getmetrics()
    sw = max(4, font.size // 9)
    return font, widths, space, sum(widths) + space * (len(widths) - 1), asc + desc, sw


def _overlap_area(a, b) -> float:
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


def place(line: Line, avoid=None, keep_clear=()):
    """Where a caption line goes: (x, y, font size). Centred in the caption band unless that puts it on the host
    (user 2026-11-09: never on the host's body); then, in order: slid sideways within the safe area, a little
    smaller and slid, or raised above the host. `avoid(box)` counts the host's pixels in a box."""
    font, widths, space, total = layout(line)
    size = font.size
    _, _, _, total, hgt, sw = _metrics(line, size)
    cx = (SAFE['left'] + SAFE['right']) / 2
    default = (cx - total / 2, CENTRE_Y - hgt / 2, size)
    if avoid is None:
        return default

    def hits(x, y, sz):                         # the host's pixels, and any on-screen text (a slam, a label)
        _, _, _, tot, hh, s = _metrics(line, sz)
        box = (x - s - CLEAR, y - s - CLEAR, x + tot + s + CLEAR, y + hh + s + CLEAR)
        return 1000 * avoid(box) + sum(_overlap_area(box, b) for b in keep_clear)    # the host above all

    key = (round(line.start, 3), line.text)
    if key in _placed and hits(*_placed[key]) == 0:
        return _placed[key]
    options = [default]

    def slid(sz):
        if sz < 44:
            return []
        _, _, _, tot, hh, s = _metrics(line, sz)
        y = CENTRE_Y - hh / 2
        return [(SAFE['left'] + s, y, sz), (SAFE['right'] - s - tot, y, sz)]
    options += slid(size) + slid(size - 6)
    for k in range(1, 9):                       # raised, centred, full size: above a standing host's head
        options.append((default[0], default[1] - 70 * k, size))
    options += slid(size - 14)
    for k in range(9, 14):                      # higher still (a label or a slam already sits just above the head)
        options.append((default[0], default[1] - 70 * k, size))
    for k in range(1, 14):                      # raised AND slid to a side: clear of a large host on the other side
        for x, _, sz in slid(size):
            options.append((x, default[1] - 70 * k, sz))
    best = min(options, key=lambda o: (hits(*o) > 0, hits(*o), options.index(o)))
    _placed[key] = best
    return best


def draw(frame: Image.Image, line: Line, t: float, avoid=None, keep_clear=()) -> dict:
    """Draw one caption line; the word being spoken is highlighted. Returns its box (for QA), and with `avoid` the
    host's pixels under it (`on_host`, 0 when clear). `keep_clear`: boxes of on-screen texts already in the frame."""
    x, y, size = place(line, avoid, keep_clear)
    font, widths, space, total, hgt, sw = _metrics(line, size)
    asc, desc = font.getmetrics()
    layer = Image.new('RGBA', frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    current = None
    for i, w in enumerate(line.words):
        nxt = line.words[i + 1].start if i + 1 < len(line.words) else line.end
        if w.start - 0.02 <= t < nxt:
            current = i
    xx = x
    for i, (w, ww) in enumerate(zip(line.words, widths)):
        d.text((xx + 3, y + 5), w.text, font=font, fill=(20, 20, 40, 120), stroke_width=sw, stroke_fill=(20, 20, 40, 120))
        d.text((xx, y), w.text, font=font, fill=HIGHLIGHT if i == current else WHITE, stroke_width=sw, stroke_fill=STROKE)
        xx += ww + space
    frame.alpha_composite(layer)
    box = [round(x - sw), round(y - sw), round(x + total + sw), round(y + asc + desc + sw)]
    out = dict(text=line.text, box=box, font_size=font.size, lines=1)
    if avoid is not None:
        out['on_host'] = int(avoid(box))
    return out


def at(lines: list, t: float):
    for line in lines:
        if line.start - 0.05 <= t < line.end:
            return line
    return None


def srt(lines: list) -> str:
    def ts(s):
        ms = max(0, round(s * 1000))
        return f'{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}'
    return '\n'.join(f'{i}\n{ts(l.start)} --> {ts(l.end)}\n{l.text}\n' for i, l in enumerate(lines, 1))
