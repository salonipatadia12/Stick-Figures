"""Kaomei Director mode (stickman-video-director, MIT; user 2026-11-11; docs/KAOMEI_INTEGRATION.md): the revelation
format, the script rules (three timed phases, continuity, pacing by length), the continuity field in the local beat
stage, and the three direction-only style packs."""
import json
import unittest
from pathlib import Path
from typing import get_args

from pipeline import script, story, styles
from pipeline.models import FORMATS, Episode, StoryFormat, Storyline
from pipeline_fixtures import storyline

ROOT = Path(__file__).resolve().parents[1]
NEW_STYLES = ('lineart', 'retro-tv', 'colorful')


class RevelationFormat(unittest.TestCase):
    def test_it_is_a_format_everywhere_formats_are_read(self):
        self.assertIn('revelation', get_args(StoryFormat))
        self.assertIn('revelation', story.ALL_FORMATS)
        self.assertEqual(FORMATS['revelation'],
                         'a counter-intuitive hook shatters what everyone thinks is true, then pulls back the curtain on '
                         'the hidden mechanism, delivers the real truth with maximum payoff, and ends with a provocative '
                         'question that sparks debate in the comments')
        self.assertIn('- revelation: a counter-intuitive hook', story.format_guide(['revelation']))
        Storyline.model_validate(storyline('revelation'))

    def test_the_rules_say_how_a_revelation_uses_the_roles(self):
        for rules in (script.SCRIPT_RULES, script.SCRIPT_RULES_COMPACT_OUTLINE):
            self.assertIn('revelation', rules)
            self.assertIn('hidden mechanism', rules)


class ScriptRules(unittest.TestCase):
    def test_full_rules_carry_the_director_text(self):
        r = script.SCRIPT_RULES
        self.assertIn('Structure each beat around three timed phases: [0-1s] establish or inherit from the previous\n'
                      "   beat; [1-3s] transform, escalate, or act out the idea; [3-end] deliver the beat's point and\n"
                      '   set up the next. Use at least three relevant visual devices per beat', r)
        self.assertIn("CONTINUITY: every beat's last frame sets up the next beat's first frame. Name the connection:\n"
                      '   a held pose, a moving object, a filled frame, a travel direction, a shape, or a camera motion.', r)
        self.assertIn('18. PACING BY LENGTH. Scale narrative density to the target duration:', r)
        self.assertIn('30s (7-8 beats): lightning hook → conflict → hidden mechanism + truth → comment question.', r)
        self.assertIn('45s (9-11 beats): hook → disrupt assumption → 2-3 hidden details → truth → elevation.', r)
        self.assertIn('60s (12-14 beats): full 5-stage structure. Most beats are inserts; at most 2 are diagrams.', r)
        self.assertIn('The first two seconds and the last two seconds carry the most weight.', r)
        self.assertLess(r.index('7. MOTION'), r.index('three timed phases'), )
        self.assertLess(r.index('three timed phases'), r.index('8. TRANSITIONS'))
        self.assertLess(r.index('8. TRANSITIONS'), r.index('CONTINUITY:'))
        self.assertLess(r.index('CONTINUITY:'), r.index('9. SOUND'))
        self.assertEqual(script.PROMPT_VERSION, 'script-v7')

    def test_compact_rules_carry_it_too_and_stay_compact(self):
        self.assertIn('PACING. 30 s, 7-8 beats', script.SCRIPT_RULES_COMPACT_OUTLINE)
        self.assertIn('Three phases', script.SCRIPT_RULES_COMPACT_BEAT)
        self.assertIn('At least three devices per beat', script.SCRIPT_RULES_COMPACT_BEAT)
        self.assertIn('CONTINUITY. continuity:', script.SCRIPT_RULES_COMPACT_BEAT)
        self.assertLess(len(script.SCRIPT_RULES_COMPACT.split()), 1350)

    def test_beat_aim_follows_rule_18(self):
        self.assertEqual(script.pacing_aim(30), (7, 8))
        self.assertEqual(script.pacing_aim(45), (9, 11))
        self.assertEqual(script.pacing_aim(60), (12, 14))
        self.assertEqual(script.pacing_aim(40), (7, 10))            # between the bands: one beat per 4-5.5 s


class BeatStage(unittest.TestCase):
    OUTLINE = dict(beats=[dict(role='hook', narration='Why is the sky red?', visual='insert', on_screen_text=''),
                          dict(role='payoff', narration='The blue scatters away first.', visual='insert',
                               on_screen_text='')])

    def test_the_schema_asks_for_a_continuity_and_the_prompt_names_the_next_beat(self):
        schema = script.beat_schema(self.OUTLINE['beats'][0], 0, ['lead'])
        self.assertEqual(schema['properties']['continuity'], {'type': 'string'})
        self.assertIn('continuity', schema['required'])
        ctx = dict(beat_head='Topic: sunsets', cast=['lead'])
        first = script._beat_prompt(ctx, self.OUTLINE, 0, schema)
        self.assertIn('Next beat (continuity hands over to it): b02 [insert]: The blue scatters away first.', first)
        last = script._beat_prompt(ctx, self.OUTLINE, 1, script.beat_schema(self.OUTLINE['beats'][1], 1, ['lead']))
        self.assertIn('This is the last beat: continuity is "".', last)

    def test_the_answer_keeps_the_continuity(self):
        raw = dict(visual_intent='x', host=dict(where='scene', side='left', size='medium', watching=False, holds='',
                                               doing='looks up at the red sky'),
                   cast=['lead'], staging=dict(setting='other', setting_detail='a beach', poses=[], actions=[], props=[]),
                   insert=dict(key='Red Sky', scene='a beach at sunset', effects=[]),
                   motion=[dict(recipe='push_in', anchor='', detail='')], sfx=[],
                   continuity='  the host points   up;  b02 opens on the sky ')
        d = script._detail_from(raw, self.OUTLINE['beats'][0], ['lead'])
        self.assertEqual(d['shot']['continuity'], 'the host points up; b02 opens on the sky')


class StylePacks(unittest.TestCase):
    def test_each_pack_has_its_three_files_and_loads(self):
        for name in NEW_STYLES:
            with self.subTest(style=name):
                folder = ROOT / 'styles' / name
                for f in ('style.json', 'style_lock.txt', 'script_brief.md'):
                    self.assertTrue((folder / f).is_file(), f)
                spec = json.loads((folder / 'style.json').read_text())
                self.assertEqual(spec['name'], name)
                self.assertEqual(spec['reference']['license'], 'MIT')
                self.assertIn('kaomei', spec['reference']['note'])
                st = styles.load(name)
                self.assertEqual(st.status, 'direction')
                self.assertFalse(st.render_ready)                   # only cozy has painted art to render with
                self.assertEqual(st.cast_ids, ['lead', 'friend'])
                self.assertTrue(st.writer_note)
                self.assertIn('No text, letters, numbers or words', (folder / 'style_lock.txt').read_text())
        self.assertIn(set(NEW_STYLES) | {'cozy'}, [set(styles.available())])

    def test_voices_and_music_as_specified(self):
        self.assertEqual((styles.load('lineart').default_voice, styles.load('lineart').music), ('am_michael', False))
        self.assertEqual((styles.load('retro-tv').default_voice, styles.load('retro-tv').music), ('am_michael', True))
        colorful = styles.load('colorful')
        self.assertEqual((colorful.default_voice, colorful.music), ('af_heart', False))
        self.assertTrue(colorful.audio['music_optional'])

    def test_cozy_is_unchanged(self):
        cozy = styles.load('cozy')
        self.assertTrue(cozy.render_ready)
        self.assertEqual((cozy.status, cozy.writer_note, cozy.music), ('ready', '', False))

    def test_writer_note_reaches_the_local_beat_stage_but_not_for_cozy(self):
        ep = Episode(slug='sunsets', topic='Why sunsets are red', target_length=45)
        cozy = script._free_context(ep, styles.load('cozy'), [])
        self.assertEqual(cozy['beat_head'], 'Topic: Why sunsets are red\n')     # exactly as before the packs
        line = script._free_context(ep, styles.load('lineart'), [])
        self.assertIn('Style lineart:', line['beat_head'])
        self.assertIn('aim for 9-11', line['outline_user'])


if __name__ == '__main__':
    unittest.main()
