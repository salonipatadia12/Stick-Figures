"""FLUX.2 klein 4B (Apache-2.0, 8-bit MLX build) edits of The Empty Chair frames, for painted base poses.

Two kinds of job:
- clean plates: the original frame with the animated parts (head, arms, hands, props) removed. Only the
  pixels inside those parts are used later (bases.py pastes them into the original art).
- new base poses (standing, other facings): candidates for review. Nothing here goes into the base
  library until the user approves it.

Runs in .venvs/mlx:  .venvs/mlx/bin/python scripts/motion_test/flux_bases.py plate-04-empty plate-03-arms ...
Every prompt is kept in styles/cozy/bases/flux-prompts.json for provenance.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EC = ROOT / 'legacy/bespoke/the-empty-chair-source/assets'
OUT = ROOT / 'renders/bases/flux'
MODEL = 'mflux-community/flux2-klein-4b-mflux-q8'
KEEP = ('Same painterly illustration style, same camera and framing, same golden light and dappled shadows. '
        'Do not redraw or move anything else.')

JOBS = {
    'plate-04-arms': dict(images=['04-waiting.png'], seeds=[11, 22], prompt=(
        "Edit this illustration. Remove ONLY the character's two navy arms and both navy mitten hands, the green pen "
        "and the open sketchbook. Where the arms were, show the plain white torso with its dark navy outline, the wooden "
        "chair back behind it and the bare wooden tabletop. Keep the round white head and its face, the white torso, the "
        "navy legs, the chairs, the round table, the green cup and saucer, the cafe, the ivy and the light exactly the same. "
        "Same painterly style, same framing.")),
    'plate-04-empty': dict(images=['04-waiting.png'], seeds=[5, 6], prompt=(
        "Edit this illustration. Remove the white round-headed character completely: head, torso, arms, hands and legs. "
        "Show what is behind them: the empty wooden chair at the round table, the tall window with trees and ivy, "
        "the cafe wall and the tiled floor. Also remove the open sketchbook and the pen; the tabletop is bare wood with "
        "only the green cup and saucer. Keep the round table, both wooden chairs, the cafe, the plants and the light "
        "exactly the same. " + KEEP)),
    'plate-03-arms': dict(images=['03-together.png'], seeds=[3, 4], prompt=(
        "Edit this illustration. Remove ONLY both characters' navy arms and navy mitten hands and the open sketchbook. "
        "Where the arms were, show each character's plain white torso with its dark navy outline, the wooden chairs "
        "behind and the bare wooden tabletop. Keep both round white heads and their faces, the teal scarf, both white "
        "torsos, the navy legs, both green cups and saucers, the orange bag, the round table, the chairs and the cafe "
        "exactly the same. " + KEEP)),
    'plate-03-arms-b': dict(images=['03-together.png'], seeds=[5, 6, 9], prompt=(
        "Edit this illustration. Delete both characters' navy arms and navy mitten hands entirely, and the open "
        "sketchbook. Each character's torso becomes one smooth white bulb shape, like a bowling pin, with a single dark "
        "navy outline along each side: no arms, no sleeves, no shoulders sticking out and no lines inside the white "
        "torsos. Show the wooden chairs behind them and the bare wooden tabletop. Keep both round heads and their faces, "
        "the teal scarf, the navy legs, both green cups and saucers, the orange bag, the round table and the cafe exactly "
        "the same. " + KEEP)),
    'plate-03-empty': dict(images=['03-together.png'], seeds=[7, 8], prompt=(
        "Edit this illustration. Remove both white round-headed characters completely, including the teal scarf, and "
        "the open sketchbook. Show what is behind them: two empty wooden chairs at the round table, the tall windows "
        "with trees and ivy, the cafe wall and the tiled floor. Keep the round table with the two green cups and saucers, "
        "the orange bag on the floor, the plants and the cafe exactly the same. " + KEEP)),
    # ---- plates for the bases approved on 2026-11-06 (sources are the approved candidates) ----
    'plate-friend-arms': dict(images=['cand-friend-seated-s52.png'], seeds=[71, 72], prompt=(
        "Edit this illustration. Delete the seated character's navy arms, navy mitten hands and the pencil entirely, and "
        "also remove the small extra green cup that stands just left of their own cup (keep their own cup and saucer). "
        "Their torso becomes one smooth white bulb shape, like a bowling pin, with a single dark navy outline along each "
        "side: no arms, no sleeves and no lines inside the white torso. Show the wooden chair behind them and the bare "
        "wooden tabletop. Keep their round head and face, the teal scarf, the navy legs, the other cups, the round table, "
        "the chairs, the bag and the cafe exactly the same. " + KEEP)),
    'plate-friend-nocup': dict(images=['plate-friend-arms-s71.png'], seeds=[75, 76], prompt=(
        "Edit this illustration. Remove the small green cup without a saucer that stands on the table just left of the "
        "larger cup on its saucer. Show the plain wooden tabletop with its dappled light where the small cup stood. Keep "
        "everything else exactly the same: the character, the scarf, the larger cup and saucer, the table and the cafe.")),
    'plate-friend-empty': dict(images=['cand-friend-seated-s52.png'], seeds=[73], prompt=(
        "Edit this illustration. Remove the seated white round-headed character with the teal scarf completely (head, "
        "torso, scarf, arms, hands and legs) and the small extra green cup next to their cup. Show what is behind them: "
        "the empty wooden chair, the cafe wall, the plants and the window. Keep the round table, the two remaining green "
        "cups and saucers, the chairs, the bag and the cafe exactly the same. " + KEEP)),
    'plate-standing-arms': dict(images=['cand-lead-standing-b-s62.png'], seeds=[81, 82], prompt=(
        "Edit this illustration. Delete the standing character's navy arms and both navy mitten hands entirely, including "
        "the hand resting on the chair back. Their torso becomes one smooth white bulb shape with a single dark navy "
        "outline along each side: no arms, no sleeves and no lines inside the white torso. Show the chair back, the door "
        "and the cafe behind where the arms were. Keep their round head and face, the white torso, the navy legs and feet, "
        "the round table, the cup, the chair with the orange bag and the cafe exactly the same. " + KEEP)),
    'plate-standing-empty': dict(images=['cand-lead-standing-b-s62.png'], seeds=[83], prompt=(
        "Edit this illustration. Remove the standing white round-headed character completely (head, torso, arms, hands, "
        "legs and feet). Show what is behind them: the green door, the cafe wall, the plants and the tiled floor. Keep "
        "the round table, the cup, the chair with the orange bag and the cafe exactly the same. " + KEEP)),
    'plate-03-nocups': dict(images=['03-together.png'], seeds=[91, 92], prompt=(
        "Edit this illustration. Delete both characters' navy arms and navy mitten hands entirely, the open sketchbook, "
        "and the two green coffee cups (keep the two green saucers on the table). Each character's torso becomes one "
        "smooth white bulb shape, like a bowling pin, with a single dark navy outline along each side: no arms, no "
        "sleeves and no lines inside the white torsos. Show the wooden chairs behind them and the bare wooden tabletop "
        "with the two empty saucers. Keep both round heads and faces, the teal scarf, the navy legs, the orange bag, the "
        "round table and the cafe exactly the same. " + KEEP)),
    'plate-03-nocups2': dict(images=['plate-03-nocups-s91.png'], seeds=[95, 96], prompt=(
        "Edit this illustration. Remove the green coffee cup that stands on its saucer on the right side of the table, "
        "and keep that saucer exactly as it is: an empty sage-green saucer. Keep everything else exactly the same: both "
        "characters, the scarf, the other empty saucer, the table and the cafe.")),
    'plate-04-nocup-b': dict(images=['plate-04-arms-s11.png'], seeds=[97, 98, 99], prompt=(
        "Edit this illustration. Remove only the green coffee cup from its saucer. The empty saucer stays exactly the "
        "same sage green as before, in the same place and size. Keep everything else exactly the same: the character, "
        "the table and the cafe.")),
    'plate-04-nocup': dict(images=['04-waiting.png'], seeds=[93, 94], prompt=(
        "Edit this illustration. Remove ONLY the character's two navy arms and both navy mitten hands, the green pen, the "
        "open sketchbook and the green coffee cup (keep its green saucer on the table). Where the arms were, show the "
        "plain white torso with its dark navy outline, the wooden chair back behind it and the bare wooden tabletop with "
        "the empty saucer. Keep the round white head and its face, the white torso, the navy legs, the chairs, the round "
        "table, the cafe, the ivy and the light exactly the same. " + KEEP)),
    # ---- new base-pose candidates (for review only) ----
    'cand-lead-standing': dict(images=['04-waiting.png', '02-question.png'], seeds=[31, 32, 33], prompt=(
        "Using the character from the first image and the cafe from both images: the same white round-headed character "
        "with a dark navy outline, white bulb-shaped torso and dark navy limbs and mitten hands, now STANDING on the tiled "
        "floor beside the round wooden cafe table, full body visible from head to feet, three-quarter view facing right, "
        "arms relaxed at the sides, same proportions as the reference (very large round head, short torso, long thin navy "
        "legs). Cafe with ivy, tall windows, golden afternoon light and dappled shadows, portrait framing like the "
        "references. No text.")),
    'cand-friend-seated': dict(images=['03-together.png'], seeds=[51, 52], prompt=(
        "Edit this illustration. Remove the white round-headed character on the LEFT completely (head, torso, arms, "
        "hands and legs) and the open sketchbook; show the empty wooden chair and the cafe behind where they sat. Keep "
        "the character on the RIGHT with the teal scarf exactly as they are, seated at the round table, and keep both "
        "green cups, the orange bag, the table and the cafe exactly the same. " + KEEP)),
    'cand-lead-standing-b': dict(images=['02-question.png'], seeds=[61, 62], prompt=(
        "Edit this illustration. Remove the seated white character on the left completely (head, torso, arms, hands, "
        "legs and pen) and the open sketchbook, and show the empty wooden chair and the cafe behind them. Remove the teal "
        "scarf from the standing character on the right, so their white torso is plain with the same navy outline and "
        "their neck shows. Keep the standing character's pose, head, face, arms, legs and feet exactly the same, and keep "
        "the table, the cup, the orange bag, the chairs and the cafe exactly the same. " + KEEP)),
    # ---- bedroom base-pose candidates for video 2 (for review only; requested 2026-11-07) ----
    'cand-bed-edge': dict(images=['04-waiting.png', '01-alone.png'], seeds=[101, 102], prompt=(
        "Using the character from these images: the same white round-headed character with a dark navy outline, white "
        "bulb-shaped torso, dark navy limbs and mitten hands, now SITTING ON THE EDGE OF A BED in a cozy bedroom, seen "
        "from the side in three-quarter view facing right, feet on a wooden floor, arms relaxed at the sides, a rumpled "
        "patchwork quilt behind them, a small wooden nightstand with a round alarm clock and a lamp, a window with golden "
        "morning light and dappled shadows, a plant. Same proportions as the reference (very large round head, short "
        "torso, long thin navy legs), same painterly illustration style, portrait framing. No text.")),
    'cand-bed-asleep-top': dict(images=['04-waiting.png', '01-alone.png'], seeds=[111, 112], prompt=(
        "Using the character from these images: the same white round-headed character with a dark navy outline, now "
        "ASLEEP IN BED seen from DIRECTLY ABOVE (a top-down view): the bed fills the frame, the round white head rests on "
        "a pale pillow at the top, the body is hidden under a warm patchwork quilt in rust, cream and teal squares, one "
        "navy mitten hand on top of the quilt, a wooden nightstand with a round alarm clock beside the bed, soft golden "
        "light and dappled shadows across the quilt. Same painterly illustration style, portrait framing. No text.")),
    'cand-bed-awake': dict(images=['04-waiting.png', '01-alone.png'], seeds=[121, 122], prompt=(
        "Using the character from these images: the same white round-headed character with a dark navy outline, now "
        "LYING IN BED ON THEIR BACK at night, seen from the side at the level of the bed, head on a pale pillow on the "
        "left, body under a patchwork quilt pulled up to the chest, both navy mitten hands resting on top of the quilt, "
        "a wooden nightstand with a round alarm clock and a small lamp glowing warm, a dark blue window with moonlight, "
        "cozy bedroom. Same proportions as the reference (very large round head), same painterly illustration style, "
        "portrait framing. No text.")),
    'cand-lead-seated-left': dict(images=['04-waiting.png', '03-together.png'], seeds=[41, 42], prompt=(
        "Using the character and cafe from the first image: the same white round-headed character with a dark navy "
        "outline, white bulb-shaped torso and dark navy limbs, seated on the wooden chair on the RIGHT side of the round "
        "cafe table, three-quarter view facing LEFT toward the table, hands resting on the tabletop, legs under the "
        "table, same proportions and painterly style. Golden light, ivy, tall windows, dappled shadows, portrait framing "
        "like the reference. No text.")),
}


def main(names):
    from mflux.models.common.config import ModelConfig
    from mflux.models.flux2.variants import Flux2KleinEdit

    OUT.mkdir(parents=True, exist_ok=True)
    log_path = ROOT / 'styles/cozy/bases/flux-prompts.json'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    t0 = time.time()
    import mlx.core as mx
    mx.set_cache_limit(2 * 1024 ** 3)          # keep peak memory down on a 16 GB Mac
    model = Flux2KleinEdit(model_config=ModelConfig.flux2_klein_4b(), model_path=MODEL)
    print(f'model loaded in {time.time() - t0:.0f}s', flush=True)
    for name in names:
        job = JOBS[name]
        for seed in job['seeds']:
            dst = OUT / f'{name}-s{seed}.png'
            if dst.exists():
                print('cached', dst.name)
                continue
            t1 = time.time()
            image = model.generate_image(seed=seed, prompt=job['prompt'], image_paths=[str(EC / p if (EC / p).exists() else OUT / p) for p in job['images']],
                                         num_inference_steps=4, width=944, height=1680)
            image.save(str(dst))
            print(f'{dst.name}: {time.time() - t1:.0f}s', flush=True)
        log[name] = dict(model=MODEL, licence='apache-2.0', steps=4, size=[944, 1680], **job)
        log_path.write_text(json.dumps(log, indent=2) + '\n')


if __name__ == '__main__':
    args = sys.argv[1:]
    # a .json argument is a jobs file (pipeline/bases.py writes one per episode); its jobs join JOBS
    for a in [a for a in args if a.endswith('.json')]:
        JOBS.update(json.loads(Path(a).read_text()))
    args = [a for a in args if not a.endswith('.json')]
    main(args or ['plate-04-empty', 'plate-03-arms', 'plate-03-empty'])
