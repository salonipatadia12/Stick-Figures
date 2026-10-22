import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Scene(Strict):
    narration: str = Field(max_length=700)
    visual: str = Field(max_length=2200)
    movement: Literal['breathe', 'nod', 'still']


class Story(Strict):
    title: str = Field(min_length=1, max_length=100)
    logline: str = Field(max_length=500)
    character_notes: str = Field(max_length=1500)
    scenes: list[Scene] = Field(min_length=3, max_length=8)

    def reviewable(self):
        if any(not s.narration or not s.visual for s in self.scenes):
            raise ValueError('Give every scene narration and a visual description before approval.')
        if sum(len(s.narration.split()) for s in self.scenes) > 360:
            raise ValueError('Keep the story under 360 spoken words.')
        return self


class Brief(Strict):
    prompt: str = Field(min_length=5, max_length=2400)
    mood: Literal['heartwarming', 'funny', 'suspense'] = 'heartwarming'
    seconds: int = Field(default=45, ge=20, le=120)
    mode: Literal['ai', 'manual'] = 'ai'


class Head(Strict):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    rx: float = Field(ge=.025, le=.35)
    ry: float = Field(ge=.025, le=.35)


class Landmarks(Strict):
    heads: list[Head] = Field(max_length=2)


def digest(value):
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
