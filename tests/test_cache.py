"""pipeline/cache.py: the three layers (search 24 h per episode, pages 7 days shared, model answers by prompt hash)."""
import tempfile
import unittest
from pathlib import Path

from pipeline.cache import MultiCache, normalise_query, prompt_hash


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class MultiCacheTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.clock = Clock()
        self.cache = MultiCache(self.root / 'ep-a', self.root / 'shared', now=self.clock)

    def test_search_hit_miss_and_ttl(self):
        self.assertIsNone(self.cache.get_search('Rayleigh scattering sunset'))
        self.cache.put_search('Rayleigh scattering sunset', [{'href': 'https://x.edu/a'}], 'duckduckgo')
        self.assertEqual(self.cache.get_search('sunset  RAYLEIGH scattering'), [{'href': 'https://x.edu/a'}])  # normalised
        self.clock.t += 23 * 3600
        self.assertIsNotNone(self.cache.get_search('Rayleigh scattering sunset'))
        self.clock.t += 2 * 3600                               # past 24 h
        self.assertIsNone(self.cache.get_search('Rayleigh scattering sunset'))
        self.assertTrue(next((self.root / 'ep-a' / 'cache').glob('search-*.json')).is_file())
        self.assertEqual(self.cache.stats['search_hit'], 2)
        self.assertEqual(self.cache.stats['search_miss'], 2)

    def test_empty_results_and_failed_pages_are_not_remembered(self):
        self.cache.put_search('blocked query', [], '')
        self.assertIsNone(self.cache.get_search('blocked query'))
        self.cache.put_page('https://x.gov/a', '   ')
        self.assertIsNone(self.cache.get_page('https://x.gov/a'))

    def test_pages_are_shared_across_episodes_for_seven_days(self):
        self.cache.put_page('https://www.noaa.gov/sunset', 'Sunlight crosses more air at sunset.', 'Sunsets')
        other = MultiCache(self.root / 'ep-b', self.root / 'shared', now=self.clock)
        self.assertEqual(other.get_page('https://www.noaa.gov/sunset'), 'Sunlight crosses more air at sunset.')
        self.assertEqual(other.get_page_title('https://www.noaa.gov/sunset'), 'Sunsets')
        self.assertTrue((self.root / 'shared' / 'pages').is_dir())
        self.assertFalse(list((self.root / 'ep-b' / 'cache').glob('page-*')))   # not per-episode
        self.clock.t += 6.9 * 86400
        self.assertIsNotNone(other.get_page('https://www.noaa.gov/sunset'))
        self.clock.t += 0.2 * 86400
        self.assertIsNone(other.get_page('https://www.noaa.gov/sunset'))

    def test_llm_answers_by_prompt_hash(self):
        key = prompt_hash('ollama', 'qwen2.5:14b', 'system', 'user', {'type': 'object'}, {'num_ctx': 4096})
        self.assertNotEqual(key, prompt_hash('ollama', 'qwen2.5:14b', 'system', 'user!', {'type': 'object'}, {'num_ctx': 4096}))
        self.assertIsNone(self.cache.get_llm(key))
        self.cache.put_llm(key, {'text': '{"a": 1}'})
        self.assertEqual(self.cache.get_llm(key), {'text': '{"a": 1}'})
        self.assertTrue((self.root / 'ep-a' / 'cache' / f'llm-{key}.json').is_file())
        self.assertIn('model 1 hit / 1 miss', self.cache.summary())

    def test_query_normalisation(self):
        self.assertEqual(normalise_query('Why  Sunsets are RED'), normalise_query('red why are sunsets'))


if __name__ == '__main__':
    unittest.main()
