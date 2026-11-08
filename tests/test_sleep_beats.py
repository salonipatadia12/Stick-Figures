"""Video 2's beats build from word times alone (no art), on the approved bedroom and café bases."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/motion_test'))

from beat_base import word_times  # noqa: E402

SLUG = 'why-youre-always-tired-after-8-hours-of-sleep'


def fake_times(narration: str, t0: float):
    """Each word 0.3 s apart from t0, as the timeline would give them."""
    words = narration.split()
    return [(w, t0 + 0.3 * i) for i, w in enumerate(words)]


class SleepBeatsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sleep_beats
        cls.sb = sleep_beats
        cls.episode = json.loads((ROOT / 'episodes' / SLUG / 'episode.json').read_text())

    def test_every_beat_builds_a_performance_for_its_cast(self):
        t = 0.5
        for b in self.episode['beats']:
            T = word_times(fake_times(b['narration'], t))
            end = t + 0.3 * len(b['narration'].split()) + 0.4
            beat = self.sb.BEATS[b['id']](T, t, end, load_art=False)
            self.assertEqual(set(beat.perfs), set(beat.cast), b['id'])
            from pipeline.rig.skeleton import GRIPPING
            for k in range(1, 20):                                  # a held prop always sits in a gripping hand
                tt = t + (end - t) * k / 20
                for (name, side), _ in beat.holding(tt).items():
                    pose = beat.pose(name, tt)
                    self.assertIn(getattr(pose, side + '_shape'), GRIPPING, f'{b["id"]} {name} {side} at {tt:.2f}')
            self.assertTrue(all(len(e) == 3 for e in beat.events), b['id'])
            t = end + 0.25

    def test_the_bedroom_beats_use_the_approved_bedroom_bases(self):
        from pipeline import qa
        for bid, base in (('b01', 'lead-bed-edge-bedroom'), ('b02', 'lead-bed-asleep-top-bedroom'), ('b03', 'lead-bed-awake-bedroom'),
                          ('b05', 'lead-bed-awake-bedroom'), ('b06', 'lead-bed-edge-bedroom'), ('b07', 'lead-bed-awake-bedroom')):
            self.assertEqual(self.sb.BEATS[bid].base_name, base)
            self.assertIn(base, qa.APPROVED_BASES)

    def test_the_library_report_covers_every_beat(self):
        self.assertEqual(sorted(self.sb.LIBRARY_REPORT), [b['id'] for b in self.episode['beats']])

    def test_the_shared_clock_prop_is_the_awake_base_clock(self):
        from beat_base import PROPS
        self.assertIn('clock', PROPS)
        self.assertGreater(PROPS['clock'].width, 50)


if __name__ == '__main__':
    unittest.main()
