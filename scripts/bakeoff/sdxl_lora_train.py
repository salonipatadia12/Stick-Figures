"""Train a small SDXL LoRA (CreativeML Open RAIL++-M base) on the 8 approved Empty Chair frames.

Memory plan for a 16 GB Mac: encode every image and caption once, free the VAE and text encoders,
then train LoRA adapters on a frozen bf16 UNet with gradient checkpointing.
Run with .venvs/torch/bin/python. Usage: sdxl_lora_train.py OUT_DIR STEPS WIDTH HEIGHT
"""
import json, random, sys, time
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image, ImageOps
from diffusers import AutoencoderKL, DDPMScheduler, StableDiffusionXLPipeline, UNet2DConditionModel
from diffusers.utils import convert_state_dict_to_diffusers
from peft import LoraConfig
from peft.utils import get_peft_model_state_dict
from transformers import CLIPTextModel, CLIPTextModelWithProjection, CLIPTokenizer

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'
MODEL = 'stabilityai/stable-diffusion-xl-base-1.0'
T = 'cozystick style illustration, '
CAPTIONS = {
    '01-alone': 'medium wide shot, white round-headed stick figure sitting alone at a round wooden cafe table drawing in a sketchbook, green coffee cup, orange shoulder bag on the empty chair',
    '02-question': 'medium wide shot, seated white round-headed stick figure looks up at a second stick figure with a teal scarf standing beside the empty chair with the orange bag',
    '02b-bag': 'medium wide shot, seated stick figure reaches for the orange bag on the chair while a smiling stick figure with a teal scarf stands beside it',
    '03-together': 'medium shot, two white round-headed stick figures seated at a round cafe table, one shows an open sketchbook to the other who wears a teal scarf, two green coffee cups',
    '04-waiting': 'medium wide shot, stick figure sitting alone at the cafe table with a sketchbook and pen, waiting, empty wooden chair beside',
    '05-disappointed': 'medium wide shot, sad stick figure sitting alone looking down at a closed sketchbook, orange bag back on the empty chair',
    '06-held-doodle': 'medium wide shot, stick figure with a teal scarf stands holding up a simple doodle, seated stick figure laughing happily, two green cups',
    '07-books-final': 'top-down view of a round wooden cafe table with two open sketchbooks side by side, two coffee cups, navy mitten hands, teal scarf at the edge',
}
OUT, STEPS, W, H = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
RANK, LR, SEED = 16, 1e-4, 7
dev = 'mps' if torch.backends.mps.is_available() else 'cpu'
OUT.mkdir(parents=True, exist_ok=True)
torch.manual_seed(SEED); random.seed(SEED)
log = lambda *a: print(*a, flush=True)


def fit(img):
    return ImageOps.fit(img.convert('RGB'), (W, H), Image.LANCZOS)


# 1. Encode images (and mirrored copies) to latent distributions.
t0 = time.perf_counter()
vae = AutoencoderKL.from_pretrained(MODEL, subfolder='vae', variant='fp16', torch_dtype=torch.float32).to(dev)
means, stds, caps = [], [], []
with torch.no_grad():
    for name, cap in CAPTIONS.items():
        base = fit(Image.open(ASSETS / f'{name}.png'))
        for img in (base, ImageOps.mirror(base)):
            x = torch.from_numpy(__import__('numpy').asarray(img)).permute(2, 0, 1)[None].float().div(127.5).sub(1).to(dev)
            dist = vae.encode(x).latent_dist
            means.append((dist.mean * vae.config.scaling_factor).cpu())
            stds.append((dist.std * vae.config.scaling_factor).cpu())
            caps.append(T + cap)
del vae

# 2. Encode captions with both CLIP encoders (SDXL convention: penultimate hidden states + pooled).
tok1 = CLIPTokenizer.from_pretrained(MODEL, subfolder='tokenizer')
tok2 = CLIPTokenizer.from_pretrained(MODEL, subfolder='tokenizer_2')
te1 = CLIPTextModel.from_pretrained(MODEL, subfolder='text_encoder', variant='fp16', torch_dtype=torch.float32)
te2 = CLIPTextModelWithProjection.from_pretrained(MODEL, subfolder='text_encoder_2', variant='fp16', torch_dtype=torch.float32)
embeds, pooled = [], []
with torch.no_grad():
    for cap in caps:
        ids1 = tok1(cap, padding='max_length', max_length=77, truncation=True, return_tensors='pt').input_ids
        ids2 = tok2(cap, padding='max_length', max_length=77, truncation=True, return_tensors='pt').input_ids
        o1 = te1(ids1, output_hidden_states=True)
        o2 = te2(ids2, output_hidden_states=True)
        embeds.append(torch.cat([o1.hidden_states[-2], o2.hidden_states[-2]], dim=-1))
        pooled.append(o2.text_embeds)
del te1, te2
log(f'encoded {len(caps)} samples at {W}x{H} in {time.perf_counter() - t0:.1f}s')

# 3. LoRA on a frozen bf16 UNet.
unet = UNet2DConditionModel.from_pretrained(MODEL, subfolder='unet', variant='fp16', torch_dtype=torch.bfloat16).to(dev)
unet.requires_grad_(False)
unet.enable_gradient_checkpointing()
unet.add_adapter(LoraConfig(r=RANK, lora_alpha=RANK, init_lora_weights='gaussian',
                            target_modules=['to_k', 'to_q', 'to_v', 'to_out.0']))
params = [p for p in unet.parameters() if p.requires_grad]
for p in params:
    p.data = p.data.float()
opt = torch.optim.AdamW(params, lr=LR, weight_decay=1e-2)
sched = DDPMScheduler.from_pretrained(MODEL, subfolder='scheduler')
time_ids = torch.tensor([[H, W, 0, 0, H, W]], dtype=torch.bfloat16, device=dev)
unet.train()
losses, t_train = [], time.perf_counter()
for step in range(1, STEPS + 1):
    i = random.randrange(len(caps))
    lat = means[i] + stds[i] * torch.randn_like(stds[i])
    noise = torch.randn_like(lat)
    t = torch.randint(0, sched.config.num_train_timesteps, (1,))
    noisy = sched.add_noise(lat, noise, t)
    pred = unet(noisy.to(dev, torch.bfloat16), t.to(dev), encoder_hidden_states=embeds[i].to(dev, torch.bfloat16),
                added_cond_kwargs={'text_embeds': pooled[i].to(dev, torch.bfloat16), 'time_ids': time_ids}).sample
    loss = F.mse_loss(pred.float(), noise.to(dev).float())
    loss.backward()
    torch.nn.utils.clip_grad_norm_(params, 1.0)
    opt.step(); opt.zero_grad(set_to_none=True)
    losses.append(loss.item())
    if step in (1, 5) or step % 25 == 0:
        el = time.perf_counter() - t_train
        log(f'step {step}/{STEPS} loss {sum(losses[-25:]) / len(losses[-25:]):.4f} {el / step:.2f}s/step')
    if step % 200 == 0 or step == STEPS:
        sd = {k: v.to(torch.float16) for k, v in convert_state_dict_to_diffusers(get_peft_model_state_dict(unet)).items()}
        StableDiffusionXLPipeline.save_lora_weights(OUT / f'step{step}', unet_lora_layers=sd, safe_serialization=True)

train_s = time.perf_counter() - t_train
(OUT / 'train.json').write_text(json.dumps(dict(
    base=MODEL, frames=list(CAPTIONS), samples=len(caps), size=[W, H], steps=STEPS, rank=RANK, lr=LR,
    train_seconds=round(train_s), seconds_per_step=round(train_s / STEPS, 2), trigger=T.strip(', '),
    final_loss_avg_50=round(sum(losses[-50:]) / len(losses[-50:]), 4)), indent=2))
log(f'done in {train_s / 60:.1f} min')
