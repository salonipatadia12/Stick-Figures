"""SDXL base 1.0 (CreativeML Open RAIL++-M) + the cozy LoRA via diffusers on MPS: the style-board frames.
Run with .venvs/torch/bin/python. Usage: sdxl_board.py SLUG OUT_DIR BEATS LORA_DIR TAG [LORA_SCALE]
"""
import json, sys, time
from pathlib import Path

import torch
from diffusers import DPMSolverMultistepScheduler, StableDiffusionXLPipeline

sys.path.insert(0, str(Path(__file__).parent))
import prompts

SLUG, OUT, BEATS, LORA, TAG = sys.argv[1], Path(sys.argv[2]), sys.argv[3].split(','), sys.argv[4], sys.argv[5]
SCALE = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
W, H, SEED, STEPS, CFG = 768, 1344, 7, 30, 6.0
OUT.mkdir(parents=True, exist_ok=True)
beats = prompts.load(SLUG)

t0 = time.perf_counter()
pipe = StableDiffusionXLPipeline.from_pretrained('stabilityai/stable-diffusion-xl-base-1.0', variant='fp16',
                                                 torch_dtype=torch.float16).to('mps')
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
if LORA != 'none':
    pipe.load_lora_weights(LORA)
    pipe.fuse_lora(lora_scale=SCALE)
pipe.set_progress_bar_config(disable=True)
load_s = time.perf_counter() - t0
timings = {}
for beat_id in BEATS:
    t = time.perf_counter()
    image = pipe(prompt=prompts.short_prompt(beats[beat_id]), negative_prompt=prompts.SDXL_NEGATIVE,
                 width=W, height=H, num_inference_steps=STEPS, guidance_scale=CFG,
                 generator=torch.Generator('cpu').manual_seed(SEED)).images[0]
    image.save(OUT / f'{TAG}-{beat_id}.png')
    timings[beat_id] = round(time.perf_counter() - t, 1)
    print(f'{beat_id}: {timings[beat_id]}s', flush=True)
stats = dict(engine=TAG, lora=LORA, lora_scale=SCALE, size=[W, H], steps=STEPS, cfg=CFG, seed=SEED,
             load_s=round(load_s, 1), per_image_s=timings,
             peak_mps_gb=round(torch.mps.driver_allocated_memory() / 1e9, 1))
(OUT / f'{TAG}-timings.json').write_text(json.dumps(stats, indent=2))
print(json.dumps(stats))
