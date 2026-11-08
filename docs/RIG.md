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
(run in `.venvs/mlx`, which has OpenCV) from a painted frame and FLUX.2 klein clean plates
(`scripts/motion_test/flux_bases.py`):

| Base | From | Seats | Body |
|---|---|---|---|
| `lead-at-table` | Empty Chair `04-waiting` | the lead, three-quarter view | leans and squashes about the hip |
| `together` | Empty Chair `03-together` | the lead and the scarf friend, each in their own chair | both lean |
| `friend-at-table` | FLUX candidate `s52` (03-together with the lead removed; the extra small cup painted out). Approved 2026-11-06 | the friend | leans |
| `lead-standing` | FLUX candidate `s62` (02-question with the seated character and the scarf removed). Approved 2026-11-06 | the lead, standing behind the table | fixed (the hips are in view) |

New bases are made with FLUX.2 klein from the Empty Chair frames and shown to the user first. Only
approved ones go into the library: `styles/cozy/bases/library.json` records each base's setting, pose,
view, cast, source candidate and approval (`pipeline/bases.py` plans a script against it and makes
candidates for what is missing; `stick bases`). `pipeline/qa.APPROVED_BASES` lists the built ones.

**Original art is kept.** FLUX redraws the whole frame a few pixels off, so each edit is first
aligned to the original. Its pixels are used only where a moving part used to be.

**Each base has these files:**

| File | What it is |
|---|---|
| `plate.png` | The painting with everything that moves taken out: arms, hands, heads, liftable cups, and (seated) the torso. The café behind a seated torso comes from an empty-café plate. |
| `body-<seat>.png` | The painted torso (with the friend's scarf), and a neck that continues the torso's own curved outline and shading under the head. It extends a little under the tabletop, so a lean or a squash never opens a gap at the table edge. |
| `head-<seat>.png` | The frame's own head, cut exactly at the outer edge of its painted outline (the ellipse is re-fitted to the stroke itself, so a dark door frame or shadow beside the head can't pull it off), with no backing ring: the only outline is the painting's. Where the stroke is missing (the old torso join) that angle alone is filled at the stroke's width, inside the cut. The eyes, brows and mouth are painted out. |
| `occluder-<seat>.png` | The torso around each shoulder, laid back over the arm roots so the arms come out from behind the torso side. It moves with the body. |
| `table.png` | The tabletop and pedestal, never any torso. It is laid over the body and over a hand that reaches under the table. |
| `front.png` | Cups that stay painted and sit in front of the arms. |
| `cup-<n>.png` | Cups that can be lifted (in `lead-at-table` and `together`). Each is cut with a measured shape: rim, bowl, handle. Its saucer stays in the plate, with the centre the cup hid filled from the saucer's own rim. |
| `empty.png` | The café with nobody in it, aligned to the plate, for inserts that show only the table. |
| `base.json` | Anchors in plate pixels: heads, shoulders (measured onto the torso outline), resting hands, hip pivot, cups, light side. |

**The body moves a little.** A seated torso leans (at most 4°) and squashes or stretches (at most
4.5%) about its hip, which stays on the chair. The head, shoulders and the shoulder occluder follow it.
Slump is a forward lean plus a squash; a bounce is a stretch.

**Layer order** (`Stage.compose`):
1. plate
2. wall props (the calendar)
3. painted torsos
4. arms under the table
5. table
6. props on the table (jars, receipts)
7. far arm
8. head
9. near arm
10. shoulder occluder
11. cups
12. flying props

## Arms

They match the painting:
- **Thickness:** about 0.13 D.
- **Shoulders:** thin where they come out from behind the torso side. The shoulder points sit on the
  torso outline, so a resting arm covers the outline as in the paintings.
- **Length:** 0.52 + 0.46 D, so the hands reach the tabletop but never the floor.
- **Finish:** near-flat navy with a slightly lighter side toward the light, a darker rim of a few
  pixels and soft edges. These values were sampled from the painted arms and legs.

By default the elbows hang down, so at rest the arms hug the torso sides and the forearms lie on the
table. An action can lift an elbow out to the side (`extra['l_droop']`).

**Which arm is drawn where:**
- The far arm passes behind the head. A facepalm or a toast brings it in front (`extra['r_front']`).
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

`tests/test_rig.py` checks, at every frame of beats 1–2 for both voices, and of every other beat in the
full sample:
- no wrist goes past its limit
- every held prop is in a gripping hand. The renderer also refuses anything else.
- no arm crosses a face unless the action needs it (a hand to the forehead)

**On the bases it also checks that:**
- the arms reach the tabletop but are too short to reach the floor
- every arm root is behind its torso
- held props sit at the grip point
- no painted head is left in a plate and no face is left on a head sprite
- the far arm goes behind the head
- a seated body leans about a fixed hip
- cups lift off cleanly
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
- both hands on the table edge, leaning in toward the jar, bouncing as the coins rain in ("Say $1,500 lands")
- a fist pump ("lands")
- two pats on the jar lid and a nod ("every other Friday")
- hands on the table edge leaning in ("It"), then both arms flung up and wide with a bounce ("huge"). The fists-under-the-chin "W" pose is gone (user, 2026-11-07)

**Gestures in beats 3–9** (`scripts/motion_test/salary_beats.py`):
- worried watching as coins hop out of the jar and a receipt prints across the table from the lead's side toward the jar, read left to right RENT, BILLS, LEFT, with a flinch on "bite" (b03)
- the phone at the chest and a tap per subscription; lifting the painted coffee cup for a sip; an easy palms-out shrug (b04)
- an insert with nobody in it: cracks spread over the jar, coins pour out, a jolt on "empty" (b05)
- the friend leans in with one finger up ("Here's the catch") (b06)
- the standing lead throws both hands up on "total", then a hand to the forehead while a receipt unrolls day by day, MON to FRI then PAYDAY, the camera panning the length of the table (b06)
- a glance at the full jar, then leaning back and waving "go ahead" as the jar whispers (b07)
- grabbing a fistful from the big jar on "split"; the big jar slides off and three small jars slide in, BILLS, WEEK 1, WEEK 2 left to right; the hand deals coins into each on its word; week one empties and is circled; a point on "early" (b08). The calendar keeps the crossed-off week from beat 1
- each character lifts their own cup with the arm on the inner side (nobody reaches across), the cups meet in the middle and touch once on "last", then both sip; the calendar (same size as beat 1, week crossed off) rings the next payday on "next" (b09)

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

**On-screen text reads on a phone.** Every text the overlay library draws (slams, the checklist, tags,
receipts, bubbles, the $ counter) logs the size its type ends up at on the 1080×1920 frame, and the QA
text check fails a render with anything under 40 px (`pipeline/qa.text_check`, `overlays.TEXT_MIN_PX`).
Captions are 64 px (never shrunk below 40). Prop lettering such as the calendar's day numbers is not text
and is not checked.

## Not yet

- **A standing body doesn't move.** Its hips are in view, so it stays as painted. The head and arms
  still move.
- **Walking and other facings:** these need new base poses (FLUX.2 klein candidates, for approval).
  The seated-facing-the-other-way pose was skipped (the user's call, 2026-11-06).
- **Hand-overs:** a prop can't pass from one character to the other yet.
- **Lip-sync:** not needed while the narrator speaks off-screen.
