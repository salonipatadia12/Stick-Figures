---
name: stick-short
description: Make a narrated, captioned stick-figure Short (vertical explainer video) about any topic with this repository's pipeline, or continue, edit, approve or check the cost of one. Use when the user asks to make, create, script or render a stick-figure video, Short, reel or explainer about a topic, or to revise one in episodes/.
---

# Make a stick-figure Short

The `stick` CLI in this repository does the work. Your job is to drive it, show the user
each approval gate in the chat, apply the edits they ask for, and report the cost.

**Current status:** `stick make` produces a researched, approved script (schema v3, with per-beat
staging fields) in `episodes/<slug>/episode.json` and stops at the bases gate when a base pose is missing.
`stick props` makes missing props. Rendering (painted bases, verbs, Kokoro voice, captions, SFX, QA) is
composed from the script by `scripts/motion_test/episode_compose.py <slug>` (docs/PLAN.md §7); videos 1 and 2
still have hand-written beats modules (`episode_salary.py`, `episode_sleep.py`).

## Setup check (once per session)

1. Run the CLI as `.venv/bin/stick`. If it's missing, create the venv and install the package:
   `python3 -m venv .venv && .venv/bin/python -m pip install -e .`
2. The only paid step, the script, needs `ANTHROPIC_API_KEY` in `.env` at the repository root.
   Images and narration run on local open-source models (`docs/LOCAL_MODELS.md`) and need no keys.
   Never suggest a paid image or voice API or a rented GPU without the user asking.
   - Never print, read aloud or commit `.env`.
   - If a key is missing, tell the user which one and stop. Don't ask them to paste it into the chat.

## Workflow

1. **Collect the brief.** Get the topic from the user. Use these defaults unless they say otherwise:

   | Option | Default |
   |---|---|
   | `--style` | `cozy` (the only approved look) |
   | `--length` | `45` |
   | `--aspect` | `9:16` |
   | `--budget` | `2.00` |

   Pass `--niche` and `--audience` when the user gives them.
2. **Show the cost before spending.** Run
   `.venv/bin/stick make "<topic>" [options] --dry-run` and tell the user the worst-case cost in one line.
3. **Generate.** Run the same command without `--dry-run`. It researches, writes the script and
   then stops at the script gate with exit code 3.
4. **Script gate.** Show the user the printed review. They may approve it or ask for changes.
   - **To make edits:** edit `episodes/<slug>/episode.json` directly. Keep the beat `id`s, and keep
     every `anchor` a word that appears in that beat's `narration`. Then run
     `.venv/bin/stick validate <slug>` and fix every error.
   - **Feasibility warnings** (crowds, animals, fast action, walking across a room, posture changes) are
     printed at the gate with a simpler staging. They do not block; relay them so the user can decide.
   - **For a new draft:** if the user asks for a completely different script, run
     `stick make --episode <slug> --regenerate script`. This is a new paid call, so mention the cost first.
   - **After approval:** run `.venv/bin/stick approve <slug> --by user`, then `.venv/bin/stick make --episode <slug>`.
5. **Bases gate.** After the script is approved, `stick make --episode <slug>` prints the **base plan**:
   which painted base pose each beat uses. If a beat needs a setting or pose the library doesn't have,
   it stops at the `bases` gate (exit code 3). Then:
   - run `.venv/bin/stick bases <slug> --candidates` (local FLUX.2 klein, free, about 80 s per image). It
     renders two candidates per missing base and writes a review sheet under `renders/bases/candidates/`.
     Bases the starter pack already has candidates for are listed as "pick there, not here" instead of being
     rendered again; an insert (no characters) is served by any approved base of that setting. Two-character
     bases are prompted as one picture of both characters, in the pack's description of the setting.
   - Show the user the sheet and **stop**. Never pick a candidate yourself.
   - When the user picks: `.venv/bin/stick approve <slug> --gate bases --by user --pick <base>=<file.png> ...`.
     Picks may come over several calls. The pick enters `styles/cozy/bases/library.json` for every later
     video. Build picked bases with `.venv/bin/stick build-bases [--episode <slug>]` (local FLUX plates, a
     derived spec, layers; about 4 minutes per base). Look at `renders/bases/debug-<name>.jpg` for each built
     base; a base with crossed or overlapping arms may need its `rest` points fixed by hand in
     `scripts/motion_test/build_bases.py`.
   - When the user rejects a base's candidates: `--reject <base>="<their reason>"` on the same command, then
     `.venv/bin/stick bases <slug> --candidates --direction <base>="<what they asked for>"`. The redo round
     renders three new seeds with the user's words in place of the pose template, using the approved bases
     of the same setting as reference images (continuity), and writes a new sheet (`-r2.jpg`). Show it and
     **stop** again.
6. **Props.** `.venv/bin/stick props <slug> --generate` makes any script prop no library has (local FLUX,
   cut-out, QA). It prompts only when a prop fails QA on both seeds (exit 3 with a sheet under `renders/props/`):
   show the sheet and ask how the prop should look, or which stand-in to use.
6b. **Inserts.** `.venv/bin/stick inserts <slug> --generate` paints the scenes the script's insert beats need
   (local FLUX, about 80 s per candidate, two seeds, QA). It asks only when a scene fails on both seeds (exit 3 with
   a sheet under `renders/inserts/`). Graphics are drawn in code and need no step.
7. **Render.** `.venv/bin/python scripts/motion_test/episode_compose.py <slug> --voice am_michael` composes the
   beats from the script's staging fields and renders the Short to `renders/sample/<slug>/`. Report the file paths
   at the very top of the reply, then the library percentage (`library.library_pct`, target 80 %) and the
   subject-on-screen percentage (`subject.subject_pct`, target 70 %) from `qa.json`.
8. **Report.** Finish with `.venv/bin/stick cost <slug>`, and tell the user what was spent and what comes next.

**Starter base pack.** `.venv/bin/stick pack starter --candidates` renders candidates for eight common settings
(office, kitchen, living room, street, park, classroom, gym, car) and writes one sheet per setting. Show the
sheets and stop; the user picks with `stick pack starter --pick <base>=<file> ...` (or `--reject`/`--direction`).

## Rules

- **Never pass `--auto`.** It approves without asking. Use it only when the user explicitly asks to skip review.
- **Never raise `--budget` on your own.** On exit code 4 (budget), tell the user the numbers and ask.
- **No paid retries.** If a paid step fails, report the error and the ledger. Don't re-run it
  unless the user asks.
- **Facts must come from research.** Any number in the narration must cite a research fact
  (`source_ids`), or be framed as an example with `illustrative_numbers: true`.
  - Don't add statistics yourself.
  - If the user supplies a fact, ask for its source URL, then add it to `sources` with the next id.
- **Exit codes:**

  | Code | Meaning |
  |---|---|
  | 0 | Done |
  | 1 | Error (read the message) |
  | 2 | The script fails editorial checks (run `stick validate`) |
  | 3 | Waiting at an approval gate |
  | 4 | The budget cap would be exceeded (nothing was sent) |
