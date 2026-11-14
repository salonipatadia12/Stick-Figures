"""pipeline/auto.py (the rules Auto mode decides gates by), `stick make --auto`'s paint-and-render step, and the QA fixes
of 2026-11-13 ("How did humans get their first job": words, text, hook). Nothing is painted or rendered here."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from pipeline import auto, cli, config, qa, styles
from pipeline.models import Beat, Episode, ScriptDraft, Storyline
from pipeline_fixtures import salary_draft, storyline


def host_episode():
    from webapp import fake
    draft = ScriptDraft.model_validate(fake.host_draft())
    return Episode(slug='salary', topic='Why your salary disappears by the 20th', title=draft.title,
                   hook_promise=draft.hook_promise, hook_options=draft.hook_options, hook_pick=0,
                   storylines=[Storyline.model_validate(storyline())], storyline_pick=1,
                   beats=[Beat.from_draft(i, b) for i, b in enumerate(draft.beats)])


class StorylineRule(unittest.TestCase):
    def test_the_first_storyline_the_gate_accepts(self):
        ep = Episode(slug='x', topic='Why honey never spoils',
                     storylines=[Storyline.model_validate(storyline(f)) for f in ('mystery', 'pov', 'countdown')])
        with mock.patch('pipeline.story.last_picked_format', return_value=('', '')):
            self.assertEqual(auto.first_storyline(ep)[0], 1)
        with mock.patch('pipeline.story.last_picked_format', return_value=('mystery', 'old-one')):
            n, why = auto.first_storyline(ep)
            self.assertEqual(n, 2)
            self.assertIn('the format of old-one', why)
        ep.storylines = ep.storylines[:1]
        with mock.patch('pipeline.story.last_picked_format', return_value=('mystery', 'old-one')):
            self.assertEqual(auto.first_storyline(ep)[0], None)


class BasesRule(unittest.TestCase):
    def test_a_beat_with_no_base_becomes_a_painted_scene_of_its_place(self):
        ep = host_episode()
        b = ep.beats[1]
        b.shot.visual, b.shot.insert = 'character', None
        b.shot.staging.setting, b.shot.staging.setting_detail = 'other', 'a busy bus stop'
        fake_missing = {'lead-standing-bus-stop': dict(beats=[b.id], setting='other', pose='standing')}
        with mock.patch('pipeline.bases.missing', return_value=fake_missing), mock.patch('pipeline.bases.plan', return_value=[]):
            notes = auto.resolve_bases(ep)
        self.assertEqual(len(notes), 1)
        self.assertEqual(b.shot.visual, 'insert')
        self.assertEqual(b.shot.insert.key, 'a-busy-bus-stop-empty')
        self.assertIn('no people', b.shot.insert.scene)
        self.assertEqual(b.shot.host.where, 'scene')

    def test_older_episodes_are_left_alone(self):
        draft = ScriptDraft.model_validate(salary_draft())
        ep = Episode(slug='s', topic='Why your salary disappears', beats=[Beat.from_draft(i, x) for i, x in enumerate(draft.beats)])
        self.assertEqual(auto.resolve_bases(ep), [])


class HookRule(unittest.TestCase):
    def test_the_first_hook_that_passes_and_none_when_all_fail(self):
        ep = host_episode()
        style = styles.load('cozy')
        with mock.patch.object(auto, 'script_problems', side_effect=lambda e, h, s: ['x'] if h == 1 else []):
            self.assertEqual(auto.pick_hook(ep, style), (2, []))
        with mock.patch.object(auto, 'script_problems', return_value=['b05: cites unknown sources']):
            self.assertEqual(auto.pick_hook(ep, style), (None, ['b05: cites unknown sources']))


class ScriptSlips(unittest.TestCase):
    def test_auto_fixes_an_empty_split_top_and_a_missing_anchor(self):
        # 2026-11-14 crude oil: auto stopped at the script for exactly these two writer slips
        from pipeline.models import Motion, SplitCue
        ep = host_episode()
        b, c = ep.beats[1], ep.beats[2]
        b.shot.visual = 'split'
        b.shot.insert.key = ''
        b.shot.split = SplitCue(key='dino-myth', scene='a dinosaur skeleton', labels=['Myth', 'Fact'], anchors=['', ''])
        c.shot.motion = [Motion(recipe='punch_in', anchor='', detail='')]
        notes = auto.fix_script(ep)
        self.assertEqual(len(notes), 2, notes)
        self.assertEqual((b.shot.visual, b.shot.insert.key), ('insert', 'dino-myth'))
        self.assertIn('Myth', [e.detail for e in b.shot.insert.effects])
        self.assertIn(c.shot.motion[0].anchor.casefold(), c.narration.casefold())
        errs = [i for i in ep.check() if i.severity == 'error' and i.where in (b.id, c.id)
                and ('split' in i.message or 'anchor' in i.message)]
        self.assertEqual(errs, [])


class AutoRender(unittest.TestCase):
    def test_paints_then_renders_and_stops_when_a_scene_fails(self):
        calls = []
        code = cli.auto_render('demo', run=lambda cmd: calls.append(cmd[2:4] if cmd[1] == '-m' else ['render']) or 0)
        self.assertEqual(code, 0)
        self.assertEqual(calls, [['pipeline', 'inserts'], ['pipeline', 'props'], ['render']])
        calls.clear()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.auto_render('demo', run=lambda cmd: calls.append(cmd) or cli.EXIT_GATE)
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertEqual(len(calls), 1)                         # nothing rendered after a failed scene
        self.assertIn('failed QA on both seeds', out.getvalue())

    def test_never_paints_or_renders_another_episodes_folder(self):
        # 2026-11-13: a test with a temporary EPISODES_DIR started rendering the real episode of the same slug
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, 'EPISODES_DIR', Path(d)), \
                mock.patch('subprocess.call', side_effect=AssertionError('must not run')):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.auto_render('demo'), cli.EXIT_ERROR)
        self.assertIn("not the repo's", out.getvalue())


class WordsFix(unittest.TestCase):
    def heard(self, text):
        return [dict(w=w) for w in text.split()]

    def test_percent_written_as_a_sign(self):
        for heard in ('gave us 20 % of it', 'gave us 20% of it'):
            self.assertEqual(qa.words_check('gave us twenty percent of it'.split(), self.heard(heard))['missing'], [], heard)

    def test_a_plural_s_merged_into_a_following_s(self):
        self.assertEqual(qa.words_check('hold the infants securely'.split(), self.heard('hold the infant securely'))['missing'], [])
        self.assertEqual(qa.words_check('the cats sat'.split(), self.heard('the cat sat'))['missing'], [])

    def test_a_real_miss_is_still_caught(self):
        self.assertEqual(qa.words_check('hold the infants today'.split(), self.heard('hold the infant today'))['missing'], ['infants'])
        self.assertEqual(qa.words_check('twenty percent'.split(), self.heard('20'))['missing'], ['percent'])


class BarLabels(unittest.TestCase):
    def test_a_label_fits_its_column(self):
        from pipeline.rig import graphics as G
        from pipeline import overlays as O
        d = ImageDraw.Draw(Image.new('RGB', (1080, 1920)))
        width = 260 + (1080 - 2 * 260) / 3 - 28
        for label in ('Spear Power: Low', 'Protection Need: High', 'Sugar'):
            lines, size = G.fit_column(d, label, width)
            self.assertGreaterEqual(size, 40)                  # never under QA's smallest readable size
            f = O.font(O.FONT, size)
            self.assertTrue(all(d.textlength(x, font=f) <= width for x in lines), (label, lines, size))
        self.assertEqual(G.fit_column(d, 'Protection Need: High', width)[0], ['Protection Need:', 'High'])
        self.assertEqual(G.fit_column(d, 'Sugar', width), (['Sugar'], G.LABEL))


if __name__ == '__main__':
    unittest.main()
