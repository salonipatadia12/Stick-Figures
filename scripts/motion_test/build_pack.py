"""Build approved bases from their FLUX plates (docs/PLAN.md §7 D). Runs in .venvs/mlx (OpenCV).

  .venvs/mlx/bin/python scripts/motion_test/build_pack.py '<job json>' ...
  job: {"name", "source", "arms": [candidate plates], "empty": [candidate plates], "cast": [...], "moving": bool}

For each job: pick the arms plate that removed the most navy where the arms were and changed the least elsewhere,
pick the empty plate with the least character left, derive the spec (spec_from_plates.derive), build the layers
(build_bases.build) and print `BUILT <name> <arms plate> <empty plate>`. Failures print `FAILED <name>: reason`
and the run goes on with the next base. `pipeline/basebuild.py` drives this and records the results.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_bases as BB  # noqa: E402
import spec_from_plates as SP  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _navy(im: np.ndarray) -> np.ndarray:
    f = im.astype(np.int32)
    lum = f.mean(-1)
    return (lum < 95) & (f[..., 2] > f[..., 0] + 6)


def pick_arms(orig: np.ndarray, candidates: list) -> str:
    """The plate that removed the most navy inside the character's silhouette band while changing least outside."""
    best, best_score = None, None
    navy0 = _navy(orig)
    for c in candidates:
        try:
            gen = BB.register(orig, BB.load(Path(c)), np.ones(orig.shape[:2], bool))
        except cv2.error:
            gen = cv2.resize(BB.load(Path(c)), orig.shape[1::-1])
        removed = (navy0 & ~_navy(gen)).sum()
        diff = np.abs(orig.astype(np.float32) - gen.astype(np.float32)).mean(-1)
        collateral = (diff[~cv2.dilate(navy0.astype(np.uint8), BB.disk(12)).astype(bool)] > 40).sum()
        score = removed - 0.5 * collateral
        if best_score is None or score > best_score:
            best, best_score = c, score
    return best


def pick_empty(orig: np.ndarray, candidates: list) -> str:
    """The plate with the least white-and-navy character left (fewest bright low-saturation and navy pixels)."""
    best, best_score = None, None
    for c in candidates:
        gen = cv2.resize(BB.load(Path(c)), orig.shape[1::-1])
        hsv = cv2.cvtColor(gen, cv2.COLOR_RGB2HSV)
        white = (hsv[..., 1] < 40) & (gen.mean(-1) > 200)
        score = white.sum() + _navy(gen).sum()
        if best_score is None or score < best_score:
            best, best_score = c, score
    return best


def main(argv):
    for raw in argv:
        job = json.loads(raw)
        name = job['name']
        try:
            orig = BB.load(Path(job['source']), (941, 1672))
            arms = pick_arms(orig, job['arms']) if len(job['arms']) > 1 else job['arms'][0]
            empty = pick_empty(orig, job['empty']) if len(job['empty']) > 1 else job['empty'][0]
            spec = SP.derive(Path(job['source']), Path(arms), Path(empty), cast=job.get('cast', ['lead']), moving=job.get('moving', False))
            SP.debug_image(spec, name)
            # build_bases wants plate names relative to renders/bases/flux and the source as a path it can find
            spec['arms'], spec['empty'] = Path(arms).name, Path(empty).name
            spec['source'] = Path(job['source']).name
            BB.build(name, spec)
            print(f'BUILT {name} {Path(arms).name} {Path(empty).name}', flush=True)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f'FAILED {name}: {e}', flush=True)


if __name__ == '__main__':
    main(sys.argv[1:])
