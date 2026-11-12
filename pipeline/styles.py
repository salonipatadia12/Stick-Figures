"""Style packs: styles/<name>/style.json plus the files it references."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, STYLES_DIR


@dataclass
class Style:
    name: str
    title: str
    folder: Path
    cast: list = field(default_factory=list)
    pacing: dict = field(default_factory=dict)
    reference: dict = field(default_factory=dict)
    script_brief: str = ''
    voice: dict = field(default_factory=dict)
    status: str = 'ready'          # 'direction': the writer gets the brief, but only cozy has painted art to render with
    status_note: str = ''
    writer_note: str = ''          # a short look note for the local writer's beat stage (cozy has none: prompts unchanged)
    audio: dict = field(default_factory=dict)

    @property
    def render_ready(self) -> bool:
        return self.status == 'ready'

    @property
    def music(self) -> bool:
        """The style's default for the soft music bed (off unless the pack turns it on)."""
        return bool(self.audio.get('music', False))

    @property
    def cast_ids(self):
        return [c['id'] for c in self.cast]

    @property
    def words_per_second(self):
        return float(self.pacing.get('words_per_second', 2.6))

    @property
    def default_voice(self):
        return self.voice.get('default', 'am_michael')

    @property
    def voice_options(self):
        return [self.default_voice, *self.voice.get('alternates', [])]

    def resolve_voice(self, voice_id: str = '') -> str:
        """The narrator for an episode: its own voice_id, else the style default."""
        return voice_id or self.default_voice

    def reference_paths(self):
        paths = [self.reference.get('video'), self.reference.get('storyboard'), *self.reference.get('frames', [])]
        return [ROOT / p for p in paths if p]


def available():
    return sorted(p.parent.name for p in STYLES_DIR.glob('*/style.json'))


def load(name):
    folder = STYLES_DIR / name
    spec_path = folder / 'style.json'
    if not spec_path.is_file():
        raise ValueError(f'Unknown style "{name}". Available: {", ".join(available()) or "none"}')
    spec = json.loads(spec_path.read_text())
    brief = (folder / spec['script_brief']).read_text() if spec.get('script_brief') else ''
    return Style(name=spec['name'], title=spec.get('title', name), folder=folder, cast=spec.get('cast', []),
                 pacing=spec.get('pacing', {}), reference=spec.get('reference', {}), script_brief=brief,
                 voice=spec.get('voice', {}), status=spec.get('status', 'ready'),
                 status_note=spec.get('status_note', ''), writer_note=spec.get('writer_note', ''),
                 audio=spec.get('audio', {}))
