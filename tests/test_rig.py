import math
import unittest
from dataclasses import replace

import numpy as np

from pipeline.rig import parts
from pipeline.rig.actions import Action, Idle, Performance
from pipeline.rig.skeleton import FORE, UPPER, Character, Held, Pose, limb, two_bone


class TwoBoneTest(unittest.TestCase):
    def test_bone_lengths_are_kept_for_reachable_targets(self):
        for target in [(30, 80), (-50, 40), (60, -20), (0, 100)]:
            mid, end, (l1, l2) = two_bone((0, 0), target, 60, 58, 1)
            self.assertAlmostEqual(math.dist((0, 0), mid), 60, places=4)
            self.assertAlmostEqual(math.dist(mid, end), 58, places=4)
            self.assertAlmostEqual(math.dist(end, target), 0, places=4)

    def test_unreachable_target_stretches_at_most_a_little(self):
        _, end, (l1, l2) = two_bone((0, 0), (0, 500), 60, 58, 1)
        self.assertLessEqual(l1 + l2, (60 + 58) * 1.12 + 1e-6)
        self.assertLess(math.dist((0, 0), end), 500)

    def test_raised_arm_keeps_its_elbow_outside_not_across_the_face(self):
        # Right shoulder at x=27 of a body centred on x=0; hand raised above the head.
        shoulder, hand = (27, -108), (30, -190)
        elbow, _, _ = limb(shoulder, hand, 60, 58, side=1, bend=1, centre_x=0)
        self.assertGreater(elbow[0], shoulder[0])
        inward, _, _ = limb(shoulder, hand, 60, 58, side=1, bend=-1, centre_x=0)
        self.assertLess(inward[0], elbow[0])


class CharacterTest(unittest.TestCase):
    def setUp(self):
        self.char = Character(D=100)

    def test_arm_bones_stay_the_same_length_across_poses(self):
        for rh in [(0.4, -0.1), (0.8, -1.5), (0.1, -1.8), (-0.2, -0.9)]:
            j = self.char.joints(replace(Pose(), rh=rh), (500, 900), 1.0, (0, 0))
            self.assertAlmostEqual(math.dist(j['r_sh'], j['r_el']), UPPER * 100, delta=UPPER * 100 * 0.13)
            self.assertAlmostEqual(math.dist(j['r_el'], j['r_wr']), FORE * 100, delta=FORE * 100 * 0.13)

    def test_held_prop_moves_with_the_hand(self):
        sprite = parts.foot_sprite()
        held = {'r': Held(sprite, (10, 10), 0.4)}
        grips = []
        for rh in [(0.5, -0.2), (0.7, -1.4)]:
            self.char.render(replace(Pose(), rh=rh, r_shape='grip'), (500, 900), 1.0, (0, 0), held=held)
            j = self.char.last_joints
            grips.append((j['r_grip'], j['r_wr']))
        (g0, w0), (g1, w1) = grips
        self.assertGreater(math.dist(g0, g1), 50)          # the grip travelled with the hand
        self.assertAlmostEqual(math.dist(g0, w0), math.dist(g1, w1), delta=2)  # and stayed in the hand

    def test_render_returns_a_layer_with_the_figure_on_it(self):
        layer, (x, y) = self.char.render(Pose(), (500, 900), 1.0, (0, 0))
        alpha = np.asarray(layer.getchannel('A'))
        self.assertGreater((alpha > 0).mean(), 0.05)


class PerformanceTest(unittest.TestCase):
    def test_action_blends_in_and_out_and_pose_is_deterministic(self):
        act = Action(1.0, 2.0, lambda t, p: replace(p, lean=10.0), fade_in=0.2, fade_out=0.2)
        perf = Performance(Pose(), Idle(seed=1), [act])
        self.assertEqual(act.weight(0.5), 0.0)
        self.assertEqual(act.weight(1.5), 1.0)
        self.assertEqual(act.weight(2.5), 0.0)
        self.assertAlmostEqual(perf.pose(1.5).lean, 10.0, delta=1.0)  # idle sway adds under a degree
        self.assertEqual(perf.pose(1.37), perf.pose(1.37))

    def test_idle_life_keeps_the_body_moving(self):
        perf = Performance(Pose(), Idle(seed=1), [])
        breaths = {round(perf.pose(t / 10).breath, 3) for t in range(40)}
        self.assertGreater(len(breaths), 10)
        blinks = [perf.pose(t / 100).eye_open for t in range(600)]
        self.assertLess(min(blinks), 0.3)


class HeadPlateTest(unittest.TestCase):
    def test_head_plate_has_no_painted_features_left(self):
        plate = np.asarray(parts.head_plate('left')).astype(int)
        h, w = plate.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w]
        inner = np.hypot(xx - w / 2, yy - h / 2) < w * 0.40
        lum = plate[..., :3].mean(-1)
        self.assertEqual(int(((lum < 120) & inner).sum()), 0)


if __name__ == '__main__':
    unittest.main()
