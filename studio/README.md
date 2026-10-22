# Stick Figures · Story Studio

A browser app for the illustrated storytelling style developed in this repository.
Enter a prompt, review/edit the story, approve it, review the storyboard, approve it,
and render/download the narrated portrait reel. Projects and partial generation
results are saved on disk. The finished, eye-corrected **The Empty Chair** is included
as a no-generation demo. Its existing 49-second video is played as-is.

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

## Two workflows

**AI assisted:** enter a prompt and choose mood/approximate length. Drafting makes
one structured story request. Edit its narration, scene directions and continuity
notes, then approve. “Generate illustrations” makes one image edit request per
missing scene plus a bounded vision request to locate heads for subtle motion.
Every shot references the selected character; later shots also reference the first
scene. Generating images may incur API charges; the button states how many new
images it will request. There are 3–8 scenes, no automatic paid retries, and partial
work is reused on an explicit retry. Account/model access and sufficient quota are
required. No key was configured in the development session, so paid live generation
was not exercised; provider contracts were tested with mocks.

**Manual:** choose “Write it myself”, write 3–8 scenes and approve the story. Upload
PNG/JPEG/WebP illustrations per scene. Upload optional audio recordings per scene
to use your own voice. If all audio is supplied, rendering works offline without
AI or speech-service requests. Otherwise missing voice recordings use the free
online Edge TTS service, with one consistent Andrew voice at rate -8%. Its availability
is external and not guaranteed. Audio follows actual recording duration, so chosen
length is guidance, not a forced cutoff.

After every scene has an image, approve the storyboard and render. Download MP4,
SRT, and the editable story JSON. The renderer outputs 720×1280, 24 fps, H.264/AAC,
with readable captions, intact illustrated eyes and small head motions where
landmarks are available. It checks the final video with FFmpeg before marking it
ready. Uploaded art stays still by default. Narration captions are phrase-based
timing estimates; uploaded speech should match the scene text.

## Approval and persistence

- Editing story content invalidates both approvals, existing scene selections,
  voice selections and output. Approve the new story and review its new storyboard.
- Replacing an illustration invalidates storyboard approval and output.
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

This makes illustrated 2D reels, not continuous generated action footage or
lip-synced dialogue. Image references improve continuity but generated art still
needs human review, especially hands, props and recurring settings. No blink
repainting is performed, avoiding the earlier dark-eye-mask artifact.

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
