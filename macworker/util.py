"""Small helpers: safe names, atomic JSON writes, backoff."""
from __future__ import annotations

import json
import os
import random
import re
import tempfile
from pathlib import Path

NAME_OK = re.compile(r'^[a-z0-9._-]+$')
SLUG_OK = re.compile(r'^[a-z0-9][a-z0-9-]{0,119}$')
KEY_OK = re.compile(r'^[a-z0-9][a-z0-9_-]{0,159}$')


def safe_name(text: str, default: str = 'file', max_len: int = 120) -> str:
    """A server file name: `[a-z0-9._-]+` only, no leading dot or dash, no '..', never empty."""
    s = re.sub(r'[^a-z0-9._-]+', '-', str(text).lower())
    s = re.sub(r'\.{2,}', '.', s)
    s = s.strip('.-_')
    if len(s) > max_len:
        stem, dot, ext = s.rpartition('.')
        s = (stem[:max_len - len(ext) - 1] + '.' + ext) if dot and len(ext) <= 8 else s[:max_len]
        s = s.strip('.-_')
    return s or default


def check_slug(slug) -> str:
    if not isinstance(slug, str) or not SLUG_OK.match(slug):
        raise ValueError(f'unsafe episode slug: {slug!r}')
    return slug


def check_key(key) -> str:
    if not isinstance(key, str) or not KEY_OK.match(key) or '..' in key:
        raise ValueError(f'unsafe library key: {key!r}')
    return key


def write_json(path: Path, data, indent: int = 1):
    """Write JSON atomically (a temp file in the same folder, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


class Backoff:
    """Exponential backoff with jitter: 5, 10, 20, ... up to `cap` seconds; reset() after a success."""

    def __init__(self, base: float = 5.0, cap: float = 300.0, rng=random.random):
        self.base, self.cap, self.rng, self.n = base, cap, rng, 0

    def next(self) -> float:
        d = min(self.cap, self.base * (2 ** self.n))
        self.n += 1
        return d * (0.8 + 0.4 * self.rng())

    def reset(self):
        self.n = 0
