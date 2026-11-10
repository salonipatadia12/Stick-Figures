"""Tables (docs/WEBAPP.md "Data") with SQLAlchemy 2 Core; Postgres via psycopg 3 in production, SQLite in tests.

Created at startup when missing (create_all == CREATE TABLE IF NOT EXISTS). Times are naive UTC.
Additions to the contract (lane B): episodes.spend_limit_usd and episodes.waiting (the spend-approval stop), and the
spend_approvals table (who allowed how much more, when).
"""
from __future__ import annotations

import datetime as dt
import time

from sqlalchemy import (JSON, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
                        create_engine, event)
from sqlalchemy.exc import OperationalError, ProgrammingError, IntegrityError

metadata = MetaData()

episodes = Table(
    'episodes', metadata,
    Column('slug', String(64), primary_key=True),
    Column('topic', Text, nullable=False),
    Column('niche', Text, nullable=False, default=''),
    Column('audience', Text, nullable=False, default=''),
    Column('state', String(32), nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('updated_at', DateTime, nullable=False),
    Column('cost_usd', Float, nullable=False, default=0.0),
    Column('error', Text),
    Column('spend_limit_usd', Float),           # null = SPEND_PREAPPROVED_USD
    Column('waiting', JSON),                    # spend stop: {kind, job_id, step, estimate_usd, spent_usd, need_usd, message}
)

jobs = Table(
    'jobs', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('slug', String(64), ForeignKey('episodes.slug'), nullable=False, index=True),
    Column('kind', String(16), nullable=False),          # pitch, script (VPS); paint, render (Mac)
    Column('status', String(16), nullable=False, index=True),
    Column('payload', JSON),
    Column('result', JSON),
    Column('progress', JSON),
    Column('attempts', Integer, nullable=False, default=0),
    Column('lease_until', DateTime),
    Column('created_at', DateTime, nullable=False),
    Column('started_at', DateTime),
    Column('finished_at', DateTime),
    Column('error', Text),
)

worker = Table(
    'worker', metadata,
    Column('id', Integer, primary_key=True),
    Column('last_seen', DateTime),
    Column('host', Text),
    Column('version', Text),
    Column('busy_job_id', Integer),
)

files = Table(
    'files', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('slug', String(64), ForeignKey('episodes.slug'), nullable=False, index=True),
    Column('name', String(128), nullable=False),
    Column('kind', String(32), nullable=False),          # mp4, srt, qa, contact_sheet, scene_preview
    Column('size', Integer, nullable=False),
    Column('created_at', DateTime, nullable=False),
)

spend_approvals = Table(
    'spend_approvals', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('slug', String(64), ForeignKey('episodes.slug'), nullable=False, index=True),
    Column('more_usd', Float, nullable=False),
    Column('limit_usd', Float, nullable=False),
    Column('by', Text, nullable=False),
    Column('at', DateTime, nullable=False),
)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(value):
    return value.strftime('%Y-%m-%dT%H:%M:%SZ') if value else None


def make_engine(url: str):
    if url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    kwargs = {}
    if url.startswith('sqlite'):
        kwargs['connect_args'] = {'check_same_thread': False, 'timeout': 30}
    else:
        kwargs['pool_pre_ping'] = True
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def _sqlite_pragmas(conn, _record):
            cur = conn.cursor()
            cur.execute('PRAGMA foreign_keys=ON')
            cur.execute('PRAGMA busy_timeout=30000')
            cur.close()
    return engine


def init(engine):
    """Create missing tables; the web app and the runner may both start at once, so tolerate the race."""
    for attempt in range(5):
        try:
            metadata.create_all(engine, checkfirst=True)
            return
        except (OperationalError, ProgrammingError, IntegrityError):
            if attempt == 4:
                raise
            time.sleep(1 + attempt)


def ping(engine) -> bool:
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception:
        return False
