"""Atomic project records; a single-owner, single-worker deployment."""
import json
import re
from pathlib import Path
from threading import RLock
import uuid
from datetime import datetime, timezone

BUSY = {'drafting', 'illustrating', 'rendering'}


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()

    def folder(self, identifier):
        if not re.fullmatch(r'[a-f0-9]{32}', identifier):
            raise FileNotFoundError('Project not found')
        return self.root / identifier

    def load(self, identifier):
        return json.loads((self.folder(identifier)/'project.json').read_text())

    def save(self, project):
        folder = self.folder(project['id']); folder.mkdir(exist_ok=True)
        project['updated_at'] = datetime.now(timezone.utc).isoformat()
        path = folder/'project.json'; temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(project, indent=2, ensure_ascii=False)); temp.replace(path)
        return project

    def create(self, brief, story=None):
        return self.save(dict(id=uuid.uuid4().hex, brief=brief, story=story, status='draft',
            progress='Ready to write your story.', error=None, story_approval=None,
            storyboard_approval=None, assets={}, audio={}, video=None, captions=None))

    def all(self):
        result=[]
        for path in self.root.glob('*/project.json'):
            try: result.append(json.loads(path.read_text()))
            except (ValueError, OSError): continue
        return sorted(result, key=lambda p:p.get('updated_at',''), reverse=True)

    def recover(self):
        for p in self.all():
            if p['status'] in BUSY:
                p.update(status='error', error='The server restarted during this job. Review the saved work and retry explicitly.', progress='Interrupted; no automatic paid retry.')
                self.save(p)
