# Stick Figures · Story Studio

A browser app for the illustrated storytelling style developed in this repository.
Enter a prompt, review/edit the story, approve it, review the storyboard, approve it,
and render/download the narrated portrait reel. Projects and partial generation
results are saved on disk. The finished, eye-corrected **The Empty Chair** is included
as a no-generation demo. The revised 40-second cut is played as-is; the earlier
49-second version remains in the repository for comparison.

The story opens as a readable script, with scene editing available when needed.
The storyboard uses a filmstrip and a single scene in focus, with previous/next
navigation. These views preserve both explicit approval gates. The final step is
a screening and download view. New briefs can start from a few editable idea prompts.

## Start on your computer

Requires Python 3.12+ and FFmpeg (`ffmpeg` and `ffprobe` on PATH).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-studio.txt
cp .env.example .env
# Edit .env locally if you want AI generation or password protection.
.venv/bin/uvicorn studio.app:app --env-file .env --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. No API key is necessary for the finished example or the
manual workflow. A key configured on the server enables prompt-to-story and new
illustrations. If a password is configured, the browser asks for the username
(`owner` by default) and password. Keys are never returned to the browser.

## Reusable animation workflow

Choose **Reusable characters · animated actions** (the browser default). Try the
editable example, import a story JSON from `studio/examples/`, write your own story,
or draft from a prompt using a configured AI account. The default API Brief remains
`illustrated` for older clients; new clients should send `render_style: "rigged"`.

1. Read the story, then **Edit the scenes**. Set narration and visual intent.
2. For each scene choose its background (café, park, street, home, office or stage),
   lighting (day, sunset or night), speaker, characters, expressions and action.
   Character colors, names, accessories and narrator are under **Characters & narrator**.
3. Add any objects by ID. An action uses that same ID. For example, `pick_up` with
   `gift` starts with that object on the table; the next scene starts with `gift`
   held by the hero. `give` transfers it to the other character, who must be idle.
4. Approve the story. **Build animated scenes** uses local code with no AI request.
5. Play or scrub each action preview. Upload an independent background image for
   any scene, or restore its preset. Important content should fit a portrait crop.
   Optional voice recordings replace online narration, one recording per scene.
6. Approve the storyboard and render. Download MP4, SRT and editable story JSON.

The library supports idle, walk, sit, stand, wave, talk, point, write, pick up,
put down, give and laugh. Objects: book, bag, cup, flower, note and ball. Use one
primary action per actor per scene; add another scene for a subsequent action.
Each actor has a starting emotion and optional ending emotion (changes at 65% of
its action window). Action start/end controls place the movement within the scene.
Before and after that window, the character holds its appropriate pose. The preview
uses the same timing fractions as the final video. A book-writing action moves the hand and pen along the visible ink
path. Heads stay rigid, with at most a small whole-head rotation for a greeting. There is
no head scaling or image warp. Neutral hands rest beside the body. Elbow bends use
a consistent joint solution, and waves follow authored shoulder/forearm angles so
the elbow cannot flip sides as the hand crosses shoulder height. The renderer validates reachable hands and checks prop
ownership across consecutive scenes. These checks do not understand all narration;
review the storyboard to confirm that the words describe the visible action.

**Backgrounds are independent.** Presets and uploaded images never bake characters
into the backdrop. Upload a background without people for best results. It is a
static image behind the animated figures. The default reusable renderer now uses painted character cels and background plates
based on the approved café reference. Heads, hands and torso are separate rigid
parts; limb joints and facial expressions are animated locally. It remains a 2D
rig, rather than generated cinematic footage. Arbitrary fights,
complex choreography, moving cameras, arbitrary objects and multi-action sequences
inside one scene require extending the action library. The scene's visual prose is
review guidance; structured animation controls determine what is actually drawn.

**Voice:** select Andrew, Ava or Sonia, or upload recordings. Online narration uses
Edge TTS at -8%, an external service whose availability is not guaranteed. If all
recordings are uploaded, rendering is fully offline. Audio-responsive mouths move
only for the selected character speaker; narrator scenes do not make everyone talk.
This uses audio amplitude, not phoneme-level lip sync. A dialogue scene should contain
only that character's spoken words. Captions use estimated phrase timing.

**Prompt-to-story:** configure `OPENAI_API_KEY` on the server. Drafting makes a single
structured request that is constrained to supported actions and prop continuity.
It may incur provider charges. There is no API key configured in this development
environment: live paid story drafting was not tested; provider contracts are mocked.
Manual editing, imported stories, backgrounds and character animation need no credits.

Story JSON includes characters, voice, settings and directions, but not uploaded
media. Back up `studio-data/` to move complete projects, or import JSON and re-upload
backgrounds/audio on another server. Changes to the story invalidate old media selections
and approvals, so finish directions before uploading media.

## Existing illustrated workflow

Choose **Illustrated scenes · still artwork** to use the existing illustration
pipeline. AI image generation uses the selected character reference and the first
scene as continuity references. Missing images can be generated with a configured
API key or uploaded manually. Paid generation reuses existing successful images.
The original artwork is held still; head warping is disabled. The completed café
example remains available as its original bespoke animation.

Both modes render 720×1280 at 24 fps, H.264/AAC, with narration, captions and a final
FFmpeg decode check. Actual voice length sets scene duration; the brief's requested
length is guidance. Stories contain 3–8 scenes and at most 360 spoken words.

## Approval and persistence

- Editing story content invalidates both approvals, existing scene selections,
  voice selections and output. Approve the new story and review its new storyboard.
- Replacing an illustration or background invalidates storyboard approval and output.
- Adding audio invalidates output; it leaves the visual approval intact.
- Active jobs reject edits and duplicate render/generation requests.
- Jobs interrupted by a restart are marked interrupted; the server does not
  automatically repeat potentially billed generation calls.

Project JSON, selected images, audio and renders live in `studio-data/` (ignored by
Git), or `STUDIO_DATA_DIR`. Back up this folder/volume. The implementation is for one
owner and one application process, using a single queued worker. Do not run multiple
Uvicorn workers or replicas against the same JSON directory. A larger multi-user
service needs a database, separate job workers, user accounts and storage quotas.

## Run on a server

The app needs a container/VM with FFmpeg and persistent disk; short-lived static or
edge hosting cannot execute its renderer. A Dockerfile and Compose definition are
included:

```sh
cp .env.example .env
# Set a strong APP_PASSWORD. Add OPENAI_API_KEY for AI generation if desired.
docker compose up --build -d
docker compose logs -f studio
```

Production refuses to start without `APP_PASSWORD`. Compose binds only to server
loopback by default. Put a TLS reverse proxy in front of port 8000 for a remote
HTTPS URL, preserving the Host header, or access it through an SSH tunnel. Keep the
provided volume mounted at `/data`, and run one replica. Never publish the `.env`
file. No cloud account, domain, paid server or public deployment is provisioned by
this repository. The local preview runs on the machine that starts Uvicorn.
Compose configuration was validated during development. The Docker daemon was not
running on that machine, so the image build/container startup was not exercised.

## Limits and tests

Two example scripts exercise different settings and voices: `studio/examples/kindness.json`
(prop pickup and handover) and `studio/examples/first-hello.json` (wave, dialogue,
laughter, office and stage). Test coverage includes the complete offline animation
pipeline, background replacement, approvals, import/export, fixed heads, hand/object
contact, pen/ink motion, dialogue mouths and every supported action.

```sh
.venv/bin/python -m pip install httpx
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover approval invalidation, missing-key behavior, job deduplication,
persistence/restart recovery, authentication, media/path access, mocked AI
contracts, and a real offline render with uploaded illustrations/audio.

Implementation follows the official [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and [image generation](https://developers.openai.com/api/docs/guides/image-generation)
APIs. Defaults are configurable via `STORY_MODEL` and `IMAGE_MODEL`.

## Café-style artwork and motion revision

`studio/assets/illustrated/` contains the generated source atlases and extracted
head, torso, relaxed hand, open hand and six painted backgrounds. These were made
with the built-in image-generation tool, using `outputs/character-focus-reference.png`
as the style reference. Exact prompts are saved in `prompts.json`. Regenerate the
registered crops with `python scripts/prepare_illustrated_assets.py`; the crop bounds
are tied to the checked-in source atlases. All assets are bundled with the app and
need no image service at runtime. Character body color and scarf accent remain editable.

A fixed, character-focused composition replaces the old distant full-frame view.
It does not animate a zoom. Custom background uploads still work, and lighting
choices grade the static plate. Images may be cropped to fit that composition.
The new example renders are `outputs/reusable-first-hello-cafe.mp4` and
`outputs/reusable-kindness-cafe.mp4`; the earlier flat renders are retained for comparison.
Motion checks cover continuous elbows, constant arm segment lengths, a settled
beginning/end and modest speaking gestures. Visual inspection is still necessary;
these checks cannot certify that every arbitrary story looks natural.
