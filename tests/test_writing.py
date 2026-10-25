import math
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs/the-empty-chair-source'))
from writing import pose, attached, arm_elbow, PIVOT, WRIST, SHOULDER, UPPER_LENGTH, FOREARM_LENGTH

class WritingTests(unittest.TestCase):
    def test_contact_tip_matches_visible_ink_endpoint(self):
        contacts=0
        for frame in range(145):
            tip,lift,angle,ink=pose(frame/24)
            if lift==0:
                rendered_tip=attached(PIVOT,tip,lift,angle)
                self.assertAlmostEqual(math.dist(rendered_tip,ink[-1][-1]),0,places=6)
                contacts+=1
        self.assertGreater(contacts,70)

    def test_arm_does_not_stretch_across_drawing(self):
        for frame in range(145):
            tip,lift,angle,_=pose(frame/24)
            wrist=attached(WRIST,tip,lift,angle);elbow=arm_elbow(wrist)
            self.assertAlmostEqual(math.dist(SHOULDER,elbow),UPPER_LENGTH,places=6)
            self.assertAlmostEqual(math.dist(elbow,wrist),FOREARM_LENGTH,places=6)

    def test_pen_up_does_not_leave_a_connecting_line(self):
        # First transfer: between the ellipse and its first leg.
        a=pose(1.80);b=pose(1.85)
        self.assertGreater(a[1],0);self.assertGreater(b[1],0)
        self.assertEqual(a[3],b[3])
        self.assertNotEqual(a[0],b[0])

if __name__=='__main__':unittest.main()
