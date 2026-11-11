# Local models

Images, narration, research and scripts run on open-source models on this Mac (scripts since 2026-11-10, when the
Anthropic credits ran out: see "Scripts: Ollama + Qwen 2.5" below). Nothing in the pipeline is paid. Only models whose
licences allow commercial use are allowed. That rules out FLUX "dev" licences, F5-TTS and XTTS.

## Scripts: Ollama + Qwen 2.5 (2026-11-10)

Research, the three storylines and the script are written by a local model through [Ollama](https://ollama.com)
(`SCRIPT_PROVIDER=ollama`, the default). The earlier Claude path stays behind `SCRIPT_PROVIDER=anthropic`.

| | |
|---|---|
| Runtime | Ollama 0.32.9, Homebrew service (`brew services start ollama`), `http://localhost:11434`; starts at login. The service sets `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`. |
| Model on this Mac (16 GB) | `qwen2.5:14b`, Q4_K_M, 9.0 GB download, about 9.1 GB in memory at a 4096-token context. Measured: ~25 s cold load, ~13 tokens/s out, a few hundred tokens/s in. |
| Machines with 32 GB or more | `qwen2.5:32b` (set `OLLAMA_MODEL=qwen2.5:32b`) |
| Licence | [Qwen2.5-14B-Instruct](https://huggingface.co/Qwen/Qwen2.5-14B-Instruct) and 32B: Apache-2.0, commercial use allowed. (The 3B and 72B Qwen 2.5 models have other licences; don't use them.) |
| Setup, once | `brew install ollama && brew services start ollama && ollama pull qwen2.5:14b` |
| Search | DuckDuckGo through [`ddgs`](https://pypi.org/project/ddgs/) (MIT; the package formerly called `duckduckgo-search`), no key or account. If DuckDuckGo refuses (rate limit or bot check) ddgs's other keyless engines are tried, then two open indexes: the reference links of the matching Wikipedia articles (Wikipedia itself is never cited) and OpenAlex (peer-reviewed abstracts, cited by DOI). |
| Page text | [trafilatura](https://pypi.org/project/trafilatura/) 2.3 (Apache-2.0) |

**Memory next to FLUX.** FLUX.2 klein peaks at about 11 GB and runs right after the script step, so the model must be
out of memory by then:
- every call uses a 4096-token context (`OLLAMA_NUM_CTX`); a call whose prompt and answer would not fit gets 8192 for
  that call only (Ollama reloads the model when the window changes, so this is the exception);
- at most `OLLAMA_PARALLEL` (2) calls are sent at once (per-beat staging, fact reading); the server queues the rest;
- each step (`story.pitch`, `script.generate`) ends with an explicit unload (`keep_alive: 0`), so the 9 GB are free
  before FLUX starts. `OLLAMA_UNLOAD=0` keeps the model loaded instead.

**How a 14B model keeps the editorial bar.** The rules are unchanged; the writer is built so a smaller model can meet
them (`pipeline/script.py`, `write_free`):
1. Two stages. A short outline call (story, hook options, per beat the role, narration, on-screen text, picture type
   and cited fact ids) with `SCRIPT_RULES_COMPACT_OUTLINE`, then one short call per beat for its staging, two at a
   time, with `SCRIPT_RULES_COMPACT_BEAT`. Together the compact rules are the same 17 rules in about 1,100 words.
2. Constrained output. Ollama's JSON-schema `format` limits every field to its allowed values: fact ids to the
   research ids, recipes, verbs, poses, settings, effects and sound names to the library, and every anchor to the
   words of that beat's narration, so an anchor can never miss.
3. The real checker, then repairs. The draft goes through `Episode.check()`. Outline-level errors (story shape, hook,
   unsourced numbers) go back to the outline call, beat errors to that beat's call only, at most three rounds; what
   still fails is shown at the script gate, and `--auto` refuses it.
4. Facts are quoted. The model reads the passages of each fetched page and returns at most two facts with the
   sentence they came from; the sentence must be on the page, every number in the fact must be in it, and the fact
   keeps its page URL. The source rule (government, university, peer-reviewed) is the same `keep_reputable`.

**Measured (2026-11-10, "Why sunsets are red", 45 s, M5 16 GB with 11-19 GB of swap in use by other apps).**
DuckDuckGo answered this machine with its bot check (HTTP 202) all day, so the results came from ddgs's other keyless
engines plus the open indexes. First `stick make`: research 192 s (6 searches, 16 reputable pages, 15 with text, 2
facts), storylines 57 s, 250 s in all. With the improved fact reader: research 81 s (5 facts; searches and pages from
the cache), storylines 58 + 56 s (one repair: a 9-word hook). Script (test episode): outline 112 s, two outline repairs
173 s, ten beats staged 259 s (about 26 s a beat, two at a time), no beat repairs needed, 544 s in all; `stick validate`
0 errors, 12 warnings (mostly the 2.5 s static-stretch warning). Writer v2 then asks longer beats for word-anchored
actions and effects (the outline came from the model cache): staging 606 s (bigger answers, and the machine was busy),
0 errors, 10 warnings, with shorter static stretches. Re-runs: research, storylines and script from the cache
in under a second; `--regenerate script` answered all 13 model calls from the model cache in 0.1 s. Ollama peaked at
8.8 GB resident (9.1 GB in `ollama ps`, 4096-token context) and was unloaded (40 MB) after each step.

**Caching** (`pipeline/cache.py`): search results per query for 24 h (`episodes/<slug>/cache/search-*.json`), page
text for 7 days in a cache shared by every episode (`cache/pages/`, or `STICK_CACHE_DIR`), model answers by
hash(system + user + model + schema + options) (`episodes/<slug>/cache/llm-*.json`), plus the finished research,
storylines and script as before. Every local call and search is recorded in the ledger at $0.00 (providers `ollama`
and `duckduckgo`).

## Hardware (checked 2026-11-05)

| | |
|---|---|
| Machine | MacBook Pro (Mac17,2), macOS 26.5.2 |
| Chip | Apple M5: 10-core CPU (4 performance, 6 efficiency), 10-core GPU, Metal 4 |
| Memory | 16 GB unified |
| Free disk | 77 GB before the bake-off; about 28 GB after the model downloads; about 58 GB after the unused 31 GB full-precision Z-Image download was deleted (2026-11-05) |

Memory is the limit. Each image engine has to fit, alongside macOS and the editor, in about 10 GB.
Engines run one at a time, never in parallel.

## What this Mac can run

| Engine | Use | Runs here? | Measured |
|---|---|---|---|
| Z-Image Turbo, 8-bit, mflux/MLX | text-to-image | **Yes**, in low-RAM mode with pre-quantized weights | 60–73 s per 768×1344 image, peak 7.5 GB |
| SDXL base 1.0 + cozy LoRA, diffusers/MPS | text-to-image locked to the Empty Chair style | **Yes** | see the bake-off results below |
| SDXL LoRA training, diffusers + peft/MPS | one-off per style | **Yes, slowly** | about 12 s per step at 768×1344, so about 2 h for 600 steps |
| FLUX.2 klein 4B, 8-bit (`mflux-community/flux2-klein-4b-mflux-q8`), mflux/MLX | image edits with Empty Chair frames as references: clean plates and new base poses | **Yes**, in low-RAM mode | about 55 s per 944×1680 edit (4 steps), peak 11.1 GB; 8.6 GB download |
| Qwen-Image-Edit-2511, mflux/MLX | reference consistency, pose variants | **No** | see the note below |
| Kokoro-82M, PyTorch/CPU | narration | **Yes**, faster than real time | 1.3–2.0 s per 3.7–4.3 s clip; loads in 3 s |
| Chatterbox 0.1.7, PyTorch/MPS | expressive narration and voice cloning | **Dropped 2026-11-05** | 9–16 s per 3 s clip in the bake-off; slower than real time |
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
| [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B), plus the [mflux-community 8-bit conversion](https://huggingface.co/mflux-community/flux2-klein-4b-mflux-q8) | Apache-2.0 | Yes |
| [Qwen/Qwen-Image-Edit-2511](https://huggingface.co/Qwen/Qwen-Image-Edit-2511) | Apache-2.0 | Yes (but it doesn't run here) |
| [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | Apache-2.0 | Yes |
| [ResembleAI/chatterbox](https://huggingface.co/ResembleAI/chatterbox) | MIT | Yes (dropped; bake-off only) |
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
- **Chatterbox was dropped from Stick_Figures on 2026-11-05** (no environment, not in the pipeline).
  Its weights in the shared Hugging Face cache were downloaded by Faceless Reels and are kept for it.
  Its maintainers retired it in August 2026. The code and weights stay MIT, but there
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
brew install espeak-ng ffmpeg
```

A setup gotcha:
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

## Motion direction (2026-11-05)

The bake-off images are no longer used as finished frames. SDXL + the cozy LoRA draws only empty
backgrounds and props (`scripts/motion_test/gen_assets.py`); characters are rigs (`docs/RIG.md`).
- **Empty background:** 1 of 1 usable on the first seed (`bg-cafe-s11`, 63 s at 768×1344, LoRA 0.75,
  "nobody" in the prompt and people in the negative prompt). No figures appeared.
- **Props:** the wallet came out in the cel style (navy outline, painted leather) at LoRA 0.6 on a
  plain white background, and is cut out by flood-filling the background.
- **Not used then:** FLUX.2 klein 4B (the rig parts did not need it; it is used from round 3 on, below) and
  the Z-Image LoRA side experiment (not run yet).

### Second round (after the review of the first clip)

The room was regenerated without furniture so the table and chair can be separate layers (the
character sits behind the table). SDXL often ignores "isolated on white" and "empty"; every
generation was checked, and these are the results:

| Prop | Seed used | Result |
|---|---|---|
| Room, no furniture in the middle | `bg-room-s47` | Usable; SDXL still painted a small table and chair at the side, kept as background |
| Café table | `prop-table-s2` | Usable; its painted floor shadow is removed by keeping only wood and iron colours |
| Chair | `prop-chair-s4` | Usable on a grey backdrop; `prop-chair-s12` was painted inside a room |
| Empty wallet | `prop-wallet-empty-s21` | SDXL drew cards inside both seeds; the cards and paper are painted out (`props.empty_out`) |
| Banknote | `prop-bill2-s19` | The first note (`prop-bill-s7`) copied real currency, with a portrait, and was rejected. The retry has a leaf ornament, and its one strip of fake lettering is blurred away |
| Glass jar | `prop-jar2-s17` | The first jar was painted inside a kitchen. The retry is a clean jar; its glass is made see-through so the coin pile shows |
| Moth | `prop-moth-s8` | Usable; SDXL added corner ornaments, removed by keeping the centre shape |
| Wall calendar | none | Rejected: SDXL drew a dense yearly planner full of fake lettering. The calendar stays code-drawn, with a painted finish, so the X marks land exactly on the grid |

### Third round: painted base poses (FLUX.2 klein 4B)

The procedural seated body was off-model, so characters now sit on painted base poses cut from The
Empty Chair frames (`docs/RIG.md`). FLUX.2 klein edits the frames
(`scripts/motion_test/flux_bases.py`, prompts in `styles/cozy/bases/flux-prompts.json`). Only the
pixels where a moving part used to be are used, after aligning each edit to the original.

| Edit | Seed used | Result |
|---|---|---|
| 04-waiting without arms, hands, pen and sketchbook | `plate-04-arms-s11` | Clean on the first try: the torso side and its navy outline rebuilt, bare tabletop, cup kept |
| 04-waiting with nobody in it (the café behind the head) | `plate-04-empty-s6` | Clean |
| 03-together without arms and the sketchbook | `plate-03-arms-b-s6` | The first prompt (seeds 3, 4) repainted the arms as white sleeves, leaving navy lines inside the torsos. Describing the torso as "one smooth white bulb with a single outline on each side" fixed it |
| 03-together with nobody in it | `plate-03-empty-s7` | Clean |

**New base-pose candidates, for review (not in the library):**

| Candidate | Result |
|---|---|
| The friend seated alone (03-together with the lead removed) | `cand-friend-seated-s51` and `-s52` are both usable |
| The lead standing (b06) | FLUX kept the seated pose from the reference (`cand-lead-standing-s31`–`s33`). The retry starts from 02-question, where the friend stands by the table: it removes the seated character and the scarf (`cand-lead-standing-b`) |
| The lead seated, facing the other way | Not usable: one seed drew both characters, the other gave the lead a scarf |

### Fourth round: clean plates for the approved bases, liftable cups

| Edit | Seed used | Result |
|---|---|---|
| `friend-at-table`: arms off | `plate-friend-arms-s71` | Clean, but FLUX kept the small extra cup (both seeds, and the empty plate too) |
| `friend-at-table`: only that cup removed | `plate-friend-nocup-s75` | Clean when it was the only instruction (`s76` kept it) |
| `lead-standing`: arms off | `plate-standing-arms-s82` | Clean; `s81` deleted the whole body. A stray sleeve line is painted out by the builder |
| Cups lifted out of 04 and 03 | `plate-04-nocup-b-s97`, `plate-03-nocups2-s95` | FLUX also removed or re-coloured the saucers, so the builder puts the original saucers back and cuts the cups with measured shapes |

**SDXL props for beats 3–9 were rejected:**
- `prop-takeout-s61` / `s62`: flat line-art with a printed symbol, or a grey backdrop.
- `prop-phone-s63` / `s64`: the phone fills the frame with scenery that won't key out.

The phone, the takeout box and the receipts are code-drawn with the painted finish instead.
