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
from pipeline_fixtures import FakeClient, research_response, story_pitch, story_response, storyline
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


class RotationTest(unittest.TestCase):
    def setUp(self):
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

    def test_make_pitches_and_waits_and_auto_never_picks(self):
        client = FakeClient([research_response(), story_response()])
        with mock.patch.object(script, 'make_client', lambda: client):
            code, out = run_cli('make', 'Why honey never spoils', '--auto')
        self.assertEqual(code, cli.EXIT_GATE)
        self.assertIn('WAITING AT GATE: storyline', out)
        self.assertIn('[mini_story]', out)
        sent = client.messages.created[1]
        self.assertIn('Allowed formats', sent['messages'][0]['content'])
        self.assertEqual(json.loads(Path(self.dir.name, 'why-honey-never-spoils', 'episode.json').read_text())['storyline_pick'], 0)

    def test_a_topic_that_cannot_carry_a_story_says_so(self):
        pitch = story_pitch()
        pitch.update(story_supported=False, note='only one fact, no conflict')
        client = FakeClient([research_response(), story_response(pitch)])
        with mock.patch.object(script, 'make_client', lambda: client):
            code, out = run_cli('make', 'Why honey never spoils')
        self.assertIn('may not carry a real story: only one fact, no conflict', out)


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
