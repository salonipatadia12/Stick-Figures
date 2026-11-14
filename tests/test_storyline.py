"""Story before script (user 2026-11-09): three storylines first, formats rotate, a story check at the script gate,
visual variety, and a QA note when a video repeats the previous one's layout."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import cli, config, qa, script, story
from pipeline.models import Episode, Storyline, from_wire
from pipeline_fixtures import FakeClient, anthropic_provider, research_response, script_response, story_pitch, story_response, storyline
from test_host_format import episode as host_episode


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class StoryCheckTest(unittest.TestCase):
    def picked(self, roles, payoff='So the volcano dimmed the sun and summer never came.'):
        ep = host_episode()
        for b, r in zip(ep.beats, roles):
            b.role = r
        ep.beats[-2].narration = payoff
        ep.storylines = [Storyline.model_validate(storyline('mystery'))]
        ep.storylines[0].payoff = 'A volcano dimmed the sun.'
        ep.storyline_pick = 1
        return {item: ok for item, ok, _ in ep.story_report()}, ep

    def test_a_real_story_passes(self):
        report, ep = self.picked(['hook', 'problem', 'clue', 'clue', 'twist', 'payoff', 'loop'])
        self.assertTrue(all(report.values()), ep.story_report())
        self.assertFalse([i for i in ep.check(('lead', 'friend')) if i.where == 'story'])

    def test_a_fact_list_fails_conflict_stakes_and_twist(self):
        report, ep = self.picked(['hook', 'promise', 'story', 'story', 'story', 'payoff', 'loop'])
        self.assertFalse(report['conflict'])
        self.assertFalse(report['rising stakes'])
        self.assertFalse(report['twist'])
        self.assertTrue(any(i.where == 'story' and i.severity == 'error' for i in ep.check(('lead', 'friend'))))

    def test_a_payoff_that_does_not_answer_the_hook_fails(self):
        report, _ = self.picked(['hook', 'problem', 'clue', 'clue', 'twist', 'payoff', 'loop'],
                                payoff='And that is the end of the story for today.')
        self.assertFalse(report['payoff answers the hook'])


class HostFormatChecksTest(unittest.TestCase):
    def test_painted_scene_beats_need_no_base_in_the_host_format(self):
        from pipeline import bases
        ep = host_episode()
        rows = bases.plan(ep, {})
        for b, r in zip(ep.beats, rows):
            if b.shot.visual != 'character':
                self.assertEqual(r['base'], f'({b.shot.visual})')

    def test_the_users_own_long_hook_is_a_warning_not_an_error(self):
        ep = host_episode()
        long = 'How do you fix a tower you really cannot touch?'
        ep.hook_options[0].line = long
        ep.beats[0].narration = long
        hook_errors = lambda: [i for i in ep.check(('lead', 'friend')) if i.where == 'hook 1' and i.severity == 'error']
        self.assertTrue(hook_errors())
        ep.meta['hook_by_user'] = long
        self.assertFalse(hook_errors())


class RenderTextTest(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/motion_test'))

    def test_a_split_screen_does_not_get_a_second_pair_of_labels(self):
        from compose_beats import text_items
        spec = dict(on_screen_text='', shot=dict(visual='split', split=dict(labels=['Prediction', 'Result']),
                                                 motion=[dict(recipe='split_compare', anchor='', detail='My bet | The research')]))
        self.assertEqual(text_items(spec, lambda a: 1.0), [])
        spec['shot']['visual'] = 'insert'
        self.assertEqual(text_items(spec, lambda a: 1.0)[0][0], 'split')

    def test_a_thin_held_prop_stays_hand_sized(self):
        from PIL import Image
        from compose_beats import held_width
        self.assertAlmostEqual(held_width(dict(width=0.4), Image.new('RGBA', (93, 339))), 1.1 * 93 / 339)
        self.assertAlmostEqual(held_width(dict(width=0.4), Image.new('RGBA', (197, 267))), 0.6, places=6)   # a jar is unchanged

    def test_checklist_rows_tick_on_their_own_words(self):
        from compose_beats import finish_text_items
        T = {'one': 6.33, 'peroxide': 7.15, 'acid': 8.64, 'missing': 9.65}
        spec = dict(narration='One: hydrogen peroxide. Two: acid. Three: missing water.', on_screen_text='')
        items = finish_text_items(spec, lambda w: T.get((w or '').lower()), [('checklist', 6.33, ['Peroxide', 'Acid', 'Missing water'])], 6.27, 10.48)
        self.assertEqual([t for _, t in items[0][2]], [7.15, 8.64, 9.65])

    def test_label_text_equal_to_the_on_screen_text_is_the_same_text(self):
        from compose_beats import same_text
        self.assertEqual(same_text('pH 3.2 to 4.5'), same_text('PH 3.2 TO 4.5!'))


class HostScaleTest(unittest.TestCase):
    def test_object_close_ups_and_places(self):
        from pipeline.inserts import scene_scale
        lib = {}
        self.assertEqual(scene_scale('a', 'A golden honey jar with a wooden dipper on a sunlit kitchen counter', lib), 'object')
        self.assertEqual(scene_scale('b', 'A kitchen counter piled with grocery bags, papers and a cold teapot', lib), 'place')
        self.assertEqual(scene_scale('c', 'Close view of the base of the leaning tower on its low side', lib), 'place')
        self.assertEqual(scene_scale('d', 'whatever', {'d': dict(scale='object')}), 'object')

    def test_checked_facts_keep_their_quotes(self):
        from pipeline.script import checked_facts
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'facts.json'
            f.write_text(json.dumps([dict(claim='A.', url='https://www.issmge.org/x.pdf', title='T', quote='q')]))
            facts = checked_facts(f)
        self.assertEqual((facts[0].id, facts[0].quote), ('s1', 'q'))
        self.assertTrue(script.reputable(facts[0].url))


class ResearchCitationTest(unittest.TestCase):
    def setUp(self):
        anthropic_provider(self)

    def blocks(self, text, cites=()):
        from types import SimpleNamespace as N
        result = N(type='web_search_tool_result', content=[N(url='https://www.consumerfinance.gov/ask-cfpb/x/', title='CFPB')])
        return [N(type='server_tool_use'), result, N(type='text', text=text, citations=list(cites))]

    def test_an_uncited_line_keeps_a_url_the_search_really_returned(self):
        from pipeline.script import facts_from_content
        facts = facts_from_content(self.blocks('Interest is charged daily on the average daily balance. '
                                               '<https://www.consumerfinance.gov/ask-cfpb/x/>\n'
                                               'A made-up line that names a page the search never returned. <https://example.gov/y>'))
        self.assertEqual([(f.url, f.quote) for f in facts], [('https://www.consumerfinance.gov/ask-cfpb/x/', '')])
        self.assertEqual(facts[0].claim, 'Interest is charged daily on the average daily balance.')

    def test_a_scholar_lookup_of_a_doi_is_the_doi(self):
        from pipeline.script import doi_link
        self.assertEqual(doi_link('https://scholar.google.com/scholar_lookup?doi=10.1006/nimg.1999.0478&amp=&pmid=1'),
                         'https://doi.org/10.1006/nimg.1999.0478')

    def test_a_doi_counts_only_when_its_publisher_is_reviewed(self):
        from unittest import mock
        from pipeline import script
        targets = {'10.24297/jap.v9i3.1353': 'https://rajpub.com/index.php/jap/article/view/1353',
                   '10.1006/nimg.1999.0478': 'https://linkinghub.elsevier.com/retrieve/pii/S1053811999904780',
                   '10.1/unknown': ''}
        with mock.patch.object(script, 'resolve_doi', lambda doi: targets[doi]):
            self.assertFalse(script.reputable('https://doi.org/10.24297/jap.v9i3.1353'))     # unlisted publisher
            self.assertTrue(script.reputable('https://doi.org/10.1006/nimg.1999.0478'))     # Elsevier
            self.assertFalse(script.reputable('https://doi.org/10.1/unknown'))              # could not resolve
            self.assertFalse(script.reputable('https://doi.org/not-a-doi'))

    def test_storylines_stand_only_on_reputable_facts(self):
        from pipeline.models import Source
        src = [Source(id='s1', claim='A reviewed fact here.', url='https://pubmed.ncbi.nlm.nih.gov/1/'),
               Source(id='s2', claim='A blog fact here, dropped.', url='https://someblog.com/x')]
        ep = Episode(slug='t', topic='why honey never spoils')
        client = FakeClient([story_response()])
        with tempfile.TemporaryDirectory() as d, mock.patch.object(script, 'research', lambda *a, **k: (src, Path(d) / 'r.json', True)), \
                mock.patch.object(story, 'allowed_formats', lambda slug: (story.ALL_FORMATS, [])):
            from pipeline.cost import Ledger
            ep2, notes = story.pitch(ep, Path(d), Ledger(Path(d) / 'ledger.json', 2.0), client)
        self.assertEqual([f.url for f in ep2.sources], ['https://pubmed.ncbi.nlm.nih.gov/1/'])
        self.assertIn('someblog.com', ' '.join(notes))
        self.assertNotIn('someblog', client.messages.created[0]['messages'][0]['content'])


class RotationTest(unittest.TestCase):
    def setUp(self):
        anthropic_provider(self)                 # the pitches below come from a fake Anthropic client
        self.dir = tempfile.TemporaryDirectory()
        p = mock.patch.object(config, 'EPISODES_DIR', Path(self.dir.name))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)

    def save(self, slug, formats, pick=0, at='2026-11-09T10:00:00'):
        from pipeline import store
        ep = Episode(slug=slug, topic=slug.replace('-', ' '),
                     storylines=[Storyline.model_validate(storyline(f)) for f in formats], storyline_pick=pick)
        if pick:
            ep.meta['approval_log'] = [dict(gate='storyline', at=at, format=formats[pick - 1])]
        store.save(ep, store.folder(slug))

    def test_never_the_last_format_and_not_formats_pending_elsewhere(self):
        self.save('old-one', ['mystery', 'pov', 'countdown'], pick=1)
        self.save('waiting', ['experiment', 'what_if', 'myth_vs_fact'])
        allowed, notes = story.allowed_formats('new-topic')
        self.assertNotIn('mystery', allowed)
        self.assertFalse({'experiment', 'what_if', 'myth_vs_fact'} & set(allowed))
        self.assertGreaterEqual(len(allowed), 3)

    def test_the_gate_refuses_the_same_format_twice_in_a_row(self):
        self.save('old-one', ['mystery', 'pov', 'countdown'], pick=1)
        self.save('new-one', ['mystery', 'pov', 'countdown'])
        code, out = run_cli('approve', 'new-one', '--gate', 'storyline', '--story', '1', '--by', 'test')
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertIn('same format', out)
        self.assertEqual(run_cli('approve', 'new-one', '--gate', 'storyline', '--story', '2', '--by', 'test')[0], 0)

    def test_make_pitches_and_waits_without_auto(self):
        client = FakeClient([research_response(), story_response()])
        with mock.patch.object(script, 'make_client', lambda: client):
            code, out = run_cli('make', 'Why honey never spoils')
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertIn('WAITING AT GATE: storyline', out)
        self.assertIn('[mini_story]', out)
        sent = client.messages.created[1]
        self.assertIn('Allowed formats', sent['messages'][0]['content'])
        self.assertEqual(json.loads(Path(self.dir.name, 'why-honey-never-spoils', 'episode.json').read_text())['storyline_pick'], 0)

    def test_auto_picks_the_first_storyline_the_gate_accepts_and_goes_on(self):
        # user 2026-11-13: auto mode has no stops. The last video was a mystery, so storyline 1 (a mystery) is skipped.
        self.save('old-one', ['mystery', 'pov', 'countdown'], pick=1)
        client = FakeClient([research_response(), story_response(story_pitch(('mystery', 'myth_vs_fact', 'pov'))),
                             script_response()])
        renders = []
        with mock.patch.object(script, 'make_client', lambda: client), \
                mock.patch.object(cli, 'auto_render', lambda slug: renders.append(slug) or 0):
            code, out = run_cli('make', 'Why honey never spoils', '--auto')
        data = json.loads(Path(self.dir.name, 'why-honey-never-spoils', 'episode.json').read_text())
        self.assertEqual(data['storyline_pick'], 2, out)
        self.assertIn('--auto: picked storyline 2 [myth_vs_fact]', out)
        log = data['meta']['approval_log']
        self.assertEqual([(e['gate'], e['by']) for e in log][0], ('storyline', 'auto'))
        self.assertEqual(len(client.messages.created), 3)             # research, storylines, then the script
        if code == 0:                                                 # the fixture script passes: approved by auto, rendered
            self.assertEqual([(e['gate'], e['by']) for e in log][1], ('script', 'auto'))
            self.assertEqual(renders, ['why-honey-never-spoils'])
        else:
            self.assertEqual(code, cli.EXIT_CHECKS)
            self.assertIn('--auto will not approve a script that fails the checks', out)
            self.assertEqual(renders, [])

    def test_a_topic_that_cannot_carry_a_story_says_so(self):
        pitch = story_pitch()
        pitch.update(story_supported=False, note='only one fact, no conflict')
        client = FakeClient([research_response(), story_response(pitch)])
        with mock.patch.object(script, 'make_client', lambda: client):
            code, out = run_cli('make', 'Why honey never spoils')
        self.assertIn('may not carry a real story: only one fact, no conflict', out)


class OllamaPitchTest(unittest.TestCase):
    """story.pitch() under SCRIPT_PROVIDER=ollama: no key, the formats constrained, one repair for a repeated format."""

    def test_pitch_repairs_a_repeated_format_and_logs_free_calls(self):
        import os
        from ollama_fixtures import FakeOllama, patched
        from pipeline.cost import Ledger
        from pipeline.models import Source
        src = [Source(id='s1', claim='Honey has very little water.', url='https://pubmed.ncbi.nlm.nih.gov/1/', quote='x')]
        answers = [story_pitch(('mystery', 'mystery', 'pov')), story_pitch(('mystery', 'pov', 'what_if'))]
        from pipeline import understand
        heard = dict(question='Why does honey never spoil?', title='Why Honey Never Spoils', claims=[], misconception='',
                     angle='its low water starves microbes', myth_subjects=[])
        fake = FakeOllama(lambda body: heard if body['messages'][0]['content'] == understand.SYSTEM else answers.pop(0))
        with tempfile.TemporaryDirectory() as d, patched(fake), \
                mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'ollama', 'ANTHROPIC_API_KEY': '', 'STICK_CACHE_DIR': d}), \
                mock.patch.object(script, 'research', lambda *a, **k: (src, Path(d) / 'r.json', True)), \
                mock.patch.object(script, 'make_client', lambda: (_ for _ in ()).throw(AssertionError('no client'))), \
                mock.patch.object(story, 'allowed_formats', lambda slug: (story.ALL_FORMATS, [])):
            ledger = Ledger(Path(d) / 'ledger.json', 2.0)
            ep, notes = story.pitch(Episode(slug='t', topic='why honey never spoils'), Path(d), ledger)
        self.assertEqual([s.format for s in ep.storylines], ['mystery', 'pov', 'what_if'])
        self.assertEqual(fake.chats[0]['messages'][0]['content'], understand.SYSTEM)     # topic understanding first
        self.assertEqual(ep.title, 'Why Honey Never Spoils')
        self.assertIn('Why does honey never spoil?', fake.chats[1]['messages'][1]['content'])
        chats = fake.chats[1:]
        self.assertEqual(len(chats), 2)
        self.assertIn('share a format', chats[1]['messages'][1]['content'])
        self.assertEqual(chats[0]['format']['properties']['storylines']['items']['properties']['format']['enum'],
                         story.ALL_FORMATS)
        self.assertEqual({e['provider'] for e in ledger.entries}, {'ollama'})
        self.assertEqual(fake.unloads[-1]['keep_alive'], 0)


class VariantsTest(unittest.TestCase):
    def test_a_split_picture_comes_back_from_the_wire(self):
        shot = dict(visual='split', picture=dict(insert_key='honey-tomb', insert_scene='a tomb', insert_effects='',
                                                 graphic='',
                                                 split='honey-jar-lab | a lab bench | Myth@says | Fact@actually'),
                    host=dict(where='scene', placement='right large watching holding a jar of honey', doing='x'),
                    staging=dict(setting='other: castle ruins', poses=[], actions=[], props=[]))
        data, notes = from_wire(dict(beats=[dict(role='suspense', shot=shot)]))
        host = data['beats'][0]['shot']['host']
        self.assertEqual((host['side'], host['size'], host['to_camera'], host['holds']), ('right', 'large', False, 'a jar of honey'))
        self.assertEqual(data['beats'][0]['shot']['staging']['setting'], 'other')
        self.assertEqual(data['beats'][0]['shot']['staging']['setting_detail'], 'castle ruins')
        self.assertEqual(data['beats'][0]['role'], 'story')
        sp = data['beats'][0]['shot']['split']
        self.assertEqual(sp['labels'], ['Myth', 'Fact'])
        self.assertEqual(sp['anchors'], ['says', 'actually'])

    def test_a_diagram_comes_back_from_the_wire(self):
        pic = dict(insert_key='', insert_scene='', insert_effects='', split='',
                   graphic='bars ; Then@then | Now@now ; 3 | 5')
        data, notes = from_wire(dict(beats=[dict(role='reveal', shot=dict(visual='graphic', picture=pic))]))
        g = data['beats'][0]['shot']['graphic']
        self.assertEqual((g['kind'], g['items'], g['anchors'], g['detail']), ('bars', ['Then', 'Now'], ['then', 'now'], '3 | 5'))
        self.assertEqual(notes, [])

    def test_a_diagram_beat_without_a_diagram_falls_back_to_its_painted_scene(self):
        pic = dict(insert_key='tower-model', insert_scene='a tower model on paper', insert_effects='', split='', graphic='')
        data, notes = from_wire(dict(beats=[dict(role='clue', shot=dict(visual='graphic', picture=pic))]))
        shot = data['beats'][0]['shot']
        self.assertEqual((shot['visual'], shot['insert']['key'], shot['graphic']), ('insert', 'tower-model', None))
        self.assertIn('no diagram', notes[0])

    def test_the_wire_schema_stays_under_the_grammar_the_api_accepted(self):
        from anthropic import transform_schema
        from pipeline.models import ScriptDraft, wire_schema
        schema = wire_schema(transform_schema(ScriptDraft))
        counts = dict(props=0, enums=0)

        def walk(o):
            if isinstance(o, dict):
                counts['props'] += len(o.get('properties', {}))
                counts['enums'] += len(o.get('enum', []))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(schema)
        self.assertLessEqual(counts['props'], 51)   # v5 (51 properties, 88 enum values) was accepted; v6 unfolded was not
        self.assertLessEqual(counts['enums'], 88)
        self.assertIn('graphic', schema['$defs']['Picture']['properties'])

    def test_qa_notes_a_video_that_repeats_the_previous_layout(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(qa, 'LAYOUTS', Path(d) / 'layouts.json'):
            same = ['scene|scene|left|medium', 'scene|scene|right|medium', 'diagram|pop_in|left|medium', 'scene|scene|right|medium']
            qa.layout_report(dict(layout=same, format='explainer'), 'one')
            r = qa.layout_report(dict(layout=same, format='explainer'), 'two')
            self.assertIn('same format', r['note'])
            self.assertIn('layout pattern', r['note'])
            other = ['split|scene|centre|small', 'close-up|scene|left|close', 'scene|off', 'scene|scene|right|large']
            r = qa.layout_report(dict(layout=other, format='mystery'), 'three')
            self.assertEqual(r['note'], '')


if __name__ == '__main__':
    unittest.main()
