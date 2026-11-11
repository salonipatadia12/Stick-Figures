"""Free research (SCRIPT_PROVIDER=ollama): DuckDuckGo search -> reputable pages -> facts quoted from those pages.

    research_topic(topic, niche, audience, cache, ...) -> list[Source]

1. Queries: four templates plus two topic-specific queries from the local model (one ~50-token call).
2. Search: every query runs in parallel (DuckDuckGo through `ddgs`; ddgs's other keyless engines if DuckDuckGo
   refuses). Results are cached per query for 24 h (cache layer 1).
3. Fetch: a page is fetched as soon as its search returns it, while other searches are still running, but only
   when it passes the same source rule as before (script.reputable: government, university or peer-reviewed). Page
   text is extracted with trafilatura and cached for 7 days in a cache shared by every episode (layer 2).
4. Facts: the model reads the passages of each page that mention the topic and returns at most two facts per page,
   each with the sentence it came from. The sentence must really be on the page (checked in code), and every number
   in the fact must be in that sentence; a fact that fails either check is dropped. Each fact keeps its page URL.

If the search engines refuse this machine (rate limits, bot checks), open indexes that need no key are used to find
pages instead: the reference links of the matching Wikipedia articles (Wikipedia itself is never cited; only the
government, university and journal pages it links to) and OpenAlex's index of peer-reviewed papers (the abstract,
cited by its DOI). The research note says which engine served.

Nothing here needs a key or an account, and nothing is paid. Searches and model calls are recorded in the ledger at
$0.00 (providers 'duckduckgo' and 'ollama').
"""
from __future__ import annotations

import concurrent.futures as cf
import difflib
import re
import time
from urllib.parse import quote as urlquote

from . import config, llm
from .models import NUMBER, Source

QUERY_TEMPLATES = [
    '{topic} scientific explanation',
    '{topic} mechanism research site:edu OR site:gov',
    '{topic} study peer-reviewed',
    '{topic} statistics data',
]
MAX_RESULTS_PER_QUERY = 8
PAGES_PER_QUERY = 3            # reputable pages taken from each query, in rank order (deterministic set)
OPEN_INDEX_PAGES_PER_QUERY = 4
MAX_FACTS, FACTS_PER_PAGE = 8, 2
MIN_PAGE_CHARS = 300
MIN_FACTS = 4                  # fewer than this from web search: the open indexes are tried too
PASSAGE_CHARS = 2400           # page text the fact reader sees (the best-matching paragraphs)
FETCH_TIMEOUT_S, FETCH_MAX_BYTES = 10, 4_000_000
USER_AGENT = 'StickFiguresResearch/0.2 (+https://github.com/salonipatadia12/Stick-Figures; research for explainer videos)'

QUERY_SYSTEM = ('You write web search queries that find explanations of a topic on university, government and journal '
                'pages. Each query is 3-7 plain words naming the mechanism, process or key term behind the topic. No '
                'quotes, no site: operators.')
QUERY_SCHEMA = {'type': 'object', 'properties': {'queries': {'type': 'array', 'items': {'type': 'string'},
                                                             'minItems': 2, 'maxItems': 2}}, 'required': ['queries']}

FACT_SYSTEM = """You read ONE web page and pick facts for a 30-60 second explainer video.
Return 0-2 facts that the page states directly and that help explain the topic. Prefer the mechanism (how or why it
happens); at most one statistic. For each fact:
- claim: one plain sentence (at most 30 words) saying only what the page says. Name the country or period if the page
  limits it. No advice, no opinions, nothing the page does not say.
- quote: the sentence (or two) from the page text that supports the claim, copied exactly, word for word.
When the page explains any part of the topic, return at least one fact. Return no facts only when the page is not
about the topic at all."""
FACT_SCHEMA = {'type': 'object', 'properties': {'facts': {'type': 'array', 'maxItems': FACTS_PER_PAGE, 'items': {
    'type': 'object', 'properties': {'claim': {'type': 'string'}, 'quote': {'type': 'string'}},
    'required': ['claim', 'quote']}}}, 'required': ['facts']}

STOP = set('the a an and or but of to in on at for with by from is are was were be been it its this that these those '
           'why how what when where who which do does did you your we our they their as than then so not no can'.split())


# ------------------------------------------------------------------ search

def _ddgs():
    try:
        from ddgs import DDGS
    except ImportError:                                   # the package's earlier name
        from duckduckgo_search import DDGS
    return DDGS


def web_search(query: str, max_results: int = MAX_RESULTS_PER_QUERY) -> tuple[list, str]:
    """(results [{title, href, body}], engine). DuckDuckGo first, then ddgs's other keyless engines."""
    DDGS = _ddgs()
    for backend in config.search_backends():
        try:
            raw = DDGS(timeout=FETCH_TIMEOUT_S).text(query, max_results=max_results, backend=backend)
        except TypeError:                                 # duckduckgo_search < 7 has no backend argument
            try:
                raw = list(DDGS().text(query, max_results=max_results))
            except Exception:
                raw = []
        except Exception:                                 # rate limit, bot check, network: try the next engine
            raw = []
        results = [dict(title=r.get('title', ''), href=r.get('href') or r.get('url', ''), body=r.get('body', ''))
                   for r in raw or [] if (r.get('href') or r.get('url', '')).startswith(('http://', 'https://'))]
        if results:
            return results, backend
    return [], ''


def cached_search(query: str, cache=None, fresh: bool = False, max_results: int = MAX_RESULTS_PER_QUERY):
    """(results, engine, from_cache)."""
    if cache is not None and not fresh:
        hit = cache.get_search(query)
        if hit is not None:
            return hit, cache.get_search_engine(query) or 'cache', True
    results, engine = web_search(query, max_results)
    if cache is not None:
        cache.put_search(query, results, engine)
    return results, engine, False


def parallel_search(queries: list, max_results_per: int = MAX_RESULTS_PER_QUERY, workers: int = 4, cache=None) -> list:
    """Run several searches at once; all results merged, deduplicated by URL, in query order."""
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        batches = list(pool.map(lambda q: cached_search(q, cache, max_results=max_results_per)[0], queries))
    seen, merged = set(), []
    for batch in batches:
        for r in batch:
            if r['href'] not in seen:
                seen.add(r['href'])
                merged.append(r)
    return merged


# ------------------------------------------------------------------ fetch

def fetch_page(url: str, timeout: int = FETCH_TIMEOUT_S, cache=None) -> tuple[str, str]:
    """(main text, title) of one page; ('', '') when it cannot be fetched or has no readable text (never cached)."""
    if cache is not None:
        hit = cache.get_page(url)
        if hit is not None:
            return hit, cache.get_page_title(url)
    try:
        import requests
        import trafilatura
        r = requests.get(url, timeout=timeout, headers={'User-Agent': USER_AGENT}, stream=True)
        kind = r.headers.get('content-type', '')
        if r.status_code != 200 or ('html' not in kind and 'xml' not in kind and kind):
            return '', ''
        body = r.raw.read(FETCH_MAX_BYTES + 1, decode_content=True)
        if len(body) > FETCH_MAX_BYTES:
            return '', ''
        html = body.decode(r.encoding or 'utf-8', errors='replace')
        text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False, fast=True) or ''
        meta = trafilatura.extract_metadata(html, default_url=url)
        title = (getattr(meta, 'title', '') or '') if meta else ''
    except Exception:
        return '', ''
    if cache is not None:
        cache.put_page(url, text, title)
    return text, title


def parallel_fetch(urls: list, workers: int = 6, timeout: int = FETCH_TIMEOUT_S, cache=None) -> dict:
    """Fetch and extract several pages at once: {url: text} ('' for pages that failed)."""
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(lambda u: (u, fetch_page(u, timeout, cache)[0]), urls))


# ------------------------------------------------------------------ open indexes (when search engines refuse)

def _get_json(url, timeout=FETCH_TIMEOUT_S):
    import requests
    r = requests.get(url, timeout=timeout, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    return r.json()


def open_index_search(query: str, max_results: int = MAX_RESULTS_PER_QUERY) -> list:
    """Keyless open indexes: the reference links of the best-matching Wikipedia articles (discovery only: Wikipedia
    pages are never cited) and OpenAlex papers (results carry their abstract as `text`, cited by DOI)."""
    out = []
    try:
        hits = _get_json('https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=3&srsearch='
                         + urlquote(query)).get('query', {}).get('search', [])
        for h in hits[:3]:
            pages = _get_json('https://en.wikipedia.org/w/api.php?action=query&prop=extlinks&ellimit=200&format=json'
                              '&titles=' + urlquote(h['title'])).get('query', {}).get('pages', {})
            for page in pages.values():
                for link in page.get('extlinks', []):
                    href = (link.get('*') or link.get('url') or '').split('#')[0]
                    m = re.match(r'https?://web\.archive\.org/web/\d+/(https?://.+)', href)
                    if m:                                  # an archived copy: the page itself
                        href = m.group(1)
                    if href.startswith('http://'):
                        href = 'https://' + href[len('http://'):]
                    if href.startswith('https://') and not href.lower().endswith('.pdf'):
                        out.append(dict(title='', href=href, body='', via=f'wikipedia references: {h["title"]}'))
    except Exception:
        pass
    try:
        works = _get_json('https://api.openalex.org/works?per-page=5&filter=has_abstract:true,type:article&search='
                          + urlquote(query)).get('results', [])
        for w in works:
            doi, inv = w.get('doi') or '', w.get('abstract_inverted_index') or {}
            if not doi or not inv:
                continue
            words = sorted(((p, word) for word, pos in inv.items() for p in pos))
            abstract = ' '.join(x for _, x in words)
            if len(_stems(f'{w.get("display_name") or ""} {abstract}') & _stems(query)) < 0.5 * len(_stems(query)):
                continue                                   # OpenAlex's search is loose: keep papers about the query
            out.append(dict(title=w.get('display_name') or '', href=doi, body='', text=abstract, via='openalex'))
    except Exception:
        pass
    seen, uniq = set(), []
    for r in out:
        if r['href'] not in seen:
            seen.add(r['href'])
            uniq.append(r)
    # A Wikipedia article links to hundreds of pages in no useful order: the ones whose address or title name the
    # query's words come first (stable, so equal scores keep the article's order).
    stems = _stems(query)

    def score(r):
        where = re.sub(r'[^a-z0-9]+', ' ', (r['href'].split('://', 1)[-1] + ' ' + r.get('title', '')).lower())
        s = len({w[:5] for w in where.split() if len(w) > 2} & stems)
        return s + (len(_stems(r.get('text', '')) & stems) / 2 if r.get('text') else 0)
    return sorted(uniq, key=lambda r: -score(r))[:max_results * 3]


# ------------------------------------------------------------------ facts

def keywords(text: str) -> set:
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP and len(w) > 2}


def passages(text: str, words: set, limit: int = PASSAGE_CHARS) -> str:
    """The paragraphs of a page that share the most words with the topic, in page order, up to `limit` characters."""
    paras = [p.strip() for p in re.split(r'\n+', text) if len(p.strip()) > 40]
    if not paras:
        return text[:limit]
    scored = sorted(range(len(paras)), key=lambda i: -len(keywords(paras[i]) & words))
    keep, size = set(), 0
    for i in scored:
        if size + len(paras[i]) > limit and keep:
            continue
        keep.add(i)
        size += len(paras[i]) + 1
        if size >= limit:
            break
    return '\n'.join(paras[i] for i in sorted(keep))[:limit]


def _norm(s: str) -> str:
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"').replace('–', '-')
    return ' '.join(re.sub(r'[^\w%$.,;:\'"()/-]+', ' ', s.lower()).split())


def verify_quote(quote: str, text: str) -> str:
    """The page's own words behind `quote` ('' if the page does not say it). An exact match after normalising
    spaces and punctuation, or one to three consecutive sentences of the page at least 90 % the same (the model
    sometimes drops or changes a word)."""
    q, t = _norm(quote).strip(' ."'), _norm(text)
    if len(q) < 20:
        return ''
    if q in t:
        return quote.strip()
    sents = [x.strip() for x in re.split(r'(?<=[.!?])\s+|\n+', text) if x.strip()]
    best, score = '', 0.0
    for n in (1, 2, 3):
        for i in range(len(sents) - n + 1):
            span = ' '.join(sents[i:i + n])
            if abs(len(span) - len(quote)) > 0.4 * len(quote):
                continue
            r = difflib.SequenceMatcher(None, q, _norm(span).strip(' ."')).ratio()
            if r > score:
                best, score = span, r
    return best if score >= 0.9 else ''


def _stems(text: str) -> set:
    return {w[:5] for w in keywords(text)}


def claim_supported(claim: str, quote: str) -> bool:
    """At least 40 % of the claim's content words (compared by their first five letters) are in its quote, so a fact
    never rests on an unrelated sentence of the same page."""
    words = _stems(claim)
    return bool(words) and len(words & _stems(quote)) >= 0.4 * len(words)


def numbers_supported(claim: str, quote: str) -> bool:
    """Every number in the claim appears in its quote (digits compared without separators)."""
    digits = lambda s: {re.sub(r'[^\d.]', '', n).strip('.') for n in NUMBER.findall(s)} - {''}
    return digits(claim) <= digits(quote)


def page_facts(topic: str, url: str, title: str, text: str, *, cache=None, ledger=None, fresh=False, focus=()) -> list:
    """[{claim, quote, url, title}] from one page (0-2), each checked against the page text."""
    words = keywords(topic + ' ' + ' '.join(focus))
    excerpt = passages(text, words)
    user = (f'Topic: {topic}\n' + (f'The story needs: {"; ".join(focus)}\n' if focus else '')
            + f'Page: {title or url}\n\nPage text:\n{excerpt}')
    try:
        data = llm.generate_json(FACT_SYSTEM, user, FACT_SCHEMA, 400, cache=cache, refresh=fresh, ledger=ledger,
                                 step='research', detail=f'facts from {url[:80]}', seed=11)
    except llm.LLMError:
        return []
    out = []
    for f in data.get('facts', [])[:FACTS_PER_PAGE]:
        claim, quote = ' '.join(str(f.get('claim', '')).split()), str(f.get('quote', ''))
        found = verify_quote(quote, text)
        if (len(claim) < 20 or not found or not numbers_supported(claim, found) or not claim_supported(claim, found)
                or any(found == o['quote'][:500] for o in out)):
            continue
        out.append(dict(claim=claim, quote=found[:500], url=url, title=title))
    return out


def generated_queries(topic: str, niche: str = '', cache=None, ledger=None, fresh=False) -> list:
    """Two topic-specific queries from the model (about 50 output tokens)."""
    user = f'Topic: {topic}\nNiche: {niche or "general"}\nWrite 2 search queries.'
    try:
        data = llm.generate_json(QUERY_SYSTEM, user, QUERY_SCHEMA, 120, cache=cache, refresh=fresh, ledger=ledger,
                                 step='research', detail='search queries', seed=3)
    except llm.LLMError:
        return []
    return [' '.join(str(q).replace('"', '').split()) for q in data.get('queries', []) if str(q).strip()][:2]


def template_queries(topic: str) -> list:
    t = topic.strip().rstrip('?.!')
    return [tpl.format(topic=t) for tpl in QUERY_TEMPLATES]


# ------------------------------------------------------------------ the whole step

def research_topic(topic: str, niche: str = '', audience: str = '', cache=None, *, ledger=None, focus=None,
                   fresh: bool = False, timings: dict | None = None, notes: list | None = None) -> list:
    """Search -> reputable pages -> checked facts, as Source objects s1.. in a stable order (by query, then rank).

    fresh=True ignores cached searches and model answers (pages stay cached: a page does not change)."""
    from .script import doi_link, reputable
    focus = focus or {}
    find = [str(x) for x in focus.get('find', [])][:4]
    timings = timings if timings is not None else {}
    notes = notes if notes is not None else []
    t0 = time.time()
    mark = lambda name: timings.__setitem__(name, round(time.time() - t0, 2))
    queries = template_queries(topic) + [f'{topic.rstrip("?")} {x}' for x in find]
    engines, results_by_q, fetched, extracted = {}, {}, {}, {}
    page_rank, page_meta = {}, {}
    workers = config.ollama_parallel()
    tried_open = False
    with cf.ThreadPoolExecutor(max_workers=6) as net, cf.ThreadPoolExecutor(max_workers=workers) as model:
        pending = {}

        def submit_search(qi, q):
            pending[net.submit(cached_search, q, cache, fresh)] = ('search', qi, q)

        for qi, q in enumerate(queries):
            submit_search(qi, q)
        pending[model.submit(generated_queries, topic, niche, cache, ledger, fresh)] = ('queries', None, None)

        def take(qi, results, per_query=PAGES_PER_QUERY):
            n = 0
            for rank, r in enumerate(results):
                url = doi_link(r['href'])
                if n >= per_query:
                    break
                if not reputable(url):
                    continue
                n += 1
                page_rank[url] = min(page_rank.get(url, (99, 99)), (qi, rank))
                page_meta.setdefault(url, r)
                if url not in fetched:
                    fetched[url] = None
                    if r.get('text'):                      # an open-index abstract: no page to fetch
                        if cache is not None:
                            cache.put_page(url, r['text'], r.get('title', ''))
                        pending[net.submit(lambda u=url, rr=r: (rr['text'], rr.get('title', '')))] = ('fetch', None, url)
                    else:
                        pending[net.submit(fetch_page, url, FETCH_TIMEOUT_S, cache)] = ('fetch', None, url)

        while pending:
            done, _ = cf.wait(list(pending), return_when=cf.FIRST_COMPLETED)
            for fut in done:
                kind, qi, arg = pending.pop(fut)
                if kind == 'queries':
                    mark('queries_s')
                    for q in fut.result():
                        queries.append(q)
                        submit_search(len(queries) - 1, q)
                elif kind == 'search':
                    results, engine, _ = fut.result()
                    if results:
                        engines[arg] = engine
                    results_by_q[qi] = results
                    mark('search_s')
                    take(qi, results)
                elif kind == 'fetch':
                    text, title = fut.result()
                    fetched[arg] = (text, title or page_meta.get(arg, {}).get('title', ''))
                    mark('fetch_s')
                    if len(text) >= MIN_PAGE_CHARS:
                        pending[model.submit(page_facts, topic, arg, fetched[arg][1], text, cache=cache, ledger=ledger,
                                             fresh=fresh, focus=find)] = ('facts', None, arg)
                elif kind == 'facts':
                    extracted[arg] = fut.result()
                    mark('facts_s')
            if not pending and not tried_open and (
                    sum(1 for v in fetched.values() if v and len(v[0]) >= MIN_PAGE_CHARS) < 3
                    or sum(len(v) for v in extracted.values()) < MIN_FACTS):
                tried_open = True
                # the search engines refused, or found too few reputable pages or facts: try the open indexes once
                notes.append('search: too few reputable pages or facts from web search; also trying open indexes '
                             '(Wikipedia references, OpenAlex)')
                base_qi = len(queries)
                for k, q in enumerate([topic.rstrip('?')] + queries[len(QUERY_TEMPLATES) + len(find):]):
                    qkey = f'open-index: {q}'
                    hit = None if (fresh or cache is None) else cache.get_search(qkey)
                    if hit is None:
                        hit = open_index_search(q)
                        if cache is not None:
                            cache.put_search(qkey, hit, 'open_index')
                    if hit:
                        engines[qkey] = 'open_index'
                    results_by_q[base_qi + k] = hit
                    take(base_qi + k, hit, OPEN_INDEX_PAGES_PER_QUERY)
    timings['total_s'] = round(time.time() - t0, 2)
    served = sorted({e for e in engines.values() if e})
    n_pages = sum(1 for v in fetched.values() if v and len(v[0]) >= MIN_PAGE_CHARS)
    timings.update(queries=len(queries), engines=served, reputable_pages=len(fetched), pages_with_text=n_pages)
    if ledger is not None:
        ledger.record_free('research', 'duckduckgo', 'ddgs', f'{len(queries)} searches ({", ".join(served) or "none"}), '
                           f'{len(fetched)} reputable pages, {n_pages} with text')
    facts, seen = [], []
    for url in sorted(extracted, key=lambda u: page_rank.get(u, (99, 99))):
        for f in extracted[url]:
            if any(difflib.SequenceMatcher(None, f['claim'].lower(), s).ratio() > 0.8 for s in seen):
                continue
            seen.append(f['claim'].lower())
            facts.append(Source(id=f's{len(facts) + 1}', claim=f['claim'], url=url, title=f['title'] or '',
                                quote=f['quote']))
            if len(facts) >= MAX_FACTS:
                break
        if len(facts) >= MAX_FACTS:
            break
    timings['facts'] = len(facts)
    return facts
