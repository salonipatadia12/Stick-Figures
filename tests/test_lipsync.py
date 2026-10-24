"""Regression checks for dialogue articulation and narrator-only silence."""
from array import array
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'outputs/the-empty-chair-source'))
from lipsync import LipSync

class LipSyncTests(unittest.TestCase):
    def setUp(self):
        self.lip=LipSync.__new__(LipSync)
        self.lip.rate=1000
        self.lip.audio=array('h',[5000])*40000

    def test_mouth_stays_open_inside_spoken_word(self):
        # "drew" must articulate throughout, not merely flash at word onset.
        for source in (13.48,13.55,13.65,13.72):
            self.assertGreater(self.lip.shape(source,source)[1],.1)

    def test_narration_does_not_move_character_mouth(self):
        self.assertEqual(self.lip.shape(2,2),(1,0))

    def test_silence_closes_mouth_even_inside_dialogue(self):
        self.lip.audio=array('h',[0])*40000
        self.assertEqual(self.lip.shape(13.55,13.55)[1],0)

if __name__=='__main__':unittest.main()
