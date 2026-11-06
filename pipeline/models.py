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


class DraftShot(Strict):
    framing: Framing
    cast: list[str] = Field(description='Style cast ids visible in the frame; empty for object-only inserts.')
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
    voice_id: str = ''  # Kokoro voice: am_michael or af_heart (the user's shortlist); empty = the style default


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
        return gate == 'script' and self.approvals.get('script') == self.script_digest()

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
            words = b.on_screen_text.split()
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
        expected = self.target_length * words_per_second
        words = self.word_count()
        if not 0.75 * expected <= words <= 1.2 * expected:
            warn('beats', f'{words} narration words ~ {words / words_per_second:.0f}s; target {self.target_length}s '
                          f'is ~{expected:.0f} words at {words_per_second} w/s')
        return issues


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
