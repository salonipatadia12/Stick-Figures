"""Topic understanding (pipeline/understand.py, user 2026-11-14) and pictures that match the story (QA story_match and
its repairs). The local model is replaced by stand-ins; the web app part runs on the test harness with a simulated Mac.
Nothing is searched, painted or rendered."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import auto, qa, repair, understand  # noqa: E402
from pipeline.models import Episode, Source, Storyline  # noqa: E402
from pipeline_fixtures import storyline  # noqa: E402

TYPED = 'how is crude oil naturally forming in th earth and not comig from the dinosaurs'
HEARD = dict(question='How does crude oil form naturally in the Earth, if not from dinosaurs?',
             title='Oil Is Not Dinosaur Juice', claims=['Crude oil forms naturally in the Earth.',
                                                        'Crude oil does not come from dinosaurs.'],
             misconception='Oil comes from dead dinosaurs.', angle='Oil is mostly ancient plankton cooked under rock.',
             myth_subjects=['dinosaur'])
FACTS = [Source(id='s1', claim='Most petroleum formed from marine plankton buried in sediments.', url='https://www.usgs.gov/a'),
         Source(id='s2', claim='Oil forms when organic matter is heated under pressure deep underground.', url='https://www.energy.gov/b')]


class Interpret(unittest.TestCase):
    def test_a_typed_topic_becomes_a_question_claims_a_title_and_a_slug(self):
        asked = []
        out = understand.interpret(TYPED, generate=lambda s, u, sc: asked.append(u) or HEARD)
        self.assertIn(TYPED, asked[0])
        self.assertEqual(out['question'], HEARD['question'])
        self.assertEqual(out['claims'], HEARD['claims'])
        self.assertEqual(out['title'], 'Oil Is Not Dinosaur Juice')
        self.assertEqual(out['slug'], 'oil-is-not-dinosaur-juice')
        self.assertNotIn('comig', out['slug'] + out['title'])
        self.assertEqual(out['source'], 'model')

    def test_the_typed_text_is_never_the_title(self):
        out = understand.interpret(TYPED, generate=lambda s, u, sc: dict(HEARD, title=TYPED))
        self.assertNotEqual(out['title'], TYPED)

    def test_the_model_being_down_never_stops_the_episode(self):
        def down(*a):
            raise RuntimeError('Ollama is not reachable')
        out = understand.interpret(TYPED, generate=down)
        self.assertEqual(out['source'], 'fallback')
        self.assertEqual(out['slug'], '')                        # nothing is renamed on a guess
        self.assertTrue(out['question'].endswith('?'))
        self.assertIn('did not answer', out['note'])


class Verify(unittest.TestCase):
    def test_each_claim_gets_a_verdict_from_the_cited_facts(self):
        interp = understand.clean(HEARD, TYPED)
        answer = dict(claims=[dict(claim=HEARD['claims'][0], verdict='supported', fact_ids=['s2'], why='heat and pressure'),
                              dict(claim=HEARD['claims'][1], verdict='supported', fact_ids=['s9'], why='plankton')])
        v = understand.verify(interp, FACTS, generate=lambda s, u, sc: answer)
        self.assertEqual([x['verdict'] for x in v], ['supported', 'unverified'])   # s9 is no cited fact
        self.assertEqual(v[0]['fact_ids'], ['s2'])

    def test_no_facts_means_unverified(self):
        v = understand.verify(understand.clean(HEARD, TYPED), [], generate=lambda *a: 1 / 0)
        self.assertEqual({x['verdict'] for x in v}, {'unverified'})


class Story(unittest.TestCase):
    def interp(self, verdict='fringe/false', misconception=''):
        i = understand.clean(dict(HEARD, misconception=misconception), TYPED)
        i['verdicts'] = [dict(claim=c, verdict=verdict, fact_ids=['s1'], why='') for c in i['claims']]
        return i

    def test_a_myth_steers_the_formats_and_the_writer(self):
        i = self.interp('fringe/false')
        self.assertEqual(understand.preferred_formats(i, ['mini_story', 'pov', 'mystery', 'myth_vs_fact'])[:2],
                         ['myth_vs_fact', 'mystery'])
        b = understand.brief(i)
        self.assertIn('Never state a disputed, false or unverified claim as fact', b)
        self.assertIn('MYTH', b)

    def test_true_claims_are_told_straight(self):
        i = self.interp('supported')
        self.assertFalse(understand.has_myth(i))
        self.assertEqual(understand.preferred_formats(i, ['pov', 'mystery']), ['pov', 'mystery'])
        self.assertIn('tell it straight', understand.brief(i))

    def test_the_prompt_is_unchanged_without_an_interpretation(self):
        ep = Episode(slug='x', topic='why honey never spoils')
        self.assertEqual(understand.topic_text(ep), 'Topic: why honey never spoils')
        ep.meta['interpretation'] = self.interp()
        self.assertIn('Topic: How does crude oil form', understand.topic_text(ep))
        self.assertEqual(understand.question(ep), HEARD['question'])

    def test_auto_picks_a_myth_format(self):
        ep = Episode(slug='x', topic=TYPED, storylines=[Storyline.model_validate(storyline(f)) for f in ('pov', 'myth_vs_fact', 'mystery')])
        ep.meta['interpretation'] = self.interp('fringe/false', 'Oil comes from dinosaurs.')
        with mock.patch('pipeline.story.last_picked_format', return_value=('', '')):
            n, why = auto.first_storyline(ep)
        self.assertEqual(n, 2)
        self.assertIn('corrects a myth', why)


def beat(id_, narration, visual='insert', key='', scene='', labels=(), ost='', intent='', items=None, split=None):
    shot = dict(visual=visual, host=dict(side='right', size='medium'), motion=[])
    if visual in ('insert', 'split'):
        shot['insert'] = dict(key=key, scene=scene, effects=[dict(kind='label', anchor='', at='top', detail=x) for x in labels])
    if visual == 'graphic':
        shot['graphic'] = dict(kind='layers', items=list(items or []), anchors=[], detail='')
    if split:
        shot['split'] = split
    return dict(id=id_, narration=narration, on_screen_text=ost, visual_intent=intent, shot=shot)


class PicturesMatchTheStory(unittest.TestCase):
    def episode(self, *beats):
        return dict(meta=dict(interpretation=dict(HEARD, verdicts=[])), beats=list(beats))

    def test_a_myth_picture_must_say_it_is_the_myth(self):
        ep = self.episode(beat('b02', 'Everyone imagines oil is made from giant beasts.', key='dinosaurs-museum-display',
                               scene='a museum hall with dinosaur skeletons', intent='a dinosaur museum display'))
        r = qa.story_match_report(ep)
        self.assertFalse(r['pass_'])
        self.assertEqual(r['unmarked_myth'][0]['beat'], 'b02')
        ep['beats'][0]['shot']['insert']['effects'].append(dict(kind='label', anchor='', at='top', detail='The Common Myth'))
        self.assertTrue(qa.story_match_report(ep)['pass_'])

    def test_a_scene_that_shows_something_else_fails(self):
        ep = self.episode(beat('b06', 'Tiny marine plants lived in the ancient ocean.', key='city-street-night',
                               scene='a busy city street at night'))
        r = qa.story_match_report(ep)
        self.assertEqual([x['beat'] for x in r['off_subject']], ['b06'])

    def test_the_repair_marks_the_myth_and_qa_then_passes(self):
        b = beat('b02', 'Everyone imagines oil comes from beasts.', key='dinosaurs-museum-display',
                 scene='a museum hall with dinosaur skeletons', intent='a dinosaur museum display')
        g = beat('b03', 'But the timeline proves those creatures were too young.', visual='graphic',
                 items=['ancient sludge', 'dinosaur fossils'], ost='Timeline Mismatch!')
        ep = self.episode(b, g)
        report = dict(summary=dict(story_match=False, all=False), story_match=qa.story_match_report(ep))
        acts = repair.diagnose(report, dict(beats=[]), ep, {})
        self.assertEqual([(a['beat'], a['hint']) for a in acts], [('b02', {'myth_mark': True}), ('b03', {'myth_mark': True})])
        hints = {'beats': {'b02': {'myth_mark': True}, 'b03': {'myth_mark': True}}}
        shown = repair.repaired_episode_dict(ep, hints)
        self.assertTrue(qa.story_match_report(shown)['pass_'])
        self.assertEqual(shown['beats'][0]['shot']['insert']['effects'][0]['detail'], 'MYTH')
        self.assertEqual(shown['beats'][1]['on_screen_text'], 'Myth: Timeline Mismatch!')
        self.assertIn('text_slam', [m['recipe'] for m in shown['beats'][1]['shot']['motion']])

    def test_an_off_subject_scene_is_painted_again_from_its_beat(self):
        import tempfile
        from pipeline import config
        ep = self.episode(beat('b06', 'Tiny marine plants lived in the ancient ocean.', key='city-street-night',
                               scene='a busy city street at night', intent='tiny plankton drifting in an ancient sea'))
        report = dict(summary=dict(story_match=False, all=False), story_match=qa.story_match_report(ep),
                      duration=dict(seconds=45, target=45, pass_=True))
        painted = []
        with tempfile.TemporaryDirectory() as t, mock.patch.object(config, 'EPISODES_DIR', Path(t)):
            (Path(t) / 'x').mkdir()
            (Path(t) / 'x/episode.json').write_text(json.dumps(ep))
            applied = repair.run_round('x', report, dict(beats=[]), 1, paint=lambda want, slug: painted.append(want) or {})
            hints = repair.load('x')['beats']['b06']
        self.assertEqual(len(applied), 1)
        key = next(iter(painted[0]))
        self.assertIn('plankton', painted[0][key]['scene'])
        self.assertEqual(hints, {'insert_key': key})


class Rename(unittest.TestCase):
    """The web app: the typed slug becomes the clean one after the pitch; the old link redirects."""

    def test_the_episode_moves_to_its_clean_slug(self):
        from webapp.testing import Harness
        try:
            from webapp.test_ollama import ALL, done, mac_pitch
        except ImportError:
            from test_ollama import ALL, done, mac_pitch
        with Harness(provider='ollama') as h:
            h.login()
            old = h.client.post('/api/episodes', json=dict(topic=TYPED)).json()['slug']
            self.assertTrue(old.startswith('how-is-crude-oil'))
            job = h.claim(ALL)
            ep = mac_pitch(job)
            ep.meta['interpretation'] = dict(understand.clean(HEARD, TYPED), verdicts=[])
            ep.title = HEARD['title']
            self.assertEqual(done(h, job, ep).status_code, 200)
            new = 'oil-is-not-dinosaur-juice'
            d = h.detail(new)
            self.assertEqual(d['state'], 'storylines')
            self.assertEqual(d['title'], HEARD['title'])
            self.assertEqual(d['interpretation']['question'], HEARD['question'])
            self.assertEqual(h.episode_file(new)['slug'], new)
            r = h.client.get(f'/api/episodes/{old}?view=status', follow_redirects=False)
            self.assertEqual(r.status_code, 307)
            self.assertIn(f'/api/episodes/{new}', r.headers['location'])
            page = h.client.get(f'/episodes/{new}')
            self.assertIn('What it understood', page.text)
            self.assertIn(HEARD['question'], page.text)


if __name__ == '__main__':
    unittest.main()


class ClaimsInTheScript(unittest.TestCase):
    def test_a_false_claim_stated_as_fact_is_an_error(self):
        from test_auto_and_qa_fixes import host_episode
        ep = host_episode()
        ep.meta['interpretation'] = dict(HEARD, verdicts=[dict(claim='Crude oil is made from dinosaur bones.', verdict='fringe/false',
                                                               fact_ids=['s1'], why='plankton')])
        ep.beats[1].narration = 'Crude oil is made from old dinosaur bones.'
        errs = [i.message for i in ep.check() if i.where == ep.beats[1].id and 'as fact' in i.message]
        self.assertTrue(errs)
        ep.beats[1].narration = 'Some people think crude oil is made from dinosaur bones.'
        self.assertFalse([i for i in ep.check() if i.where == ep.beats[1].id and 'as fact' in i.message])
