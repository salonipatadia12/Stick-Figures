---
name: stick-short
description: Make a narrated, captioned stick-figure Short (vertical explainer video) about any topic with this repository's pipeline, or continue, edit, approve or check the cost of one. Use when the user asks to make, create, script or render a stick-figure video, Short, reel or explainer about a topic, or to revise one in episodes/.
---

# Make a stick-figure Short

The `stick` CLI in this repository does the work. Your job is to drive it, show the user
each approval gate in the chat, apply the edits they ask for, and report the cost.

**Current status:** `stick make` produces a researched, approved script in
`episodes/<slug>/episode.json`. Rendering (painted base poses, Kokoro voice, captions, SFX, QA) is
done per episode by a render script modelled on `scripts/motion_test/episode_salary.py`
(see `docs/RIG.md`); it is not yet wired into the `stick` CLI.

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
   - **For a new draft:** if the user asks for a completely different script, run
     `stick make --episode <slug> --regenerate script`. This is a new paid call, so mention the cost first.
   - **After approval:** run `.venv/bin/stick approve <slug> --by user`, then `.venv/bin/stick make --episode <slug>`.
5. **Report.** Finish with `.venv/bin/stick cost <slug>`, and tell the user what was spent and what comes next.

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
