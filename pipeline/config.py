"""Paths and environment. Keys are read from the repository's .env, never from code."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = Path(os.getenv('STICK_EPISODES_DIR', ROOT / 'episodes'))
STYLES_DIR = ROOT / 'styles'
SCHEMA_PATH = ROOT / 'schemas' / 'episode.schema.json'

DEFAULT_STYLE = 'cozy'
DEFAULT_LENGTH = 45
DEFAULT_ASPECT = '9:16'
DEFAULT_BUDGET_USD = 2.00
SCRIPT_MODEL = 'claude-sonnet-5-5'

# Research, storylines and the script (user 2026-11-10: the Anthropic credits ran out). `ollama` is free and local
# (research by DuckDuckGo, writing by a local model); `anthropic` is the earlier paid path, kept as it was.
SCRIPT_PROVIDERS = ('ollama', 'anthropic')
DEFAULT_SCRIPT_PROVIDER = 'ollama'
DEFAULT_OLLAMA_HOST = 'http://localhost:11434'
DEFAULT_OLLAMA_MODEL = 'qwen2.5:14b'    # Apache-2.0; 16 GB Macs. qwen2.5:32b on 32 GB or more.


def load_env():
    """Load ROOT/.env without overriding variables already set in the shell."""
    load_dotenv(ROOT / '.env', override=False)


def has_anthropic_key():
    return bool(os.getenv('ANTHROPIC_API_KEY', '').strip())


def _flag(name):
    return os.getenv(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def script_provider(client=None) -> str:
    """Which provider writes research, storylines and scripts.

    SCRIPT_PROVIDER decides when it is set. When it is not set: the web app's fake-Anthropic mode
    (STICK_FAKE_ANTHROPIC=1) and a caller that hands in its own Anthropic client mean `anthropic`; everything else is
    `ollama` (the default: free, local, no key)."""
    value = os.getenv('SCRIPT_PROVIDER', '').strip().lower()
    if value:
        if value not in SCRIPT_PROVIDERS:
            raise ValueError(f'SCRIPT_PROVIDER={value!r}: use one of {", ".join(SCRIPT_PROVIDERS)}')
        return value
    if _flag('STICK_FAKE_ANTHROPIC') or client is not None:
        return 'anthropic'
    return DEFAULT_SCRIPT_PROVIDER


def ollama_host() -> str:
    return (os.getenv('OLLAMA_HOST', '').strip() or DEFAULT_OLLAMA_HOST).rstrip('/')


def ollama_model() -> str:
    return os.getenv('OLLAMA_MODEL', '').strip() or DEFAULT_OLLAMA_MODEL


def ollama_num_ctx() -> int:
    """Context window per call. 4096 keeps a 14B model fast and small next to FLUX on 16 GB; a call that needs more
    gets 8192 for that call only."""
    return int(os.getenv('OLLAMA_NUM_CTX', '4096'))


def ollama_parallel() -> int:
    """Concurrent model calls (per-beat details, fact extraction). Ollama queues what its server cannot run at once."""
    return max(1, min(4, int(os.getenv('OLLAMA_PARALLEL', '2'))))


def ollama_keep_alive() -> str:
    """How long the model stays loaded between calls of one step. The model is unloaded when the step ends (so FLUX
    gets the memory) unless OLLAMA_UNLOAD=0."""
    return os.getenv('OLLAMA_KEEP_ALIVE', '').strip() or '5m'


def ollama_unload_after_step() -> bool:
    return os.getenv('OLLAMA_UNLOAD', '1').strip().lower() not in ('0', 'false', 'no', 'off')


def has_ollama(timeout: float = 2.0) -> bool:
    """True when the Ollama server answers and has OLLAMA_MODEL pulled."""
    from . import llm
    return llm.is_available(timeout=timeout)


def shared_cache_dir() -> Path:
    """Cache shared by every episode (fetched pages): STICK_CACHE_DIR, else <repo>/cache."""
    return Path(os.getenv('STICK_CACHE_DIR', '').strip() or ROOT / 'cache')


def search_backends() -> list:
    """ddgs backends tried in order: DuckDuckGo first, then ddgs's other keyless engines ('auto')."""
    raw = os.getenv('SEARCH_BACKENDS', '').strip() or 'duckduckgo,auto'
    return [b.strip() for b in raw.split(',') if b.strip()]
