"""Three-layer cache for the free research and script path (Ollama + DuckDuckGo).

Layer 1  search results by query          episodes/<slug>/cache/search-<hash>.json   24 h
Layer 2  extracted page text by URL       <shared>/pages/page-<hash>.json            7 days, shared by every episode
Layer 3  LLM responses by prompt hash     episodes/<slug>/cache/llm-<hash>.json      no expiry (same prompt, same answer)

A query is normalised before hashing (lowercased, words sorted), so "Sunset red physics" and "physics sunset red" are
one entry. Empty search results and failed page fetches are never cached: a blocked or flaky request is tried again
next time instead of being remembered as "nothing there". Writes are atomic (tmp file + rename), so the parallel
searches, fetches and model calls can share one cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

SEARCH_TTL_HOURS = 24
PAGE_TTL_DAYS = 7


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


def _read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def normalise_query(query: str) -> list:
    return sorted(re.findall(r'[\w:.\-]+', query.lower()))


def prompt_hash(*parts) -> str:
    """Layer-3 key: hash(system prompt + user message + model + schema + options)."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


class MultiCache:
    def __init__(self, episode_dir: Path, shared_dir: Path, *, now=time.time):
        self.episode_cache = Path(episode_dir) / 'cache'
        self.shared_cache = Path(shared_dir)
        self.pages_dir = self.shared_cache / 'pages'
        self.episode_cache.mkdir(parents=True, exist_ok=True)
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.stats = dict(search_hit=0, search_miss=0, page_hit=0, page_miss=0, llm_hit=0, llm_miss=0)
        self._lock = threading.Lock()

    def _count(self, key):
        with self._lock:
            self.stats[key] += 1

    @staticmethod
    def _key(*parts) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]

    # ---- layer 1: search results
    def search_path(self, query: str) -> Path:
        return self.episode_cache / f'search-{self._key(normalise_query(query))}.json'

    def get_search(self, query: str, ttl_hours: float = SEARCH_TTL_HOURS):
        data = _read(self.search_path(query))
        if data and self.now() - data.get('ts', 0) < ttl_hours * 3600:
            self._count('search_hit')
            return data['results']
        self._count('search_miss')
        return None

    def get_search_engine(self, query: str) -> str:
        return (_read(self.search_path(query)) or {}).get('engine', '')

    def put_search(self, query: str, results: list, engine: str = '') -> None:
        if results:
            _atomic_write(self.search_path(query), dict(query=query, engine=engine, results=results, ts=self.now()))

    # ---- layer 2: page text, shared across episodes
    def page_path(self, url: str) -> Path:
        return self.pages_dir / f'page-{self._key(url)}.json'

    def get_page(self, url: str, ttl_days: float = PAGE_TTL_DAYS):
        data = _read(self.page_path(url))
        if data and self.now() - data.get('ts', 0) < ttl_days * 86400:
            self._count('page_hit')
            return data['text']
        self._count('page_miss')
        return None

    def put_page(self, url: str, text: str, title: str = '') -> None:
        if text and text.strip():
            _atomic_write(self.page_path(url), dict(url=url, title=title, text=text, ts=self.now()))

    def get_page_title(self, url: str) -> str:
        data = _read(self.page_path(url))
        return (data or {}).get('title', '')

    # ---- layer 3: model responses
    def llm_path(self, key: str) -> Path:
        return self.episode_cache / f'llm-{key}.json'

    def get_llm(self, key: str):
        data = _read(self.llm_path(key))
        self._count('llm_hit' if data is not None else 'llm_miss')
        return data

    def put_llm(self, key: str, response: dict) -> None:
        _atomic_write(self.llm_path(key), response)

    def summary(self) -> str:
        s = self.stats
        return (f'cache: search {s["search_hit"]} hit / {s["search_miss"]} miss, pages {s["page_hit"]} hit / '
                f'{s["page_miss"]} miss, model {s["llm_hit"]} hit / {s["llm_miss"]} miss')


def for_episode(base) -> MultiCache:
    from . import config
    return MultiCache(Path(base), config.shared_cache_dir())
