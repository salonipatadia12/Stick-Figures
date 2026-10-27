import json
import math
from pathlib import Path
import unittest
from studio.animation import AnimatedScene, tilt_point
from studio.models import Story
from studio.directing import check_prompt_actions


def story():return Story.model_validate_json(Path('studio/examples/swing-together.json').read_text())


class SwingTests(unittest.TestCase):
    def test_prompt_cannot_be_satisfied_by_mentions_alone(self):
        s=story();check_prompt_actions(s,'Two friends playing on the swing')
        for scene in s.scenes:
            for actor in scene.animation.actors:actor.action='idle'
            scene.narration='They played on the swings.'
        with self.assertRaisesRegex(ValueError,'omitted'):check_prompt_actions(s,'Two friends swinging together')
        check_prompt_actions(s,'Two friends swing by a cafe')

    def test_riders_seats_and_rope_grips_share_geometry(self):
        s=story().model_dump();e=AnimatedScene(s['scenes'][0],s['characters']);xs=[]
        for frame in range(82):
            poses,_=e.poses(0,frame/24)
            for z in poses.values():
                sx,sy=z['swing_seat'];rest=z['swing_anchor'];xs.append(sx)
                self.assertAlmostEqual(math.hypot(sx-rest,sy-270),550)
                self.assertAlmostEqual(sy-z['hip'],20)
                self.assertAlmostEqual(sy-z['head_point'][1],290)
                for side,key in [(z['direction'],'hand'),(-z['direction'],'backhand')]:
                    x,y=z[key];fraction=(y-270)/(sy-270)
                    self.assertAlmostEqual(x,rest+side*66+(sx-rest)*fraction)
                    shoulder=(z['x']+side*43,z['shoulder']+5)
                    self.assertLess(math.dist(shoulder,z[key]),180)
            self.assertAlmostEqual(poses['friend']['x']-poses['hero']['x'],.42*720)
        self.assertGreater(max(xs)-min(xs),400)

    def test_swings_continue_across_speaker_cuts(self):
        s=story().model_dump();a=AnimatedScene(s['scenes'][0],s['characters']);b=AnimatedScene(s['scenes'][1],s['characters'])
        b.time_offset=4.125
        self.assertEqual(a.poses(1,4.125),b.poses(0,0))
        self.assertEqual(a.camera_box,(0,0,720,1280))

    def test_gesture_elbows_keep_authored_lengths_and_stay_outside_torso(self):
        s=json.loads(Path('studio/examples/first-hello.json').read_text());e=AnimatedScene(s['scenes'][1],s['characters'])
        previous=None
        for frame in range(145):
            z=e.poses(frame/144)[0]['hero'];side=z['direction']
            sh=tilt_point((z['x']+side*43,z['shoulder']+5),(z['x'],z['hip']),z['lean'])
            elbow=z['elbows'][side]
            self.assertAlmostEqual(math.dist(sh,elbow),90)
            self.assertAlmostEqual(math.dist(elbow,z['hand']),90)
            self.assertGreater(side*(elbow[0]-sh[0]),0)
            if previous:self.assertLess(math.dist(previous,elbow),2)
            previous=elbow


if __name__=='__main__':unittest.main()
