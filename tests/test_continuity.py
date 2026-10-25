from pathlib import Path
import sys
import unittest
from PIL import ImageChops
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs/the-empty-chair-source'))
from motion import MovementScene,PropRig
from render import SPECS,HERE
from recut import cues, SHOTS, at

class ContinuityTests(unittest.TestCase):
    def test_bag_lift_keeps_faces_pixel_stable(self):
        scene=MovementScene('bag',SPECS['bag'])
        a=scene.frame(0,'lift');b=scene.frame(.7,'lift')
        for face in [(110,350,365,555),(375,225,600,460)]:
            self.assertIsNone(ImageChops.difference(a.crop(face),b.crop(face)).getbbox())

    def test_grip_does_not_include_the_other_characters_hand(self):
        rig=PropRig('bag',SPECS['bag']['file'])
        self.assertIsNone(rig.hand.getchannel('A').crop((647,789,668,795)).getbbox())

    def test_both_books_only_appear_after_the_chair_invitation(self):
        invitation_end=cues()[-1][1]
        book_times=[at(shot[0]) for shot in SHOTS if shot[1]=='books']
        self.assertTrue(book_times)
        self.assertTrue(all(t>invitation_end for t in book_times))
        for time in [29.8,31.4,32.5]:
            shot=max((shot for shot in SHOTS if at(shot[0])<=time),key=lambda shot:shot[0])
            self.assertEqual(shot[1],'return')

    def test_ending_does_not_claim_a_second_bag_move(self):
        self.assertNotIn('moved her bag',cues()[-1][2])
        self.assertIn('pulled out his chair',cues()[-1][2])
        self.assertTrue((HERE/'narration-continuity.mp3').is_file())

if __name__=='__main__':unittest.main()
