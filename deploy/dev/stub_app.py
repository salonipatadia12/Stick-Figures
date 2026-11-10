"""A throwaway stand-in for webapp.app, ONLY for checking the Docker stack before lane B's web app exists:
/api/health with a real database round trip. Never deployed (deploy/dev/compose.stub.yaml mounts it)."""
import os

import psycopg
from fastapi import FastAPI

app = FastAPI()


@app.get('/api/health')
def health():
    url = os.environ.get('DATABASE_URL', '').replace('postgresql+psycopg://', 'postgresql://')
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            db = conn.execute('select 1').fetchone()[0] == 1
    except Exception:
        db = False
    return dict(ok=db, db=db, worker=dict(online=False, last_seen=None, busy=None), stub=True)
