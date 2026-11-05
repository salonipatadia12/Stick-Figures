"""Episode folders: episodes/<slug>/{episode.json, ledger.json, cache/, out/}."""
import json
import os
import re
import unicodedata
from pathlib import Path

from . import config
from .cost import Ledger
from .models import Episode


def slugify(text, limit=48):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    words = re.sub(r'[^a-z0-9]+', ' ', text.lower()).split()
    slug = ''
    for word in words:
        candidate = f'{slug}-{word}' if slug else word
        if len(candidate) > limit:
            break
        slug = candidate
    return slug or 'episode'


def folder(slug):
    return config.EPISODES_DIR / slug


def exists(slug):
    return (folder(slug) / 'episode.json').is_file()


def load(slug_or_path):
    path = Path(slug_or_path)
    if path.suffix == '.json' and path.is_file():
        return Episode.model_validate_json(path.read_text()), path.parent
    if path.is_dir() and (path / 'episode.json').is_file():
        return Episode.model_validate_json((path / 'episode.json').read_text()), path
    if not exists(str(slug_or_path)):
        raise ValueError(f'No episode "{slug_or_path}" in {config.EPISODES_DIR}')
    base = folder(str(slug_or_path))
    return Episode.model_validate_json((base / 'episode.json').read_text()), base


def save(episode, base=None):
    base = Path(base or folder(episode.slug))
    (base / 'cache').mkdir(parents=True, exist_ok=True)
    data = episode.model_dump(mode='json')
    tmp = base / 'episode.json.tmp'
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, base / 'episode.json')
    return base / 'episode.json'


def ledger(base, budget_usd=None):
    return Ledger.open(Path(base) / 'ledger.json', budget_usd, default_budget=config.DEFAULT_BUDGET_USD)


def list_episodes():
    if not config.EPISODES_DIR.is_dir():
        return []
    return sorted(p.parent.name for p in config.EPISODES_DIR.glob('*/episode.json'))
