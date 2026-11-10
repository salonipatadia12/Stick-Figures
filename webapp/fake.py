"""Fake Anthropic for STICK_FAKE_ANTHROPIC=1 (tests and the e2e fake mode): nothing is ever sent.

Built from tests/pipeline_fixtures.py. One client answers every pipeline call by looking at the request, so the order
of research -> storylines -> script does not matter and cached steps are simply never asked:
- a request with tools is research (the fixtures' cited salary facts),
- a request with the storyline system prompt is the pitch (three storylines in the formats the request allows, so the
  rotation rule never refuses a fake pick),
- anything else is the script (a host-format script, three hook options, that passes Episode.check).
All usage is zero, so the ledger records $0.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = None


def fixtures():
    """tests/pipeline_fixtures.py, loaded by path (tests/ is not a package)."""
    global _FIXTURES
    if _FIXTURES is None:
        path = ROOT / 'tests' / 'pipeline_fixtures.py'
        spec = importlib.util.spec_from_file_location('stick_pipeline_fixtures', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _FIXTURES = module
    return _FIXTURES


def zero_usage():
    return fixtures().usage(0, 0, 0)


def _host(where='scene', side='centre', size='medium', doing='stands in the scene, talking to you', holds=''):
    return dict(where=where, side=side, size=size, holds=holds, doing=doing, to_camera=True)


def _insert_beat(role, narration, key, scene, motion, *, side='centre', size='medium', doing='', text='', sources=(),
                 effects=(), actions=(), holds=''):
    F = fixtures()
    b = F.beat(role, narration, f'The host in {scene}.', motion, text=text, cast=('lead',), sources=sources,
               kind='insert', insert=dict(key=key, scene=scene, effects=[dict(kind=k, anchor=a, at=at, detail=d)
                                                                          for k, a, at, d in effects]),
               stage=F.staging(('lead',), setting='other', pose='standing', detail=scene[:40], actions=actions))
    b['shot']['host'] = _host(side=side, size=size, doing=doing or 'stands in the scene, talking to you', holds=holds)
    return b


HOOK = "Your salary didn't shrink. It leaked."
LOOP = "So next payday, your salary won't shrink."


def host_draft():
    """A host-format v6 script on the fixtures' salary facts: hook, problem, stakes, stakes, twist, payoff, loop."""
    beats = [
        _insert_beat('hook', HOOK, 'leaking-coin-jar', 'a glass jar of coins on a kitchen counter, coins trickling from '
                     'a crack, morning light', [('punch_in', 'leaked', '')], side='right', size='large',
                     doing='stares at the cracked jar, shocked'),
        _insert_beat('problem', 'Payday feels huge, but the money is gone before the month ends.', 'calendar-wall',
                     'a wall calendar beside a window, pages for a whole month, soft daylight',
                     [('push_in', '', ''), ('punch_in', 'gone', '')], side='left', doing='points at the calendar'),
        _insert_beat('stakes', 'Small buys do not feel like spending, so they slip past you.', 'counter-small-buys',
                     'a counter covered with coffee cups and delivery parcels, warm evening light',
                     [('punch_in', 'slip', '')], side='centre', size='small', doing='looks over the pile of cups'),
        _insert_beat('stakes', 'Subscriptions renew quietly while you sleep, every single month.', 'night-doorway-envelopes',
                     'a dark hallway at night, small envelopes sliding under a door',
                     [('punch_in', 'renew', '')], side='right', size='medium', doing='watches the envelopes arrive'),
        _insert_beat('twist', 'Here is the twist: spending tends to rise as income rises.', 'two-jars-split',
                     'two coin jars side by side on a table, one tall and one draining, cool light',
                     [('text_slam', 'twist', '')], text='LIFESTYLE CREEP', sources=['s1'], side='left', size='large',
                     doing='holds up the draining jar', holds='glass jar of coins'),
        _insert_beat('payoff', 'So pay yourself first: move savings out before any spending.', 'savings-jar-full',
                     'a second jar filling with coins on a sunny windowsill',
                     [('punch_in', 'first', '')], sources=['s2'], side='centre', size='medium',
                     doing='pours coins into the second jar'),
        _insert_beat('loop', LOOP, 'leaking-coin-jar', 'a glass jar of coins on a kitchen counter, the crack taped over, '
                     'morning light', [('push_in', '', '')], side='right', size='close', doing='taps the taped jar, smiling'),
    ]
    option = lambda line, loop: dict(line=line, frame_one='The host beside a cracked glass jar, coins trickling out.',
                                     host_doing='stares at the cracked jar', scene_key='', scene='',
                                     answer='Spending rises with income, so pay yourself first.', loop_line=loop)
    return dict(title='Where Your Salary Goes', hook_promise='Why the money runs out before the month does',
                hook_options=[option(HOOK, LOOP), option('Where did your salary actually go?', 'So next payday, your salary stays.'),
                              option('Payday was huge. So why is it gone?', 'Next payday, it will not be gone.')],
                subject_settings=[], beats=beats)


def _allowed_formats(request):
    text = ' '.join(m.get('content', '') for m in request.get('messages', []) if isinstance(m.get('content'), str))
    m = re.search(r'Allowed formats \(pick 3 different ones that fit this topic best\): ([a-z_, ]+)', text)
    return [f.strip() for f in m.group(1).split(',') if f.strip()] if m else ['mini_story', 'myth_vs_fact', 'mystery']


def story_response(request):
    F = fixtures()
    formats = (_allowed_formats(request) + ['mini_story', 'myth_vs_fact', 'mystery'])[:3]
    pitch = F.story_pitch(tuple(formats))
    r = F.story_response(pitch)
    r.usage = zero_usage()
    return r


def research_response():
    r = fixtures().research_response()
    r.usage = zero_usage()
    return r


def script_response(draft=None):
    r = fixtures().script_response(draft or host_draft())
    r.usage = zero_usage()
    return r


class FakeMessages:
    def __init__(self, script=None):
        self.created = []
        self.counted = []
        self.script = script

    def count_tokens(self, **params):
        self.counted.append(params)
        return NS(input_tokens=1200)

    def create(self, **params):
        self.created.append(params)
        from pipeline import story
        if params.get('tools'):
            return research_response()
        if str(params.get('system', '')).startswith(story.STORY_SYSTEM[:60]):
            return story_response(params)
        return script_response(copy.deepcopy(self.script) if self.script else None)


class FakeClient:
    """Stands in for anthropic.Anthropic: messages.count_tokens and messages.create only."""
    fake = True

    def __init__(self, script=None):
        self.messages = FakeMessages(script)


def make_client():
    return FakeClient()


def dumps(obj):
    return json.dumps(obj)
