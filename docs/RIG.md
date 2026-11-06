# Character rig

Characters are jointed cut-out rigs, animated in code. AI draws only the empty backgrounds and
props. The rig lives in `pipeline/rig/`:

| File | What it holds |
|---|---|
| `parts.py` | Part artwork. The head plate is cut from the approved Empty Chair art: the painted shading is kept, the eyes, brows and mouth are removed, and the outline is redrawn. The torso, hands and feet are cel shapes in the sampled Empty Chair palette. |
| `skeleton.py` | `Pose` (every animatable channel), two-bone IK, layering, the face, held props, and window-light rim and grade. |
| `actions.py` | Idle life, eased action blending, and reusable gestures. |
| `props.py` | AI prop cut-out, plus procedural props whose insides animate: calendar, jar, cash, moth, and falling coins. |

**Proportions** come from the standing Empty Chair figure, in head diameters (D):
- torso 0.78 × 1.5
- arms 0.60 + 0.58
- legs 0.62 + 0.62
- about 3.4 D tall

Bones keep their lengths, with at most 12% rubber-hose stretch.

**Elbows and knees** are chosen by body logic: outward and down by default. So a raised hand never folds its arm across the face.

## What the rig can do so far

Everything below is shown in the beats 1–2 test clip (`scripts/motion_test/beats_1_2.py`).

**Idle life (always on, under every action):**
- breathing (chest and shoulders)
- blinks on an irregular rhythm, with occasional double blinks
- slow weight shift
- head drift

**Body:**
- lean
- knee bend and crouch
- slump
- hop with squash on take-off and landing
- anticipation dips before big moves

**Head and face:**
- tilt and nod
- three-quarter turn (the face slides toward the look direction, and the far eye narrows)
- eyes that track things
- open, blink, happily closed, and wide eyes
- brows: raise and worry
- smile and frown
- open mouth for delight or a gasp

**Arms and hands:**
- both arms reach any target by IK
- hand shapes: relaxed, open, grip, fist, point
- hands rotate with the forearm, plus an extra wrist angle

**Gestures in the clip, each keyed to a word:**
- fanning yourself with cash, other hand on hip ("Payday feels rich")
- looking up at the calendar as the X marks land ("So why are you…")
- slump ("broke")
- turning the wallet upside down and shaking it, a moth flying out ("a week later")
- hand to forehead
- rubbing hands
- hands clasped under the chin, bouncing, eyes tracking falling coins
- fist pump ("lands")
- two pats on the jar and a nod ("every other Friday")
- wind-up, then arms flung wide with a hop ("It feels huge")

**Props:**
- Held props sit in the hand's grip point and move and rotate with it (the cash fan, the wallet).
- Props leave the hand from where the hand was at release (the bills, the moth).
- Coins fall under gravity into a jar that fills as they land, and the on-screen counter is tied to the coins that have landed.

## Not yet

- **Walking:** feet are IK targets, but there is no walk cycle yet.
- **Other views:** no profile, back view, or turning all the way round.
- **Sitting on a chair:** not built yet.
- **Two characters:** the scarf friend has not been built, and there are no hand-overs between characters.
- **Lip-sync:** not needed while the narrator speaks off-screen.
- **Cloth and hair:** none on this design.

## Assets and timing

- **Backgrounds and props:** `scripts/motion_test/gen_assets.py` uses SDXL + the cozy LoRA, with seeds, so they can be reproduced. Outputs go to `renders/motion-test/assets/`.
- **Narration:** `scripts/motion_test/voice.py` uses Kokoro (`am_michael` or `af_heart`) and writes per-word timings. Every action is anchored to a word, so a different voice re-times the whole performance.
