"""A fake Ollama server for the free-path tests: stands in for `requests` inside pipeline.llm, so the real generate()
(cache, ledger, options, errors) runs against canned answers. Nothing reaches a real model."""
import json
import threading
import time
from types import SimpleNamespace as NS


class Resp:
    def __init__(self, status=200, data=None, text=''):
        self.status_code, self._data, self.text = status, data or {}, text or json.dumps(data or {})

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class FakeOllama:
    """route(body) -> answer text (or a Resp for errors). Records every /api/chat body and the peak concurrency."""

    def __init__(self, route, models=('qwen2.5:14b', 'qwen3.5:9b'), delay=0.0):
        self.route, self.models, self.delay = route, list(models), delay
        self.chats, self.unloads, self.gets = [], [], []
        self.active = self.peak = 0
        self._lock = threading.Lock()

    def get(self, url, timeout=None, **kw):
        self.gets.append(url)
        if url.endswith('/api/tags'):
            return Resp(data={'models': [{'name': m, 'model': m} for m in self.models]})
        return Resp(404)

    def post(self, url, json=None, timeout=None, **kw):
        if url.endswith('/api/generate'):
            self.unloads.append(json)
            return Resp(data={'done': True})
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.chats.append(json)
        try:
            if self.delay:
                time.sleep(self.delay)
            out = self.route(json)
        finally:
            with self._lock:
                self.active -= 1
        if isinstance(out, Resp):
            return out
        if not isinstance(out, str):
            out = globals()['json'].dumps(out)
        return Resp(data={'message': {'role': 'assistant', 'content': out}, 'done_reason': 'stop',
                          'prompt_eval_count': 120, 'eval_count': 40})


def patched(fake):
    """with patched(fake): pipeline.llm talks to `fake` instead of HTTP."""
    from unittest import mock
    from pipeline import llm
    return mock.patch.object(llm, '_requests', lambda: NS(get=fake.get, post=fake.post))
