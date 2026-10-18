"""Story format, validation, and portable JSON authoring instructions."""
import json
import re
from pathlib import Path

BACKGROUNDS = ('home', 'desk', 'cafe', 'standing')
ACTIONS = ('idle', 'read', 'type', 'send', 'think', 'nod', 'celebrate')
EXPRESSIONS = ('neutral', 'happy', 'tired', 'shock')
ACTIONS_BY_BACKGROUND = {
    'home': ACTIONS,
    'desk': ('idle', 'read', 'type', 'think', 'nod'),
    'cafe': ('idle', 'nod'),
    'standing': ('idle', 'think', 'nod', 'celebrate'),
}
SCENE_DEFAULTS = dict(background='home', action='idle', expression='neutral',
                      caption='', overlay='', message='', event_delay=0.6,
                      duration=3.0, audio='')
SCENE_PROPERTIES = {
    'narration': {'type': 'string'},
    'background': {'type': 'string', 'enum': list(BACKGROUNDS)},
    'action': {'type': 'string', 'enum': list(ACTIONS)},
    'expression': {'type': 'string', 'enum': list(EXPRESSIONS)},
    'caption': {'type': 'string'}, 'overlay': {'type': 'string'},
    'message': {'type': 'string'}, 'event_delay': {'type': 'number'},
    'duration': {'type': 'number'}, 'audio': {'type': 'string'},
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'version': {'type': 'integer', 'enum': [1]}, 'title': {'type': 'string'},
        'voice': {'type': 'string'}, 'rate': {'type': 'string'},
        'scenes': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'properties': SCENE_PROPERTIES, 'required': list(SCENE_PROPERTIES),
        }},
    }, 'required': ['version', 'title', 'voice', 'rate', 'scenes'],
}


def clean_text(value, field, limit):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be text')
    if len(value) > limit:
        raise ValueError(f'{field} must be at most {limit} characters')
    if any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f'{field} contains unsupported control characters')
    return value.strip()


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('Story must be a JSON object')
    unknown = set(raw) - set(SCHEMA['properties'])
    if unknown:
        raise ValueError(f'Unknown story fields: {sorted(unknown)}')
    if raw.get('version', 1) != 1:
        raise ValueError('Only story version 1 is supported')
    story = dict(version=1, title=clean_text(raw.get('title', 'Untitled'), 'title', 100),
                 voice=raw.get('voice', 'en-US-AndrewNeural'), rate=raw.get('rate', '-3%'))
    if not isinstance(story['voice'], str) or not re.fullmatch(r'[A-Za-z0-9-]{3,100}', story['voice']):
        raise ValueError('voice must be a neural voice name, such as en-US-AndrewNeural')
    if not isinstance(story['rate'], str) or not re.fullmatch(r'[+-](?:[0-9]|[1-3][0-9]|40)%', story['rate']):
        raise ValueError('rate must be a signed percentage from -40% to +40%')
    items = raw.get('scenes')
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError('A story needs 1–20 scenes')
    scenes = []
    for i, item in enumerate(items):
        prefix = f'scene {i + 1}'
        if not isinstance(item, dict):
            raise ValueError(f'{prefix} must be an object')
        if set(item) - set(SCENE_PROPERTIES):
            raise ValueError(f'{prefix} contains unsupported fields: {sorted(set(item)-set(SCENE_PROPERTIES))}')
        scene = {**SCENE_DEFAULTS, **item}
        for key, limit in [('narration', 500), ('caption', 100), ('message', 130), ('overlay', 55), ('audio', 500)]:
            scene[key] = clean_text(scene.get(key, ''), f'{prefix}.{key}', limit)
        for key, values in [('background', BACKGROUNDS), ('action', ACTIONS), ('expression', EXPRESSIONS)]:
            if scene[key] not in values:
                raise ValueError(f'{prefix}.{key} must be one of {values}')
        if scene['action'] not in ACTIONS_BY_BACKGROUND[scene['background']]:
            raise ValueError(f"{prefix}: {scene['background']} supports {ACTIONS_BY_BACKGROUND[scene['background']]}")
        for key, lo, hi in [('duration', .5, 30), ('event_delay', 0, 10)]:
            value = scene[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f'{prefix}.{key} must be between {lo} and {hi}')
        scenes.append(scene)
    story['scenes'] = scenes
    return story


def load(path):
    return validate(json.loads(Path(path).read_text(encoding='utf-8')))


def authoring_prompt(prompt):
    return f'''Write an original 25–40 second stick figure reel about this brief:
{prompt}

Return only a JSON object matching the schema below, without Markdown fences.
Use 5–8 scenes, 65–90 spoken words in total. Give the protagonist a specific desire,
a believable obstacle, an action they choose, and a concrete ending. No unsupported
psychology claims or generic motivational slogans. Use conversational contractions.
Only use the supported scenes and actions. Do not pretend these are arbitrary
image-generation prompts: all characters use preset, fixed layouts. Match each
visual to what the narrator says. Vary expressions and actions sparingly.
Place is background: home (seated with phone), desk (seated with notebook), cafe
(two people at coffee), standing (one person gesturing). Actions: idle/read/type/
send/think/nod/celebrate. Expressions: neutral/happy/tired/shock.
Allowed combinations: home accepts all actions; desk accepts idle/read/type/think/nod;
cafe accepts idle/nod; standing accepts idle/think/nod/celebrate.
Caption: at most 70 characters. Message: at most 100 characters, or empty.
Overlay: a short time/location label, or empty. event_delay: when an on-screen
message appears, usually 0.5–1.5 seconds. duration: 3 for narrated scenes (audio
will determine their actual duration), 0.5–30 for scenes with no narration.
Use audio="" for all generated scenes. Voice en-US-AndrewNeural, rate -3%.
Schema:
{json.dumps(SCHEMA, indent=2)}
'''
