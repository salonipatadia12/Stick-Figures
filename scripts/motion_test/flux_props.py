"""FLUX.2 klein 4B (Apache-2.0, 8-bit MLX build) text-to-image for single props (docs/PLAN.md §7 C).

Runs in .venvs/mlx:  .venvs/mlx/bin/python scripts/motion_test/flux_props.py <jobs.json> <job names...>
A job: {"prompt": str, "seeds": [int, ...], "size": int}. Output renders/props/flux/<job>-s<seed>.png.
Every prompt is kept in styles/cozy/props/flux-prompts.json for provenance.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'renders/props/flux'
MODEL = 'mflux-community/flux2-klein-4b-mflux-q8'


def main(jobs_file: Path, names: list):
    from mflux.models.common.config import ModelConfig
    from mflux.models.flux2.variants import Flux2Klein

    jobs = json.loads(jobs_file.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / 'styles/cozy/props/flux-prompts.json'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    t0 = time.time()
    import mlx.core as mx
    mx.set_cache_limit(2 * 1024 ** 3)
    model = Flux2Klein(model_config=ModelConfig.flux2_klein_4b(), model_path=MODEL)
    print(f'model loaded in {time.time() - t0:.0f}s', flush=True)
    for name in names:
        job = jobs[name]
        size = int(job.get('size', 512))
        for seed in job['seeds']:
            dst = OUT / f'{name}-s{seed}.png'
            if dst.exists():
                print('cached', dst.name)
                continue
            t1 = time.time()
            image = model.generate_image(seed=seed, prompt=job['prompt'], num_inference_steps=4, width=size, height=size)
            image.save(str(dst))
            print(f'{dst.name}: {time.time() - t1:.0f}s', flush=True)
        log[name] = {**job, 'model': MODEL, 'licence': 'apache-2.0', 'steps': 4, 'size': [size, size]}
        log_path.write_text(json.dumps(log, indent=2) + '\n')


if __name__ == '__main__':
    main(Path(sys.argv[1]), sys.argv[2:])
