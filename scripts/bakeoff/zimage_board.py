"""Z-Image Turbo (Apache-2.0) via mflux/MLX: one style-board frame per process, low-RAM mode.

Uses the pre-quantized 8-bit weights so the full-precision 31 GB checkpoint never has to fit in 16 GB,
and frees the text encoder after the prompt is encoded. Run with .venvs/mlx/bin/python.
Usage: zimage_board.py SLUG OUT_DIR BEAT_ID MODEL_PATH TAG
"""
import json, resource, sys, time
from pathlib import Path

import mlx.core as mx
from mflux.callbacks.callback_manager import CallbackManager
from mflux.models.z_image.cli.z_image_turbo_generate import ZImageTurboCommand, build_parser

sys.path.insert(0, str(Path(__file__).parent))
import prompts

SLUG, OUT, BEAT, MODEL_PATH, TAG = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
W, H, SEED, STEPS = 768, 1344, 7, 9
OUT.mkdir(parents=True, exist_ok=True)
prompt = prompts.long_prompt(prompts.load(SLUG)[BEAT])

sys.argv = ['mflux-generate-z-image-turbo', '--model', MODEL_PATH, '--low-ram', '--steps', str(STEPS),
            '--seed', str(SEED), '--width', str(W), '--height', str(H), '--prompt', prompt]
args = build_parser().parse_args()
t0 = time.perf_counter()
model = ZImageTurboCommand.load(args)
CallbackManager.register_callbacks(args=args, model=model, latent_creator=ZImageTurboCommand.latent_creator)
load_s = time.perf_counter() - t0
t = time.perf_counter()
image = ZImageTurboCommand.generate(model, args, SEED, prompt)
gen_s = time.perf_counter() - t
image.save(path=OUT / f'{TAG}-{BEAT}.png')
stats = dict(engine=TAG, beat=BEAT, size=[W, H], steps=STEPS, seed=SEED, load_s=round(load_s, 1),
             generate_s=round(gen_s, 1), peak_mlx_gb=round(mx.get_peak_memory() / 1e9, 1),
             max_rss_gb=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 1))
(OUT / f'{TAG}-{BEAT}.json').write_text(json.dumps(stats, indent=2))
print(json.dumps(stats))
