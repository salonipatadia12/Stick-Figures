"""episode.json: the single source of truth for one Short.

`Episode` is what lives on disk. `ScriptDraft` is the narrower shape Claude fills in;
`Episode.check()` holds the editorial rules that a JSON schema cannot express.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

Aspect = Literal['9:16', '1:1', '16:9']
ASPECT_SIZES = {'9:16': (1080, 1920), '1:1': (1080, 1080), '16:9': (1920, 1080)}

# script v5 (host format): hook -> promise -> story... -> payoff -> loop; v1-v4 roles stay valid for old episodes
BeatRole = Literal['hook', 'promise', 'story', 'setup', 'tension', 'reveal', 'payoff', 'loop', 'ending',
                   'problem', 'stakes', 'clue', 'reason', 'twist']
HOST_ROLES = ('hook', 'promise', 'story', 'payoff', 'loop')
# Script v6 (user 2026-11-09): a storyline is picked before the script, and the middle beats say what they do for the
# story, so the script gate can check for a conflict, rising stakes, a twist and a payoff that answers the hook.
STORY_ROLES = ('promise', 'setup', 'problem', 'stakes', 'clue', 'reason', 'twist', 'reveal', 'story')
StoryFormat = Literal['mini_story', 'myth_vs_fact', 'what_if', 'countdown', 'mystery', 'pov', 'experiment']
FORMATS = {
    'mini_story': 'the host has a problem and solves it (setup, problem, attempts that raise the stakes, a twist, the fix)',
    'myth_vs_fact': 'a belief everyone holds, tested and overturned; the real story is better than the myth',
    'what_if': '"What if...": a counterfactual played out step by step, each step bigger, then back to what really happens',
    'countdown': '"3 reasons..." counting down to the biggest or most surprising one; the last reason is the twist',
    'mystery': 'a puzzle in the hook; one clue per beat, each narrowing it down; the reveal at the end answers it',
    'pov': '"You\'re a farmer in 1816...": the viewer lives it in second person, the stakes rising, then the reveal of why',
    'experiment': 'the host runs an experiment on screen: a question, a setup, a prediction, a surprising result, why',
}
Framing = Literal['wide', 'medium', 'close', 'insert', 'top_down']
Transition = Literal['cut', 'whip', 'match_cut']
SfxName = Literal['pop', 'whoosh', 'ding', 'coin', 'thud', 'paper', 'typing']
MotionRecipe = Literal[
    # camera
    'push_in', 'pan_left', 'pan_right', 'punch_in',
    # character
    'parallax', 'breathe', 'pose_swap', 'enter_left', 'enter_right', 'exit_left', 'exit_right', 'impact_shake',
    # info graphics
    'text_slam', 'number_counter', 'underline_draw', 'arrow_draw', 'icon_pop', 'checklist_tick', 'split_compare',
]
# Recipes that change the picture on a specific spoken word.
ANCHORED_RECIPES = {'punch_in', 'pose_swap', 'impact_shake', 'text_slam', 'number_counter',
                    'underline_draw', 'arrow_draw', 'icon_pop', 'checklist_tick'}
MAX_ON_SCREEN_WORDS = 5

# ---- staging vocabulary (script v3). The base library is keyed by setting + pose + cast; the action
# library (pipeline/rig/actions.py) is keyed by verb. A script is composed from these, so the words are fixed.
Setting = Literal['cafe', 'bedroom', 'office', 'kitchen', 'living-room', 'doorway', 'street', 'park', 'classroom', 'gym', 'car', 'other']
PoseName = Literal['seated-at-table', 'standing', 'bed-edge', 'bed-awake', 'bed-asleep-top', 'seated-sofa', 'seated-desk',
                   'seated-bench', 'driving', 'passenger', 'walking']
Verb = Literal[
    # arm and head verbs the rig performs in place
    'reach', 'point', 'shrug', 'facepalm', 'pick_up', 'put_down', 'throw', 'drink', 'type', 'write', 'wave', 'nod',
    'shake_head', 'slump', 'jolt', 'gaze',
    # host verbs (script v5): a whole-body shiver, looking up at the sky, a finger snap, freezing mid-step
    'shiver', 'look_up', 'snap', 'freeze',
    # posture and travel: a painted torso cannot do these, so they are staged as a match cut between two bases
    # of the same setting on the anchor word (walk: a pan, or the far-off drawn figure)
    'lie_down', 'sit_up', 'stand_up', 'walk',
]
STAGING_VERBS = {'lie_down', 'sit_up', 'stand_up', 'walk'}

# ---- visual types (script v4). A beat is acted by the characters, or it SHOWS the subject: a painted scene with
# no characters (insert) or a code-drawn diagram (graphic). Explainer topics aim for at least half of the beats
# showing the subject; characters react to an insert by a cut on `reaction_anchor`.
VisualType = Literal['character', 'insert', 'graphic', 'split']
InsertEffect = Literal[
    # particles over the scene
    'ash', 'snow', 'dust', 'embers', 'rain', 'haze',
    # light and colour
    'grade', 'dim', 'flash', 'sun_path', 'crossfade',
    # camera ('focus': a close-up push into one region of the painting)
    'push', 'pan', 'shake', 'focus',
    # annotation ('pin': a map pin drops on a region, with a 1-3 word label)
    'label', 'arrow', 'pin',
]
Region = Literal['', 'sky', 'top', 'centre', 'ground', 'left', 'right', 'peak', 'horizon']
GraphicKind = Literal['flow', 'bounce', 'scatter', 'two_paths', 'waves', 'bars', 'thermometer', 'cards', 'layers']
MAX_EFFECTS, MAX_GRAPHIC_ITEMS = 4, 4
SUBJECT_TARGET = 0.5                    # share of beats the writer aims to give an insert or a graphic
PEOPLE = re.compile(r'\b(people|person|man|woman|men|women|child|children|kids?|farmers?|villagers?|crowd|figure|figures|'
                    r'character|characters|someone|somebody|family|families|workers?|he|she|they)\b', re.I)
MAX_ACTIONS, MAX_PROPS, MAX_CAST = 3, 4, 2
HostWhere = Literal['scene', 'pop_in', 'off']
HostSize = Literal['small', 'medium', 'large', 'close']
HOOK_MAX_WORDS, HOOK_OPTIONS = 8, 3
CHARACTER_TARGET = 0.7                  # script v5: the host is on screen in at least 70 % of the beats
# Things the approach cannot stage well; the script gate warns and suggests simpler staging (user item 8).
CROWD = re.compile(r'\b(crowd|crowded|crowds|dozens of|hundreds of|a group of|audience|everyone in the|packed with people|queue of|line of people)\b', re.I)
ANIMAL = re.compile(r'\b(dog|dogs|cat|cats|bird|birds|horse|horses|cow|cows|fish|animal|animals|pet|pets|puppy|kitten|pigeon|squirrel|insect|bee|bees|mouse|mice|rat|rats|wolf|lion|elephant)\b', re.I)
FAST = re.compile(r'\b(runs?|running|sprints?|sprinting|jumps?|jumping|leaps?|falls?|falling|trips? over|fights?|fighting|dances?|dancing|chases?|chasing|punches|kicks?|climbs?|climbing|swims?|swimming|rides? a bike|cycling|skating)\b', re.I)
WALK_FAR = re.compile(r'\b(across|around|through|down the|along|over to|out of|into|away|to the (door|window|other))\b', re.I)
GREETING = re.compile(r"^\s*(hi|hey|hello|welcome|what'?s up|in this video|today,? (we|i)|let'?s talk|so,)\b", re.I)
TALKING_HEAD = re.compile(r'talking head|talks? (directly )?(to|into) (the )?camera|speaks? to (the )?camera|addresses the viewer', re.I)
# A digit that is not part of an ordinal date ("the 20th") counts as a number claim.
# The possessive [\d,.]*+ stops "20th" from backtracking to a bare "2".
NUMBER = re.compile(r'(?<![\w])(?:[$€£₹¥]\s?)?\d[\d,.]*+(?!\s?(?:st|nd|rd|th)\b)%?', re.I)


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


# ---------------------------------------------------------------- draft (Claude-facing)
# Every field is required with no defaults: the structured-output schema stays fully
# specified, and validation of content limits happens in Episode.check().

class Motion(Strict):
    recipe: MotionRecipe
    anchor: str = Field(description="Exact word or short phrase from THIS beat's narration where the change "
                                    "happens. Required for punch_in, pose_swap, impact_shake and every info "
                                    "recipe; empty string for continuous moves (push_in, pan, parallax, breathe).")
    detail: str = Field(description='Recipe data or empty string. number_counter: "0 -> 31"; checklist_tick: '
                                    'items separated by " | "; icon_pop: one concrete object; '
                                    'split_compare: "left label | right label".')


class SfxCue(Strict):
    name: SfxName
    anchor: str = Field(description="Exact word from this beat's narration where the sound lands.")


class CastPose(Strict):
    who: str = Field(description='A cast id from shot.cast.')
    pose: PoseName = Field(description='The painted base pose this character holds for the whole beat.')


class ActionCue(Strict):
    who: str = Field(description='A cast id from shot.cast.')
    verb: Verb = Field(description='One verb from the action library.')
    target: str = Field(description='What the verb acts on: a prop name from props, a cast id, "window", "camera", '
                                    'or empty string.')
    anchor: str = Field(description="Exact word from THIS beat's narration where the action happens.")


class PropCue(Strict):
    name: str = Field(description='Short lowercase noun for one prop in the frame ("alarm clock", "coffee cup").')
    held_by: str = Field(description='Cast id holding it at the start of the beat, or empty string.')
    where: str = Field(description='Where it sits: "nightstand", "table", "left hand", "wall", "floor"…')


class EffectCue(Strict):
    kind: InsertEffect
    anchor: str = Field(description="Word of THIS beat's narration where the effect starts or peaks; empty string for the "
                                    'whole beat.')
    at: Region = Field(description='Where in the picture: sky, top, centre, ground, left, right, peak, horizon, or empty.')
    detail: str = Field(description='grade: "blue -> red" or "warm -> cold"; crossfade: the second scene in a few words (same '
                                    'place, changed); sun_path: "high -> low"; label: 1-3 words; arrow: "from -> to" regions; '
                                    'otherwise empty string.')


class InsertCue(Strict):
    """A full-frame painted scene with NO characters, generated once and kept in the insert library, animated in code."""
    key: str = Field(description='Short kebab-case name for the scene, reused across videos when it already exists '
                                 '("volcano-erupting", "street-sunset").')
    scene: str = Field(description='The place or thing, painted, 10-30 words: what is in frame, time of day, weather. '
                                   'No people, no characters, no text.')
    effects: list[EffectCue] = Field(description='0-4 code effects that animate the scene on narration words.')


class GraphicCue(Strict):
    """A code-drawn diagram on paper: flow (A -> B -> C), bounce (light hits a layer and bounces back), scatter (a beam
    hits small particles and sprays in every direction), two_paths (light's short vs long path through the air), waves
    (short wave scatters, long wave passes), bars (2-3 bars), thermometer (a value falls or rises), cards
    (chapters/steps), layers (stacked bands: ground, air, haze)."""
    kind: GraphicKind
    items: list[str] = Field(description='1-4 labels, 1-3 words each, in reading order.')
    anchors: list[str] = Field(description="Word of THIS beat's narration where each item appears (same length as items), "
                                           'or empty list to reveal them in turn.')
    detail: str = Field(description='bars: relative heights "3 | 5" (only numbers the narration says or illustrative '
                                    'heights with no axis); thermometer: "from -> to"; otherwise empty string.')


class SplitCue(Strict):
    """Two painted scenes stacked, top and bottom (myth vs fact, before vs after, here vs there), each labelled; the
    bottom one wipes in on its label's word."""
    key: str = Field(description='Kebab-case name of the second (bottom) scene; the top scene is shot.insert.')
    scene: str = Field(description='The second scene, painted WITHOUT people, 10-30 words.')
    labels: list[str] = Field(description='Two labels of 1-3 words: top, bottom ("Myth", "Fact").')
    anchors: list[str] = Field(default_factory=list, description='Narration word where each label (and the bottom scene) appears.')


class Staging(Strict):
    """What the beat needs from the libraries: which painted base (setting + pose per character), which verbs
    on which words, which props. The base planner and the composer read these fields, not the prose."""
    setting: Setting = Field(description='The place. "other" only when none of the listed settings fits.')
    setting_detail: str = Field(description='For "other": the place in 3-6 words. Otherwise empty string.')
    poses: list[CastPose] = Field(description='One entry per cast id in shot.cast; empty for object-only inserts.')
    actions: list[ActionCue] = Field(description='0-3 verbs, each on a narration word. Prefer the in-place verbs; '
                                                 'lie_down/sit_up/stand_up/walk become a cut to another base.')
    props: list[PropCue] = Field(description='0-4 props the picture needs beyond the base furniture.')


class HostCue(Strict):
    """Script v5: the lead is the HOST. It talks to the viewer and acts the story out INSIDE the picture: full-body in
    the painted scene (an insert or a setting), matched to its light and scale, or popping in at the frame edge to
    point at a diagram. Its pose and verbs are the "lead" entries of shot.cast and shot.staging (kept there so the
    output grammar stays small)."""
    where: HostWhere = Field(description='scene: full-body inside this beat\'s picture (the insert scene or the setting); '
                                         'pop_in: pops in at a frame edge to point at the diagram; off: not on screen.')
    side: Literal['left', 'centre', 'right'] = Field('centre', description='Where the host stands (or pops in): left, '
                                                         'centre or right. Vary it from beat to beat.')
    size: HostSize = Field('medium', description='small (far, the place dominates), medium (full body), large (knees up, '
                                                 'close to camera) or close (head and shoulders: a reaction or a confession). '
                                                 'Vary it from beat to beat.')
    holds: str = Field('', description='A prop the host holds up or uses in this beat ("jar of honey", "old map"), or '
                                       'empty string.')
    doing: str = Field(description='What the host does in the picture, 4-15 words ("shivers in a t-shirt, staring at '
                                   'snow on the roses").')
    to_camera: bool = Field(description='True when the host talks to the viewer (eyes to camera, lips moving); false '
                                        'when it watches the scene.')
    cut_on: str = ''        # a narration word where the picture cuts to the host at the next spot (no walk animation)


class DraftShot(Strict):
    host: HostCue = Field(description='Where the host is and what it does in this beat; where="off" only when the host '
                                      'truly is not in the picture. When the host is on screen, "lead" is in cast with a '
                                      'pose and its verbs in staging.')
    visual: VisualType = Field(description='character: the characters act it out; insert: a painted scene of the subject '
                                           'with no characters; graphic: a drawn diagram. On explainer topics at least '
                                           'half of the beats are insert or graphic.')
    insert: InsertCue | None = Field(description='Required when visual is insert, else null.')
    graphic: GraphicCue | None = Field(description='Required when visual is graphic, else null.')
    split: SplitCue | None = None
    reaction_anchor: str = Field('', description='insert/graphic beats: a narration word where the picture cuts to the '
                                                 'cast reacting; empty string for no reaction cut.')
    framing: Framing = 'medium'
    cast: list[str] = Field(description='Style cast ids visible in the frame; empty for object-only inserts.')
    staging: Staging
    motion: list[Motion] = Field(description='1-2 motion recipes.')
    # Not asked of the writer since script v5 (WIRE_OMIT: a smaller output grammar); earlier episodes keep theirs.
    image_prompt: str = ''
    pose_variants: list[str] = Field(default_factory=list)
    transition: Transition = 'cut'


class DraftBeat(Strict):
    role: BeatRole
    narration: str = Field(description='Spoken words for this beat: one idea, conversational, short sentences.')
    on_screen_text: str = Field(description='At most 5 words shown on screen, or empty string.')
    visual_intent: str = Field(description='How the picture SHOWS the idea through action, metaphor or a '
                                           'physical diagram. Never a talking head.')
    shot: DraftShot
    sfx: list[SfxCue]
    source_ids: list[str] = Field(description='Ids of research facts this beat relies on; empty if none.')
    illustrative_numbers: bool = Field(description='True only when the narration uses a made-up example number '
                                                   'that is clearly framed as an example ("say you earn 3,000").')


WIRE_OMIT = {'DraftShot': ('image_prompt', 'pose_variants', 'transition', 'reaction_anchor', 'framing'),
             'HostCue': ('cut_on',), 'HookOption': ('host_doing', 'scene_key', 'scene')}
# The writer fills a flat "picture" of plain strings instead of the nested insert/graphic objects: with them the
# compiled output grammar was too large for the API (HTTP 400, measured 2026-11-08); from_wire() rebuilds them.
PICTURE = {
    'type': 'object', 'additionalProperties': False,
    'description': 'What the frame shows besides the host. For visual "insert" fill the insert_* fields; for "graphic" '
                   'fill the graphic_* fields; leave the others as empty strings.',
    'properties': {
        'insert_key': {'type': 'string', 'description': 'Kebab-case scene name, reused across videos ("volcano-erupting").'},
        'insert_scene': {'type': 'string', 'description': 'The place or thing painted WITHOUT people, 10-30 words: what is '
                                                          'in frame, time of day, weather. No text.'},
        'insert_effects': {'type': 'string', 'description':
            '0-4 code effects separated by ";", each "kind | anchor word | region | detail". kind: ash, snow, dust, embers, '
            'rain, haze, grade, dim, flash, sun_path, crossfade, push, pan, shake, label, arrow. region: sky, top, centre, '
            'ground, left, right, peak, horizon or empty. detail: grade "blue -> red" or "warm -> cold"; crossfade the '
            'changed scene in a few words; sun_path "high -> low"; label 1-3 words; arrow "from -> to" regions; else '
            'empty; focus: a close-up push into the region; pin: a map pin on the region, detail its 1-3 word label. '
            'Example: "ash | | | ; shake | erupted | | ; label | sulfur | sky | Sulfur". No ";" inside a field.'},
        'split': {'type': 'string', 'description': 'visual "split" only: "bottom-scene-key | the bottom scene painted '
                                                   'without people, 10-30 words | Top label@word | Bottom label@word" (the '
                                                   'top scene is insert_*), e.g. "honey-lab-bench | a lab bench with jars | '
                                                   'Myth@says | Fact@actually"; else empty.'},
    },
}
PICTURE['required'] = list(PICTURE['properties'])


def wire_schema(schema: dict) -> dict:
    """The structured-output schema sent to the writer: without the fields it is no longer asked for, and with the
    flat picture in place of the nested insert/graphic objects."""
    defs = schema.get('$defs', {})
    for name, fields in WIRE_OMIT.items():
        d = defs.get(name)
        if d:
            for f in fields:
                d.get('properties', {}).pop(f, None)
                if f in d.get('required', []):
                    d['required'].remove(f)
    host = defs.get('HostCue')
    if host:                                           # v6: side and size travel as one string (a smaller grammar)
        props = {}
        for k, val in host.get('properties', {}).items():
            if k == 'side':
                props['placement'] = {'type': 'string', 'description': '"<side> <size>", then "watching" when the host '
                                      'watches the scene instead of talking to the viewer, then "holding <thing>" when it '
                                      'holds a prop. side: left, centre or right; size: small (the place dominates), medium '
                                      '(full body), large (knees up, near the camera) or close (head and shoulders). E.g. '
                                      '"left medium", "right large holding a jar of honey", "centre small watching". Vary '
                                      'it beat to beat.'}
            elif k not in ('size', 'holds', 'to_camera'):
                props[k] = val
        host['properties'] = props
        host['required'] = list(props)
    stg = defs.get('Staging')
    if stg and 'setting_detail' in stg.get('properties', {}):   # setting and its detail as one string
        stg['properties'].pop('setting_detail')
        stg['properties']['setting'] = {'type': 'string', 'description': 'The place from the list (cafe, bedroom, office, '
                                        'kitchen, living-room, doorway, street, park, classroom, gym, car), or "other: <the '
                                        'place in 3-6 words>".'}
        stg['required'] = list(stg['properties'])
        defs.pop('Setting', None)
    beat = defs.get('DraftBeat')
    if beat and 'role' in beat.get('properties', {}):  # the role as a plain string: 14 enum values compiled too large
        beat['properties']['role'] = {'type': 'string', 'description': 'What the beat does: hook (beat 1), then setup, '
                                      'problem, stakes, clue, reason, twist, reveal or promise, then payoff, then loop '
                                      '(last).'}
        defs.pop('BeatRole', None)
    shot = defs.get('DraftShot')
    if shot and 'insert' in shot['properties']:
        props = {}
        for k, val in shot['properties'].items():          # keep the field order, picture where insert was
            if k == 'insert':
                props['picture'] = {'$ref': '#/$defs/Picture'}
            elif k not in ('graphic', 'split'):
                props[k] = val
        shot['properties'] = props
        shot['required'] = list(props)
        defs['Picture'] = PICTURE
        for unused in ('InsertCue', 'EffectCue', 'GraphicCue', 'SplitCue'):
            defs.pop(unused, None)
    return schema


def _split(text: str, sep: str) -> list:
    return [x.strip() for x in (text or '').split(sep)]


def from_wire(data: dict) -> tuple[dict, list]:
    """Rebuild insert/graphic objects from each shot's flat picture. Unknown effect kinds, regions or graphic kinds
    are dropped with a note rather than losing the whole (paid) script."""
    from typing import get_args
    notes = []
    kinds, regions, gkinds = set(get_args(InsertEffect)), set(get_args(Region)), set(get_args(GraphicKind))
    roles = set(get_args(BeatRole))
    for n, beat in enumerate(data.get('beats', []), 1):
        shot = beat.get('shot', {})
        if beat.get('role') is not None and beat['role'] not in roles:
            notes.append(f'b{n:02d}: role "{beat["role"]}" unknown; read as "story"')
            beat['role'] = 'story'
        host = shot.get('host')
        if isinstance(host, dict) and 'placement' in host:
            text = (host.pop('placement') or '').strip()
            head, _, held = text.partition('holding ')
            words = head.lower().replace(',', ' ').split()
            host['side'] = next((w for w in words if w in ('left', 'centre', 'center', 'right')), 'centre').replace('center', 'centre')
            host['size'] = next((w for w in words if w in ('small', 'medium', 'large', 'close')), 'medium')
            host['to_camera'] = 'watching' not in words
            host['holds'] = held.strip().strip('."')
        stg = shot.get('staging')
        if isinstance(stg, dict) and 'setting_detail' not in stg:
            place, _, detail = (stg.get('setting') or 'other').partition(':')
            place = place.strip().lower()
            if place not in get_args(Setting):
                detail, place = (detail or place).strip(), 'other'
            stg['setting'], stg['setting_detail'] = place, detail.strip() if place == 'other' else ''
        pic = shot.pop('picture', None)
        if pic is None:
            continue
        insert = graphic = None
        if shot.get('visual') == 'insert' or pic.get('insert_key'):
            effects = []
            for raw in filter(None, _split(pic.get('insert_effects', ''), ';')):
                f = (_split(raw, '|') + ['', '', '', ''])[:4]
                if f[0] not in kinds:
                    notes.append(f'b{n:02d}: effect "{f[0]}" is not in the library; dropped')
                    continue
                if f[2] not in regions:
                    notes.append(f'b{n:02d}: region "{f[2]}" unknown; effect kept without a region')
                    f[2] = ''
                effects.append(dict(kind=f[0], anchor=f[1], at=f[2], detail=f[3]))
            if pic.get('insert_key'):
                insert = dict(key=pic['insert_key'], scene=pic.get('insert_scene', ''), effects=effects)
        if shot.get('visual') == 'graphic' or pic.get('graphic_kind'):
            kind = (pic.get('graphic_kind') or '').strip()
            if kind in gkinds:
                pairs = [x.split('@', 1) + [''] for x in _split(pic.get('graphic_items', ''), '|') if x]
                items = [a.strip() for a, b, *_ in pairs]
                anchors = [b.strip() for a, b, *_ in pairs]
                anchors = anchors if all(anchors) else []
                graphic = dict(kind=kind, items=items, anchors=anchors if len(anchors) == len(items) else [],
                               detail=pic.get('graphic_detail', ''))
            elif kind:
                notes.append(f'b{n:02d}: graphic kind "{kind}" is not in the library; dropped')
        split = None
        parts = _split(pic.get('split', ''), '|')
        if len(parts) >= 3 and parts[0]:
            pairs = [x.split('@', 1) + [''] for x in parts[2:4]]
            split = dict(key=parts[0], scene=parts[1], labels=[a.strip() for a, b, *_ in pairs],
                         anchors=[b.strip() for a, b, *_ in pairs])
        elif shot.get('visual') == 'split':
            notes.append(f'b{n:02d}: a split with no second scene; shown as the top scene alone')
            shot['visual'] = 'insert'
        shot['insert'], shot['graphic'], shot['split'] = insert, graphic, split
    return data, notes


class HookOption(Strict):
    """One of three hooks the user chooses between at the script gate. Option 1 is written into beat 1; picking
    another rewrites beat 1 from these fields (flat on purpose: a nested beat made the output grammar too large)."""
    line: str = Field(description=f'The spoken hook, at most {HOOK_MAX_WORDS} words: a question or a bold, honest claim '
                                  'that the payoff answers. Never walked back later.')
    frame_one: str = Field(description='What the very first frame shows: the host already in a surprising situation, '
                                       '10-25 words.')
    # filled at the gate when the user picks the hook (not asked of the writer: a smaller output grammar)
    host_doing: str = ''
    scene_key: str = ''
    scene: str = ''
    answer: str = Field(description='One sentence: how the payoff beat answers this hook.')
    loop_line: str = Field(description='The narration of the final loop beat for this hook: its last words tie back to '
                                       'the hook line.')


class Storyline(Strict):
    """One way to tell the topic as a story (script v6): the user picks one of three before any script is written."""
    format: StoryFormat
    title: str = Field(description='Working title, at most 60 characters.')
    hook: str = Field(description=f'The opening line, at most {HOOK_MAX_WORDS} words, honest, answered by the payoff.')
    pitch: str = Field(description='The story in 2-3 short sentences: the setup and conflict, the twist, the payoff.')
    conflict: str = Field(description='The problem or tension that drives it, one sentence.')
    stakes: str = Field(description='How the stakes rise along the way, one sentence.')
    twist: str = Field(description='The surprise or turn, one sentence. It must be true (from the research).')
    payoff: str = Field(description='How the ending answers the hook, one sentence.')
    visuals: list[str] = Field(description='3-5 picture ideas in order, each a few words, mixing tools: a scene that '
                                           'animates, a close-up, a split screen, a map, an object the host holds or uses, '
                                           'a camera move, a diagram (at most one).')
    fact_ids: list[str] = Field(description='Research fact ids the story stands on.')


class StoryPitch(Strict):
    story_supported: bool = Field(description='False when the facts cannot carry a real story (conflict, rising stakes, '
                                              'a true twist) without inventing things.')
    note: str = Field(description='When story_supported is false: why, in one or two sentences. Else empty string.')
    storylines: list[Storyline] = Field(description='Exactly 3 storylines, each in a DIFFERENT allowed format.')


class ScriptDraft(Strict):
    title: str = Field(description='Working title, at most 60 characters.')
    hook_promise: str = Field(description='The question or tension the hook opens; the payoff must resolve it.')
    hook_options: list[HookOption] = Field(description=f'Exactly {HOOK_OPTIONS} hooks. Option 1 is the one used in beats.')
    subject_settings: list[str] = Field(description='Settings that ARE the subject (e.g. "doorway" for a video about '
                                                       'doorways); empty for most topics.')
    beats: list[DraftBeat]


# ---------------------------------------------------------------- episode (on disk)

class Layer(Strict):
    kind: Literal['background', 'character', 'prop', 'variant']
    file: str


class Shot(DraftShot):
    host: HostCue | None = None                        # script v5; None on earlier episodes
    staging: Staging | None = None                     # None on episodes written before script v3 (keyword planning)
    visual: VisualType = 'character'                   # script v4; earlier episodes are all character beats
    insert: InsertCue | None = None
    graphic: GraphicCue | None = None
    reaction_anchor: str = ''
    layers: list[Layer] = Field(default_factory=list)  # filled by pipeline/layers.py (Phase 2)


class Beat(Strict):
    # Same fields as DraftBeat, with the stable id first so episode.json reads top-down.
    id: str = Field(pattern=r'^b\d{2}$')
    role: BeatRole
    narration: str
    on_screen_text: str = ''
    visual_intent: str
    shot: Shot
    sfx: list[SfxCue] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    illustrative_numbers: bool = False
    duration: float | None = None  # filled from the real narration audio (Phase 3)

    @classmethod
    def from_draft(cls, index, draft):
        return cls(id=f'b{index + 1:02d}', **draft.model_dump())


V4_DEFAULTS = dict(visual='character', insert=None, graphic=None, reaction_anchor='', host=None, split=None)


class Source(Strict):
    id: str = Field(pattern=r'^s\d+$')
    claim: str
    url: str = Field(pattern=r'^https?://')
    title: str = ''
    quote: str = ''


class Voice(Strict):
    """Narration runs locally on Kokoro-82M (Apache-2.0)."""
    provider: Literal['kokoro'] = 'kokoro'
    voice_id: str = ''  # Kokoro voice; empty = the style default (cozy: am_michael, alternate af_heart)


class Episode(Strict):
    schema_version: int = SCHEMA_VERSION
    slug: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,63}$')
    topic: str = Field(min_length=3)
    niche: str = ''
    audience: str = ''
    style: str = 'cozy'
    aspect: Aspect = '9:16'
    target_length: int = Field(45, ge=15, le=180)
    voice: Voice = Field(default_factory=Voice)
    title: str = ''
    hook_promise: str = ''
    hook_options: list[HookOption] = Field(default_factory=list)   # script v5: three hooks for the user to choose from
    hook_pick: int = 0                                             # 1-3 once the user picked; 0 = not yet
    storylines: list[Storyline] = Field(default_factory=list)      # script v6: three pitches before the script
    storyline_pick: int = 0                                        # 1-3 once the user picked; 0 = not yet
    story_note: str = ''                                           # the writer's note when a topic cannot carry a story
    subject_settings: list[str] = Field(default_factory=list)
    beats: list[Beat] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    approvals: dict[str, str] = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)

    # ---- content identity: approvals are bound to these digests
    @staticmethod
    def _shot_dump(shot):
        d = shot.model_dump(exclude={'layers'})
        for k, default in V4_DEFAULTS.items():          # script-v4 fields at their defaults: same digest as before v4
            if d.get(k) == default:
                d.pop(k, None)
        if isinstance(d.get('host'), dict):             # v6 host fields at their defaults: same digest as v5
            for k, default in (('size', 'medium'), ('holds', '')):
                if d['host'].get(k) == default:
                    d['host'].pop(k, None)
        return d

    def script_digest(self):
        content = dict(title=self.title, hook_promise=self.hook_promise, sources=[s.model_dump() for s in self.sources],
                       beats=[b.model_dump(exclude={'duration'}, exclude_none=True) | {'shot': self._shot_dump(b.shot)}
                              for b in self.beats])
        if self.hook_options:                          # script v5 only: earlier digests stay the same
            content |= dict(hook_options=[h.model_dump() for h in self.hook_options], hook_pick=self.hook_pick,
                            subject_settings=list(self.subject_settings))
        if self.storyline_pick:                        # script v6: the picked storyline is part of what was approved
            content |= dict(storyline=self.storyline.model_dump())
        return digest(content)

    @property
    def storyline(self):
        return self.storylines[self.storyline_pick - 1] if self.storyline_pick else None

    @property
    def story_format(self) -> str:
        return self.storyline.format if self.storyline else ''

    @property
    def host_format(self) -> bool:
        return bool(self.hook_options)

    def pick_hook(self, n: int):
        """Use hook option n (1-based): beat 1 becomes its hook beat and the loop beat says its loop line."""
        if not 1 <= n <= len(self.hook_options):
            raise ValueError(f'hook option {n}: pick 1-{len(self.hook_options)}')
        opt = self.hook_options[n - 1]
        b = self.beats[0]
        b.narration, b.visual_intent = opt.line, opt.frame_one
        spoken = opt.line.casefold()
        keep = lambda anchor: not anchor or anchor.casefold() in spoken          # cues on words the new line no longer says go
        b.shot.motion = [m for m in b.shot.motion if keep(m.anchor)] or [Motion(recipe='push_in', anchor='', detail='')]
        b.sfx = [x for x in b.sfx if keep(x.anchor)]
        if b.on_screen_text and not all(w.casefold() in spoken for w in re.findall(r"[\w']+", b.on_screen_text)):
            b.on_screen_text = ''
        if b.shot.host:
            b.shot.host.doing = opt.host_doing or opt.frame_one
        if b.shot.staging:
            b.shot.staging.actions = [a for a in b.shot.staging.actions if keep(a.anchor)]
        if opt.scene_key:
            effects = [e for e in (b.shot.insert.effects if b.shot.insert else []) if keep(e.anchor)]
            b.shot.visual, b.shot.insert = 'insert', InsertCue(key=opt.scene_key, scene=opt.scene, effects=effects)
        elif b.shot.insert:
            b.shot.insert.effects = [e for e in b.shot.insert.effects if keep(e.anchor)]
        if self.beats[-1].role in ('loop', 'ending'):
            self.beats[-1].narration = opt.loop_line
        self.hook_pick = n

    def beat_digest(self, beat):
        """Per-beat identity so later stages regenerate only beats whose content changed."""
        return digest(dict(style=self.style, aspect=self.aspect,
                           beat=beat.model_dump(exclude={'duration', 'id'}) | {'shot': self._shot_dump(beat.shot)}))

    def is_approved(self, gate):
        if gate == 'script':
            return self.approvals.get('script') == self.script_digest()
        if gate == 'bases':                       # bound to the base plan (pipeline/bases.py) by its digest
            return 'bases' in self.approvals
        return False

    # ---- editorial rules
    def word_count(self):
        return sum(len(b.narration.split()) for b in self.beats)

    def check(self, cast_ids=(), words_per_second=2.7):
        issues = []
        err = lambda where, msg: issues.append(Issue('error', where, msg))
        warn = lambda where, msg: issues.append(Issue('warning', where, msg))
        if not self.beats:
            err('beats', 'no beats yet')
            return issues
        lo, hi = beat_range(self.target_length)
        if not lo <= len(self.beats) <= hi:
            err('beats', f'{len(self.beats)} beats; a {self.target_length}s Short needs {lo}-{hi} (one idea per beat)')
        ids = [b.id for b in self.beats]
        if len(set(ids)) != len(ids):
            err('beats', 'beat ids must be unique')
        if self.beats[0].role != 'hook':
            err(self.beats[0].id, 'the first beat must be the hook')
        if self.host_format:
            self._check_host_format(err, warn)
        if self.host_format and self.storyline_pick:    # script v6: written from a picked storyline
            for item, ok, detail in self.story_report():
                if not ok:
                    err('story', f'{item}: {detail}')
        if not any(b.role == 'payoff' for b in self.beats):
            err('beats', 'no payoff beat: the hook promise is never delivered')
        if self.beats[-1].role == 'payoff':           # a payoff that closes the video (user cut the ending, 2026-11-08)
            warn(self.beats[-1].id, 'the video ends on the payoff with no loop-back ending')
        elif self.beats[-1].role not in ('ending', 'loop'):
            err(self.beats[-1].id, 'the last beat must be the ending (loop back or tease a follow-up)')
        if not self.hook_promise:
            err('hook_promise', 'state what the hook promises so the payoff can be checked against it')
        hook = self.beats[0].narration
        first_sentence = re.split(r'(?<=[.!?])\s', hook, maxsplit=1)[0]
        if GREETING.search(hook):
            err(self.beats[0].id, 'the hook opens with a greeting or preamble; open on the tension itself')
        if len(first_sentence.split()) > 10:
            err(self.beats[0].id, f'hook sentence is {len(first_sentence.split())} words; it must land in ~2 s (<= 10 words)')
        elif len(first_sentence.split()) > 7:
            warn(self.beats[0].id, 'hook sentence is over 7 words; tighter hooks land within 2 s')
        source_ids = {s.id for s in self.sources}
        for b in self.beats:
            where = b.id
            words = [w for w in b.on_screen_text.split() if any(c.isalnum() for c in w)]   # '+' or '&' is not a word
            if len(words) > MAX_ON_SCREEN_WORDS:
                err(where, f'on-screen text has {len(words)} words (max {MAX_ON_SCREEN_WORDS})')
            if not b.narration:
                err(where, 'empty narration')
            if not b.visual_intent:
                err(where, 'missing visual intent')
            if not self.host_format and TALKING_HEAD.search(b.visual_intent + ' ' + b.shot.image_prompt):
                err(where, 'visual is a talking head; show the idea through action, metaphor or a diagram')
            if not 1 <= len(b.shot.motion) <= 2:
                err(where, f'{len(b.shot.motion)} motion recipes; pick 1-2')
            spoken = b.narration.casefold()
            for m in b.shot.motion:
                if m.recipe in ANCHORED_RECIPES and not m.anchor:
                    err(where, f'{m.recipe} needs an anchor word from the narration')
                if m.anchor and m.anchor.casefold() not in spoken:
                    err(where, f'{m.recipe} anchor "{m.anchor}" is not in the narration')
                if m.recipe == 'pose_swap' and not b.shot.pose_variants and not self.host_format:
                    err(where, 'pose_swap needs at least one pose variant')
            if len(b.shot.pose_variants) > 2:
                err(where, 'at most 2 pose variants')
            for s in b.sfx:
                if s.anchor.casefold() not in spoken:
                    err(where, f'sfx {s.name} anchor "{s.anchor}" is not in the narration')
            unknown = [c for c in b.shot.cast if cast_ids and c not in cast_ids]
            if unknown:
                err(where, f'unknown cast {unknown}; this style has {list(cast_ids)}')
            missing = [i for i in b.source_ids if i not in source_ids]
            if missing:
                err(where, f'cites unknown sources {missing}')
            numbers = NUMBER.findall(b.narration + ' ' + b.on_screen_text)
            if numbers and not b.source_ids and not b.illustrative_numbers:
                err(where, f'number(s) {numbers} with no source; cite a research fact or frame it as an example')
            if b.illustrative_numbers and not numbers:
                warn(where, 'marked illustrative_numbers but contains no number')
            seconds = len(b.narration.split()) / words_per_second
            anchored = (any(m.recipe in ANCHORED_RECIPES for m in b.shot.motion)
                        or (b.shot.visual == 'insert' and b.shot.insert and any(e.anchor for e in b.shot.insert.effects))
                        or (b.shot.visual == 'graphic' and b.shot.graphic is not None) or bool(b.shot.reaction_anchor))
            if seconds > 2.5 and not anchored:
                warn(where, f'~{seconds:.1f}s of narration with no word-anchored change; the picture may sit static')
            if len(b.shot.cast) > MAX_CAST:
                warn(where, f'{len(b.shot.cast)} characters in one frame; the bases hold at most {MAX_CAST}. Stage it as two shots')
            self._check_staging(b, spoken, err, warn)
            self._check_visual(b, spoken, err, warn)
        shows = sum(1 for b in self.beats if b.shot.visual in ('insert', 'graphic'))
        if any(b.shot.visual != 'character' for b in self.beats) and shows < SUBJECT_TARGET * len(self.beats):
            warn('beats', f'{shows}/{len(self.beats)} beats show the subject (insert or graphic); explainers aim for at least '
                          f'{int(SUBJECT_TARGET * 100)}%. Suggest: turn a character beat about a thing, place or process into an insert')
        expected = self.target_length * words_per_second
        words = self.word_count()
        if not 0.75 * expected <= words <= 1.2 * expected:
            warn('beats', f'{words} narration words ~ {words / words_per_second:.0f}s; target {self.target_length}s '
                          f'is ~{expected:.0f} words at {words_per_second} w/s')
        return issues

    def _check_host_format(self, err, warn):
        """Script v5 (user 2026-11-08): the lead hosts and tells the story; hook -> promise -> story -> payoff -> loop;
        a hook of at most 8 words with the host in frame 1, answered in the payoff; the last line ties back."""
        roles = [b.role for b in self.beats]
        story = bool(self.storyline_pick)              # v6: the shape and the middle roles are checked by story_report
        if not story and any(r not in HOST_ROLES for r in roles):
            err('beats', f'host format roles are {"/".join(HOST_ROLES)}; got {sorted(set(roles) - set(HOST_ROLES))}')
        order = [r for r in roles if r in HOST_ROLES]
        if not story and len(roles) >= 4 and (roles[1] != 'promise' or roles[-1] != 'loop' or 'payoff' not in roles
                                or roles.index('payoff') != len(roles) - 2 or any(r != 'story' for r in roles[2:-2])):
            err('beats', f'order must be hook, promise, story..., payoff, loop; got {", ".join(order)}')
        if len(self.hook_options) != HOOK_OPTIONS:
            err('hook_options', f'{len(self.hook_options)} hook options; give exactly {HOOK_OPTIONS}')
        for k, h in enumerate(self.hook_options, 1):
            n = len(re.findall(r"[\w'’-]+", h.line))
            if n > HOOK_MAX_WORDS:
                err(f'hook {k}', f'"{h.line}" is {n} words; at most {HOOK_MAX_WORDS}')
            if not h.answer:
                err(f'hook {k}', 'say how the payoff answers this hook')
            if not _shares_word(h.loop_line, h.line):
                warn(f'hook {k}', f'loop line "{h.loop_line}" does not echo the hook "{h.line}"; the last line should tie back')
        if self.hook_options and self.beats[0].narration.strip() != self.hook_options[(self.hook_pick or 1) - 1].line.strip():
            warn(self.beats[0].id, 'beat 1 should say exactly the chosen hook line, nothing more (0-3 s)')
        if not self.beats[0].shot.host or self.beats[0].shot.host.where == 'off':
            err(self.beats[0].id, 'frame 1 must show the host')
        hosted = [b for b in self.beats if b.shot.host and b.shot.host.where != 'off']
        if len(hosted) < CHARACTER_TARGET * len(self.beats):
            warn('beats', f'the host is on screen in {len(hosted)}/{len(self.beats)} beats; aim for at least '
                          f'{int(CHARACTER_TARGET * 100)}% (the host tells the story)')
        for b in self.beats:
            if b.shot.host is None:
                err(b.id, 'shot.host is missing: say where the host is in this beat')
                continue
            if b.shot.host.where != 'off' and 'lead' not in b.shot.cast:
                err(b.id, 'the host is on screen: put "lead" in shot.cast with a pose in staging')
            if 'friend' in b.shot.cast and b.shot.visual == 'character' and b.shot.staging and b.shot.staging.setting in ('cafe', 'living-room'):
                warn(b.id, 'the friend chatting at a cafe or on a sofa: keep the friend for moments the story needs')

    def story_report(self) -> list:
        """The story check at the script gate (user 2026-11-09): [(item, passed, detail)] for a clear conflict, rising
        stakes, a twist or surprise, and a payoff that answers the hook."""
        roles = [b.role for b in self.beats]
        fmt = self.story_format
        out = []
        if len(roles) < 5 or roles[-1] != 'loop' or roles[-2] != 'payoff':
            out.append(('shape', False, f'hook ... payoff, loop expected; got {", ".join(roles)}'))
        bad = sorted({r for r in roles[1:-2] if r not in STORY_ROLES})
        if bad:
            out.append(('roles', False, f'middle beats use {bad}; use {", ".join(STORY_ROLES)}'))
        first = next((i for i, r in enumerate(roles) if r == 'problem'), None)
        out.append(('conflict', first is not None and first <= 3,
                    f'problem stated in {self.beats[first].id}' if first is not None and first <= 3 else
                    'no "problem" beat in the first beats: the story needs a clear conflict early'))
        pay = len(roles) - 2
        start = first if first is not None else 1
        rising = [self.beats[i].id for i in range(start + 1, pay) if roles[i] in ('stakes', 'clue', 'reason')]
        need = 2 if fmt in ('mystery', 'countdown') else 1
        out.append(('rising stakes', len(rising) >= need,
                    f'{", ".join(rising)} raise the stakes' if len(rising) >= need else
                    f'{len(rising)} beat(s) raise the stakes after the problem; a {fmt or "story"} needs at least {need} '
                    '("stakes", or "clue"/"reason" beats)'))
        twists = [self.beats[i].id for i in range(start + 1, pay + 1) if roles[i] in ('twist', 'reveal')]
        out.append(('twist', bool(twists), f'twist in {", ".join(twists)}' if twists else
                    'no "twist" or "reveal" beat before the payoff: add the surprise'))
        answer = ''
        if self.hook_options:
            answer = self.hook_options[(self.hook_pick or 1) - 1].answer
        if self.storyline:
            answer += ' ' + self.storyline.payoff
        payoff = self.beats[pay].narration if 0 <= pay < len(self.beats) else ''
        shared = sorted(_keywords(answer) & _keywords(payoff))
        out.append(('payoff answers the hook', len(shared) >= 2,
                    f'shares {", ".join(shared)}' if len(shared) >= 2 else
                    f'the payoff "{payoff}" does not say the promised answer ("{answer.strip()}")'))
        return out

    @staticmethod
    def _check_staging(b, spoken, err, warn):
        """Staging fields are consistent with the shot, and what the rig cannot stage well is flagged with a
        simpler staging, at the script gate, before any base or render work (user item 8)."""
        where = b.id
        prose = f'{b.visual_intent} {b.shot.image_prompt}'
        if CROWD.search(prose):
            warn(where, f'crowd in the picture ("{CROWD.search(prose).group(0)}"): the rig draws at most a few far-off figures. '
                        'Suggest: 3-6 small figures behind glass, or an empty place with signs of people')
        if ANIMAL.search(prose):
            warn(where, f'animal in the picture ("{ANIMAL.search(prose).group(0)}"): no animal rig. Suggest: a prop that implies it '
                        '(a leash, a bowl, feathers), or leave it to the narration')
        if FAST.search(prose):
            warn(where, f'fast physical action ("{FAST.search(prose).group(0)}"): painted bases do not run, jump or fall. '
                        'Suggest: the moment before or after (a frozen pose, impact_shake, a whip cut)')
        st = b.shot.staging
        if st is None:
            return
        posed = [p.who for p in st.poses]
        if sorted(posed) != sorted(b.shot.cast):
            err(where, f'staging.poses names {posed}, shot.cast is {b.shot.cast}; give exactly one pose per cast id')
        if st.setting == 'other' and not st.setting_detail:
            err(where, 'staging.setting is "other" with no setting_detail')
        if st.setting == 'other':
            warn(where, f'setting "{st.setting_detail}" is not in the library: a new base will stop at the bases gate')
        if len(st.actions) > MAX_ACTIONS:
            err(where, f'{len(st.actions)} actions; at most {MAX_ACTIONS} per beat')
        if len(st.props) > MAX_PROPS:
            err(where, f'{len(st.props)} props; at most {MAX_PROPS} per beat')
        prop_names = {p.name for p in st.props}
        for a in st.actions:
            if a.who not in b.shot.cast:
                err(where, f'action {a.verb} by "{a.who}", who is not in shot.cast')
            if not a.anchor or a.anchor.casefold() not in spoken:
                err(where, f'action {a.verb} anchor "{a.anchor}" is not in the narration')
            if a.verb in ('pick_up', 'put_down', 'drink', 'throw') and a.target and a.target not in prop_names:
                warn(where, f'{a.verb} targets "{a.target}", which is not listed in staging.props')
            if a.verb in STAGING_VERBS:
                if a.verb == 'walk' and WALK_FAR.search(a.target + ' ' + prose):
                    warn(where, f'walk ("{a.target}"): walking across a room cannot be animated on a painted base. '
                                'Suggest: cut to the arrival on the anchor word, or a pan over the far-off figure')
                else:
                    warn(where, f'{a.verb} on "{a.anchor}": a posture change is staged as a match cut between two bases of the '
                                'same setting on that word (not a continuous move)')
        for p in st.props:
            if p.held_by and p.held_by not in b.shot.cast:
                err(where, f'prop "{p.name}" held by "{p.held_by}", who is not in shot.cast')


    @staticmethod
    def _check_visual(b, spoken, err, warn):
        """Visual type, insert and graphic cues are consistent; numbers drawn on screen are numbers the beat says."""
        where, sh = b.id, b.shot
        said = spoken + ' ' + b.on_screen_text.casefold()
        drawn = [m.detail for m in sh.motion if m.recipe in ('split_compare', 'checklist_tick', 'text_slam')]
        if sh.graphic:
            drawn += sh.graphic.items
        if sh.insert:
            drawn += [e.detail for e in sh.insert.effects if e.kind == 'label']
        for text in drawn:
            for n in NUMBER.findall(text):
                if n.strip().casefold() not in said:
                    err(where, f'"{n}" is drawn on screen ("{text}") but this beat never says it; on-screen numbers must '
                               'match the narration or on_screen_text')
        # on_screen_text is drawn by a text recipe; without one the composer slams it on a shared word (a fallback)
        shows = {m.recipe for m in sh.motion}
        ost = b.on_screen_text.strip()
        if ost and not shows & {'text_slam', 'number_counter', 'split_compare', 'checklist_tick'}:
            warn(where, f'on_screen_text "{ost}" has no text_slam to show it; the composer will slam it on the first '
                        'word it shares with the narration')
        elif ost and 'number_counter' in shows and 'text_slam' not in shows:
            left = [w for w in ost.split() if not NUMBER.search(w)]
            if left:
                warn(where, f'the number_counter draws only its number, so "{" ".join(left)}" from on_screen_text is not '
                            'shown; use text_slam if those words matter')
        if sh.visual == 'character' and not sh.cast:
            warn(where, 'a character beat with no cast; make it an insert (a scene of the subject) or a graphic')
        if sh.visual == 'insert':
            if sh.insert is None:
                err(where, 'visual is insert but shot.insert is empty: describe the scene')
                return
            if not sh.insert.key or not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', sh.insert.key):
                err(where, f'insert key "{sh.insert.key}" must be kebab-case')
            if PEOPLE.search(sh.insert.scene):
                warn(where, f'insert scene mentions people ("{PEOPLE.search(sh.insert.scene).group(0)}"): inserts are painted with '
                            'no characters. Suggest: show the place, the thing or signs of people (smoke from a chimney, a cart)')
            if len(sh.insert.effects) > MAX_EFFECTS:
                err(where, f'{len(sh.insert.effects)} insert effects; at most {MAX_EFFECTS}')
            for e in sh.insert.effects:
                if e.anchor and e.anchor.casefold() not in spoken:
                    err(where, f'insert effect {e.kind} anchor "{e.anchor}" is not in the narration')
        if sh.visual == 'split':
            if sh.insert is None or sh.split is None:
                err(where, 'visual is split: give the top scene (insert) and the bottom scene (split)')
                return
            if len(sh.split.labels) != 2 or any(len(x.split()) > 3 for x in sh.split.labels):
                err(where, f'split needs two labels of 1-3 words; got {sh.split.labels}')
            if not re.fullmatch(r'[a-z0-9]+(-[a-z0-9]+)*', sh.split.key or ''):
                err(where, f'split key "{sh.split.key}" must be kebab-case')
            for a in sh.split.anchors:
                if a and a.casefold() not in spoken:
                    err(where, f'split anchor "{a}" is not in the narration')
        if sh.visual == 'graphic':
            if sh.graphic is None:
                err(where, 'visual is graphic but shot.graphic is empty')
                return
            g = sh.graphic
            if not 1 <= len(g.items) <= MAX_GRAPHIC_ITEMS:
                err(where, f'graphic has {len(g.items)} items; 1-{MAX_GRAPHIC_ITEMS}')
            for it in g.items:
                if len(it.split()) > 3:
                    err(where, f'graphic label "{it}" is over 3 words')
            if g.anchors and len(g.anchors) != len(g.items):
                err(where, 'graphic anchors must match the items one to one (or be empty)')
            for a in g.anchors:
                if a and a.casefold() not in spoken:
                    err(where, f'graphic anchor "{a}" is not in the narration')
        if sh.reaction_anchor:
            if sh.visual == 'character':
                warn(where, 'reaction_anchor on a character beat does nothing; it cuts from an insert or graphic to the cast')
            elif not sh.cast:
                err(where, 'reaction_anchor needs the reacting cast in shot.cast (and their poses in staging)')
            if sh.reaction_anchor.casefold() not in spoken:
                err(where, f'reaction_anchor "{sh.reaction_anchor}" is not in the narration')


STOP = {'the', 'a', 'an', 'and', 'or', 'but', 'so', 'is', 'it', 'its', "it's", 'you', 'your', 'to', 'of', 'in', 'on',
        'at', 'why', 'what', 'how', 'did', 'do', 'does', 'i', 'me', 'my', 'we', 'this', 'that', 'same', 'next', 'time'}


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP and len(w) > 2}


def _shares_word(a: str, b: str) -> bool:
    words = lambda t: {w for w in re.findall(r"[a-z0-9']+", t.lower()) if w not in STOP and len(w) > 2}
    return bool(words(a) & words(b))


@dataclass
class Issue:
    severity: Literal['error', 'warning']
    where: str
    message: str

    def __str__(self):
        return f'{self.severity.upper():7} {self.where:12} {self.message}'


def beat_range(seconds):
    """7-14 beats for a 30-60 s Short; scaled outside that window (about 3.5-8 s per beat)."""
    if 30 <= seconds <= 60:
        return 7, 14
    return max(3, math.floor(seconds / 8)), max(5, math.ceil(seconds / 3.5))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def episode_json_schema():
    schema = Episode.model_json_schema()
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    schema['title'] = 'Stick-Figures episode'
    return schema
