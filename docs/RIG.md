# Character rig

Characters are jointed cut-out rigs, animated in code. AI (SDXL + the cozy LoRA) draws only the
empty backgrounds and the props. The rig lives in `pipeline/rig/`.

## Files

| File | What it holds |
|---|---|
| `parts.py` | Art cut from The Empty Chair. The head plate keeps its painted shading; the eyes, brows and mouth are removed so they can animate. The torso comes from `body-base.png`, with its shading band, hatch strokes and heavy navy edge, extended to full length. The friend's teal scarf comes from `02-question.png`. Everything is cached in `styles/cozy/rig/`. |
| `hands.py` | The hand pose library. See "Hands" below. |
| `skeleton.py` | `Pose`, two-bone IK, jointed limbs, the face, held props, seating, and the light pass. |
| `actions.py` | Idle life, eased action blending, and reusable gestures. |
| `props.py` | Cut-outs of the AI-painted props, plus finishing: glass, coin piles, the cash fan, emptying a wallet, a painted finish for code-drawn props, and the calendar's ink marks. |

## Body

**Proportions** come from the Empty Chair figures, in head diameters (D):
- **Arms:** 0.62 + 0.58 D long, about 0.13 D thick.
- **Legs:** 0.70 + 0.95 D long, about 0.22 D thick.

**Limbs** are separate navy segments with a visible rounded elbow or knee and a gentle bend, not
rubber-hose noodles. The side facing the window gets a lighter painted band.

**Bones** keep their lengths, with at most 12% stretch.

**Elbows and knees** bend outward and down. The rig picks the joint position by body logic, so a
raised hand keeps its elbow out to the side.

**Seated** (the default, as in The Empty Chair):
- The hips sit at the table's front edge.
- The thighs run sideways under the table in three-quarter view, the shins drop to the floor, and
  the feet point where the knees point.
- The chair is a layer behind; the table is a layer in front.

**Arms over the table:** an arm whose hand is above the tabletop is drawn over the table, so hands
can rest on it or reach the jar.

**Lighting** puts the character inside the painting:
- a key light from the window side
- a softer shadow side
- a warm grade
- a warm rim on the lit edge, plus a faint back rim

Props get the same pass. Soft contact shadows sit under the feet, the chair, the table and the jar.

**The friend** is the same rig with `accessory='scarf'`.

## Hands

Seven poses, each drawn for the right hand. The left hand is its mirror image showing the same
face (`hands.contact_sheet`, `renders/motion-test/hand-library.jpg`):
- open palm
- back of hand
- fist
- grip (holding)
- pointing
- thumbs up
- resting

**Thumb side**, checked against a reference photo of a right hand from the front and back:
- **Right hand, palm toward the camera:** the thumb is on the viewer's right.
- **Right hand, back toward the camera:** the thumb is on the viewer's left.

**Wrist:** the hand continues the forearm, bent at most 65° either way, and is rotated only in the
picture plane. Palm versus back is always a different drawing, never a flip.

**Holding:**
- Every pose has a back layer and a front layer, and a held prop is drawn between them. So it sits
  in the grip, with the fingers wrapped over it and the thumb on the other side.
- A prop can only be held by a `grip` or `fist` hand; the renderer refuses anything else.

**`tests/test_rig.py` checks, at every frame of the beats 1–2 performance and for both voices:**
- no wrist goes past its limit
- every held prop is in a gripping hand, with its anchor at the grip point
- no arm crosses the face unless the action needs it (the facepalm)

## What the rig can do so far

**Idle (always on):**
- breathing
- blinks on an irregular rhythm
- weight shift
- head drift

**Body:**
- sitting at a table
- leaning
- slumping
- a bounce in the chair, with squash and stretch
- anticipation dips

Standing and hops also work in the rig, but beats 1–2 don't use them.

**Head and face:**
- tilt and nod
- a three-quarter turn
- eyes that track
- blinks, happily closed eyes and wide eyes
- brows: raise and worry
- smile and frown
- an open mouth

**Gestures in beats 1–2, each keyed to a narration word:**
- fanning with cash ("Payday feels rich")
- looking up at the calendar as the X marks land ("So why are you…")
- slumping, then reaching under the table ("broke")
- shaking the upturned wallet, with a moth flying out ("a week later")
- the back of the hand to the forehead ("…later?")
- rubbing hands at the jar
- fists under the chin while coins rain into the jar ("Say $1,500 lands")
- a fist pump ("lands")
- two pats on the jar lid and a nod ("every other Friday")
- a wind-up, then arms flung wide with a bounce ("It feels huge")

**Props:**
- Held props move with the hand.
- Thrown things arc up and out, then fall, at a constant size: bills from the fan, and the moth.
- Coins fall into the jar and fill it, and the counter follows the coins that have landed.

## Not yet

- **Walking:** no walk cycle yet.
- **Other views:** no profile, back view, or full turn.
- **Hand-overs:** a prop can't pass from one character to the other yet.
- **Lip-sync:** not needed while the narrator speaks off-screen.
