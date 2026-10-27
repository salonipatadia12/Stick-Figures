## Prompt → ending → finished reel

The default browser flow now asks for a prompt and ending, then **Create my reel**.
Endings: happy, bittersweet, twist, funny, cliffhanger, tragic. Mood, duration and
manual review are under **More options**. The selected ending is sent as an explicit
story requirement. The app cannot objectively guarantee the literary quality of
that ending; the completed story stays editable.

Automatic mode makes one budget-capped story call, validates and stages the animated
characters, creates scene previews, synthesizes narration, adds captions, renders
and validates the MP4. It skips manual approval gates because the user requested
that workflow; records identify the approval source as `automatic_workflow_requested`.
No new paid images are purchased. Manual review remains available, and existing
projects retain their original workflow. API clients opt in with
`workflow: "automatic"`, `render_style: "rigged"`, and `ending: "happy"` (or another
supported ending). Omitted workflow remains `review` for compatibility.

There are no automatic paid retries. A failed voice/render job preserves the story,
assets and approvals for a render-only retry. Local staging separates actor paths,
corrects standing-still poses, preserves continuous lighting and carried objects,
and keeps interaction targets within reach. Walks now lift alternating feet while
the other foot stays planted; held props no longer create a table automatically.
Expressions blend instead of switching at one frame. These are improvements to the
existing 2D rig, not a replacement for authored character acting or arbitrary action
animation. Narration/action semantic alignment still requires further evaluation.

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
manual workflow. An Anthropic or OpenAI key enables prompt-to-story. New AI illustrations
require OpenAI; animated characters and preset backgrounds render locally. If a password is configured, the browser asks for the username
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
The selected narrator voice is reserved for narrator turns. Hero and friend each
receive one of the other two voices, consistently across their turns. This is
automatic role casting, not voice cloning or character-specific emotional delivery.
The audio cache includes the chosen voice, and uploaded recordings take precedence.
New rigged-story prompts request 5–8 speaking turns: brief narrator setup and actual
back-and-forth dialogue with a listener reaction. Existing scripts and rendered
videos are not rewritten automatically; rerendering applies the new voice casting.
This uses audio amplitude, not phoneme-level lip sync. A dialogue scene should contain
only that character's spoken words. Captions use estimated phrase timing.

**Prompt-to-story:** configure Anthropic or OpenAI on the server. Both use the same
story schema, action vocabulary and continuity validation. Drafts still require
story approval and storyboard approval. Provider contracts and the Anthropic-only application flow are tested with mocks.
A live automatic prompt-to-MP4 run completed with Anthropic, online narration,
local character rendering and captions. The saved example is The Poem in Her Pocket.
The pipeline is functional; arbitrary prompts can still produce unsupported narrated
actions or inaccurate dialogue assignment, so animation/story quality needs further work.
Manual editing, imported stories, backgrounds and character animation need no credits.

### Anthropic setup

Edit your local `.env` (already ignored by Git):

```dotenv
STORY_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key-here
ANTHROPIC_STORY_MODEL=claude-sonnet-5-5
```

Use a workspace-scoped key, or set `ANTHROPIC_WORKSPACE_ID` if your key requires it.
Restart the server with `.venv/bin/uvicorn studio.app:app --env-file .env --host 127.0.0.1 --port 8000`,
then refresh the studio. **Draft my story** becomes available and Setup shows
Anthropic configured. This reports configuration, not a successful authentication
check; invalid keys produce a readable error on drafting. Never paste keys into
story prompts or commit them.

`STORY_PROVIDER=auto` selects Anthropic when its key exists, otherwise OpenAI.
An explicit `anthropic` or `openai` selection never silently falls back to the other
provider. OpenAI retains `STORY_MODEL`; Claude uses `ANTHROPIC_STORY_MODEL`, so an
older `.env` with a GPT model cannot accidentally send that model to Anthropic.
Claude generates story text and structured scene directions. It does not generate
new raster artwork here; existing art, preset backgrounds and uploads work without
an OpenAI key.

### $0.25 story drafting limit

The server enforces a maximum **$0.25 API token budget per new story draft**, with
only $0.20 allocated and $0.05 held back as a safety margin. Before generation it
counts input tokens (including the story schema), adds a 1,024-token overhead
allowance, and limits the output to the remaining budget or 8,192 tokens, whichever
is smaller. If fewer than 2,048 output tokens fit, drafting stops before spending.
Unknown model prices, unavailable token counts and invalid counts also stop the
request. There are no automatic paid retries, repairs, provider fallbacks, premium
service tiers, or server tools. A failed or truncated draft can still incur the
cost of its one request. Clicking Draft again creates a new draft with a new budget.

Rates verified on 2026-10-25: Claude Sonnet 5.5 $2 input / $10 output per million
and GPT-4.1 mini $0.40 / $1.60. These are standard token rates; account taxes and
future provider price changes are outside application control. Update
`STORY_RATES` against official pricing before enabling another model. This limit
covers story writing and directions, **not separately approved new AI illustrations**.
The reusable animated workflow uses bundled artwork and no paid image calls.

References: [Anthropic model pricing](https://platform.claude.com/docs/en/models/overview),
[Anthropic token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting),
[OpenAI model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

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
# Set a strong APP_PASSWORD. Add ANTHROPIC_API_KEY for story drafting if desired.
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
APIs, plus the [Anthropic Messages API](https://platform.claude.com/docs/en/api/overview)
and [tool input schemas](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools).
Model settings are `ANTHROPIC_STORY_MODEL`, `STORY_MODEL` and `IMAGE_MODEL`;
story model changes require verified pricing in the budget allowlist.

## Café-style artwork and motion revision

### Dialogue acting test

`studio/examples/listening.json` is a four-turn performance test: narrator setup,
Mia's question, Theo's reply, and Mia's response. Render it with
`.venv/bin/python scripts/render_dialogue_test.py`. The result is saved under
`outputs/dialogue-acting-test/`; the first run uses online TTS and subsequent runs
reuse the cached audio. No story or image API call is made by this script.

The reusable renderer now adds a directed gaze change, slight posture lean, one
listener acknowledgment, brief coordinated blinks, a reading-height page grip,
and a smoothed speech envelope. Heads retain constant dimensions. The new optional
`animation.framing` is `two_shot` (default) or `speaker` (a fixed closer shot, selected
in the scene editor). These improvements use the existing painted rig; they do not
reproduce the detailed bespoke café artwork, full 3D turning, or phoneme lip sync.
The generated plan still determines which emotion and action is performed.

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


### Reliable Claude scene plans

Claude now uses `strict: true` on the story tool with an API-compatible schema.
Unsupported numeric, length and array limits are described in the schema and still
enforced locally. All fields are required in the API schema to stay within the
optional-field grammar limit. Exact enum values are preserved; casing alone is
normalized because the provider documents that enum capitalization can vary.
Custom prop, timing, reach and continuity rules still run before accepting a draft.

Sonnet 5.5 uses `thinking: {type: "between_tools"}` so a short story does not spend
its output allowance on up-front reasoning. Both preflight and generation carry
this setting. The token and dollar caps remain unchanged. No automatic repair
request is made when a candidate fails validation.

Returned candidate data (when present), token usage and specific validation failures
are saved in the project's local `draft-attempt.json`, without request headers,
API credentials or model thinking. This artifact is excluded from browser file
access and Git. It enables local corrections without paying for another response.
The UI distinguishes a failed draft from an actively running job.

See [strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use),
[schema limitations](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#json-schema-limitations),
and [Sonnet 5.5 reasoning settings](https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5).
