# Upstream attribution and integration

Source: https://github.com/kaomei/stickman-video-director

Reviewed revision: `5841335db851b5f0352cf77955147393b97beabc`
Retrieved: 2026-10-20. License: MIT; the original notice is in `LICENSE`.

`cozy-storytelling.md` and `voice-continuity.md` are unmodified upstream reference
documents. They are retained for provenance and future direction. They are not
installed as executable skills.

## Applied in this project

The original local renderer in `builder/cozy.py` adapts Style 4's visual contract:
white circular head, simple dark eyes, closed white tapered torso, single-line
limbs, dark hands/feet, and full-color cartoon environments. It also applies the
repository's guidance on purposeful wide/close/detail cuts, consistent props,
visible hand contacts, restrained actions, and one narrator identity.

The sample retains the existing Andrew narration recordings. It does not
regenerate or improve that performance. Voice continuity here comes from
reusing the same voice/rate and cached takes; scene-level synthesis may still
have differences in delivery.

The upstream project is a directing skill and prompt library for Flow/Gemini,
not a Python animation engine. This implementation does not run Flow, Gemini,
Omni Flash, or copy their generated video assets. Local rendering uses
Pillow/FFmpeg and needs no generation credits. Its motion is procedural and
its current cozy layouts support notebook stories only.

Upstream approval gates and ten-second clip requirements belong to its
external generation workflow. This adaptation uses an editable JSON storyboard
and audio-length scenes. The director guidance informs the implementation;
it is not a claim of executing the upstream skill verbatim.
