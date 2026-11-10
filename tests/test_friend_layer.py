"""The friend in the host format (user 2026-11-10): a second composited character opposite the host, facing it, in
frame, the host's head size; a friend's feather reach is a tickle and the host laughs; captions keep off the friend;
single-host beats draw exactly as before."""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/motion_test'))

import compose_beats as CB  # noqa: E402
from pipeline.rig import verbs as V  # noqa: E402

NARRATION = 'Touch from someone else gets through. Me? I still cannot tickle myself.'


def T_for(text, t0=10.0, step=0.34):
    out = {}
    for i, w in enumerate(text.split()):
        k = ''.join(c for c in w.lower() if c.isalnum() or c in "'$,").strip(',')
        out[k if k not in out else f'{k}#{i}'] = t0 + 0.2 + i * step
    return out


def spec(cast=('lead', 'friend'), side='left', size='medium', actions=None, props=None, narration=NARRATION, motion=None):
    return dict(id='b10', role='loop', narration=narration, on_screen_text='', sfx=[], v6=True,
                shot=dict(visual='insert', insert=dict(key='base:lead-standing-street', scene='', effects=[]),
                          host=dict(where='scene', side=side, size=size, holds='', to_camera=True, cut_on=''),
                          cast=list(cast), motion=motion or [], framing='medium',
                          staging=dict(setting='street', poses=[dict(who=w, pose='standing') for w in cast],
                                       actions=actions if actions is not None else [], props=props or [])))


TICKLE = dict(actions=[dict(who='friend', verb='reach', target='lead', anchor='through'),
                       dict(who='lead', verb='shrug', target='camera', anchor='myself')],
              props=[dict(name='feather on a stick', held_by='friend', where='right hand')])


def shot_for(s, t0=10.0, t1=15.0):
    cls = CB.make_visual_class(s)
    cls.host_format = True
    return cls(T_for(s['narration'], t0), t0, t1)


def bbox(layer):
    import numpy as np
    x, y, a = layer.last
    ys, xs = np.where(a)
    return x + xs.min(), y + ys.min(), x + xs.max(), y + ys.max()


def overlap(a_layer, b_layer) -> int:
    import numpy as np
    ax, ay, a = a_layer.last
    bx, by, b = b_layer.last
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + a.shape[1], bx + b.shape[1]), min(ay + a.shape[0], by + b.shape[0])
    if x1 <= x0 or y1 <= y0:
        return 0
    return int(np.logical_and(a[y0 - ay:y1 - ay, x0 - ax:x1 - ax], b[y0 - by:y1 - by, x0 - bx:x1 - bx]).sum())


class FriendScriptTest(unittest.TestCase):
    def test_a_feather_reach_toward_the_host_is_a_tickle(self):
        s = spec(**TICKLE)
        self.assertEqual([a['verb'] for a in CB.friend_actions(s)], ['tickle'])
        self.assertEqual(CB.tickle_anchors(s), ['through'])
        bare = spec(actions=TICKLE['actions'])                        # no feather: a plain reach
        self.assertEqual([a['verb'] for a in CB.friend_actions(bare)], ['reach'])
        alone = spec(cast=('lead',), **TICKLE)                        # no friend in the cast: nothing
        self.assertEqual(CB.friend_actions(alone), [])

    def test_tickle_and_laugh_are_verbs(self):
        from typing import get_args
        from pipeline.models import Verb
        for v in ('tickle', 'laugh'):
            self.assertIn(v, V.VERBS)
            self.assertIn(v, V.IN_PLACE)
            self.assertIn(v, get_args(Verb))

    def test_held_props_come_from_the_library_or_are_drawn(self):
        spr, anchor, width = CB.held_prop('feather on a stick')
        self.assertGreater(spr.width, 3 * spr.height)                 # a long thin thing, held at its end
        self.assertLess(anchor[0], 0.1 * spr.width)
        self.assertIsNotNone(CB.held_prop("friend's bill"))


class FriendLayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tickle = shot_for(spec(**TICKLE))

    def test_the_friend_stands_opposite_the_host_facing_it_with_its_head_size(self):
        for side, size in (('left', 'medium'), ('right', 'large')):
            sh = shot_for(spec(side=side, size=size, actions=[dict(who='friend', verb='nod', target='', anchor='through')]))
            self.assertIsNotNone(sh.friend)
            sh.frame(11.0)
            h, f = sh.host.placed, sh.friend.placed
            self.assertEqual(f['fx'] > h['fx'], side == 'left')       # the other side of the frame
            self.assertNotEqual(f['flip'], h['flip'])                 # both face in: toward each other
            self.assertAlmostEqual(sh.host.D * h['k'], sh.friend.D * f['k'], places=6)
            self.assertEqual(overlap(sh.host, sh.friend), 0)

    def test_the_friend_is_kept_inside_the_frame(self):
        sh = shot_for(spec(side='right', size='medium'))
        sh.frame(11.0)
        x0, y0, x1, y1 = bbox(sh.friend)
        self.assertGreaterEqual(x0, CB.HOST_MARGIN - 1)
        self.assertLessEqual(x1, CB.W - CB.HOST_MARGIN)
        self.assertLessEqual(y1, CB.H - CB.HOST_MARGIN)
        logged = [b for b in CB.O.take_box_log() if b.get('who') == 'friend']
        self.assertTrue(logged and logged[-1]['in_frame'] and logged[-1]['host'])   # QA host_check counts the friend

    def test_the_host_laughs_on_the_tickle_and_only_the_feather_touches(self):
        sh = self.tickle
        at = sh.host.c.tw('through')
        laugh = [a for a in sh.host.c.perfs['lead'].actions if a.name == 'laugh']
        self.assertEqual(len(laugh), 1)
        self.assertAlmostEqual(laugh[0].start, at + V.TICKLE_REACH - 0.05, places=6)
        self.assertTrue(any(a.name.startswith('tickle') for a in sh.friend.c.perfs['friend'].actions))
        self.assertIsNotNone(sh.friend.gap)
        sh.frame(at - 1.0)                                            # holding the feather: apart
        self.assertEqual(overlap(sh.host, sh.friend), 0)
        sh.frame(at + V.TICKLE_REACH + 0.5)                           # the tip at the host's side
        touch = overlap(sh.host, sh.friend)
        self.assertGreater(touch, 0)
        self.assertLess(touch, 0.01 * int(sh.host.last[2].sum()))     # a touch, not two bodies on top of each other
        self.assertLess(sh.host.c.pose('lead', at + V.TICKLE_REACH + 0.4).eye_open, 0.3)   # eyes shut, laughing

    def test_captions_keep_off_the_friend(self):
        from PIL import Image
        from pipeline import captions as C
        sh = self.tickle
        t = sh.host.c.tw('through') + V.TICKLE_REACH + 0.5
        frame = sh.frame(t)
        fb = bbox(sh.friend)
        self.assertGreater(sh.host_hit(fb), sh.host.hit(fb))          # the friend's pixels count
        line = C.Line([C.Word(w, t - 0.2 + i * 0.1, t + i * 0.1) for i, w in enumerate('gets through to you'.split())], t - 0.2, t + 0.6)
        out = C.draw(Image.new('RGBA', frame.size), line, t, avoid=sh.host_hit)
        self.assertEqual(out['on_host'], 0)

    def test_the_friend_walks_in_on_an_enter_recipe(self):
        s = spec(motion=[dict(recipe='enter_right', anchor='someone', detail='')], **TICKLE)
        sh = shot_for(s)
        at = sh.friend.enter_at
        sh.frame(at - 0.5)
        self.assertIsNone(sh.friend.last)                             # not in yet
        self.assertEqual(sh.host_hit((0, 0, CB.W, CB.H)), sh.host.hit((0, 0, CB.W, CB.H)))
        sh.frame(at + 1.0)
        self.assertIsNotNone(sh.friend.last)


class SingleHostUnchangedTest(unittest.TestCase):
    # md5 of a single-host frame, drawn by the code before the friend existed (2026-11-10)
    FRAME_MD5 = 'e24358a4e56042602dfdd251d68bf8da'

    def test_a_single_host_beat_has_no_friend_and_draws_as_before(self):
        sh = shot_for(spec(cast=('lead',), actions=[dict(who='lead', verb='shrug', target='camera', anchor='myself')]))
        self.assertIsNone(sh.friend)
        self.assertEqual(sh.host.laughs, [])
        self.assertFalse(any(a.name == 'laugh' for a in sh.host.c.perfs['lead'].actions))
        self.assertEqual(sh.scene.host, sh.host.draw)
        frame = sh.frame(12.0)
        self.assertEqual(sh.host_hit((0, 0, CB.W, CB.H)), sh.host.hit((0, 0, CB.W, CB.H)))
        digest = hashlib.md5(frame.convert('RGB').tobytes()).hexdigest()
        if self.FRAME_MD5:
            self.assertEqual(digest, self.FRAME_MD5)


if __name__ == '__main__':
    unittest.main()
