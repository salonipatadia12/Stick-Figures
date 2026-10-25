"""Portable scene directions for the reusable 2D character renderer."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ACTIONS=['idle','walk','sit','stand','wave','talk','point','write','pick_up','put_down','give','laugh']
EMOTIONS=['neutral','happy','sad','worried','surprised','angry']
SETTINGS=['cafe','park','street','home','office','stage']
PROPS=['book','bag','cup','flower','note','ball']

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Character(Strict):
    id: Literal['hero','friend']
    name: str=Field(min_length=1,max_length=40)
    color: str=Field(default='#F8F3E8',pattern=r'^#[0-9a-fA-F]{6}$')
    accent: str=Field(default='#3D8785',pattern=r'^#[0-9a-fA-F]{6}$')
    accessory: Literal['none','scarf','glasses']='none'

class Actor(Strict):
    character: Literal['hero','friend']
    x: float=Field(default=.32,ge=.18,le=.82)
    end_x: float=Field(default=.65,ge=.18,le=.82)
    pose: Literal['standing','seated']='standing'
    action: Literal['idle','walk','sit','stand','wave','talk','point','write','pick_up','put_down','give','laugh']='idle'
    emotion: Literal['neutral','happy','sad','worried','surprised','angry']='neutral'
    end_emotion: Literal['neutral','happy','sad','worried','surprised','angry'] | None=None
    action_start: float=Field(default=0,ge=0,le=.85)
    action_end: float=Field(default=1,ge=.15,le=1)
    prop: str=Field(default='',max_length=30,pattern=r'^[a-z0-9_-]*$')

class Prop(Strict):
    id: str=Field(min_length=1,max_length=30,pattern=r'^[a-z0-9_-]+$')
    kind: Literal['book','bag','cup','flower','note','ball']
    x: float=Field(default=.52,ge=.12,le=.88)
    holder: Literal['none','hero','friend']='none'

class Animation(Strict):
    setting: Literal['cafe','park','street','home','office','stage']='cafe'
    light: Literal['day','sunset','night']='day'
    speaker: Literal['narrator','hero','friend']='narrator'
    time_jump: bool=False
    actors: list[Actor]=Field(min_length=1,max_length=2)
    props: list[Prop]=Field(default_factory=list,max_length=5)

    @model_validator(mode='after')
    def coherent(self):
        ids=[a.character for a in self.actors]
        if len(ids)!=len(set(ids)):raise ValueError('Use each character once per scene.')
        objects={p.id:p for p in self.props}
        if len(objects)!=len(self.props):raise ValueError('Use unique prop IDs.')
        if self.speaker!='narrator' and self.speaker not in ids:raise ValueError('The speaker must appear in this scene.')
        for p in self.props:
            if p.holder!='none' and p.holder not in ids:raise ValueError('A prop holder must appear in the scene.')
        held=[p.holder for p in self.props if p.holder!='none']
        if len(held)!=len(set(held)):raise ValueError('Each character can hold one object at a time.')
        interactions=set()
        for a in self.actors:
            if a.action_end-a.action_start<.15-1e-9:raise ValueError('Leave at least 15% of the scene for an action.')
            if a.action in {'pick_up','put_down','give','write'}:
                if a.prop not in objects:raise ValueError(f'{a.action} needs a prop in this scene.')
                if a.prop in interactions:raise ValueError('Only one character may control a prop in one scene.')
                interactions.add(a.prop);p=objects[a.prop]
                if a.action in {'put_down','give'} and p.holder!=a.character:raise ValueError(f'{a.action}: {a.character} must start holding {a.prop}.')
                if a.action in {'pick_up','write'} and any(item.holder==a.character for item in self.props):raise ValueError('Put the held object down before picking up another object or writing.')
                if a.action=='pick_up' and p.holder!='none':raise ValueError('Pick-up requires a prop on the table.')
                if a.action=='write' and (p.kind!='book' or p.holder!='none'):raise ValueError('Writing requires a book on the table.')
                if a.action in {'pick_up','put_down','write'} and abs(p.x-a.x)>.26:raise ValueError('Place the prop within reach of the actor (at most 26% of scene width away).')
                if a.action=='give':
                    if len(ids)!=2:raise ValueError('Giving needs a second character.')
                    other=next(b for b in self.actors if b.character!=a.character)
                    if any(item.holder==other.character for item in self.props):raise ValueError('The receiving character needs a free hand.')
                    if other.action!='idle':raise ValueError('The receiving character must use idle during a handover.')
                    if abs(a.x-other.x)>.42:raise ValueError('Move the characters closer before handing over a prop.')
        return self

def default_characters():
    return [Character(id='hero',name='Milo'),Character(id='friend',name='June',accessory='scarf',accent='#D19A52')]

def final_holders(plan):
    holders={p.id:p.holder for p in plan.props}
    for a in plan.actors:
        if a.action=='pick_up':holders[a.prop]=a.character
        elif a.action=='put_down':holders[a.prop]='none'
        elif a.action=='give':holders[a.prop]=next(b.character for b in plan.actors if b.character!=a.character)
    return holders

def check_continuity(scenes):
    previous=None;holders={};kinds={}
    for i,scene in enumerate(scenes):
        plan=scene.animation
        if plan is None:raise ValueError(f'Scene {i+1} needs animation directions.')
        if previous and not plan.time_jump and plan.setting==previous.setting:
            for p in plan.props:
                if p.id in holders and p.holder!=holders[p.id]:
                    raise ValueError(f'Scene {i+1}: {p.id} should still be held by {holders[p.id]}. Add a transfer action or mark a time jump.')
        for p in plan.props:
            if p.id in kinds and kinds[p.id]!=p.kind:raise ValueError(f'Prop {p.id} changes type. Give a different object its own ID.')
            kinds[p.id]=p.kind
        holders.update(final_holders(plan));previous=plan
