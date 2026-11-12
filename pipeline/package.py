"""`stick package <slug>`: a publishing kit from the episode's own data (adapted from kaomei's Thumbnail & Publishing
Kit, stickman-video-director, MIT; docs/KAOMEI_INTEGRATION.md). No paid call, ever.

Writes episodes/<slug>/out/:
  thumbnail.jpg            1280x720, the busiest frame of the rendered video cropped to 16:9 around its captions,
                           with the hook as a 2-4 word overlay on the side away from the host
  titles.txt               5 title variants: search intent, curiosity, broad appeal (rule-based; with --titles one
                           local Ollama call writes them, falling back to the rules if it fails)
  description.txt          a paste-ready description: summary, the research sources, credits, 8-12 hashtags
  comment.txt              one pinned discussion question
  publish_checklist.json   each check marked checked, pending or not_applicable, with what it is based on

Nothing is published or uploaded. Without a rendered video the text items are still written and the thumbnail and
its checks are pending.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .config import ROOT

THUMB_W, THUMB_H = 1280, 720
FRAME_W, FRAME_H = 1080, 1920
CROP_H = round(FRAME_W * 9 / 16)                 # 608: a 16:9 band of the vertical frame
SCORE_W = 270                                    # frames are scored at a quarter of their width
FONT = '/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf'
INK = (24, 30, 58)                               # the cozy navy
TITLE_MAX = 100                                  # YouTube's title limit
STOP = set('a an and are as at be but by can could did do does for from had has have how i if in into is it its '
           "it's just me my no not of on or our out so than that the their them then there these they this to too "
           'up us was we were what when where which who why will with you your'.split())
DONT_PROMISE = re.compile(r'\b(viral|guarantee|guaranteed|you won\'t believe|shocking|insane|#1)\b', re.I)


class PackageError(RuntimeError):
    pass


# ------------------------------------------------------------------ text helpers

def _words(text: str) -> list:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’-]*", text or '')


def _keywords(text: str) -> list:
    return [w.lower().strip("'’") for w in _words(text) if w.lower() not in STOP and len(w) > 2]


def _title_case(text: str) -> str:
    small = {'a', 'an', 'and', 'as', 'at', 'but', 'by', 'for', 'in', 'of', 'on', 'or', 'the', 'to', 'vs'}
    out = []
    for i, w in enumerate(text.split()):
        low = w.lower()
        out.append(low if i and low in small else (w[:1].upper() + w[1:]))
    return ' '.join(out)


def _clip(text: str, limit: int = TITLE_MAX) -> str:
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(' ', 1)[0].rstrip(',;:-')
    return cut + '…'


def hook_line(ep) -> str:
    if ep.hook_options:
        return ep.hook_options[(ep.hook_pick or 1) - 1].line
    return ep.beats[0].narration if ep.beats else ''


def short_topic(ep) -> str:
    """The topic without its subtitle ("The 1816 year without a summer: how one volcano..." -> the part before ':')."""
    topic = ' '.join(ep.topic.split()).rstrip('?.!')
    head = topic.split(':')[0].strip()
    return head if len(_words(head)) >= 2 else topic


def overlay_words(ep) -> str:
    """The thumbnail's 2-4 words. They open the question and never give the payoff away: the hook beat's on-screen
    text, else a 2-4 word sentence of the hook (a question first), else the topic, else the hook's question cut to
    four words with an ellipsis."""
    cands = []
    if ep.beats and ep.beats[0].on_screen_text:
        cands.append(ep.beats[0].on_screen_text)
    hook = hook_line(ep)
    sentences = [s.strip() for s in re.findall(r'[^.?!]+[.?!]*', hook) if _words(s)]
    sentences.sort(key=lambda s: (not s.endswith('?'), -len(_words(s))))   # a question, then the fullest that fits
    cands += sentences + [short_topic(ep)]
    for c in cands:
        if 2 <= len(_words(c)) <= 4:
            return c.strip().rstrip('.')
    q = next((x for x in sentences if x.endswith('?')), sentences[0] if sentences else short_topic(ep))
    words = q.rstrip('?.!').split()
    return ' '.join(words[:4]) + ('…' if len(words) > 4 else '')


# ------------------------------------------------------------------ titles, description, tags, comment

def rule_titles(ep) -> list:
    """Five accurate titles from the script: search intent, curiosity (the hooks), the story, broad appeal."""
    topic = short_topic(ep)
    hooks = [h.line for h in ep.hook_options or []] or ([ep.beats[0].narration] if ep.beats else [])
    low = topic.lower()
    if low.startswith('why '):
        broad = 'The Real Reason ' + topic[4:]
    elif low.startswith('how '):
        broad = "Here's How " + topic[4:]
    else:
        broad = f'The Story Behind {topic[:1].lower() + topic[1:] if topic.startswith("The ") else topic}'
    # search intent (no length in it: the cut can run longer than the target), curiosity, broad appeal, then more
    out = [f'{_title_case(topic)}, Explained', *hooks[:1], broad, ep.storyline.title if ep.storyline else '',
           ep.hook_promise, *hooks[1:], ep.title]
    seen, titles = set(), []
    for t in out:
        t = _clip(t)
        key = re.sub(r'\W+', '', t.lower())
        if t and key not in seen and not DONT_PROMISE.search(t):
            seen.add(key)
            titles.append(t)
    while len(titles) < 5:                                                      # never fewer than five
        titles.append(_clip(f'{_title_case(topic)} ({["The Short Version", "In Plain English", "A Quick Story"][len(titles) % 3]})'))
    return titles[:5]


TITLES_SYSTEM = """You write titles for a 30-60 second vertical explainer video. Write exactly five distinct titles:
one for search intent (the plain question people type), two for curiosity (an open loop the video closes), one for
broad appeal, and one more of your choice. Every title is accurate to the script below: no number, claim or event the
script does not contain, no promise of virality, money or results, no clickbait words. At most 70 characters each."""


def llm_titles(ep, base, ledger) -> list:
    """One local Ollama call (never a paid call). Raises PackageError when it cannot run or returns too few."""
    from . import config, llm
    from .cache import for_episode
    if config.script_provider() != 'ollama':
        raise PackageError('--titles uses the local model only; SCRIPT_PROVIDER is not ollama, so no call was made')
    if not llm.is_available():
        raise PackageError(f'Ollama is not running or {config.ollama_model()} is not pulled')
    lines = '\n'.join(f'{b.id} {b.role}: {b.narration}' for b in ep.beats)
    user = (f'Topic: {ep.topic}\nWorking title: {ep.title}\nHook: {hook_line(ep)}\n'
            f'Question the hook opens: {ep.hook_promise}\nScript:\n{lines}')
    schema = {'type': 'object', 'properties': {'titles': {'type': 'array', 'items': {'type': 'string'},
                                                          'minItems': 5, 'maxItems': 5}}, 'required': ['titles']}
    try:
        data = llm.generate_json(TITLES_SYSTEM, user, schema, 600, temperature=0.6, cache=for_episode(base),
                                 ledger=ledger, step='package', detail='titles')
    except llm.LLMError as exc:
        raise PackageError(f'the local model could not write titles: {exc}') from None
    finally:
        if config.ollama_unload_after_step():
            llm.unload()
    titles, seen = [], set()
    for t in data.get('titles') or []:
        t = _clip(str(t).strip().strip('"'))
        key = re.sub(r'\W+', '', t.lower())
        if t and key not in seen and not DONT_PROMISE.search(t):
            seen.add(key)
            titles.append(t)
    if len(titles) < 5:
        raise PackageError(f'the local model gave {len(titles)} usable titles, not 5')
    return titles[:5]


TAG_SKIP = set('only keep keeps kept way ways off same year years make makes made really never always every much many more '
               'most very get gets got goes going one two three four five first last actually without across over under '
               'just still even ever also because about after before thing things here want wants need needs cant dont '
               'wont yourself myself itself tried said says people someone'.split())


def hashtags(ep) -> list:
    """8-12 hashtags: the short topic as one tag, its real keywords (4+ letters, no filler or bare numbers), the
    niche, then general ones."""
    tags = []

    def add(t):
        t = re.sub(r'[^a-z0-9]', '', t.lower())
        if 3 < len(t) <= 28 and not t[0].isdigit() and t not in tags:       # no bare numbers or ordinals
            tags.append(t)
    add(''.join(_words(short_topic(ep))))
    for w in _keywords(ep.topic) + _keywords(ep.title):
        if w not in TAG_SKIP and len(tags) < 8:
            add(w)
    for w in _keywords(ep.niche or ''):
        add(w)
    for t in ('shorts', 'explainer', 'didyouknow', 'learnsomething', 'stickfigure', 'animation', 'science', 'facts',
              'education', 'explained'):
        if len(tags) >= 12:
            break
        if t == 'science' and not re.search(r'scien|physic|biolog|chemis|sky|light|body|brain|sleep', ep.topic + ep.niche, re.I):
            continue
        add(t)
    return ['#' + t for t in tags[:12]]


def _domain(url: str) -> str:
    m = re.match(r'https?://(?:www\.)?([^/]+)', url or '')
    return m.group(1) if m else url


def _source_name(s) -> str:
    """The page title when it reads as one; the site's domain when the title is page furniture."""
    t = ' '.join((s.title or '').split())
    if not t or len(t) > 90 or '::' in t or _domain(s.url) in t or sum(c.isupper() for c in t) > 0.5 * len(t):
        return _domain(s.url)
    return t


def description(ep) -> str:
    """Paste-ready: a two-sentence summary from the storyline (or the hook and payoff), the research sources that the
    script cites, credits and hashtags. No chapters: a Short has none."""
    payoff = next((b.narration for b in ep.beats if b.role == 'payoff'), '')
    if ep.storyline and ep.storyline.pitch:
        summary = ep.storyline.pitch
    else:
        summary = ' '.join(x for x in (hook_line(ep), payoff) if x)
    cited = {sid for b in ep.beats for sid in b.source_ids}
    sources = [s for s in ep.sources if s.id in cited] or list(ep.sources)
    parts = [summary.strip()]
    if sources:
        parts.append('Sources:\n' + '\n'.join(f'- {_source_name(s)}: {s.url}' for s in sources))
    parts.append('Made with free, local tools: narration by Kokoro-82M (Apache-2.0), pictures painted with '
                 'FLUX.2 klein 4B (Apache-2.0), synthesized sound effects.')
    parts.append(' '.join(hashtags(ep)))
    return '\n\n'.join(parts) + '\n'


def pinned_comment(ep) -> str:
    """One honest discussion question: the loop beat's own question when it asks one (a revelation ends on one),
    else a question about what viewers believed before watching."""
    last = ep.beats[-1].narration if ep.beats else ''
    qs = [s.strip() for s in re.findall(r'[^.?!]+\?', last)]
    hook = re.sub(r'\W+', ' ', hook_line(ep).lower())
    if qs and (ep.story_format == 'revelation' or re.sub(r'\W+', ' ', qs[-1].lower()).strip() not in hook):
        return qs[-1]                             # a new question, not the hook said again
    topic = short_topic(ep)
    low = topic.lower()
    for w in ('why', 'how'):
        if low.startswith(w + ' '):
            return f'Before watching, {w} did you think {topic[len(w) + 1:]}? Did this change your mind?'
    about = topic[:1].lower() + topic[1:] if topic.startswith('The ') else topic
    return f'What surprised you most about {about}? Did you already know?'


# ------------------------------------------------------------------ thumbnail

def find_video(slug: str) -> Path | None:
    out = ROOT / 'renders' / 'sample' / slug
    for name in (f'{slug}.mp4', 'video.mp4'):
        if (out / name).is_file():
            return out / name
    return None


def _current_stems(out_dir: Path) -> set:
    listing = out_dir / 'shots.txt'
    stems = set()
    if listing.is_file():
        for line in listing.read_text(errors='replace').splitlines():
            m = re.match(r"\s*file\s+'(.+)'\s*$", line)
            if m:
                stems.add(Path(m.group(1)).name.rsplit('.', 1)[0])
    return stems


def overlays_by_time(out_dir: Path) -> dict:
    """{t: dict(boxes=[caption and label boxes], host=[host boxes])} in 1080x1920 frame pixels, from the current
    shots' captions.json and text.json (written by the renderer)."""
    out = {}
    for stem in _current_stems(out_dir):
        for kind in ('captions', 'text'):
            p = out_dir / 'shots' / f'{stem}.{kind}.json'
            try:
                rows = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            for r in rows:
                t = round(float(r.get('t', -1)), 2)
                slot = out.setdefault(t, dict(boxes=[], host=[]))
                if kind == 'captions' and r.get('box'):
                    slot['boxes'].append(r['box'])
                if kind == 'text':
                    slot['boxes'] += [it[:4] for it in r.get('items') or [] if len(it) >= 4]
                    slot['host'] += [h['box'] for h in r.get('host') or [] if h.get('box')]
    return out


def _nearest(table: dict, t: float, within: float = 0.25) -> dict:
    if not table:
        return dict(boxes=[], host=[])
    k = min(table, key=lambda x: abs(x - t))
    return table[k] if abs(k - t) <= within else dict(boxes=[], host=[])


def _frame(video: Path, t: float, width: int | None = None):
    from PIL import Image
    w = width or FRAME_W
    h = round(w * FRAME_H / FRAME_W)
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', str(video), '-frames:v', '1', '-vf',
                          f'scale={w}:{h}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    if len(raw) < w * h * 3:
        return None
    return Image.frombytes('RGB', (w, h), raw[:w * h * 3])


def density(im) -> float:
    """Visual density of an image: mean edge strength plus colourfulness, both 0-1."""
    from PIL import ImageFilter, ImageStat
    edges = ImageStat.Stat(im.convert('L').filter(ImageFilter.FIND_EDGES)).mean[0] / 255
    sat = ImageStat.Stat(im.convert('HSV').getchannel('S')).mean[0] / 255
    return round(4 * edges + 0.5 * sat, 4)


HEAD_BONUS = 0.35                                 # a frame showing the host's face beats a busier frame without it


def head_box(host_box, generous: bool = False) -> list:
    """The host's head inside its body box (frame pixels): the top of the box, about a third of a standing figure. A
    close-up's box is cut by the frame edge, so `generous` (used to keep text off the face) takes the head as tall as
    the box is wide: the round head is never wider than the box."""
    x0, y0, x1, y1 = host_box
    h = min(y1, FRAME_H) - y0
    return [x0, y0, x1, y0 + (min(h, x1 - x0) if generous else min(0.4 * h, 0.8 * (x1 - x0)))]


TEXT_ROOM = 0.3                                   # a band needs this much width (or 1/4 of its height) free of the face


def _room_for_text(band_y: int, faces: list) -> bool:
    """True when the band leaves space for the overlay beside, above or below every face."""
    for f in faces:
        side = max(f[0], FRAME_W - f[2])
        above, below = f[1] - band_y, band_y + CROP_H - f[3]
        if side < TEXT_ROOM * FRAME_W and max(above, below) < CROP_H / 4:
            return False
    return True


def best_crop(video: Path, timeline: dict, overlays: dict) -> dict:
    """The (time, band) with the highest visual density whose 16:9 band cuts through no caption or label and, when the
    host is on screen, keeps its whole head (a band with a face scores HEAD_BONUS more)."""
    scale = SCORE_W / FRAME_W
    band = round(CROP_H * scale)
    times = []
    for b in timeline.get('beats', []):
        s, e = float(b['start']), float(b['end'])
        times += [s + (e - s) * f for f in (0.3, 0.55, 0.8)]
    best = None
    for t in times:
        im = _frame(video, t, SCORE_W)
        if im is None:
            continue
        ov = _nearest(overlays, round(t, 2))
        heads = [head_box(h) for h in ov['host'] if h[1] < FRAME_H]
        faces = [head_box(h, generous=True) for h in ov['host'] if h[1] < FRAME_H]
        for y in range(0, FRAME_H - CROP_H + 1, 32):
            if any(min(y + CROP_H, bx[3]) - max(y, bx[1]) > 0 for bx in ov['boxes']):
                continue                          # the band would cut through text
            cut = [hd for hd in heads if min(y + CROP_H, hd[3]) - max(y, hd[1]) > 0 and not (y <= hd[1] and hd[3] <= y + CROP_H)]
            if cut:
                continue                          # the band would cut through the host's head
            inside = [hd for hd in heads if y <= hd[1] and hd[3] <= y + CROP_H]
            near = [f for f in faces if min(y + CROP_H, f[3]) - max(y, f[1]) > 0]
            if not _room_for_text(y, near):
                continue                          # the face would fill the frame: no room for the words
            ys = round(y * scale)
            score = density(im.crop((0, ys, SCORE_W, ys + band))) + (HEAD_BONUS if inside else 0)
            if best is None or score > best['score']:
                best = dict(t=round(t, 3), y=y, score=round(score, 4), heads=inside, faces=near)
    if best is None:
        raise PackageError('no frame of the video has a 16:9 band clear of captions')
    return best


def _fit_font(draw, text, max_w, max_lines=2, start=150, floor=70):
    from PIL import ImageFont
    words = text.split()
    for size in range(start, floor - 1, -6):
        font = ImageFont.truetype(FONT, size)
        lines, cur = [], ''
        for w in words:
            trial = (cur + ' ' + w).strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= max_lines and all(draw.textlength(l, font=font) <= max_w for l in lines):
            return font, lines, size
    font = ImageFont.truetype(FONT, floor)
    return font, [text], floor


def _luminance(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def make_thumbnail(video: Path, slug: str, words: str, dst: Path) -> dict:
    from PIL import Image, ImageDraw, ImageFilter, ImageStat
    out_dir = video.parent
    timeline = json.loads((out_dir / 'timeline.json').read_text())
    overlays = overlays_by_time(out_dir)
    pick = best_crop(video, timeline, overlays)
    frame = _frame(video, pick['t'])
    if frame is None:
        raise PackageError(f'could not read the frame at {pick["t"]} s')
    im = frame.crop((0, pick['y'], FRAME_W, pick['y'] + CROP_H)).resize((THUMB_W, THUMB_H), Image.LANCZOS)
    # the text never covers the host's face: try the four corners, keep the one that fits the largest text
    k = THUMB_W / FRAME_W
    faces = [[hd[0] * k, (hd[1] - pick['y']) * k, hd[2] * k, (hd[3] - pick['y']) * k] for hd in pick['faces']]
    margin = 48
    draw = ImageDraw.Draw(im)
    text = words.upper() if len(words) <= 18 else words
    best_pos = None
    for side in ('left', 'right', 'across'):
        for where in ('top', 'bottom'):
            max_w = round(THUMB_W * 0.6) if side != 'across' else THUMB_W - 2 * margin
            for f in faces if side != 'across' else ():          # room beside the face on this side
                room = (f[0] - 24 - margin) if side == 'left' else (THUMB_W - margin - f[2] - 24)
                max_w = min(max_w, max(0, round(room)))
            if max_w < 260:
                continue
            font, lines, size = _fit_font(draw, text, max_w)
            line_h = round(size * 1.08)
            block_h = line_h * len(lines)
            top = margin if where == 'top' else THUMB_H - margin - block_h
            text_w = max(draw.textlength(l, font=font) for l in lines)
            x0 = (margin if side == 'left' else THUMB_W - margin - text_w if side == 'right'
                  else (THUMB_W - text_w) / 2)
            box = (x0 - 12, top - 12, x0 + text_w + 12, top + block_h + 12)
            if any(min(box[2], f[2]) > max(box[0], f[0]) and min(box[3], f[3]) > max(box[1], f[1]) for f in faces):
                continue
            rank = (size, side != 'across', where == 'top')
            if best_pos is None or rank > best_pos[0]:
                best_pos = (rank, side, font, lines, size, line_h, block_h, top, x0)
    if best_pos is None:                          # no room beside the face: a smaller line across the top
        font, lines, size = _fit_font(draw, text, THUMB_W - 2 * margin, max_lines=1, start=110, floor=60)
        line_h, side = round(size * 1.08), 'top'
        block_h, top = line_h, margin
        x0 = margin
    else:
        _, side, font, lines, size, line_h, block_h, top, x0 = best_pos
    max_w = max(draw.textlength(l, font=font) for l in lines)
    region = im.crop((int(x0), int(top), int(x0 + max_w), int(top + block_h)))
    bg = tuple(round(v) for v in ImageStat.Stat(region).mean[:3])
    shadow = Image.new('RGBA', im.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    stroke = max(6, size // 14)
    for i, line in enumerate(lines):
        sd.text((x0 + 6, top + i * line_h + 8), line, font=font, fill=(0, 0, 0, 150), stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
    im = Image.alpha_composite(im.convert('RGBA'), shadow.filter(ImageFilter.GaussianBlur(8))).convert('RGB')
    draw = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        draw.text((x0, top + i * line_h), line, font=font, fill=(255, 255, 255), stroke_width=stroke, stroke_fill=INK)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, 'JPEG', quality=92, optimize=True)
    text_w = max(draw.textlength(l, font=font) for l in lines)
    return dict(path=str(dst.relative_to(ROOT)), frame_s=pick['t'], band_top_px=pick['y'], density=pick['score'],
                text=text, lines=lines, font_px=size, text_height_pct=round(100 * size / THUMB_H, 1), side=side,
                text_box=[round(x0), round(top), round(x0 + text_w), round(top + block_h)], face_in_frame=bool(faces), caption_data=bool(overlays),
                inside_safe_margin=x0 >= 40 and x0 + text_w <= THUMB_W - 40 and top + block_h <= THUMB_H - 40,
                fill_vs_outline_contrast=contrast((255, 255, 255), INK), fill_vs_background_contrast=contrast((255, 255, 255), bg))


# ------------------------------------------------------------------ the kit

def _check(status, note, **data):
    return dict(status=status, note=note, **data)


def checklist(ep, video: Path | None, thumb: dict | None, titles_from: str) -> dict:
    qa = {}
    if video and (video.parent / 'qa.json').is_file():
        try:
            qa = json.loads((video.parent / 'qa.json').read_text())
        except ValueError:
            qa = {}
    summary = qa.get('summary') or {}
    approved = ep.is_approved('script')
    c = {}
    if thumb:
        readable = thumb['text_height_pct'] >= 9 and len(thumb['lines']) <= 2
        c['thumbnail_mobile_readability'] = _check('checked' if readable else 'pending',
            f"{thumb['font_px']} px text ({thumb['text_height_pct']} % of the height), {len(thumb['lines'])} line(s)"
            + ('' if readable else ': too small or too long for a phone; shorten the overlay'))
        c['thumbnail_safe_zone'] = _check('checked' if thumb['inside_safe_margin'] else 'pending',
            'the text sits inside a 40 px margin' if thumb['inside_safe_margin'] else 'the text reaches the edge')
        ok = thumb['fill_vs_outline_contrast'] >= 4.5
        c['thumbnail_contrast'] = _check('checked' if ok else 'pending',
            f"white text with a navy outline: {thumb['fill_vs_outline_contrast']}:1 (background behind it "
            f"{thumb['fill_vs_background_contrast']}:1)")
        c['thumbnail_matches_video'] = _check('checked', f"a frame of the rendered video at {thumb['frame_s']} s, the "
                                                         'hook as its text: no scene the video does not show')
        c['thumbnail_clear_of_captions'] = (
            _check('checked', 'the crop avoids every caption and label on screen at that moment (from the shot data)')
            if thumb['caption_data'] else
            _check('pending', 'no shot data for this render (renders/sample/<slug>/shots/*.captions.json), so a burned-in '
                              'caption may show: look at it, or re-render and package again'))
    else:
        for k in ('thumbnail_mobile_readability', 'thumbnail_safe_zone', 'thumbnail_contrast', 'thumbnail_matches_video',
                  'thumbnail_clear_of_captions'):
            c[k] = _check('pending', 'no thumbnail: the video is not rendered yet')
    c['titles_match_script'] = _check('checked' if approved else 'pending',
        f'titles {titles_from} from the ' + ('approved script' if approved else
                                              'script, which is not approved (or changed since): re-check them'))
    c['final_cut_matches_script'] = _check(
        'checked' if approved and video and qa else 'pending',
        'the video was rendered from the approved script and has a QA report' if approved and video and qa else
        'needs a render of the approved script')
    if qa:
        c['qa_report'] = _check('checked' if summary.get('all') else 'pending',
                                'every QA check passed' if summary.get('all') else
                                'failing checks: ' + ', '.join(k for k, v in summary.items() if k != 'all' and not v),
                                summary=summary)
        cont = qa.get('continuity') or {}
        if cont.get('warnings'):
            c['continuity_warnings'] = _check('pending', cont.get('note', ''), warnings=cont['warnings'])
    else:
        c['qa_report'] = _check('pending', 'no qa.json: the video is not rendered yet')
    c['timestamps'] = _check('not_applicable', 'a Short has no chapters; the description has none')
    c['sources'] = _check('checked' if ep.sources else 'pending',
                          f'{len(ep.sources)} research source(s) listed in the description, each a real fetched URL'
                          if ep.sources else 'no research sources on this episode')
    c['voice_licence'] = _check('checked', 'Kokoro-82M, Apache-2.0: commercial use allowed')
    c['image_licence'] = _check('checked', 'FLUX.2 klein 4B, Apache-2.0, run locally; the Empty Chair character art is '
                                           'the channel\'s own')
    c['music_and_sfx_licence'] = _check('checked', 'sound effects and the optional music bed are synthesized in code '
                                                   '(no samples)')
    c['content_id_and_monetization'] = _check('pending', 'cannot be certified here: the platform decides')
    c['your_final_approval'] = _check('pending', 'watch the final video and approve it yourself before publishing')
    counts = {s: sum(1 for v in c.values() if v['status'] == s) for s in ('checked', 'pending', 'not_applicable')}
    return dict(slug=ep.slug, counts=counts, checks=c)


def build(slug: str, use_llm: bool = False) -> dict:
    from . import store
    ep, base = store.load(slug)
    if not ep.beats:
        raise PackageError(f'{slug} has no script yet: package needs at least an approved script')
    out = base / 'out'
    out.mkdir(parents=True, exist_ok=True)
    notes = []
    titles, titles_from = None, 'written by rules'
    if use_llm:
        try:
            titles = llm_titles(ep, base, store.ledger(base))
            titles_from = 'written by the local model'
        except PackageError as exc:
            notes.append(f'titles: {exc}; used the rule-based titles')
    titles = titles or rule_titles(ep)
    (out / 'titles.txt').write_text('\n'.join(titles) + '\n')
    (out / 'description.txt').write_text(description(ep))
    (out / 'comment.txt').write_text(pinned_comment(ep) + '\n')
    video, thumb = find_video(slug), None
    if video and (video.parent / 'timeline.json').is_file():
        try:
            thumb = make_thumbnail(video, slug, overlay_words(ep), out / 'thumbnail.jpg')
        except PackageError as exc:
            notes.append(f'thumbnail: {exc}')
    else:
        notes.append('thumbnail: no rendered video yet (renders/sample/<slug>/<slug>.mp4); the text items are drafts')
    check = checklist(ep, video, thumb, titles_from)
    check['thumbnail'] = thumb
    check['notes'] = notes
    (out / 'publish_checklist.json').write_text(json.dumps(check, indent=1, ensure_ascii=False) + '\n')
    return dict(out=str(out.relative_to(ROOT)), titles=titles, comment=pinned_comment(ep), thumbnail=thumb,
                counts=check['counts'], notes=notes, video=str(video.relative_to(ROOT)) if video else None)
