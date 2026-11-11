"""One way to ask a language model for text or JSON, whichever provider writes the scripts.

    generate(system, user, json_schema=None, max_tokens=8000)  -> str
    generate_batch([dict(system=..., user=..., ...), ...])      -> [str, ...]   (parallel, order kept)
    is_available()                                              -> bool
    unload()                                                    -> frees the model's memory (Ollama keep_alive=0)

SCRIPT_PROVIDER=ollama (the default): a local model through Ollama's HTTP API (OLLAMA_HOST, OLLAMA_MODEL). Structured
output is Ollama's JSON-schema `format`, which constrains decoding, so the answer always parses and enum fields
(recipes, verbs, sound names) can only take allowed values. Every call is free and is recorded in the episode's
ledger at $0.00 with provider 'ollama'; answers are cached by hash(system + user + model + schema + options).

SCRIPT_PROVIDER=anthropic: the same call through the Anthropic API, budgeted in the ledger before it is sent (no
retries). research/story/script keep their own Anthropic requests; this branch is for callers of this module.

Memory (16 GB Macs run FLUX right after the script step): calls use a 4096-token context unless one needs more, at
most OLLAMA_PARALLEL (2) run at once, and a step ends with unload() so the model leaves memory.
"""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time

from . import config

CHARS_PER_TOKEN = 3.8           # measured ~4.3 for these prompts on qwen2.5; a little high, to pick the context window
BIG_CTX = 8192
REQUEST_TIMEOUT_S = 900
_ledger_lock = threading.Lock()


class LLMError(RuntimeError):
    pass


def _requests():
    import requests
    return requests


def is_available(timeout: float = 2.0, host: str | None = None, model: str | None = None) -> bool:
    """Ollama answers at OLLAMA_HOST and OLLAMA_MODEL is pulled (anthropic: the key is set)."""
    if config.script_provider() == 'anthropic':
        return config.has_anthropic_key()
    host, model = host or config.ollama_host(), model or config.ollama_model()
    try:
        r = _requests().get(f'{host}/api/tags', timeout=timeout)
        r.raise_for_status()
        names = {m.get('name', '') for m in r.json().get('models', [])} | {m.get('model', '') for m in r.json().get('models', [])}
    except Exception:
        return False
    return model in names or f'{model}:latest' in names


def context_for(system: str, user: str, max_tokens: int, base: int | None = None) -> int:
    """The smallest context window that holds the prompt and the answer: OLLAMA_NUM_CTX (4096), else 8192.
    (Ollama reloads the model when the window changes, so it changes only when a call really needs it.)"""
    base = base or config.ollama_num_ctx()
    need = int((len(system) + len(user)) / CHARS_PER_TOKEN) + max_tokens + 64
    return base if need <= base else max(BIG_CTX, base)


def _record(ledger, step, model, detail, usage):
    if ledger is None:
        return
    with _ledger_lock:
        ledger.record_free(step, 'ollama', model, detail, usage)


def generate(system: str, user: str, json_schema: dict | None = None, max_tokens: int = 8000, *, temperature: float = 0.2,
             seed: int = 7, num_ctx: int | None = None, cache=None, ledger=None, step: str = 'llm', detail: str = '',
             keep_alive: str | None = None, client=None, refresh: bool = False) -> str:
    """The model's answer as text (JSON text when json_schema is given). refresh=True skips a cached answer (and
    caches the new one)."""
    if config.script_provider(client) == 'anthropic':
        return _anthropic(system, user, json_schema, max_tokens, ledger=ledger, step=step, detail=detail, client=client)
    model = config.ollama_model()
    ctx = num_ctx or context_for(system, user, max_tokens)
    options = dict(num_ctx=ctx, num_predict=max_tokens, temperature=temperature, seed=seed)
    key = None
    if cache is not None:
        from .cache import prompt_hash
        key = prompt_hash('ollama', model, system, user, json_schema, options)
        hit = None if refresh else cache.get_llm(key)
        if hit is not None:
            return hit['text']
    body = dict(model=model, stream=False, keep_alive=keep_alive or config.ollama_keep_alive(), options=options,
                messages=[{'role': 'system', 'content': system}, {'role': 'user', 'content': user}])
    if json_schema is not None:
        body['format'] = json_schema
    host = config.ollama_host()
    t0 = time.time()
    try:
        r = _requests().post(f'{host}/api/chat', json=body, timeout=REQUEST_TIMEOUT_S)
    except Exception as exc:
        raise LLMError(f'Ollama is not reachable at {host} ({exc.__class__.__name__}). Start it with '
                       '`brew services start ollama` (or `ollama serve`).') from None
    if r.status_code == 404:
        raise LLMError(f'Ollama has no model {model}: run `ollama pull {model}` once.')
    if r.status_code != 200:
        raise LLMError(f'Ollama returned HTTP {r.status_code}: {r.text[:300]}')
    data = r.json()
    text = (data.get('message') or {}).get('content', '')
    seconds = round(time.time() - t0, 2)
    usage = dict(input=int(data.get('prompt_eval_count') or 0), output=int(data.get('eval_count') or 0),
                 seconds=seconds, num_ctx=ctx, done_reason=data.get('done_reason', ''))
    _record(ledger, step, model, detail, usage)
    if data.get('done_reason') == 'length':
        raise LLMError(f'The {step} answer hit the {max_tokens}-token limit before it finished.')
    if cache is not None and key:
        cache.put_llm(key, dict(text=text, model=model, step=step, usage=usage, created=time.strftime('%Y-%m-%dT%H:%M:%S')))
    return text


def generate_json(system, user, json_schema, max_tokens=4000, **kw) -> dict:
    text = generate(system, user, json_schema, max_tokens, **kw)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LLMError(f'The {kw.get("step", "model")} answer was not JSON ({exc}).') from None


def generate_batch(prompts: list, workers: int | None = None, as_json: bool = False) -> list:
    """Run several generate() calls at once (OLLAMA_PARALLEL, default 2) and return the answers in order. A call that
    fails returns its LLMError in its slot instead of failing the others, so only that one is retried."""
    workers = workers or config.ollama_parallel()
    fn = generate_json if as_json else generate

    def one(p):
        p = dict(p)
        try:
            return fn(p.pop('system'), p.pop('user'), p.pop('json_schema', None), p.pop('max_tokens', 8000), **p)
        except LLMError as exc:
            return exc
    if len(prompts) <= 1 or workers == 1:
        return [one(p) for p in prompts]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, prompts))


def unload(model: str | None = None) -> bool:
    """Free the model's memory now (keep_alive=0), e.g. before FLUX paints. True when Ollama confirmed."""
    if config.script_provider() == 'anthropic':
        return False
    try:
        r = _requests().post(f'{config.ollama_host()}/api/generate', json=dict(model=model or config.ollama_model(),
                                                                             keep_alive=0), timeout=30)
        return r.status_code == 200
    except Exception:
        return False


# ------------------------------------------------------------------ anthropic (SCRIPT_PROVIDER=anthropic)

def _anthropic(system, user, json_schema, max_tokens, *, ledger, step, detail, client):
    """One budgeted Anthropic call (worst case reserved first, no retries), for callers of this module."""
    from .cost import token_cost
    if ledger is None:
        raise LLMError('A paid Anthropic call needs the episode ledger (every paid call is budgeted).')
    from .script import ScriptError, count_input_tokens, make_client, _send
    client = client or make_client()
    request = dict(model=config.SCRIPT_MODEL, max_tokens=max_tokens, system=system,
                   messages=[{'role': 'user', 'content': user}])
    if json_schema is not None:
        request['output_config'] = {'format': {'type': 'json_schema', 'schema': json_schema}}
    n, _ = count_input_tokens(client, request)
    try:
        response = _send(client, ledger, step, token_cost(config.SCRIPT_MODEL, input=n, output=max_tokens),
                         detail or step, request)
    except ScriptError as exc:
        raise LLMError(str(exc)) from None
    return next((b.text for b in response.content if getattr(b, 'type', None) == 'text'), '')
