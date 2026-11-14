"""Tables (docs/WEBAPP.md "Data") with SQLAlchemy 2 Core; Postgres via psycopg 3 in production, SQLite in tests.

Created at startup when missing (create_all == CREATE TABLE IF NOT EXISTS). Times are naive UTC.
Additions to the contract (lane B): episodes.spend_limit_usd and episodes.waiting (the spend-approval stop), and the
spend_approvals table (who allowed how much more, when). 2026-11-13: episodes.auto and episodes.auto_note (Auto mode:
topic to finished video with no stops; why it stopped when it had to) and push_subscriptions (Web Push to the phone).
Columns added to an existing table are added at startup (`add_missing_columns`): create_all never alters a table.
"""
from __future__ import annotations

import datetime as dt
import time

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text,
                        create_engine, event, inspect, text)
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
    Column('auto', Boolean, nullable=False, default=False),   # Auto mode: every gate decided by pipeline/auto.py rules
    Column('auto_note', Text),                  # Auto mode: why it stopped at a gate (null while it runs)
    Column('renamed_from', Text),
    Column('title', Text),                      # the clean title from topic understanding (the typed topic stays in topic)               # the slug made from the typed topic, before topic understanding renamed it
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
    Column('approved_by', Text, nullable=False),
    Column('approved_at', DateTime, nullable=False),
)


push_subscriptions = Table(
    'push_subscriptions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('endpoint', Text, nullable=False, unique=True),
    Column('p256dh', Text, nullable=False),
    Column('auth', Text, nullable=False),
    Column('created_at', DateTime, nullable=False),
    Column('last_ok_at', DateTime),
)

# Columns added to existing tables after their first release: name -> SQL type and default, per dialect.
ADDED_COLUMNS = {'episodes': {'auto': ('BOOLEAN', 'FALSE'), 'auto_note': ('TEXT', None), 'renamed_from': ('TEXT', None), 'title': ('TEXT', None)}}


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
            add_missing_columns(engine)
            return
        except (OperationalError, ProgrammingError, IntegrityError):
            if attempt == 4:
                raise
            time.sleep(1 + attempt)


def add_missing_columns(engine):
    """ALTER TABLE ... ADD COLUMN for the columns in ADDED_COLUMNS that an older database lacks (idempotent)."""
    insp = inspect(engine)
    for table, cols in ADDED_COLUMNS.items():
        have = {c['name'] for c in insp.get_columns(table)}
        for name, (sqltype, default) in cols.items():
            if name in have:
                continue
            dflt = f' NOT NULL DEFAULT {default}' if default is not None else ''
            if engine.dialect.name == 'sqlite' and default == 'FALSE':
                dflt = ' NOT NULL DEFAULT 0'
            try:
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sqltype}{dflt}'))
            except (OperationalError, ProgrammingError):     # added by the other process starting at the same time
                if name not in {c['name'] for c in inspect(engine).get_columns(table)}:
                    raise


def ping(engine) -> bool:
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return True
    except Exception:
        return False
