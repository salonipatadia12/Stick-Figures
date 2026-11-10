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
        self.assertAlmostEqual(sizes['checklist'], 40 * O.CHECKLIST_SCALE, places=1)   # readable on a phone (user 2026-11-10)

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


class SafeAreaTest(unittest.TestCase):
    """Overlay text is logged with its box and must stay inside the caption safe area (user, video 2 review)."""
    def setUp(self):
        O.take_text_log()

    def test_slam_logs_its_box_and_slides_inside_the_safe_area(self):
        from pipeline import captions as C
        frame = Image.new('RGBA', (1080, 1920))
        O.slam(frame, O.text_layer('Stress keeps you alert', 78), 2.0, 1.0, centre=(540, 1130))
        box = O.take_text_log()[0]['box']
        self.assertLessEqual(box[2], C.SAFE['right'])
        self.assertGreaterEqual(box[0], C.SAFE['left'])

    def test_a_too_wide_slam_is_drawn_smaller_not_clipped(self):
        from pipeline import captions as C
        frame = Image.new('RGBA', (1080, 1920))
        O.slam(frame, O.text_layer('A sentence far too long for a phone screen', 90), 2.0, 1.0)
        d = O.take_text_log()[0]
        self.assertLessEqual(d['box'][2], C.SAFE['right'])
        self.assertLess(d['em'], 90)
        self.assertGreaterEqual(d['em'], 40)

    def test_split_compare_fits_both_labels_inside_the_safe_area(self):
        from pipeline import captions as C
        frame = Image.new('RGBA', (1080, 1920))
        O.split_compare(frame, 3.0, 'eight hours', 'eight good ones?', 1.0, 2.0, centre=(540, 330))
        log = O.take_text_log()
        self.assertEqual(len(log), 2)
        self.assertLessEqual(max(d['box'][2] for d in log), C.SAFE['right'])
        self.assertGreaterEqual(min(d['em'] for d in log), 40)

    def test_text_check_fails_overlay_text_outside_the_safe_area(self):
        r = qa.text_check([dict(t=1.0, label='x', em=60, box=[600, 300, 1000, 380], box_label='x')], [])
        self.assertFalse(r['pass_'])
        self.assertEqual(r['frames_outside_safe_area'], 1)
        r = qa.text_check([dict(t=1.0, label='x', em=60, box=[100, 1400, 500, 1500], box_label='x')], [])
        self.assertFalse(r['pass_'], 'bottom quarter')
        r = qa.text_check([dict(t=1.0, label='x', em=60, box=[100, 300, 900, 380], box_label='x')], [])
        self.assertTrue(r['pass_'])


if __name__ == '__main__':
    unittest.main()
