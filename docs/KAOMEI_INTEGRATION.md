# Kaomei Director integration (2026-11-11)

Source: [kaomei/stickman-video-director](https://github.com/kaomei/stickman-video-director), MIT licence
(© 2026 Stickman Video Director contributors; the notice is in
[references/stickman-video-director/LICENSE](../references/stickman-video-director/LICENSE)).
Reviewed revision: `7af3957dff97914b2d762fb248ee2427d7fca72c` (2026-11-11). An earlier adaptation of its Style 4 for
the legacy reel builder is recorded in [references/stickman-video-director/UPSTREAM.md](../references/stickman-video-director/UPSTREAM.md).

Kaomei is a directing skill and prompt library: Mode A (Director) plans a video scene by scene, and Mode B (Autopilot)
renders one with its own runtime. We took ideas and wording from Mode A's planning contract, plus the visual
direction written in three of its style specs. We took no code, no runtime, and no assets (images, video or audio).
Our pipeline (rig, painting, compositor, voice, QA) is unchanged, and the new rules feed into it.

## What was adapted

| Kaomei (file) | Here |
|---|---|
| 5-stage Heartbeat Engine: golden hook, disrupt the assumption, insider secrets, the truth, elevation and a comment question (`storyboard-template.md`) | The `revelation` story format (`pipeline/models.py` `FORMATS`). The script rules map it onto our beat roles: "problem" breaks the belief, "clue" beats show the hidden mechanism, "reveal" is the truth, and the loop ends on an open question. The story check at the script gate applies unchanged. |
| Visual-density recipe: three timed phases per clip and a minimum number of visual devices (`storyboard-template.md`) | Rule 7 (MOTION) in `SCRIPT_RULES` and the compact local rules: [0-1s] inherit, [1-3s] transform, [3-end] land the point and set up the next; at least three devices per beat, named in our own vocabulary (host action, scene effect, camera move, label, split, diagram item, on-screen text). Kaomei's clips are 10 s and need four devices; our beats are 2-6 s, so we ask for three. |
| Continuity: every clip ends on something the next one inherits (`storyboard-template.md`) | Rule 8 (TRANSITIONS), plus a new `shot.continuity` field the writer fills, naming both sides of the link. QA (`pipeline/qa.py` `continuity_report`) warns, but never fails, when adjacent beats change place on a hard cut with no named link. Scripts written before script-v7 are reported as "not checked". An empty field leaves every existing digest and approval as it was. |
| Clip allocation by duration (`storyboard-template.md`) | Rule 18 (PACING BY LENGTH): 30 s is 7-8 beats, 45 s is 9-11, 60 s is 12-14. The local writer's beat aim follows it (`script.pacing_aim`). |
| Thumbnail and Publishing Kit (`thumbnail-publishing-kit.md`) | `stick package <slug>` (`pipeline/package.py`) writes to `episodes/<slug>/out/`: a 1280x720 thumbnail cut from the rendered video (the busiest frame whose 16:9 band keeps the host's face and avoids every caption, with the hook as 2-4 words kept off the face), 5 titles, a description with the cited sources, credits and 8-12 hashtags, one pinned comment, and `publish_checklist.json`. Each check is marked checked, pending or not_applicable, as kaomei requires: no rendered video means its checks are pending, and "your final approval" and "Content ID" are always pending. Titles are written by rules; `--titles` makes one free local Ollama call instead, and never a paid one. Nothing is uploaded. |
| Style B1 Minimal Hand-Drawn (`b1-minimal-handdrawn.md`) | `styles/lineart/`: black ink on near-white paper with a faint grid, three marker accents with fixed meanings, slender expressive figures, large hand-lettered scene text, Kokoro `am_michael`, no music. |
| Style B3 Retro TV (`b3-television.md`) | `styles/retro-tv/`: a dark field, white and gray glowing figures with blank round heads, one red accent used sparingly, a restrained CRT finish (bloom, scanlines, vignette, brief signal breaks), ambient music on, Kokoro `am_michael`. |
| Style B2 Colorful Hand-Drawn (`b2-colorful-handdrawn.md`) | `styles/colorful/`: warm cream paper, round-headed figures in simple colourful clothes, full-colour places with a new place for every idea, Kokoro `af_heart`, an optional acoustic bed (off by default). |

## The three new style packs are direction only

Each pack has `style.json`, `style_lock.txt` (the image-model look) and `script_brief.md` (for the Claude path). It
also has a short `writer_note` that the local writer's beat stage reads; cozy has none, so its prompts are unchanged.
`style.json` marks each pack `"status": "direction"`, and `stick make --style lineart` says so. The painted bases,
host cut-out, insert scenes and props exist only for cozy, and the compositor has no CRT pass yet, so a render in
these styles still uses the cozy art. To make them real, paint each pack's library (bases, host, inserts) and, for
retro-tv, add the television pass to the compositor.

## What was not adapted

- **Mode B (Autopilot):** none of its runtime, CLI (`autopilot.mjs`), project manifests, layered-asset format,
  validation or preview commands. From the B1-B3 style specs we took only their written visual direction. Their
  caption presets, typography sizes, approved-baseline media and acceptance procedures were not used.
- **B5 (character explainer rig)** and its motion, avatar and production-gate references.
- **Mode A's generation workflow:** Omni Flash or Flow prompts, 10-second clips, its Phase A/B approval contract,
  aspect-ratio rules for generated video, and the no-text-in-video rule for generated clips. Our gates (storyline,
  script, bases) are unchanged.
- **Any kaomei media:** no frames, GIFs, sample videos, voices (its examples use Google Gemini voices) or music. Our
  narration stays Kokoro-82M (Apache-2.0), our pictures FLUX.2 klein 4B (Apache-2.0), our sound synthesized.
- **Styles A1-A6 and B4**, and other references (amber noir, prehistoric, historical cartoon). Style 4, Cozy
  Storytelling, was adapted earlier for the legacy reel builder (see UPSTREAM.md).

## Where it lives

`pipeline/models.py` (format, `continuity`), `pipeline/script.py` (rules 7, 8 and 18, `pacing_aim`, the beat-stage
schema), `pipeline/qa.py` (`continuity_report`), `pipeline/package.py` and `stick package`, `styles/{lineart,retro-tv,colorful}/`.
Tests: `tests/test_director.py`, `tests/test_continuity_qa.py`, `tests/test_package.py`.
