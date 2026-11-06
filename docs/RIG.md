# Character rig

Characters sit on **painted base poses**, as in The Empty Chair motion edition. The seated body, legs,
chair, table and café are an Empty Chair painting. Only the head, arms, hands and props are animated
on top of it, in code. The rig lives in `pipeline/rig/`.

## Files

| File | What it holds |
|---|---|
| `puppet.py` | Characters on a painted base: the head (the frame's own painted head with a live face), the arms, the hands, held props, and the `Stage` that layers everything. |
| `hands.py` | The hand pose library. See "Hands" below. |
| `actions.py` | Idle life, eased action blending, and reusable gestures. |
| `props.py` | Cut-outs of the AI-painted props, plus finishing: glass, coin piles, the cash fan, emptying a wallet, a painted finish for code-drawn props, and the wall calendar. |
| `skeleton.py` | `Pose`, two-bone IK, held props, the light pass, and the older procedural body (kept for tests and as a fallback). |
| `parts.py` | Art cut from The Empty Chair for the procedural body. |

## Base poses

The base library is in `styles/cozy/bases/<name>/`. It's built by `scripts/motion_test/build_bases.py`
(run in `.venvs/mlx`, which has OpenCV) from an Empty Chair frame and two FLUX.2 klein clean plates
(`scripts/motion_test/flux_bases.py`):

| Base | From | Seats |
|---|---|---|
| `lead-at-table` | `04-waiting` | the lead, three-quarter view, facing the empty chair |
| `together` | `03-together` | the lead and the scarf friend, each in their own chair |

**Original art is kept.** FLUX redraws the whole frame a few pixels off, so each edit is first
aligned to the original. Its pixels are used only where a moving part used to be:

- the arms, the hands and the props on the table;
- inside the old heads, the empty café from a second plate. A neck continuing the torso (or, for the
  friend, the scarf) is painted under each head, so a nodding head never comes loose.

**Each base has these files:**

| File | What it is |
|---|---|
| `plate.png` | The painting with the moving parts removed. |
| `head-<seat>.png` | The frame's own head with the eyes, brows and mouth painted out. Its outline and shading are untouched. |
| `table.png` | The round table, laid over a hand that reaches under it. |
| `front.png` | The cups, which sit in front of the arms. |
| `occluder.png` | The torso around each shoulder, laid back over the arm roots so the arms come out from behind the torso side, like the Empty Chair sleeves. |
| `base.json` | Anchors in plate pixels: heads (centre, size), shoulders, resting hands, tabletop, light side. |

**Layer order** (`Stage.compose`):
1. plate
2. wall props (the calendar)
3. arms under the table
4. table
5. props on the table (the jar)
6. far arm
7. head
8. near arm
9. shoulder occluder
10. cups
11. flying props

So the chair seat is under the body, the body is behind the table edge, and hands and props on the
table are on top of it.

**New base poses** (standing, other facings, other places) are made with FLUX.2 klein from the Empty
Chair frames. They are shown to the user first, and only approved ones go into the library.

## Arms

They match the painting:
- **Thickness:** about 0.13 D.
- **Shoulders:** thin where they come out from behind the torso.
- **Length:** 0.52 + 0.46 D, so the hands reach the tabletop but never the floor.

By default the elbows hang down, so at rest the arms hug the torso sides and the forearms lie on the
table, as in 04-waiting. An action can lift an elbow out to the side (`extra['l_droop']`).

**Which arm is drawn where:**
- The far arm passes behind the head. The facepalm brings it in front (`extra['r_front']`).
- A hand reaching under the table goes behind the tabletop (`extra['l_under']`).

## Hands

Seven poses, each drawn for the right hand. The left hand is its mirror image showing the same face
(`hands.contact_sheet`, `renders/motion-test/hand-library.jpg`):
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

Screen-left is the character's right hand, in three-quarter view as well as from the front.

**Wrist:** the hand continues the forearm, bent at most 65° either way, and is rotated only in the
picture plane. Palm versus back is always a different drawing, never a flip.

**Holding:**
- Every pose has a back layer and a front layer, and a held prop is drawn between them. So it sits
  in the grip, with the fingers wrapped over it and the thumb on the other side.
- A prop can only be held by a `grip` or `fist` hand; the renderer refuses anything else.

## Tests

`tests/test_rig.py` checks, at every frame of the beats 1–2 performance and for both voices:
- no wrist goes past its limit
- every held prop is in a gripping hand
- no arm crosses the face unless the action needs it (the facepalm)

**On the painted bases it also checks that:**
- the arms reach the tabletop but are too short to reach the floor
- the arm roots are covered by the torso
- held props sit at the grip point
- no painted head is left in a plate and no face is left on a head sprite
- the far arm goes behind the head
- in the two-shot each character's hands stay on their own side of the table

## What the rig can do so far

**Idle (always on):**
- breathing (head and shoulders)
- blinks on an irregular rhythm
- head drift

**Head and face:**
- nod, tilt and bob on the neck
- a strong three-quarter turn, as in the paintings
- eyes that track
- blinks, happily closed eyes and wide eyes
- brows: raise and worry
- smile and frown
- an open mouth

**Gestures in beats 1–2, each keyed to a narration word:**
- fanning with cash beside the head ("Payday feels rich")
- looking at the wall calendar as the X marks land ("So why are you…")
- the head sinks while a hand reaches under the table ("broke")
- shaking the upturned empty wallet, with a moth flying out ("a week later")
- the back of the hand to the forehead ("…later?")
- rubbing hands beside the jar
- fists under the chin while coins rain into the jar ("Say $1,500 lands")
- a fist pump ("lands")
- two pats on the jar lid and a nod ("every other Friday")
- a wind-up, then both arms flung up and wide with a bounce of the head ("It feels huge")

**Props:**
- Held props move with the hand.
- Thrown things arc up and out, then fall, at a constant size: bills from the fan, and the moth.
- Coins fall into the jar and fill it, and the counter follows the coins that have landed.
- The calendar hangs on the wall the lead faces, about a quarter of the frame wide, so the X marks
  read on a phone.

**On-screen text** stays in the upper-middle, clear of:
- the bottom quarter and the right edge, where the Shorts title and buttons sit
- the calendar
- the head

## Not yet

- **The painted body doesn't move.** Lean, slump and bounce show only in the head and arms, as in
  The Empty Chair motion edition.
- **Standing, walking and other facings:** these need new base poses (FLUX.2 klein candidates, for
  approval).
- **Hand-overs:** a prop can't pass from one character to the other yet.
- **Lip-sync:** not needed while the narrator speaks off-screen.
