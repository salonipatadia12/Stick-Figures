"""The pronunciation lexicon (user 2026-11-19: "add data/pronunciations.json (word -> spoken respelling or phonemes for
Kokoro ...). The voice uses the respelling; captions and on-screen text keep the real spelling").

    entries_for(text)        -> {word as written in the text: entry}   the lexicon words a line says
    marked(text, phonemes)   -> the text Kokoro reads: each such word as a misaki link "[kerogen](/kˈɛɹəʤən/)", so
                                the voice says the phonemes and its word timings (the captions) keep "kerogen"
    add(word, say, source)   -> the self-repair adds a respelling (pipeline/repair.py)

No imports beyond the standard library: the voice runs in the torch venv (scripts/motion_test/voice.py).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / 'data' / 'pronunciations.json'
RESPELLING = re.compile(r"^[A-Za-z]+(?:-[A-Za-z]+)*$")


def load(path: Path | None = None) -> dict:
    """{word (lowercase): entry}; {} when the file is missing or broken."""
    try:
        data = json.loads(Path(path or PATH).read_text())
    except (OSError, ValueError):
        return {}
    return {k.lower(): v for k, v in (data.get('words') or {}).items() if isinstance(v, dict) and (v.get('say') or v.get('phonemes'))}


def entries_for(text: str, lexicon: dict | None = None) -> dict:
    """{the word as the text writes it: its entry} for every lexicon word the text says (whole words)."""
    lex = load() if lexicon is None else lexicon
    out = {}
    for m in re.finditer(r"[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]", text or ''):
        w = m.group(0)
        for cand in (w, w[:-2] if w.lower().endswith("'s") else w):
            if cand.lower() in lex:
                out[cand] = lex[cand.lower()]
                break
    return out


def stamp(text: str, lexicon: dict | None = None) -> str:
    """What a take depends on: the lexicon entries its line uses ('' when none), for the voice cache."""
    got = entries_for(text, lexicon)
    return '; '.join(f'{w}={e.get("phonemes") or e.get("say")}' for w, e in sorted(got.items()))


def valid_say(say: str) -> bool:
    """A respelling: letters in syllables joined by hyphens, the stressed syllable in capitals ("KAIR-uh-jen")."""
    say = (say or '').strip()
    if not RESPELLING.match(say):
        return False
    parts = say.split('-')
    caps = [x for x in parts if x.isupper()]
    return bool(caps) and (len(caps) < len(parts) or len(parts) == 1)


# respelling conventions ("KAIR-uh-jen") -> Kokoro's phonemes (misaki: A = ay, I = eye, O = oh, W = ow, Y = oy)
VOWELS = [('eye', 'I'), ('igh', 'I'), ('air', 'ɛɹ'), ('are', 'ɛɹ'), ('ear', 'ɪɹ'), ('eer', 'ɪɹ'), ('ar', 'ɑɹ'),
          ('or', 'ɔɹ'), ('ur', 'ɜɹ'), ('er', 'ɜɹ'), ('ir', 'ɜɹ'), ('ay', 'A'), ('ai', 'A'), ('ee', 'i'), ('ea', 'i'),
          ('oh', 'O'), ('oa', 'O'), ('oo', 'u'), ('ow', 'W'), ('ou', 'W'), ('oy', 'Y'), ('oi', 'Y'), ('aw', 'ɔ'),
          ('au', 'ɔ'), ('ah', 'ɑ'), ('uh', 'ə'), ('a', 'æ'), ('e', 'ɛ'), ('i', 'ɪ'), ('o', 'ɑ'), ('u', 'ʌ')]
CONSONANTS = [('tch', 'ʧ'), ('th', 'θ'), ('dh', 'ð'), ('sh', 'ʃ'), ('zh', 'ʒ'), ('ch', 'ʧ'), ('ng', 'ŋ'), ('ck', 'k'),
              ('ph', 'f'), ('j', 'ʤ'), ('r', 'ɹ'), ('c', 'k'), ('g', 'ɡ'), ('x', 'ks'), ('q', 'k'), ('y', 'j')]


def from_say(say: str, g2p=None) -> str:
    """Kokoro phonemes from a respelling, by its conventions (no model): "KAIR-uh-jen" -> "kˈɛɹəʤɛn". The
    syllable in capitals takes the primary stress; an unstressed "uh" is a schwa."""
    parts = say.split('-')
    out = []
    for syl in parts:
        stressed = syl.isupper() and len(parts) > 1
        w, ph = syl.lower(), ''
        k = 0
        while k < len(w):
            for table in (VOWELS if w[k] in 'aeiouy' and not (w[k] == 'y' and k == 0) else CONSONANTS, CONSONANTS):
                hit = next(((a, b) for a, b in table if w.startswith(a, k)), None)
                if hit:
                    a, b = hit
                    if a == 'y' and k == len(w) - 1 and k > 0:   # a final y: "eye" alone ("HY", "by"), else "ee"
                        b = 'I' if not re.search('[aeiou]', w[:k]) else 'i'
                    ph += 'ʌ' if (a == 'uh' and stressed) else b
                    k += len(a)
                    break
            else:
                ph += w[k]
                k += 1
        onset = re.match(r"[^æɛɪɑʌəAIOWYiuɔɜ]*", ph).group(0)
        out.append(onset + 'ˈ' + ph[len(onset):] if stressed else ph)
    return ''.join(out)


def marked(text: str, phonemes_of, lexicon: dict | None = None) -> str:
    """The text with each lexicon word as a misaki pronunciation link; `phonemes_of(entry)` gives its phonemes."""
    got = entries_for(text, lexicon)
    for w, e in sorted(got.items(), key=lambda kv: -len(kv[0])):
        ph = e.get('phonemes') or phonemes_of(e)
        if not ph:
            continue
        text = re.sub(rf"(?<![\w\[-]){re.escape(w)}(?![\w\]-])", f'[{w}](/{ph}/)', text)
    return text


def add(word: str, say: str, source: str, path: Path | None = None) -> bool:
    """Add (or replace) a respelling; False when it is not a respelling."""
    if not word or not valid_say(say):
        return False
    p = Path(path or PATH)
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        data = {}
    words = data.setdefault('words', {})
    words[word.lower()] = dict(say=say, source=f'auto: {source} ({time.strftime("%Y-%m-%d")})')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    return True
