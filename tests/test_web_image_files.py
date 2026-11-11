"""The web image is built from an allow-list (deploy/Dockerfile.web.dockerignore). Every file the web app reads at
runtime from styles/ must be on it, or the episode page 500s on the VPS (2026-11-11: styles/cozy/script_brief.md
was left out and the script view crashed with FileNotFoundError)."""
import json
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
IGNORE = ROOT / 'deploy' / 'Dockerfile.web.dockerignore'


def _rules():
    out = []
    for line in IGNORE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        neg = line.startswith('!')
        out.append((neg, line[1:] if neg else line))
    return out


def in_image(rel: str) -> bool:
    """Docker semantics, close enough for this allow-list: rules apply in order and the last match wins;
    a bare '*' excludes everything; a pattern ending in '/' covers the whole folder."""
    p = PurePosixPath(rel)
    included = True
    for neg, pat in _rules():
        if pat == '*':
            hit = True
        elif pat.endswith('/'):
            hit = rel.startswith(pat)
        else:
            hit = p.full_match(pat) or any(q.full_match(pat) for q in p.parents if str(q) != '.')
        if hit:
            included = neg
    return included


class WebImageFilesTest(unittest.TestCase):
    def test_style_files_the_app_reads_are_in_the_image(self):
        styles = [d for d in (ROOT / 'styles').iterdir() if (d / 'style.json').is_file()]
        self.assertTrue(styles)
        for d in styles:
            spec = json.loads((d / 'style.json').read_text())
            needed = [d / 'style.json'] + ([d / spec['script_brief']] if spec.get('script_brief') else [])
            for f in needed:
                rel = f.relative_to(ROOT).as_posix()
                with self.subTest(file=rel):
                    self.assertTrue(f.is_file(), rel)
                    self.assertTrue(in_image(rel), f'{rel} is read by pipeline/styles.py but not in the web image')

    def test_secrets_and_media_stay_out(self):
        for rel in ('.env', 'episodes/x/episode.json', 'styles/cozy/host/a.png', '.git/config'):
            with self.subTest(file=rel):
                self.assertFalse(in_image(rel))


if __name__ == '__main__':
    unittest.main()
