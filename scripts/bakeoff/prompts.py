"""Builds image prompts for a beat from episode.json and the style pack (shared by every engine)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAMING = {'wide': 'wide shot', 'medium': 'medium shot', 'close': 'close-up', 'insert': 'close insert shot',
           'top_down': 'top-down view', 'over_shoulder': 'over-the-shoulder shot'}
CAST_LONG = {'lead': 'the main character',
             'friend': 'a second identical character who wears only a muted teal knitted scarf'}
CAST_SHORT = {'lead': 'white round-headed stick figure',
              'friend': 'second stick figure with teal scarf'}
TRIGGER = 'cozystick style'


def load(slug):
    episode = json.loads((ROOT / 'episodes' / slug / 'episode.json').read_text())
    return {b['id']: b for b in episode['beats']}


def long_prompt(beat):
    """Natural-language prompt for engines with an LLM text encoder (Z-Image, Qwen)."""
    lock = (ROOT / 'styles/cozy/style_lock.txt').read_text().strip()
    shot = beat['shot']
    cast = ', '.join(CAST_LONG[c] for c in shot['cast']) or 'no characters, objects only'
    return f"{FRAMING.get(shot['framing'], shot['framing'])}. Cast: {cast}. Scene: {shot['image_prompt']}\n\n{lock}"


def short_prompt(beat):
    """CLIP-sized prompt (77 tokens) for SDXL: trigger word first, then the scene."""
    shot = beat['shot']
    cast = ', '.join(CAST_SHORT[c] for c in shot['cast'])
    parts = [f'{TRIGGER} illustration', FRAMING.get(shot['framing'], shot['framing']), cast, shot['image_prompt']]
    return ', '.join(p for p in parts if p)


SDXL_NEGATIVE = ('text, letters, words, numbers, watermark, caption, photo, photorealistic, 3d render, '
                 'human face, hair, nose, ears, clothing, blurry, deformed hands, extra limbs')
