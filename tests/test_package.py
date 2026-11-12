"""`stick package` (pipeline/package.py): the publishing kit from the episode's own data. No model runs (the --titles
call is mocked), nothing paid is sent, and the real episodes/ folder is never touched (EPISODES_DIR is a temp dir)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from pipeline import config, package, store
from pipeline.models import Episode, HookOption, Source, Storyline
from pipeline_fixtures import salary_draft, storyline

SLUG = 'why-your-salary-disappears-by-the-20th'


def make_episode(fmt='mini_story', loop=None, hook="Your salary didn't shrink. It leaked."):
    from pipeline.models import Beat, ScriptDraft
    draft = ScriptDraft.model_validate(salary_draft())
    beats = [Beat.from_draft(i, b) for i, b in enumerate(draft.beats)]
    beats[0].narration = hook
    if loop:
        beats[-1].narration = loop
    beats[4].source_ids = ['s1']
    sl = Storyline.model_validate(dict(storyline(fmt), title='The leaking jar'))
    return Episode(slug=SLUG, topic='Why your salary disappears by the 20th', niche='personal finance', target_length=45,
                   title=draft.title, hook_promise='Why does the money run out before the month does?',
                   hook_options=[HookOption(line=hook, frame_one='a jar leaking coins', answer='spending creeps up',
                                            loop_line=beats[-1].narration),
                                 HookOption(line='Where did the money go?', frame_one='an empty jar', answer='x', loop_line='y'),
                                 HookOption(line='Payday. Then nothing.', frame_one='a calendar', answer='x', loop_line='y')],
                   hook_pick=1, beats=beats, storylines=[sl], storyline_pick=1,
                   sources=[Source(id='s1', claim='Spending tends to rise with income.', url='https://www.example.gov/a',
                                   title='Saving basics'),
                            Source(id='s2', claim='Unused.', url='https://www.example.edu/b',
                                   title='WWW.EXAMPLE.EDU PAGE:: HOME')])


class TextItems(unittest.TestCase):
    def setUp(self):
        self.ep = make_episode()

    def test_five_distinct_titles_cover_search_curiosity_and_appeal(self):
        titles = package.rule_titles(self.ep)
        self.assertEqual(len(titles), 5)
        self.assertEqual(len({t.lower() for t in titles}), 5)
        self.assertEqual(titles[0], 'Why Your Salary Disappears by the 20th, Explained')          # search intent
        self.assertIn("Your salary didn't shrink. It leaked.", titles)                          # curiosity: the hook
        self.assertIn('The Real Reason your salary disappears by the 20th', titles)             # broad appeal
        for t in titles:
            self.assertLessEqual(len(t), package.TITLE_MAX)
            self.assertNotRegex(t, r'\b(45|seconds?)\b')        # no length: the cut can run longer than the target
            self.assertIsNone(package.DONT_PROMISE.search(t))

    def test_overlay_is_two_to_four_words_and_opens_the_question(self):
        ep = make_episode(hook="I can't tickle myself. Here's why.")
        ep.beats[0].on_screen_text = ''
        self.assertEqual(package.overlay_words(ep), "I can't tickle myself")   # the fullest sentence that fits
        ep = make_episode(hook='Wait... why did I come in here?')
        ep.beats[0].on_screen_text = ''
        ep.topic = 'Why you forget why you walked into a room and then stand there'
        self.assertEqual(package.overlay_words(ep), 'why did I come…')          # never a cut-off fragment
        ep = make_episode()
        ep.beats[0].on_screen_text = 'Gone by the 20th'
        self.assertEqual(package.overlay_words(ep), 'Gone by the 20th')         # the hook beat's own text first

    def test_hashtags(self):
        tags = package.hashtags(self.ep)
        self.assertTrue(8 <= len(tags) <= 12, tags)
        self.assertTrue(all(t.startswith('#') and t[1:].isalnum() for t in tags))
        self.assertEqual(len(tags), len(set(tags)))
        self.assertIn('#salary', tags)
        self.assertIn('#finance', tags)                          # from the niche
        for filler in ('#only', '#your', '#the', '#20th', '#000'):
            self.assertNotIn(filler, tags)

    def test_description_lists_cited_sources_and_credits(self):
        text = package.description(self.ep)
        self.assertIn('- Saving basics: https://www.example.gov/a', text)
        self.assertNotIn('example.edu/b', text)                 # s2 is not cited by any beat
        self.assertIn('Kokoro-82M (Apache-2.0)', text)
        self.assertNotIn('renders/', text)
        self.assertNotRegex(text, r'\d+:\d\d')                  # no chapters in a Short

    def test_messy_page_titles_become_the_domain(self):
        s = Source(id='s9', claim='x', url='https://www.example.edu/b', title='WWW.EXAMPLE.EDU PAGE:: HOME')
        self.assertEqual(package._source_name(s), 'example.edu')

    def test_pinned_comment(self):
        ep = make_episode(fmt='revelation', loop='So is the leak your fault, or the bank\'s?')
        self.assertEqual(package.pinned_comment(ep), "So is the leak your fault, or the bank's?")
        ep = make_episode(loop='Wait... why did I come in here?', hook='Wait... why did I come in here?')
        comment = package.pinned_comment(ep)                     # the hook said again is not a discussion question
        self.assertEqual(comment, 'Before watching, why did you think your salary disappears by the 20th? Did this '
                                  'change your mind?')


class Thumbnail(unittest.TestCase):
    """A synthetic 1080x1920 render with one caption band and a standing host: the crop must keep the head, skip the
    caption, and the words must stay off the face."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.out = Path(self.dir.name) / 'renders/sample' / SLUG
        (self.out / 'shots').mkdir(parents=True)
        self.video = self.out / f'{SLUG}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=1080x1920:rate=10', '-t', '2',
                        '-pix_fmt', 'yuv420p', str(self.video)], check=True)
        (self.out / 'timeline.json').write_text(json.dumps(dict(duration=2.0, beats=[
            dict(id='b01', start=0.0, end=1.0, words=[]), dict(id='b02', start=1.0, end=2.0, words=[])])))
        stem = 'b01-0123456789abcdef'
        (self.out / 'shots.txt').write_text(f"file '/old/place/renders/sample/{SLUG}/shots/{stem}.mp4'\n")
        times = [round(i / 10, 2) for i in range(20)]
        self.caption = [0, 900, 1080, 1000]
        self.host = [100, 1100, 500, 1920]
        (self.out / 'shots' / f'{stem}.captions.json').write_text(json.dumps([dict(t=t, box=self.caption) for t in times]))
        (self.out / 'shots' / f'{stem}.text.json').write_text(json.dumps([dict(t=t, label='', host=[dict(box=self.host)])
                                                                          for t in times]))

    def test_crop_keeps_the_head_skips_the_caption_and_text_avoids_the_face(self):
        dst = Path(self.dir.name) / 'thumbnail.jpg'
        with mock.patch.object(package, 'ROOT', Path(self.dir.name)):
            info = package.make_thumbnail(self.video, SLUG, 'Gone by the 20th', dst)
        with Image.open(dst) as im:
            self.assertEqual(im.size, (1280, 720))
        y = info['band_top_px']
        self.assertFalse(min(y + package.CROP_H, self.caption[3]) - max(y, self.caption[1]) > 0, 'the band cuts the caption')
        head = package.head_box(self.host)
        self.assertTrue(y <= head[1] and head[3] <= y + package.CROP_H, 'the head is not in the band')
        self.assertTrue(info['face_in_frame'] and info['caption_data'])
        k = 1280 / 1080
        face = package.head_box(self.host, generous=True)
        fx0, fy0, fx1, fy1 = face[0] * k, (face[1] - y) * k, face[2] * k, (face[3] - y) * k
        x0, ty0, x1, ty1 = info['text_box']
        self.assertFalse(min(x1, fx1) > max(x0, fx0) and min(ty1, fy1) > max(ty0, fy0), 'the words cover the face')
        self.assertTrue(info['inside_safe_margin'])
        self.assertGreaterEqual(info['fill_vs_outline_contrast'], 4.5)

    def test_without_shot_data_the_caption_check_is_pending(self):
        for f in (self.out / 'shots').iterdir():
            f.unlink()
        dst = Path(self.dir.name) / 'thumbnail.jpg'
        with mock.patch.object(package, 'ROOT', Path(self.dir.name)):
            info = package.make_thumbnail(self.video, SLUG, 'Gone by the 20th', dst)
        self.assertFalse(info['caption_data'])
        check = package.checklist(make_episode(), self.video, info, 'written by rules')['checks']
        self.assertEqual(check['thumbnail_clear_of_captions']['status'], 'pending')


class Build(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        for p in (mock.patch.object(config, 'EPISODES_DIR', Path(self.dir.name) / 'episodes'),
                  mock.patch.object(package, 'ROOT', Path(self.dir.name))):
            p.start()
            self.addCleanup(p.stop)
        store.save(make_episode(), store.folder(SLUG))

    def test_writes_the_five_items_without_a_video_and_marks_the_thumbnail_pending(self):
        with mock.patch('pipeline.llm.generate', side_effect=AssertionError('no model call without --titles')):
            res = package.build(SLUG)
        out = store.folder(SLUG) / 'out'
        for name in ('titles.txt', 'description.txt', 'comment.txt', 'publish_checklist.json'):
            self.assertTrue((out / name).is_file(), name)
        self.assertFalse((out / 'thumbnail.jpg').exists())
        self.assertEqual(len((out / 'titles.txt').read_text().splitlines()), 5)
        check = json.loads((out / 'publish_checklist.json').read_text())
        statuses = {k: v['status'] for k, v in check['checks'].items()}
        self.assertEqual(statuses['thumbnail_mobile_readability'], 'pending')
        self.assertEqual(statuses['final_cut_matches_script'], 'pending')
        self.assertEqual(statuses['your_final_approval'], 'pending')          # never claimed for the user
        self.assertEqual(statuses['content_id_and_monetization'], 'pending')
        self.assertEqual(statuses['timestamps'], 'not_applicable')
        self.assertTrue(set(statuses.values()) <= {'checked', 'pending', 'not_applicable'})
        self.assertTrue(any('no rendered video' in n for n in res['notes']))

    def test_titles_flag_uses_one_local_call_and_unloads(self):
        answer = dict(titles=['Where Your Salary Really Goes', 'Why Payday Never Lasts', 'The Leak in Your Paycheck',
                              'Your Salary, Explained', 'The 20th Problem'])
        with mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'ollama'}), \
                mock.patch('pipeline.llm.is_available', return_value=True), \
                mock.patch('pipeline.llm.generate_json', return_value=answer) as gen, \
                mock.patch('pipeline.llm.unload') as unload:
            res = package.build(SLUG, use_llm=True)
        self.assertEqual(gen.call_count, 1)
        self.assertEqual(unload.call_count, 1)
        self.assertEqual(res['titles'], answer['titles'])

    def test_titles_flag_never_makes_a_paid_call(self):
        with mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'anthropic'}), \
                mock.patch('pipeline.llm.generate_json', side_effect=AssertionError('paid call')):
            res = package.build(SLUG, use_llm=True)
        self.assertEqual(len(res['titles']), 5)                   # the rule-based ones
        self.assertTrue(any('SCRIPT_PROVIDER is not ollama' in n for n in res['notes']))

    def test_a_bad_model_answer_falls_back_to_the_rules(self):
        with mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'ollama'}), \
                mock.patch('pipeline.llm.is_available', return_value=True), \
                mock.patch('pipeline.llm.generate_json', return_value=dict(titles=['Only one', 'Only one'])), \
                mock.patch('pipeline.llm.unload'):
            res = package.build(SLUG, use_llm=True)
        self.assertEqual(res['titles'], package.rule_titles(make_episode()))
        self.assertTrue(any('usable titles' in n for n in res['notes']))

    def test_cli(self):
        import contextlib
        import io
        from pipeline import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(['package', SLUG])
        self.assertEqual(code, 0)
        self.assertIn('nothing was published or uploaded', buf.getvalue())
        self.assertIn('titles.txt', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
