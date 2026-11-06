import math
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from pipeline.rig import hands, parts, puppet
from pipeline.rig.actions import Action, Idle, Performance
from pipeline.rig.puppet import Puppet, Stage, load_base
from pipeline.rig.skeleton import FORE, GRIPPING, UPPER, Character, Held, Pose, limb, two_bone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/motion_test'))
import scene_salary  # noqa: E402

# Word times of the am_michael take (seconds on the video timeline); the heart take is faster.
MICHAEL = dict(payday=0.8, feels=1.21, rich=1.54, so=2.29, broke=2.98, a=3.25, week=3.36, later=3.66, say=5.55,
               amount=5.79, lands=7.56, every=7.99, other=8.33, friday=8.61, it=9.62, huge=10.26)
HEART = {k: round(v * 0.86, 3) for k, v in MICHAEL.items()}


def segment_distance(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ((dx * dx + dy * dy) or 1e-9)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


class TwoBoneTest(unittest.TestCase):
    def test_bone_lengths_are_kept_for_reachable_targets(self):
        for target in [(30, 80), (-50, 40), (60, -20), (0, 100)]:
            mid, end, _ = two_bone((0, 0), target, 60, 58, 1)
            self.assertAlmostEqual(math.dist((0, 0), mid), 60, places=4)
            self.assertAlmostEqual(math.dist(mid, end), 58, places=4)

    def test_raised_arm_keeps_its_elbow_outside_not_across_the_face(self):
        shoulder, hand = (27, -108), (30, -190)
        elbow, _, _ = limb(shoulder, hand, 60, 58, side=1, bend=1, centre_x=0)
        self.assertGreater(elbow[0], shoulder[0])


class HandLibraryTest(unittest.TestCase):
    def test_thumb_is_on_the_anatomically_correct_side(self):
        # Right hand, palm to camera, fingers up: thumb on the viewer's right. Back of the right hand: viewer's left.
        self.assertEqual(hands.thumb_side('open_palm', 'R'), 1)
        self.assertEqual(hands.thumb_side('back', 'R'), -1)
        self.assertEqual(hands.thumb_side('open_palm', 'L'), -1)
        self.assertEqual(hands.thumb_side('back', 'L'), 1)

    def test_thumb_pixels_are_where_the_thumb_side_says(self):
        for pose in ('open_palm', 'back'):
            for hand in ('R', 'L'):
                d = hands.drawing(pose, hand)
                a = np.asarray(d.front.getchannel('A')) > 128
                h = a.shape[0]
                band = a[int(h * 0.35):int(h * 0.72)]          # the thumb sticks out at palm height
                cols = np.nonzero(band.any(0))[0]
                centre = d.wrist[0]
                reach_plus, reach_minus = cols.max() - centre, centre - cols.min()
                expected = hands.thumb_side(pose, hand)
                self.assertEqual(1 if reach_plus > reach_minus else -1, expected, f'{pose} {hand}')

    def test_every_pose_exists_for_both_hands_and_left_mirrors_right(self):
        for pose in hands.POSES:
            r, l = hands.drawing(pose, 'R'), hands.drawing(pose, 'L')
            self.assertEqual(r.front.size, l.front.size)
            self.assertTrue(np.array_equal(np.asarray(r.front)[:, ::-1], np.asarray(l.front)))

    def test_grip_point_is_inside_the_closed_hand(self):
        for pose in GRIPPING:
            for hand in ('R', 'L'):
                d = hands.drawing(pose, hand)
                alpha = d.front.getchannel('A').getpixel((round(d.grip[0]), round(d.grip[1])))
                self.assertGreater(alpha, 200, f'{pose} {hand}: the grip point must be inside the fingers')

    def test_wrist_bend_is_clamped(self):
        self.assertEqual(hands.clamp_bend(120), hands.WRIST_LIMIT)
        self.assertEqual(hands.clamp_bend(-300), -hands.WRIST_LIMIT)
        self.assertEqual(hands.clamp_bend(20), 20)


class CharacterTest(unittest.TestCase):
    def setUp(self):
        self.char = Character(D=100)

    def test_arm_bones_stay_the_same_length_across_poses(self):
        for rh in [(0.4, -0.1), (0.8, -1.5), (0.1, -1.8), (-0.2, -0.9)]:
            j = self.char.joints(replace(Pose(), rh=rh), (500, 900), 1.0, (0, 0))
            self.assertAlmostEqual(math.dist(j['r_sh'], j['r_el']), UPPER * 100, delta=UPPER * 100 * 0.13)
            self.assertAlmostEqual(math.dist(j['r_el'], j['r_wr']), FORE * 100, delta=FORE * 100 * 0.13)

    def test_held_prop_sits_in_the_grip_and_moves_with_the_hand(self):
        sprite = Image.new('RGBA', (40, 20), (200, 100, 50, 255))
        held = {'r': Held(sprite, (20, 18), 0.4)}
        grips = []
        for rh in [(0.5, -0.2), (0.7, -1.4)]:
            self.char.render(replace(Pose(), rh=rh, r_shape='grip'), (500, 900), 1.0, (0, 0), held=held)
            j = self.char.last_joints
            self.assertEqual(j['r_held_anchor'], j['r_grip'])
            grips.append(j['r_grip'])
        self.assertGreater(math.dist(*grips), 50)

    def test_a_held_prop_needs_a_gripping_hand(self):
        sprite = Image.new('RGBA', (40, 20), (200, 100, 50, 255))
        with self.assertRaises(ValueError):
            self.char.render(replace(Pose(), r_shape='open_palm'), (500, 900), 1.0, (0, 0), held={'r': Held(sprite, (20, 18), 0.4)})

    def test_render_returns_body_and_front_layers(self):
        out = self.char.render(replace(Pose(), rh=(0.4, -1.2)), (500, 900), 1.0, (0, 0), table_y=850)
        self.assertGreater((np.asarray(out['body'][0].getchannel('A')) > 0).mean(), 0.05)
        self.assertIsNotNone(out['front'])   # the raised hand is above the table, so it is drawn over it


class ChoreographyTest(unittest.TestCase):
    """The beats 1-2 performance must respect the anatomy rules at every frame, for both voices."""

    def frames(self, T, cut, end):
        scene = scene_salary.Scene(T, cut, end, load_art=False)
        char = scene.lead
        for i in range(int(end * 30)):
            t = i / 30
            pose = scene.perf.pose(t)
            j = char.joints(pose)
            for side in ('l', 'r'):
                char.hand_placement(pose, j, side)
            yield t, scene, pose, j

    def check(self, T, cut, end):
        for t, scene, pose, j in self.frames(T, cut, end):
            face_r = 0.30 * scene.lead.D
            for side in ('l', 'r'):
                requested = pose.l_rot if side == 'l' else pose.r_rot
                self.assertLessEqual(abs(requested), hands.WRIST_LIMIT, f'{t:.2f}s {side}: wrist asked to bend {requested:.0f} deg')
                self.assertLessEqual(abs(j[side + '_bend']), hands.WRIST_LIMIT)
            for side, what in scene.holding(t).items():
                shape = pose.l_shape if side == 'l' else pose.r_shape
                self.assertIn(shape, GRIPPING, f'{t:.2f}s {side} holds {what} with a {shape!r} hand')
            if not set(scene.perf.active(t)) & set(scene.face_ok):
                for side in ('l', 'r'):
                    for a, b in ((side + '_sh', side + '_el'), (side + '_el', side + '_wr')):
                        dist = segment_distance(j['head'], j[a], j[b])
                        self.assertGreater(dist, face_r, f'{t:.2f}s {side} arm crosses the face ({scene.perf.active(t)})')

    def test_michael_take(self):
        self.check(MICHAEL, 4.93, 12.38)

    def test_heart_take(self):
        self.check(HEART, 4.93 * 0.86, 12.38 * 0.86)


class PaintedBaseTest(unittest.TestCase):
    """Characters on a painted Empty Chair base: only the head, arms, hands and props are drawn."""

    def setUp(self):
        self.base = load_base('lead-at-table')
        self.lead = Puppet(self.base, 'lead')

    def test_arms_match_the_art_hands_reach_the_tabletop_not_the_floor(self):
        reach = (puppet.UPPER + puppet.FORE) * self.lead.D
        for side in ('l', 'r'):
            sx, sy = self.lead.shoulders[side]
            rx, ry = self.lead.s['rest'][side]
            self.assertLess(math.dist((sx, sy), (rx, ry)), reach, f'{side} hand cannot reach its place on the table')
            floor = 1380   # the painted feet stand at about y 1380-1500 in 04-waiting
            self.assertLess(sy + reach, floor, 'arms long enough to reach the floor')
        self.assertGreater(puppet.ARM_W[1], 0.11)   # thick navy segments, like the painted sleeves

    def test_arm_roots_are_hidden_behind_the_torso_side(self):
        occ, (ox, oy) = self.base.layers['occluder']
        a = np.asarray(occ.getchannel('A'))
        for side in ('l', 'r'):
            sx, sy = self.lead.shoulders[side]
            self.assertGreater(a[round(sy) - oy, round(sx) - ox], 200, f'{side} shoulder is not covered by the torso')

    def test_held_prop_sits_in_the_grip_on_the_painted_base(self):
        sprite = Image.new('RGBA', (40, 20), (200, 100, 50, 255))
        grips = []
        for target in ((120, 640), (110, 560)):
            pose = replace(Pose(), lh=self.lead.body(target), l_shape='grip')
            self.lead.render(pose, 1.0, (0, 0), held={'l': Held(sprite, (20, 18), 0.4)})
            j = self.lead.last_joints
            self.assertEqual(j['l_held_anchor'], j['l_grip'])
            grips.append(j['l_grip'])
        self.assertGreater(math.dist(*grips), 50)
        with self.assertRaises(ValueError):
            self.lead.render(replace(Pose(), l_shape='open_palm'), held={'l': Held(sprite, (20, 18), 0.4)})

    def test_painted_heads_are_gone_from_the_plates_and_the_head_art_has_no_face(self):
        for name in ('lead-at-table', 'together'):
            base = load_base(name)
            plate = np.asarray(base.plate).astype(int)
            for seat in base.meta['seats']:
                cx, cy = seat['head']
                r = seat['D'] * 0.30
                inner = plate[int(cy - r):int(cy + r), int(cx - r):int(cx + r)]
                self.assertLess((inner.mean(-1) > 235).mean(), 0.5, f'{name}: {seat["name"]} head still painted in the plate')
                sprite, (hx, hy) = base.heads[seat['name']]
                face = np.asarray(sprite).astype(int)[int(hy - r):int(hy + r), int(hx - r):int(hx + r)]
                self.assertEqual(int(((face[..., :3].mean(-1) < 120) & (face[..., 3] > 200)).sum()), 0,
                                 f'{name}: {seat["name"]} head art still has painted features')

    def test_far_arm_goes_behind_the_head_unless_the_action_needs_it(self):
        self.assertEqual(self.lead.arm_group(Pose(), self.lead.far), 'far')
        self.assertEqual(self.lead.arm_group(replace(Pose(), extra={self.lead.far + '_front': 1.0}), self.lead.far), 'near')
        self.assertEqual(self.lead.arm_group(replace(Pose(), extra={'l_under': 1.0}), 'l'), 'under')

    def test_two_shot_hands_stay_on_their_own_side(self):
        base = load_base('together')
        lead, friend = Puppet(base, 'lead'), Puppet(base, 'friend')
        self.assertLess(max(lead.s['rest']['l'][0], lead.s['rest']['r'][0]), min(friend.s['rest']['l'][0], friend.s['rest']['r'][0]))


class PerformanceTest(unittest.TestCase):
    def test_action_blends_in_and_out_and_pose_is_deterministic(self):
        act = Action(1.0, 2.0, lambda t, p: replace(p, lean=10.0), fade_in=0.2, fade_out=0.2)
        perf = Performance(Pose(), Idle(seed=1), [act])
        self.assertEqual(act.weight(0.5), 0.0)
        self.assertEqual(act.weight(1.5), 1.0)
        self.assertAlmostEqual(perf.pose(1.5).lean, 10.0, delta=1.0)
        self.assertEqual(perf.pose(1.37), perf.pose(1.37))

    def test_idle_life_keeps_the_body_moving(self):
        perf = Performance(Pose(), Idle(seed=1), [])
        self.assertGreater(len({round(perf.pose(t / 10).breath, 3) for t in range(40)}), 10)
        self.assertLess(min(perf.pose(t / 100).eye_open for t in range(600)), 0.3)


class ArtTest(unittest.TestCase):
    def test_head_plate_has_no_painted_features_left(self):
        plate = np.asarray(parts.head_plate('left')).astype(int)
        h, w = plate.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        inner = np.hypot(xx - w / 2, yy - h / 2) < w * 0.40
        self.assertEqual(int(((plate[..., :3].mean(-1) < 120) & inner).sum()), 0)

    def test_torso_comes_from_the_empty_chair_art(self):
        sprite, anchors = parts.torso_art()
        self.assertGreater(sprite.height / anchors['art_d'], 1.3)
        lum = np.asarray(sprite)[..., :3].mean(-1)[np.asarray(sprite.getchannel('A')) > 200]
        self.assertGreater((lum > 200).mean(), 0.5)    # mostly the painted white body
        self.assertGreater((lum < 60).mean(), 0.02)    # with its navy outline


if __name__ == '__main__':
    unittest.main()
