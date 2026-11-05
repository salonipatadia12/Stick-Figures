# Local models

Images and narration run on open-source models on this Mac. The Claude script call is the only
paid step. Only models whose licences allow commercial use are allowed. That rules out FLUX "dev"
licences, F5-TTS and XTTS.

## Hardware (checked 2026-11-05)

| | |
|---|---|
| Machine | MacBook Pro (Mac17,2), macOS 26.5.2 |
| Chip | Apple M5: 10-core CPU (4 performance, 6 efficiency), 10-core GPU, Metal 4 |
| Memory | 16 GB unified |
| Free disk | 77 GB before the bake-off; about 28 GB after the model downloads |

Memory is the limit. Each image engine has to fit, alongside macOS and the editor, in about 10 GB.
Engines run one at a time, never in parallel.

## What this Mac can run

| Engine | Use | Runs here? | Measured |
|---|---|---|---|
| Z-Image Turbo, 8-bit, mflux/MLX | text-to-image | **Yes**, in low-RAM mode with pre-quantized weights | 60–73 s per 768×1344 image, peak 7.5 GB |
| SDXL base 1.0 + cozy LoRA, diffusers/MPS | text-to-image locked to the Empty Chair style | **Yes** | see the bake-off results below |
| SDXL LoRA training, diffusers + peft/MPS | one-off per style | **Yes, slowly** | about 12 s per step at 768×1344, so about 2 h for 600 steps |
| Qwen-Image-Edit-2511, mflux/MLX | reference consistency, pose variants | **No** | see the note below |
| Kokoro-82M, PyTorch/CPU | narration | **Yes**, faster than real time | 1.3–2.0 s per 3.7–4.3 s clip; loads in 3 s |
| Chatterbox 0.1.7, PyTorch/MPS | expressive narration and voice cloning | **Yes**, slower than real time | 9–16 s per 3 s clip; loads in 11 s |
| faster-whisper small.en, CTranslate2/CPU | word timings and checking that every word was spoken | **Yes** | a few seconds per clip |

**Qwen-Image-Edit** pairs a 20B image model with a 7B Qwen2.5-VL text encoder.
- mflux keeps that text encoder in bf16: about 15.5 GB for edits on its own, before the image model.
- At 4-bit the download is about 25 GB, and mflux's README warns that 6-bit or lower degrades edits
  noticeably.
- So it can't run in 16 GB without heavy swapping, and swapping is exactly what made the first
  (full-precision) Z-Image attempt hit a Metal GPU timeout.
- It needs a machine with more memory.

## Licences

Each licence was checked on the model's Hugging Face card or the package metadata on 2026-11-05.

### Models

| Model | Licence | Commercial use |
|---|---|---|
| [Tongyi-MAI/Z-Image-Turbo](https://huggingface.co/Tongyi-MAI/Z-Image-Turbo), plus the [mflux-community 8-bit conversion](https://huggingface.co/mflux-community/z-image-turbo-mflux-q8) | Apache-2.0 | Yes |
| [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md) | CreativeML Open RAIL++-M | Yes. See the note after this table. |
| [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | Apache-2.0 | Yes (but it doesn't run here) |
| [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 | Yes |
| [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) | MIT | Yes |
| [Systran/faster-whisper-small.en](https://huggingface.co/Systran/faster-whisper-small.en) | MIT | Yes |

The SDXL licence grants a royalty-free licence to use, reproduce and distribute the model, and
Stability "claims no rights in the Output". It carries use restrictions (Attachment A): no illegal
use, no harm to minors, no defamation, no harmful misinformation, no medical advice, and so on.
Those restrictions must be passed on if the model or LoRA is ever distributed.

### Libraries

| Library | Licence |
|---|---|
| mflux 0.24.0 | MIT |
| MLX 0.32.4 | MIT |
| PyTorch 2.15.0 | BSD-style |
| diffusers 0.42.1, transformers 5.22.1, peft 0.21.2 | Apache-2.0 |
| kokoro 0.9.4, misaki 0.9.4 | Apache-2.0 |
| spaCy 3.8.18, en_core_web_sm 3.8.0 | MIT |
| faster-whisper 1.2.1, CTranslate2 | MIT |
| chatterbox-tts 0.1.7, resemble-perth 1.0.1 | MIT |

Notes on the libraries:
- **espeak-ng 1.52 (Homebrew)** is GPL-3.0. Kokoro's phonemizer calls it for words that aren't in
  its dictionary. It runs as a separate local program. The GPL covers distributing that program, not
  the audio we make with it.
- **Chatterbox's maintainers retired it in August 2026.** The code and weights stay MIT, but there
  will be no fixes. Every file it generates carries an inaudible Perth watermark, which marks the
  audio as AI-generated.
- **Training data:** the cozy LoRA is trained only on the 8 approved Empty Chair frames from this
  repository.

## Environments

Each engine has its own virtual environment under `.venvs/`, which is git-ignored, because their
pinned dependencies conflict. Weights live in the shared Hugging Face cache (`~/.cache/huggingface`).

```sh
uv venv -p 3.12 .venvs/mlx    && uv pip install -p .venvs/mlx/bin/python mflux huggingface_hub
uv venv -p 3.12 .venvs/torch  && uv pip install -p .venvs/torch/bin/python torch diffusers transformers accelerate peft safetensors "kokoro>=0.9.4" soundfile pillow faster-whisper \
  "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
uv venv -p 3.11 .venvs/chatterbox && uv pip install -p .venvs/chatterbox/bin/python chatterbox-tts soundfile "setuptools<81"
brew install espeak-ng ffmpeg
```

Two setup gotchas:
- **Chatterbox's watermarker** imports `pkg_resources`, which uv environments don't include.
  Hence `setuptools<81`.
- **Kokoro's English phonemizer** needs spaCy's `en_core_web_sm`. It can't download that itself
  inside a uv environment, so it's installed explicitly.

## Bake-off (Phase 2)

The scripts are in `scripts/bakeoff/`; the outputs go to `renders/` (git-ignored).

Each engine renders the same 3 beats of the salary episode:
- b01, the hook
- b06, a mid beat with both characters
- b09, the ending

Each runs at 768×1344 with seed 7, one image per beat and no cherry-picking. The prompts are built
from `episode.json`, `styles/cozy/style_lock.txt` and the cast list, by `scripts/bakeoff/prompts.py`.

### Results (2026-11-05)

Board: `renders/bakeoff/style-board.jpg`. Review page with the voice clips: `renders/review.html`.

| | Z-Image Turbo, 8-bit (MLX) | SDXL + cozy LoRA (MPS) |
|---|---|---|
| Time per image (768×1344) | 71 / 73 / 60 s | 78 / 127 / 147 s (slowed by swapping; see below) |
| Peak memory | 7.5 GB | the Metal driver reached 18.4 GB, more than the 16 GB of RAM |
| Empty Chair look (painted, golden light, foreground plants) | Weak: a flatter vector-cartoon finish | Strong: very close to the reference |
| Character model (round head, navy limbs, eyebrows) | Consistent across frames but off-model: white chibi bodies | b01 is on-model; b06 and b09 drift (a solid navy figure, an extra third figure) |
| Follows the beat (wallet, calendar, receipt, jars) | Good, except that b06 drops the lead character | Weak: the LoRA pulls every scene back toward its sketchbook-at-the-table training frames |

Notes:
- **The cozy LoRA** was trained on the 8 approved frames plus mirrored copies (16 samples), at 576×1024, rank 16, for 500 steps. That took 21.7 min at 2.6 s per step.
- **The first attempt at 768×1344** ran at 24 s per step and was stopped. Memory pressure from other apps pushed it into swapping.
- **SDXL's prompts** are cut at 77 tokens (a CLIP limit), so the scene details at the end of each prompt are lost.
