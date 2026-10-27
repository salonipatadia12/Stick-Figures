"""Deterministic staging before an automatic reel; no extra AI calls."""
from copy import deepcopy
from .animation_models import final_holders
import re


def check_prompt_actions(story,prompt):
    """Fail a known playground request rather than substitute another story.

    This is a targeted guard, not a general semantic understanding guarantee.
    """
    wanted=re.search(r'\b(?:on|riding|playing on)\s+(?:(?:a|the|their)\s+)?swings?\b|\bswinging\s+together\b|\bplayground\s+swings?\b',prompt,re.I)
    if wanted:
        two=bool(re.search(r'\b(?:two|2|both)\b',prompt,re.I))
        required=2 if two else 1
        if not any(sum(a.action=='swing' for a in s.animation.actors)>=required for s in story.scenes):
            raise ValueError('The scene plan omitted the requested swinging action. No substitute video was rendered. The saved draft needs a scene with the requested riders on swings.')


def prepare_story(story):
    from .models import Story
    data=deepcopy(story.model_dump())
    previous=None
    for scene in data['scenes']:
        plan=scene['animation'];actors=plan['actors'];ids={a['character'] for a in actors}
        if previous and plan['setting']==previous['setting'] and not plan['time_jump']:
            # A continuous room does not change lighting or drop carried objects.
            plan['light']=previous['light']
            existing={p['id'] for p in plan['props']}
            for item in previous['props']:
                if item['holder'] in ids and item['id'] not in existing:
                    plan['props'].append(deepcopy(item))
        # Maintain enough space for the 204px heads throughout the full walk path.
        for actor in actors:
            if len(actors)==2:
                left=actor['character']=='hero'
                lo,hi=(.20,.34) if left else (.66,.80)
            else:lo,hi=.24,.76
            actor['x']=min(hi,max(lo,actor['x']))
            actor['end_x']=min(hi,max(lo,actor['end_x']))
            # "Stand" means rise from seated; standing still is idle.
            if actor['action']=='stand' and actor['pose']=='standing':actor['action']='idle'
            if actor['action']=='swing':
                actor['x']=.29 if actor['character']=='hero' else .71
                if len(actors)==1:actor['x']=.5
                actor['pose']='seated';actor['end_x']=actor['x']
                plan['framing']='two_shot'
        for prop in plan['props']:
            controller=next((a for a in actors if a['prop']==prop['id']),None)
            if controller and controller['action'] in {'pick_up','put_down','write'}:
                x=controller['x'];side=1 if x<.5 else -1
                prop['x']=x+side*.10
        # Run the original structural / prop interaction validation after staging.
        from .animation_models import Animation
        checked=Animation.model_validate(plan)
        previous=checked.model_dump()
        holders=final_holders(checked)
        for prop in previous['props']:prop['holder']=holders[prop['id']]
    return Story.model_validate(data).reviewable()
