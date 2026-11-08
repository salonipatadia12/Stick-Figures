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

BeatRole = Literal['hook', 'setup', 'tension', 'reveal', 'payoff', 'ending']
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
Setting = Literal['cafe', 'bedroom', 'office', 'kitchen', 'living-room', 'street', 'park', 'classroom', 'gym', 'car', 'other']
PoseName = Literal['seated-at-table', 'standing', 'bed-edge', 'bed-awake', 'bed-asleep-top', 'seated-sofa', 'seated-desk',
                   'seated-bench', 'driving', 'walking']
Verb = Literal[
    # arm and head verbs the rig performs in place
    'reach', 'point', 'shrug', 'facepalm', 'pick_up', 'put_down', 'throw', 'drink', 'type', 'write', 'wave', 'nod',
    'shake_head', 'slump', 'jolt', 'gaze',
    # posture and travel: a painted torso cannot do these, so they are staged as a match cut between two bases
    # of the same setting on the anchor word (walk: a pan, or the far-off drawn figure)
    'lie_down', 'sit_up', 'stand_up', 'walk',
]
STAGING_VERBS = {'lie_down', 'sit_up', 'stand_up', 'walk'}
MAX_ACTIONS, MAX_PROPS, MAX_CAST = 3, 4, 2
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


class Staging(Strict):
    """What the beat needs from the libraries: which painted base (setting + pose per character), which verbs
    on which words, which props. The base planner and the composer read these fields, not the prose."""
    setting: Setting = Field(description='The place. "other" only when none of the listed settings fits.')
    setting_detail: str = Field(description='For "other": the place in 3-6 words. Otherwise empty string.')
    poses: list[CastPose] = Field(description='One entry per cast id in shot.cast; empty for object-only inserts.')
    actions: list[ActionCue] = Field(description='0-3 verbs, each on a narration word. Prefer the in-place verbs; '
                                                 'lie_down/sit_up/stand_up/walk become a cut to another base.')
    props: list[PropCue] = Field(description='0-4 props the picture needs beyond the base furniture.')


class DraftShot(Strict):
    framing: Framing
    cast: list[str] = Field(description='Style cast ids visible in the frame; empty for object-only inserts.')
    staging: Staging
    image_prompt: str = Field(description='One painted frame: setting, who is where, pose, expression, props, '
                                          'lighting. Concrete and visual. No text, numbers or letters in the art. '
                                          'Do not describe the art style; it is appended separately.')
    pose_variants: list[str] = Field(description='0-2 alternate poses for the SAME frame (same camera, setting, '
                                                 'props; only the character changes), used by pose_swap.')
    motion: list[Motion] = Field(description='1-2 motion recipes.')
    transition: Transition = Field(description='cut unless a whip or match cut is motivated by the story.')


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


class ScriptDraft(Strict):
    title: str = Field(description='Working title, at most 60 characters.')
    hook_promise: str = Field(description='The question or tension the hook opens; the payoff must resolve it.')
    beats: list[DraftBeat]


# ---------------------------------------------------------------- episode (on disk)

class Layer(Strict):
    kind: Literal['background', 'character', 'prop', 'variant']
    file: str


class Shot(DraftShot):
    staging: Staging | None = None                     # None on episodes written before script v3 (keyword planning)
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
    beats: list[Beat] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    approvals: dict[str, str] = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)

    # ---- content identity: approvals are bound to these digests
    def script_digest(self):
        content = dict(title=self.title, hook_promise=self.hook_promise, sources=[s.model_dump() for s in self.sources],
                       beats=[b.model_dump(exclude={'duration'}, exclude_none=True) | {'shot': b.shot.model_dump(exclude={'layers'})}
                              for b in self.beats])
        return digest(content)

    def beat_digest(self, beat):
        """Per-beat identity so later stages regenerate only beats whose content changed."""
        return digest(dict(style=self.style, aspect=self.aspect,
                           beat=beat.model_dump(exclude={'duration', 'id'}) | {'shot': beat.shot.model_dump(exclude={'layers'})}))

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
        if not any(b.role == 'payoff' for b in self.beats):
            err('beats', 'no payoff beat: the hook promise is never delivered')
        if self.beats[-1].role != 'ending':
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
            if not b.visual_intent or not b.shot.image_prompt:
                err(where, 'missing visual intent or image prompt')
            if TALKING_HEAD.search(b.visual_intent + ' ' + b.shot.image_prompt):
                err(where, 'visual is a talking head; show the idea through action, metaphor or a diagram')
            if not 1 <= len(b.shot.motion) <= 2:
                err(where, f'{len(b.shot.motion)} motion recipes; pick 1-2')
            spoken = b.narration.casefold()
            for m in b.shot.motion:
                if m.recipe in ANCHORED_RECIPES and not m.anchor:
                    err(where, f'{m.recipe} needs an anchor word from the narration')
                if m.anchor and m.anchor.casefold() not in spoken:
                    err(where, f'{m.recipe} anchor "{m.anchor}" is not in the narration')
                if m.recipe == 'pose_swap' and not b.shot.pose_variants:
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
            anchored = any(m.recipe in ANCHORED_RECIPES for m in b.shot.motion)
            if seconds > 2.5 and not anchored:
                warn(where, f'~{seconds:.1f}s of narration with no word-anchored change; the picture may sit static')
            if len(b.shot.cast) > MAX_CAST:
                warn(where, f'{len(b.shot.cast)} characters in one frame; the bases hold at most {MAX_CAST}. Stage it as two shots')
            self._check_staging(b, spoken, err, warn)
        expected = self.target_length * words_per_second
        words = self.word_count()
        if not 0.75 * expected <= words <= 1.2 * expected:
            warn('beats', f'{words} narration words ~ {words / words_per_second:.0f}s; target {self.target_length}s '
                          f'is ~{expected:.0f} words at {words_per_second} w/s')
        return issues

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
