"""FLUX.2 klein 4B (Apache-2.0, 8-bit MLX build) full-frame painted scenes for explainer inserts (docs/PLAN.md §7 H).

Runs in .venvs/mlx:  .venvs/mlx/bin/python scripts/motion_test/flux_scenes.py <jobs.json> <job names...>
A job: {"prompt": str, "seeds": [int, ...], "images": [paths]?}. With images it is an edit of those images (a
changed version of an existing scene: the same place at sunset, under frost); without, text-to-image.
Output renders/inserts/flux/<job>-s<seed>.png at 944x1680 (the bases' size). Every prompt is kept in
styles/cozy/inserts/flux-prompts.json for provenance.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'renders/inserts/flux'
MODEL = 'mflux-community/flux2-klein-4b-mflux-q8'
W, H = 944, 1680
PS = None


def paintsafe():
    global PS
    if PS is None:                             # pipeline/paintsafe.py by path: this env has no pydantic
        import importlib.util
        spec = importlib.util.spec_from_file_location('stick_paintsafe', ROOT / 'pipeline/paintsafe.py')
        PS = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(PS)
    return PS


def main(jobs_file: Path, names: list):
    from mflux.models.common.config import ModelConfig
    jobs = json.loads(jobs_file.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / 'styles/cozy/inserts/flux-prompts.json'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    import mlx.core as mx
    mx.set_cache_limit(2 * 1024 ** 3)
    edit_names = [n for n in names if jobs[n].get('images')]
    t2i_names = [n for n in names if not jobs[n].get('images')]
    for kind, todo in (('t2i', t2i_names), ('edit', edit_names)):
        if not todo:
            continue
        t0 = time.time()
        if kind == 't2i':
            from mflux.models.flux2.variants import Flux2Klein
            model = Flux2Klein(model_config=ModelConfig.flux2_klein_4b(), model_path=MODEL)
        else:
            from mflux.models.flux2.variants import Flux2KleinEdit
            model = Flux2KleinEdit(model_config=ModelConfig.flux2_klein_4b(), model_path=MODEL)
        print(f'{kind} model loaded in {time.time() - t0:.0f}s', flush=True)
        for name in todo:
            job = jobs[name]
            for seed in job['seeds']:
                ps = paintsafe()
                dst = ps.flux_png(OUT, name, seed)        # always <safe name>-s<seed>.png
                if ps.is_image(dst):
                    print('cached', dst.name)
                    continue
                dst.unlink(missing_ok=True)               # a broken file from an earlier run: paint it again
                t1 = time.time()
                kw = dict(seed=seed, prompt=job['prompt'], num_inference_steps=4, width=W, height=H)
                if kind == 'edit':
                    kw['image_paths'] = [str(p if Path(p).is_absolute() else ROOT / p) for p in job['images']]
                if ps.save_checked(lambda d: model.generate_image(**kw).save(str(d)), dst, log=lambda m: print(m, flush=True)):
                    print(f'{dst.name}: {time.time() - t1:.0f}s', flush=True)
            log[name] = {**job, 'model': MODEL, 'licence': 'apache-2.0', 'steps': 4, 'size': [W, H], 'mode': kind}
            log_path.write_text(json.dumps(log, indent=2) + '\n')
        del model


if __name__ == '__main__':
    main(Path(sys.argv[1]), sys.argv[2:])
