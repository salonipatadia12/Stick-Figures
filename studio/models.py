import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .animation_models import Animation, Character, default_characters, check_continuity


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Scene(Strict):
    narration: str = Field(max_length=700, description="Exact words the voice speaks. If animation.speaker is hero or friend, write ONLY that character’s direct first-person dialogue, never third-person narration about them. If narrator, do not mix in quoted dialogue.")
    visual: str = Field(max_length=2200)
    movement: Literal['breathe', 'nod', 'still'] = 'still'
    animation: Animation | None = None


class Story(Strict):
    render_style: Literal['illustrated','rigged'] = 'illustrated'
    characters: list[Character] = Field(default_factory=default_characters,min_length=1,max_length=2)
    voice: Literal['en-US-AndrewNeural','en-US-AvaNeural','en-GB-SoniaNeural'] = 'en-US-AndrewNeural'
    title: str = Field(min_length=1, max_length=100)
    logline: str = Field(max_length=500)
    character_notes: str = Field(max_length=1500)
    scenes: list[Scene] = Field(min_length=3, max_length=8)

    def reviewable(self):
        if any(not s.narration or not s.visual for s in self.scenes):
            raise ValueError('Give every scene narration and a visual description before approval.')
        if sum(len(s.narration.split()) for s in self.scenes) > 360:
            raise ValueError('Keep the story under 360 spoken words.')
        if self.render_style=='rigged':
            ids=[c.id for c in self.characters]
            if len(ids)!=len(set(ids)):raise ValueError('Character IDs must be unique.')
            check_continuity(self.scenes)
            for i,scene in enumerate(self.scenes):
                if any(a.character not in ids for a in scene.animation.actors):
                    raise ValueError(f'Scene {i+1} uses an undefined character.')
                from .animation import AnimatedScene
                import math
                engine=AnimatedScene(scene.model_dump(),[c.model_dump() for c in self.characters])
                for frame in range(25):
                    poses,_=engine.poses(frame/24)
                    for z in poses.values():
                        from .animation import tilt_point
                        shoulder=tilt_point((z['x']+z['direction']*43,z['shoulder']+5),(z['x'],z['hip']),z['lean'])
                        if math.dist(shoulder,z['hand'])>180:
                            raise ValueError(f'Scene {i+1}: move the object closer to the character or change the starting pose so the hand can reach it.')
        return self


class Brief(Strict):
    prompt: str = Field(min_length=5, max_length=2400)
    mood: Literal['heartwarming', 'funny', 'suspense'] = 'heartwarming'
    seconds: int = Field(default=45, ge=20, le=120)
    mode: Literal['ai', 'manual'] = 'ai'
    ending: Literal['happy','bittersweet','twist','funny','cliffhanger','tragic'] = 'happy'
    workflow: Literal['review','automatic'] = 'review'
    render_style: Literal['illustrated','rigged'] = 'illustrated'


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
