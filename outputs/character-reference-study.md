# Character redesign — reference study

Reviewed 18 October 2026. Counts are the public numbers displayed during this review; views were rounded by YouTube. These are selected high-engagement examples, not an exhaustive ranking of every animation on YouTube. Popularity, publication age and audience differ; engagement does not establish that a visual choice caused success.

| Reference | Views shown | Likes shown | Review |
|---|---:|---:|---|
| [Bahaa AI — Why Your Stickman Animation Videos Fail?!](https://www.youtube.com/watch?v=gly2wBcnZUQ) | 98K | 1,977 | Read the auto transcript; sampled the finished example around 18:47–18:52. Simple eyes, bold outlines, readable hand gestures, restrained color. Its example uses illustrated scene changes, not continuous character acting throughout. |
| [Mark Ai Guy — How to Create Stickman Animation with AI 100% FREE](https://www.youtube.com/watch?v=H8FAaKeCguA) | 198K | 4,683 | Read the auto transcript; sampled the opening and completed vertical example around 1:25–1:30. Character identity stays recognizable, props are held clearly, expressions and clothing distinguish the pair. |
| [Alan Becker — Cave Spider Roller Coaster](https://www.youtube.com/watch?v=pCBP4M08ndE) | 539M | 3,053,644 | First result in the supplied search after selecting Popularity; sampled opening poses and prop interaction. Strong silhouette, clear bent joints and feet grounded in the scene. This is a long-form action reference, not the same format as a narrated reel. |
| [Alan Becker — Faces, An Actual Short](https://www.youtube.com/shorts/VKXMICY7l_g) | 87M in search | 3.5M | Sampled the short's neutral stance and face/celebration change. Body pose carries the reaction, even with very few facial details. |

The popularity search also showed Animation vs. Minecraft (original), 352M views; Note Blocks, 236M; and Animator vs. Animation 4, 204M. Their search metadata was reviewed, but they were not additional visual studies. Likes were not obtained for these three, so they are not ranked by likes.

## Why the earlier character was weak

The old figure's outlined eyes, pupils, eyebrows and hair competed for attention. The thin body did little acting. Many arms were generic curves with unclear elbows, and facial changes carried too much of the story. Repeated large message boxes emphasized text over action.

## Implemented design

- An original rounded head with simple solid oval eyes and a more economical expression system.
- Stronger black limbs, round stroke ends, small hands and compact feet.
- A small teal scarf as a consistent identity cue; restrained teal and warm wood props.
- Two-segment arms with constant lengths; reaching poses solve the elbow from the wrist contact.
- Fixed feet, deliberate pose settling, short blinks, restrained nods and a single celebration gesture.
- Writing makes visible marks; the pen follows the writing hand. Phone motion stays attached to the hands.
- Softer message bubbles and supersampled outlines.
- All four reusable layouts updated. Existing JSON stories default to the new character; `character_style: "classic"` preserves the previous renderer.

These are design interpretations of the references, not a copy of their character assets or a prediction of engagement. The references use different production methods; this implementation remains an editable local Python/Pillow/FFmpeg animation system. No tutorial software was installed and no existing animation footage was reused.

## Deliverables and comparison

`character-redesign.png` shows the old and new characters at comparable scale. `character-pose-sheet.png` shows four poses. `the-first-page-redesigned.mp4` uses the same story and narration as the earlier First Page reel so the character redesign can be judged directly. This is still a constrained everyday-story renderer; it does not reproduce complex frame-by-frame action choreography.
