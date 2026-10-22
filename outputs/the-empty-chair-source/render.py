"""The Empty Chair: illustrated character animation, narration and local assembly.

Rebuild offline with Pillow and FFmpeg. All timings follow the included voice take.
Source coordinates use the 941 x 1672 illustration canvas; rendering is 720 x 1280.
"""
from array import array
import argparse
from bisect import bisect_right
import json
import math
from pathlib import Path
import random
import subprocess
import wave

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
SIZE = (720, 1280)
W, H = SIZE
FPS = 24
SPEED = .95
LEAD = .4
PAUSES = [(20.298, .5), (24.347, 1.0), (29.293, .55), (37.350, .3)]
TAIL = 3.0
VOICE_LENGTH = 41.016
DURATION = math.ceil((LEAD + VOICE_LENGTH / SPEED + sum(p[1] for p in PAUSES) + TAIL) * FPS) / FPS
FONT = '/System/Library/Fonts/Supplemental/Arial Bold.ttf'
if not Path(FONT).exists():
    FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'


def ease(x):
    x = max(0, min(1, x))
    return x * x * (3 - 2 * x)


def pulse(t, start, length):
    return math.sin(math.pi * (t - start) / length) ** 2 if start < t < start + length else 0


def when(t, after=True):
    return LEAD + t / SPEED + sum(gap for at, gap in PAUSES if at < t or (after and at == t))


# Shot duration is independent of camera motion: no constant zooming or floating.
# crop = center x, center y, scale, measured in original illustration coordinates.
EDIT = [
    (0, 'alone', (470, 830, 1.04), 'draw', 'THE EMPTY CHAIR'),
    (3.220, 'alone', (355, 825, 1.48), 'draw', ''),
    (5.135, 'alone', (706, 1040, 2.0), 'still', ''),
    (6.970, 'question', (470, 730, 1.13), 'ask', ''),
    (10.421, 'question', (290, 690, 1.70), 'hesitate', ''),
    (11.700, 'bag', (470, 850, 1.08), 'lift', ''),
    (13.016, 'together', (470, 800, 1.12), 'talk', ''),
    (16.222, 'together', (630, 650, 1.55), 'ask', ''),
    (17.853, 'together', (300, 650, 1.65), 'nod', ''),
    (18.858, 'together', (470, 790, 1.16), 'talk', ''),
    (20.298, 'waiting', (470, 830, 1.06), 'wait', 'THE NEXT MORNING'),
    (23.125, 'waiting', (670, 850, 1.25), 'still', ''),
    (24.347, 'waiting', (320, 685, 1.57), 'wait', 'THREE MORNINGS'),
    (26.793, 'disappointed', (470, 830, 1.06), 'sad', 'THE FOURTH MORNING'),
    (29.293, 'return', (470, 770, 1.08), 'return', ''),
    (32.350, 'return', (650, 880, 1.48), 'show', ''),
    (34.021, 'books', (655, 790, 1.42), 'still', ''),
    (37.350, 'return', (370, 730, 1.25), 'laugh', ''),
    (40.964, 'books', (470, 835, 1.02), 'still', ''),
]


# heads: ellipse and eye centers in the original illustration canvas.
SPECS = {
    'alone': dict(file='01-alone.png', heads=[(313, 576, 157, 150, [(298, 624), (397, 651)])],
                  hands=[(235, 858, 68, 72)]),
    'question': dict(file='02-question.png', heads=[
        (254, 582, 145, 139, [(268, 612), (358, 566)]),
        (641, 462, 143, 137, [(548, 497), (645, 522)])], hands=[(650, 815, 78, 67)]),
    'bag': dict(file='02b-bag.png', heads=[
        (313, 597, 145, 131, [(338, 620), (423, 565)]),
        (641, 462, 143, 137, [(548, 497), (645, 522)])], hands=[(699, 778, 100, 110)]),
    'together': dict(file='03-together.png', heads=[
        (248, 578, 145, 133, [(268, 609), (355, 565)]),
        (662, 546, 147, 142, [(562, 593), (658, 614)])], hands=[(615, 810, 95, 87)]),
    'waiting': dict(file='04-waiting.png', heads=[
        (300, 565, 155, 140, [(347, 585), (430, 557)])], hands=[(235, 858, 68, 72)]),
    'disappointed': dict(file='05-disappointed.png', heads=[
        (314, 576, 154, 143, [(293, 646), (390, 663)])], hands=[]),
    'return': dict(file='06-return-final.png', heads=[
        (249, 577, 146, 133, [(263, 598), (348, 545)]),
        (641, 430, 143, 132, [(545, 457), (640, 489)])], hands=[]),
    'books': dict(file='07-books-final.png', heads=[], hands=[]),
}


def read_cues():
    def sec(value):
        h, m, value = value.split(':')
        s, ms = value.split(',')
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    cues = []
    for block in (HERE / 'narration.srt').read_text().strip().split('\n\n'):
        rows = block.splitlines()
        a, b = rows[1].split(' --> ')
        cues.append((when(sec(a)), when(sec(b), after=False), ' '.join(rows[2:])))
    return cues


def prepare_voice():
    raw = HERE / 'voice-paced.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(HERE / 'narration.mp3'),
                    '-af', f'atempo={SPEED}', '-ar', '24000', '-ac', '1', str(raw)], check=True)
    with wave.open(str(raw), 'rb') as f:
        sr = f.getframerate()
        samples = array('h', f.readframes(f.getnframes()))
    result = array('h', [0]) * round(LEAD * sr)
    start = 0
    for at, gap in PAUSES + [(VOICE_LENGTH, 0)]:
        end = min(round(at / SPEED * sr), len(samples))
        piece = samples[start:end]
        # Four millisecond boundary fades prevent clicks without changing delivery.
        fade = min(96, len(piece) // 2)
        for i in range(fade):
            piece[i] = round(piece[i] * i / fade)
            piece[-i-1] = round(piece[-i-1] * i / fade)
        result.extend(piece)
        result.extend(array('h', [0]) * round(gap * sr))
        start = end
    result.extend(samples[start:])
    result.extend(array('h', [0]) * max(0, round(DURATION * sr) - len(result)))
    path = HERE / 'voice-final.wav'
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr); f.writeframes(result.tobytes())
    return path


class Scene:
    def __init__(self, name, spec):
        self.name = name
        self.im = Image.open(HERE / 'assets' / spec['file']).convert('RGB').resize(SIZE, Image.Resampling.LANCZOS)
        self.heads = [(x * W / 941, y * H / 1672, rx * W / 941, ry * H / 1672,
                       [(ex * W / 941, ey * H / 1672) for ex, ey in eyes])
                      for x, y, rx, ry, eyes in spec['heads']]
        self.hands = [(x * W / 941, y * H / 1672, rx * W / 941, ry * H / 1672)
                      for x, y, rx, ry in spec['hands']]

    def frame(self, t, acting):
        im = self.im
        # Preserve the illustrated eyes. Flat sampled-color blink masks can
        # sample a dark head outline and flash a black oval over the face.
        if not self.heads and not self.hands:
            return im.copy()
        transforms = []
        for i, (cx, cy, rx, ry, _) in enumerate(self.heads):
            angle = .22 * math.sin(t * 1.4 + i)
            lift = .65 * math.sin(t * 1.8 + i)
            if acting in ('nod', 'ask', 'talk'):
                beat = pulse(t, .25 + i * .45, 1.1)
                angle += (1.5 if i == 0 else -1.2) * beat
                lift -= 1.5 * beat
            elif acting == 'hesitate':
                angle -= .9 * ease(t / 1.2)
            elif acting == 'wait':
                angle -= 1.0 * ease(t / 1.6)
            elif acting == 'sad':
                angle += 1.6 * ease(t / 1.5)
                lift += 1.1 * ease(t / 1.5)
            elif acting == 'laugh':
                angle += .8 * math.sin(t * 7) * math.exp(-t / 4)
                lift -= 2.4 * math.sin(t * 8) ** 2 * math.exp(-t / 5)
            transforms.append((cx, cy, rx, ry, math.radians(angle), lift))

        def point(x, y):
            sx, sy = x, y
            for cx, cy, rx, ry, angle, lift in transforms:
                radius = math.sqrt(((x-cx)/rx)**2 + ((y-cy)/ry)**2)
                weight = ease((1.26-radius)/.22)
                c, s = math.cos(-angle), math.sin(-angle)
                sx += weight * (cx+(x-cx)*c-(y-cy)*s-x)
                sy += weight * (cy+(x-cx)*s+(y-cy)*c-y-lift)
            for i, (cx, cy, rx, ry) in enumerate(self.hands):
                weight = ease((1-math.sqrt(((x-cx)/rx)**2+((y-cy)/ry)**2))/.32)
                if acting == 'draw':
                    sx -= weight * .7 * math.sin(t*6)
                    sy -= weight * .5 * math.sin(t*9)
                elif acting == 'lift':
                    sy += weight * 3.8 * (1-ease(t/1.2))
                elif acting in ('show', 'return', 'talk'):
                    sy -= weight * 1.3 * math.sin(t*1.6 + i)
            return sx, sy

        mesh = []
        for top in range(0, H, 32):
            bottom = min(top+32, H)
            for left in range(0, W, 32):
                right = min(left+32, W)
                pts = [point(x, y) for x, y in [(left, top), (left, bottom), (right, bottom), (right, top)]]
                mesh.append(((left, top, right, bottom), tuple(v for p in pts for v in p)))
        return im.transform(SIZE, Image.Transform.MESH, mesh, Image.Resampling.BICUBIC)


def lines_for(text, font, width):
    d = ImageDraw.Draw(Image.new('RGB', (1, 1)))
    rows, row = [], ''
    for word in text.split():
        trial = (row+' '+word).strip()
        if row and d.textlength(trial, font=font) > width:
            rows.append(row); row = word
        else:
            row = trial
    return rows + [row] if row else rows


class Reel:
    def __init__(self):
        self.scenes = {name: Scene(name, spec) for name, spec in SPECS.items()}
        self.cues = read_cues()
        self.starts = [0] + [when(row[0]) for row in EDIT[1:]]
        self.font = ImageFont.truetype(FONT, 31)
        self.label = ImageFont.truetype(FONT, 22)

    def frame(self, t):
        i = bisect_right(self.starts, t) - 1
        _, scene, crop, acting, label = EDIT[i]
        local = t - self.starts[i]
        im = self.scenes[scene].frame(local, acting)
        if acting == 'sad':
            im = ImageEnhance.Color(im).enhance(.74)
            im = ImageEnhance.Brightness(im).enhance(.96)
        # Only the opening and final insert have a small motivated camera settle.
        cx, cy, zoom = crop
        if i == 0 or i == len(EDIT)-1:
            zoom += .018 * ease(local/3)
        cx, cy = cx*W/941, cy*H/1672
        cw, ch = W/zoom, H/zoom
        cx = max(cw/2, min(W-cw/2, cx)); cy = max(ch/2, min(H-ch/2, cy))
        im = im.crop(tuple(round(v) for v in (cx-cw/2, cy-ch/2, cx+cw/2, cy+ch/2))).resize(SIZE, Image.Resampling.LANCZOS).convert('RGBA')
        overlay = Image.new('RGBA', SIZE)
        d = ImageDraw.Draw(overlay)
        text = next((text for a, b, text in self.cues if a <= t < b), '')
        if text:
            rows = lines_for(text, self.font, 604)
            if len(rows) > 3:
                raise ValueError(f'Caption exceeds safe area: {text}')
            height = len(rows)*39 + 26
            top = 1210-height
            d.rounded_rectangle((36, top, 684, 1210), radius=17, fill=(19, 24, 36, 218))
            for n, row in enumerate(rows):
                d.text((360, top+12+n*39), row, font=self.font, anchor='mt', fill='#FFF9EC')
        if label:
            width = d.textlength(label, font=self.label)+40
            d.rounded_rectangle((360-width/2, 58, 360+width/2, 105), radius=23, fill=(255, 249, 237, 232))
            d.text((360, 81), label, font=self.label, anchor='mm', fill='#263C3B')
        im = Image.alpha_composite(im, overlay).convert('RGB')
        if t > DURATION-.65:
            im = Image.blend(im, Image.new('RGB', SIZE, '#18252B'), ease((t-DURATION+.65)/.65))
        return im


def sound_bed():
    """Quiet original plucked harmony and two restrained cafe door chimes."""
    sr = 24000
    pcm = array('h')
    chords = [(130.81, 164.81, 196), (110, 130.81, 164.81), (87.31, 130.81, 174.61), (98, 146.83, 196)]
    chimes = [when(6.970)-.25, when(29.293)-.25]
    plucks = [(2+bar*4.5+n*.65, f*2) for bar in range(11) for n, f in enumerate(chords[bar%4])]
    for k in range(round(DURATION*sr)):
        t = k/sr
        val = 0
        for at, freq in plucks:
            q = t-at
            if 0 < q < 3:
                env = min(1, q/.012) * math.exp(-q*1.75)
                val += .012 * env * (math.sin(2*math.pi*freq*q) + .18*math.sin(2*math.pi*freq*2*q))
        for at in chimes:
            q = t-at
            if 0 < q < 1.1:
                val += .015*min(1,q/.01)*math.exp(-q*4)*(math.sin(2*math.pi*1046.5*q)+.3*math.sin(2*math.pi*1568*q))
        val *= ease(t/1.2)*(1-ease((t-DURATION+1.5)/1.5))
        pcm.append(round(max(-.9,min(.9,val))*32767))
    target = HERE/'music-and-foley.wav'
    with wave.open(str(target), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sr); f.writeframes(pcm.tobytes())
    return target


def srt_time(sec):
    ms = round(sec*1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'


def render(preview=False, output=None):
    reel = Reel()
    target = Path(output).resolve() if output else OUT/'the-empty-chair.mp4'
    if preview:
        indices = [0, 2, 3, 5, 6, 10, 12, 13, 14, 16, 17, 18]
        sheet = Image.new('RGB', (1080, 2560), '#FFF8EB')
        for j, i in enumerate(indices):
            t = reel.starts[i]+.5
            sheet.paste(reel.frame(t).resize((360, 640)), ((j%3)*360, (j//3)*640))
        sheet.save(target.with_suffix('.storyboard.jpg'), quality=92)
        return
    voice = prepare_voice()
    music = sound_bed()
    temp = target.with_name(target.stem+'.partial.mp4')
    command = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
               '-s', '720x1280', '-r', str(FPS), '-i', '-', '-i', str(voice), '-i', str(music),
               '-filter_complex', '[1:a]loudnorm=I=-17:TP=-2:LRA=9[voice];[voice][2:a]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[a]',
               '-map', '0:v', '-map', '[a]', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-t', str(DURATION),
               '-movflags', '+faststart', str(temp)]
    log = target.with_suffix('.render.log')
    with log.open('w') as err:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=err)
        try:
            for k in range(round(DURATION*FPS)):
                if k%(FPS*3) == 0:
                    print(f'Rendering {k/FPS:.0f}/{DURATION:.2f}s', flush=True)
                process.stdin.write(reel.frame(k/FPS).tobytes())
            process.stdin.close()
            if process.wait():
                raise RuntimeError(log.read_text())
            temp.replace(target)
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
            temp.unlink(missing_ok=True)
    target.with_suffix('.srt').write_text('\n\n'.join(f'{n+1}\n{srt_time(a)} --> {srt_time(b)}\n{text}' for n,(a,b,text) in enumerate(reel.cues))+'\n')
    target.with_suffix('.timeline.json').write_text(json.dumps(dict(title='The Empty Chair', duration=DURATION,
        width=W, height=H, fps=FPS, source_images=len(SPECS), voice='en-US-AndrewNeural', rate='-8%',
        playback_tempo=SPEED, pauses=PAUSES, shots=[dict(start=reel.starts[i],
            end=reel.starts[i+1] if i+1<len(EDIT) else DURATION, image=row[1], camera=row[2],
            acting=row[3], label=row[4]) for i,row in enumerate(EDIT)], speech_cues=reel.cues), indent=2))
    print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--output', help='Optional output MP4 path')
    args = parser.parse_args()
    render(args.preview, args.output)
