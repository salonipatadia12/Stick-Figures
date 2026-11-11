"""pipeline/search.py with DuckDuckGo, the pages and the model all faked: parallel search, the reputable-source filter,
facts checked against their page, the shared page cache, the engine fallback and the open-index fallback."""
import io
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from pipeline import search
from pipeline.cache import MultiCache
from pipeline.cost import Ledger
from ollama_fixtures import FakeOllama, patched

PAGES = {
    'https://www.noaa.gov/sunset': ('<html><head><title>Why sunsets are red</title></head><body><article>'
                                    '<p>At sunset, sunlight travels through much more of the atmosphere to reach your eyes.</p>'
                                    '<p>Along that longer path, air molecules scatter most of the blue light out of the beam, '
                                    'so mostly red and orange light is left.</p>'
                                    '<p>The air mass at the horizon is about 38 times the air mass overhead.</p>'
                                    '<p>Sunrise and sunset colours change with the weather, the season and how clean the air is that day.</p>'
                                    '</article></body></html>'),
    'https://physics.example.edu/rayleigh': ('<html><head><title>Rayleigh scattering</title></head><body><article>'
                                             '<p>Rayleigh scattering by air molecules is much stronger for short wavelengths '
                                             'such as blue light than for long wavelengths such as red light.</p>'
                                             '<p>This is why the daytime sky looks blue to us on a clear day.</p>'
                                             '<p>Lord Rayleigh described this scattering of sunlight by small particles in the nineteenth century, and it is named after him today.</p>'
                                             '</article></body></html>'),
}
RESULTS = {
    'scientific explanation': [dict(title='NOAA', href='https://www.noaa.gov/sunset', body=''),
                               dict(title='A blog', href='https://someblog.com/sunsets', body='')],
    'site:edu': [dict(title='Rayleigh', href='https://physics.example.edu/rayleigh', body=''),
                 dict(title='Wiki', href='https://en.wikipedia.org/wiki/Sunset', body='')],
}


class FakeDDGS:
    calls = []
    fail_backends = set()

    def __init__(self, timeout=None):
        pass

    def text(self, query, max_results=8, backend='auto'):
        FakeDDGS.calls.append((query, backend))
        if backend in FakeDDGS.fail_backends:
            raise RuntimeError('202 Ratelimit')
        time.sleep(0.05)
        for k, v in RESULTS.items():
            if k in query:
                return [dict(title=r['title'], href=r['href'], body=r['body']) for r in v]
        return [dict(title='Other', href='https://www.example.com/other', body='')]


class FakeHTTP:
    def __init__(self):
        self.urls = []
        self.lock = threading.Lock()

    def get(self, url, timeout=None, headers=None, stream=False):
        with self.lock:
            self.urls.append(url)
        html = PAGES.get(url)
        from types import SimpleNamespace as NS
        if html is None:
            return NS(status_code=404, headers={'content-type': 'text/html'}, raw=io.BytesIO(b''), encoding='utf-8')
        raw = io.BytesIO(html.encode())
        raw.read = (lambda f: (lambda n=-1, decode_content=True: f(n)))(raw.read)
        return NS(status_code=200, headers={'content-type': 'text/html; charset=utf-8'}, raw=raw, encoding='utf-8')


def model(body):
    system, user = body['messages'][0]['content'], body['messages'][1]['content']
    if system == search.QUERY_SYSTEM:
        return {'queries': ['Rayleigh scattering blue light', 'sunset light path atmosphere']}
    if 'longer path' in user:
        return {'facts': [
            {'claim': 'At sunset, air scatters most blue light out of the longer path, leaving mostly red and orange.',
             'quote': 'Along that longer path, air molecules scatter most of the blue light out of the beam, so mostly red and orange light is left.'},
            {'claim': 'At the horizon sunlight crosses about 40 times more air than overhead.',   # 40 is not on the page
             'quote': 'The air mass at the horizon is about 38 times the air mass overhead.'}]}
    if 'Rayleigh scattering by air' in user:
        return {'facts': [
            {'claim': 'Air molecules scatter short blue wavelengths much more strongly than long red ones.',
             'quote': 'Rayleigh scattering by air molecules is much stronger for short wavelengths such as blue light than for long wavelengths such as red light.'},
            {'claim': 'Sunsets are red because of volcanic dust.',                                # quote not on the page
             'quote': 'Volcanic dust is the reason every sunset is red.'}]}
    return {'facts': []}


class ResearchTopic(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        FakeDDGS.calls, FakeDDGS.fail_backends = [], set()
        self.http = FakeHTTP()
        self.fake = FakeOllama(model)
        for p in (mock.patch.dict(os.environ, {'SCRIPT_PROVIDER': 'ollama', 'SEARCH_BACKENDS': 'duckduckgo,auto'}),
                  mock.patch.object(search, '_ddgs', lambda: FakeDDGS), mock.patch('requests.get', self.http.get),
                  mock.patch.object(search, 'open_index_search', lambda q, n=8: []), patched(self.fake)):
            p.start()
            self.addCleanup(p.stop)

    def run_research(self, episode='ep', **kw):
        cache = MultiCache(self.root / episode, self.root / 'shared')
        ledger = Ledger.open(self.root / episode / 'ledger.json', 2.0)
        timings, notes = {}, []
        facts = search.research_topic('Why sunsets are red', 'science', '', cache, ledger=ledger, timings=timings,
                                      notes=notes, **kw)
        return facts, cache, ledger, timings

    def test_facts_come_only_from_reputable_pages_with_their_url_and_quote(self):
        facts, cache, ledger, timings = self.run_research()
        self.assertEqual([f.id for f in facts], ['s1', 's2'])
        self.assertEqual({f.url for f in facts}, {'https://www.noaa.gov/sunset', 'https://physics.example.edu/rayleigh'})
        self.assertTrue(all(f.quote and f.quote in PAGES[f.url] for f in facts))
        claims = ' '.join(f.claim for f in facts)
        self.assertNotIn('40 times', claims)                  # a number its quote does not have: dropped
        self.assertNotIn('volcanic', claims)                  # a quote the page does not contain: dropped
        self.assertNotIn('someblog.com', self.http.urls)      # never fetched: not reputable
        self.assertNotIn('https://en.wikipedia.org/wiki/Sunset', self.http.urls)
        self.assertEqual(len([q for q, _ in FakeDDGS.calls]), 6)  # 4 templates + 2 from the model
        entries = {(e['step'], e['provider']) for e in ledger.entries}
        self.assertEqual(entries, {('research', 'ollama'), ('research', 'duckduckgo')})
        self.assertEqual(ledger.spent(), 0)
        self.assertEqual(timings['engines'], ['duckduckgo'])

    def test_order_is_stable_and_a_rerun_is_all_cache(self):
        first, *_ = self.run_research()
        calls, chats, fetched = len(FakeDDGS.calls), len(self.fake.chats), len(self.http.urls)
        again, cache, _, _ = self.run_research()
        self.assertEqual([f.model_dump() for f in again], [f.model_dump() for f in first])
        self.assertEqual((len(FakeDDGS.calls), len(self.fake.chats), len(self.http.urls)), (calls, chats, fetched))
        self.assertGreater(cache.stats['search_hit'], 0)

    def test_the_page_cache_is_shared_by_another_episode(self):
        self.run_research('ep-one')
        fetched = len(self.http.urls)
        facts, cache, _, _ = self.run_research('ep-two')       # its own search + model caches, the shared page cache
        self.assertEqual(len(self.http.urls), fetched)
        self.assertGreaterEqual(cache.stats['page_hit'], 2)
        self.assertEqual(len(facts), 2)

    def test_duckduckgo_refusing_falls_back_to_the_other_keyless_engines(self):
        FakeDDGS.fail_backends = {'duckduckgo'}
        facts, _, _, timings = self.run_research()
        self.assertEqual(len(facts), 2)
        self.assertEqual(timings['engines'], ['auto'])
        self.assertIn(('Why sunsets are red scientific explanation', 'duckduckgo'), FakeDDGS.calls)

    def test_open_indexes_when_web_search_finds_nothing(self):
        FakeDDGS.fail_backends = {'duckduckgo', 'auto'}
        hits = [dict(title='', href='https://www.noaa.gov/sunset', body='', via='wikipedia references: Sunset'),
                dict(title='Paper', href='https://doi.org/10.1/abc', body='', via='openalex',
                     text='Rayleigh scattering by air molecules is much stronger for short wavelengths such as blue light '
                          'than for long wavelengths such as red light. ' * 3)]
        with mock.patch.object(search, 'open_index_search', lambda q, n=8: hits):
            facts, _, _, timings = self.run_research()
        self.assertIn('open_index', timings['engines'])
        self.assertEqual({f.url for f in facts}, {'https://www.noaa.gov/sunset', 'https://doi.org/10.1/abc'})

    def test_parallel_search_merges_and_deduplicates(self):
        merged = search.parallel_search(['scientific explanation', 'site:edu', 'scientific explanation again'])
        self.assertEqual([r['href'] for r in merged], ['https://www.noaa.gov/sunset', 'https://someblog.com/sunsets',
                                                       'https://physics.example.edu/rayleigh', 'https://en.wikipedia.org/wiki/Sunset'])

    def test_parallel_fetch_and_passages(self):
        pages = search.parallel_fetch(list(PAGES) + ['https://www.noaa.gov/missing'])
        self.assertIn('longer path', pages['https://www.noaa.gov/sunset'])
        self.assertEqual(pages['https://www.noaa.gov/missing'], '')
        text = 'Cats sleep a lot.\n' * 3 + 'Sunsets look red because blue light scatters out of the long path.\n'
        self.assertIn('Sunsets look red', search.passages(text, search.keywords('why sunsets are red'), limit=80))

    def test_quote_checks(self):
        page = 'The air mass at the horizon is about 38 times the air mass overhead. Other text.'
        self.assertTrue(search.verify_quote('The air mass at the horizon is about 38 times the air mass overhead.', page))
        self.assertTrue(search.verify_quote('the air mass at the horizon is about 38 times the airmass overhead', page))
        self.assertFalse(search.verify_quote('Sunsets are caused by volcanoes on most days of the year.', page))
        two = 'Sunlight takes a longer path at sunset. Most of the blue is scattered out of it. Other text here.'
        self.assertTrue(search.verify_quote('Sunlight takes a longer path at sunset, and most of the blue is scattered out of it.', two))
        self.assertTrue(search.numbers_supported('About 38 times more air.', page))
        self.assertFalse(search.numbers_supported('About 40 times more air.', page))


if __name__ == '__main__':
    unittest.main()
