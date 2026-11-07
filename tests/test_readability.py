"""On-screen text must read on a phone: the overlay library logs the size every text is drawn at, and the
QA text check fails a render whose type is under 40 px (font em in frame pixels)."""
import unittest

from PIL import Image

from pipeline import overlays as O
from pipeline import qa
from pipeline.rig.props import WallCalendar


class TextSizeLogTest(unittest.TestCase):
    def setUp(self):
        O.take_text_log()

    def test_sprites_carry_their_type_size_and_place_text_scales_it(self):
        frame = Image.new('RGBA', (1080, 1920))
        tg = O.tag('WEEK 1', 150)
        self.assertIn('em', tg.info)
        O.place_text(frame, tg, (300, 300), 1.1)
        log = O.take_text_log()
        self.assertEqual(len(log), 1)
        self.assertAlmostEqual(log[0]['em'], tg.info['em'] * 1.1, places=1)

    def test_slam_and_checklist_log_their_settled_size(self):
        frame = Image.new('RGBA', (1080, 1920))
        O.slam(frame, O.stacked(('Make it', 'last'), (110, 130)), 2.0, 1.0)
        O.checklist(frame, 2.0, [('Subscriptions', '-$15', 1.2), ('Coffee', '-$5', 1.4)], 1.0)
        sizes = {d['label'].split(':')[0]: d['em'] for d in O.take_text_log()}
        self.assertAlmostEqual(sizes['Make it / last'], 110, places=1)
        self.assertEqual(sizes['checklist'], 40)

    def test_receipt_strip_takes_per_entry_widths_and_keeps_its_type_size(self):
        strip = O.receipt_strip((('MON', '-$12'), ('PAYDAY', 'next')), (128, 168), 120)
        self.assertEqual(strip.width, 128 + 168 + 24)
        self.assertEqual(strip.info['em'], int(120 * 0.35))

    def test_checklist_widens_so_a_long_label_never_meets_its_price(self):
        f = O.font(O.FONT, 44)
        need = f.getlength('Subscriptions') + O.font(O.FONT, 40).getlength('-$15') + 30 + 26 * 2 + 74
        frame = Image.new('RGBA', (1080, 1920))
        O.checklist(frame, 2.0, [('Subscriptions', '-$15', 1.0)], 1.0, width=470)
        # the card is at least `need` wide: its painted paper spans past 470 px
        alpha = frame.getchannel('A').getbbox()
        self.assertGreaterEqual(alpha[2] - alpha[0], need * 0.6)

    def test_calendar_day_numbers_are_prop_lettering_not_logged_text(self):
        WallCalendar(240).render(7, 1.0)
        self.assertEqual(O.take_text_log(), [])


class TextCheckTest(unittest.TestCase):
    def test_passes_when_every_text_is_40px_or_more(self):
        r = qa.text_check([dict(t=1.0, label='tag: WEEK 1', em=41.8)], [dict(font_size=64, box=[0, 0, 1, 1])])
        self.assertTrue(r['pass_'])
        self.assertEqual(r['smallest_px'], 41.8)

    def test_fails_and_names_the_small_text(self):
        r = qa.text_check([dict(t=1.0, label='receipt: MON', em=21.0), dict(t=1.1, label='tag: BILLS', em=50)],
                          [dict(font_size=64, box=[0, 0, 1, 1])])
        self.assertFalse(r['pass_'])
        self.assertEqual(r['too_small'], [dict(label='receipt: MON', em=21.0)])

    def test_fails_on_a_small_caption(self):
        r = qa.text_check([], [dict(font_size=36, box=[0, 0, 1, 1])])
        self.assertFalse(r['pass_'])
        self.assertEqual(r['caption_frames_too_small'], 1)


if __name__ == '__main__':
    unittest.main()
