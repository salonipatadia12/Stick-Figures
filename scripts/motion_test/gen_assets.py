"""SDXL base 1.0 + the cozy LoRA: EMPTY café backgrounds (no people) and loose props for the motion test.
Characters are never generated here; they are cut-out rigs (pipeline/rig).
Run with .venvs/torch/bin/python. Usage: gen_assets.py OUT_DIR LORA_DIR [ITEM ...]
"""
import json, sys, time
from pathlib import Path

import torch
from diffusers import DPMSolverMultistepScheduler, StableDiffusionXLPipeline

OUT, LORA = Path(sys.argv[1]), sys.argv[2]
ONLY = set(sys.argv[3:])
STEPS, CFG = 30, 6.0
NO_PEOPLE = ('people, person, character, figure, stick figure, man, woman, child, face, head, eyes, hands, arms, body, '
             'crowd, text, letters, words, numbers, watermark, caption, photo, photorealistic, 3d render, blurry, lowres')
# CLIP reads only the first 77 tokens, so the subject comes first and each prompt stays short.
ITEMS = {
    'bg-cafe': dict(size=(768, 1344), lora=0.75, seeds=[11, 23, 37, 41], prompt=(
        'cozystick style illustration, empty sunlit cafe interior, nobody, cream plaster wall with ivy and a small framed '
        'picture, terracotta tile floor in the lower third, small round wooden table on the right, tall window with golden '
        'light, eye-level view, painterly')),
    'prop-wallet': dict(size=(1024, 1024), lora=0.6, seeds=[5, 8, 13], prompt=(
        'cozystick style illustration, a single open brown leather bifold wallet, empty, front view, centered, '
        'isolated on a plain flat white background, simple painted object, no shadow')),
    'prop-coin': dict(size=(1024, 1024), lora=0.6, seeds=[3, 9], prompt=(
        'cozystick style illustration, a single shiny gold coin, front view, centered, isolated on a plain flat white '
        'background, simple painted object, no shadow')),
    # Round 2 (2026-11-05 review): a furniture-free set, furniture as layers, painterly props.
    'bg-room': dict(size=(768, 1344), lora=0.75, seeds=[11, 29, 47], prompt=(
        'cozystick style illustration, empty sunlit cafe interior, nobody, cream plaster back wall with ivy and a small '
        'framed picture, tall window on the left, terracotta tile floor, open empty floor in the middle, eye-level view, '
        'painterly'), negative='table, chair, furniture in the middle, '),
    'prop-table': dict(size=(1024, 768), lora=0.6, seeds=[2, 6], prompt=(
        'cozystick style illustration, a single round wooden cafe table with one dark iron pedestal and round base, '
        'seen from the front at eye level, isolated on a plain flat white background, painted object')),
    'prop-chair': dict(size=(768, 1024), lora=0.6, seeds=[4, 12], prompt=(
        'cozystick style illustration, a single wooden cafe chair with a spindle back, front view facing the viewer, '
        'isolated on a plain flat white background, painted object')),
    'prop-wallet-empty': dict(size=(768, 768), lora=0.6, seeds=[21, 34], prompt=(
        'cozystick style illustration, an open empty brown leather wallet, completely empty, nothing inside, no cards, '
        'no money, front view, isolated on a plain flat white background, painted object'),
        negative='cards, credit card, money, banknote, bills, cash, coins, '),
    'prop-bill': dict(size=(1024, 768), lora=0.6, seeds=[7], prompt=(
        'cozystick style illustration, a single green paper banknote lying flat, front view, plain ornament, '
        'isolated on a plain flat white background, painted object')),
    'prop-jar': dict(size=(768, 768), lora=0.6, seeds=[3], prompt=(
        'cozystick style illustration, a single empty clear glass jar with a wide open mouth, front view, isolated on a '
        'plain flat white background, painted object')),
    'prop-calendar': dict(size=(768, 1024), lora=0.6, seeds=[5], prompt=(
        'cozystick style illustration, a paper wall calendar with a red top band and an empty grid of blank square '
        'boxes, front view, isolated on a plain flat white background, painted object')),
    'prop-moth': dict(size=(768, 768), lora=0.6, seeds=[8], prompt=(
        'cozystick style illustration, a single small dusty brown moth with spread wings, top view, isolated on a plain '
        'flat white background, painted object')),
    # Retries with stricter prompts (the first jar came inside a kitchen; the first note copied real currency).
    'prop-jar2': dict(size=(768, 768), lora=0.45, seeds=[17, 40], prompt=(
        'cozystick style illustration, one empty clear glass jar, no lid, simple object illustration, centered, '
        'isolated on a plain flat white background'), negative='room, kitchen, table, window, shelf, scenery, background, '),
    'prop-bill2': dict(size=(1024, 640), lora=0.45, seeds=[19, 52], prompt=(
        'cozystick style illustration, one plain green paper money note with a simple oval leaf ornament in the middle, '
        'blank, centered, isolated on a plain flat white background'),
        negative='portrait, face, president, person, text, letters, numbers, words, seal, ')
}
OUT.mkdir(parents=True, exist_ok=True)
t0 = time.perf_counter()
pipe = StableDiffusionXLPipeline.from_pretrained('stabilityai/stable-diffusion-xl-base-1.0', variant='fp16',
                                                 torch_dtype=torch.float16).to('mps')
pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
pipe.load_lora_weights(LORA, adapter_name='cozy')
pipe.set_progress_bar_config(disable=True)
print(f'loaded in {time.perf_counter() - t0:.0f}s', flush=True)
log = {}
for name, spec in ITEMS.items():
    if ONLY and name not in ONLY:
        continue
    pipe.set_adapters(['cozy'], adapter_weights=[spec['lora']])
    for seed in spec['seeds']:
        path = OUT / f'{name}-s{seed}.png'
        if path.exists():
            continue
        t = time.perf_counter()
        w, h = spec['size']
        image = pipe(prompt=spec['prompt'], negative_prompt=spec.get('negative', '') + NO_PEOPLE, width=w, height=h, num_inference_steps=STEPS,
                     guidance_scale=CFG, generator=torch.Generator('cpu').manual_seed(seed)).images[0]
        image.save(path)
        log[path.name] = dict(seconds=round(time.perf_counter() - t, 1), seed=seed, lora_scale=spec['lora'],
                              prompt=spec['prompt'])
        print(f'{path.name}: {log[path.name]["seconds"]}s', flush=True)
        (OUT / 'gen-log.json').write_text(json.dumps(log, indent=2))
