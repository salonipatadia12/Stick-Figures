"""Writes renders/review.html: the style board plus the voice auditions, for local review in a browser."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RENDERS = ROOT / 'renders'
LINE = 'Payday feels rich. So why are you broke a week later?'
CLIPS = [
    ('kokoro-af_heart.wav', 'Kokoro · af_heart', 'American female · Kokoro grade A · default candidate'),
    ('kokoro-am_michael.wav', 'Kokoro · am_michael', 'American male · grade C+'),
    ('kokoro-am_fenrir.wav', 'Kokoro · am_fenrir', 'American male · grade C+'),
    ('chatterbox-default.wav', 'Chatterbox · built-in voice', 'exaggeration 0.5, cfg 0.5'),
    ('chatterbox-expressive.wav', 'Chatterbox · built-in voice, expressive', 'exaggeration 0.7, cfg 0.3'),
]
rows = '\n'.join(f'<div class="clip"><b>{t}</b><span>{n}</span><audio controls preload="none" src="auditions/{f}"></audio></div>'
                 for f, t, n in CLIPS if (RENDERS / 'auditions' / f).exists())
html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Style board and voices</title>
<style>
:root {{ --bg:#faf6ee; --ink:#1e233c; --muted:#666; --card:#fff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16181f; --ink:#ece8df; --muted:#9a9a9a; --card:#21242d; }} }}
body {{ background:var(--bg); color:var(--ink); font:16px/1.5 -apple-system,system-ui,sans-serif; margin:0; padding:24px 16px; }}
main {{ max-width:1200px; margin:auto; }} img {{ max-width:100%; border-radius:6px; }}
.clip {{ background:var(--card); border-radius:8px; padding:12px 16px; margin:8px 0; display:grid; gap:4px; }}
.clip span {{ color:var(--muted); font-size:14px; }} audio {{ width:100%; }}
</style></head><body><main>
<h1>Style board and voice auditions</h1>
<h2>Voices: beat 1</h2><p>“{LINE}”</p>
{rows}
<h2>Style board: same 3 beats per engine (one seed each, not cherry-picked)</h2>
<img src="bakeoff/style-board.jpg" alt="Style board comparing The Empty Chair with each local engine">
</main></body></html>'''
(RENDERS / 'review.html').write_text(html)
print(RENDERS / 'review.html')
