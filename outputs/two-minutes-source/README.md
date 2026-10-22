# Two Minutes — short illustrated reel

New story, 10.5 seconds, three different scene illustrations and four cuts:

1. Daylight study: a quick phone break.
2. Nighttime sofa: absorbed in the next video.
3. Sunrise bedroom: the morning alarm interrupts the scrolling.
4. Closer reaction in that final scene for the punchline.

The protagonist follows the character design the user selected. Each scene changes
pose, framing, lighting and environment. The local renderer adds small head/grip
movements, eyelid cels and alarm vibration. These are illustrated shots with restrained
2D animation, not externally generated full-motion video.

Narration:

> She took a two-minute break. Just one more video. When she looked up, her morning
> alarm was ringing.

One continuous Andrew neural recording, rate -20%, with a 0.25-second lead-in. The
short pauses at the end leave time for the reaction and alarm. Captions, time labels,
scroll ticks and a soft alarm are assembled in postproduction. Sounds are synthesized
locally; no external music is used.

## Rebuild

From the repository root:

```sh
outputs/the-message-source/.venv/bin/python outputs/two-minutes-source/render.py --preview
outputs/the-message-source/.venv/bin/python outputs/two-minutes-source/render.py
```

The included images and narration allow offline rebuilding using Python, Pillow 13
and FFmpeg. No Flow account, generation credits or paid API is required to rebuild.
Output: `../two-minutes.mp4`, 720×1280, 24 fps, H.264/AAC.

## Reference research and limits

Public search results were reviewed on 2026-10-21 for narrated stick-figure topics.
A recurring current theme was compulsive scrolling, alongside procrastination and
overthinking. This is a topic-pattern observation, not a verified global popularity
ranking. YouTube pages could not be fetched directly during this check, and reliable
comparable live view/like counts were not available. Promotional claims about viral
reach or retention were not treated as evidence. No claim that this exact story is
the highest-engagement format is made.

Useful indexed examples:

- StickFury, "One More Video…", August 21, 2026: a 27-second late-night scrolling
  short, indexed at https://salda.ws/video.php?id=Vr9B1M0pEuU
- StickAnime, "Phone Addiction: 7 Signs You're Hooked and How to Break Free",
  October 8, 2026, indexed at https://salda.ws/video.php?id=MCqCrzLP84Y
- Euro entertain, "He Wasn't Addicted to His Phone… Until He Realized This",
  August 22, 2026, indexed at https://salda.ws/video.php?id=lwh9WpbCmiU

These references informed the theme only. Script, illustrations, sound design and
assembly here are new. The earlier notebook story is not used in this reel.

## Image generation

Three project assets were created with the built-in image-generation tool, using
`../character-focus-reference.png` only for character identity. Prompts are in
`asset-prompts.md`. Character appearance was kept consistent in intent; the generated
shots vary their pose, camera angle and expression.
