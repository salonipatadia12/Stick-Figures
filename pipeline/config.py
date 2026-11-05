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


def load_env():
    """Load ROOT/.env without overriding variables already set in the shell."""
    load_dotenv(ROOT / '.env', override=False)


def has_anthropic_key():
    return bool(os.getenv('ANTHROPIC_API_KEY', '').strip())
