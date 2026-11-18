# Web app, queue and Mac worker: the contract

Goal (user, 2026-11-10): from a phone, type a topic, make the picks, and download a QA-passed video, with the VPS and
the Mac worker running. This file is the contract the three build lanes share (B web app, C deployment + Mac worker,
D tests). Change it only through the coordinator.

## Shape

```
phone ──HTTPS──► VPS (Docker Compose, small)                       Mac (Apple silicon)
                 caddy (TLS) ─► web (FastAPI, HTML + JSON API)      macworker (launchd)
                                 ├─ runner: research/storylines/      │ pulls jobs over HTTPS,
                                 │  script (only SCRIPT_PROVIDER=      │ one job at a time:
                                 │  anthropic; idle with ollama)       │ pitch, script (ollama: Ollama on the Mac)
                                 ├─ postgres: episodes, jobs, worker   │ paint (FLUX scenes/props)
                                 └─ /data volume: episodes/, outputs/  │ render (voice, compose, QA)
                                         ▲                            │
                                         └──── outbound HTTPS only ───┘
```

- **SCRIPT_PROVIDER** (2026-11-10, Anthropic credits ran out): `ollama` (the default) moves research/storylines
  (`pitch`) and the script (`script`) to the Mac worker, which runs them with the pipeline's free local model
  (Ollama + qwen3.5:9b, a Homebrew service on the Mac); the VPS only queues them. `anthropic` is the original paid
  path on the VPS runner, unchanged. The rest of this file describes both; where they differ it says so.

- The VPS never paints or renders. It runs the web app, the queue, Postgres, the light pipeline steps under
  `SCRIPT_PROVIDER=anthropic` (Anthropic calls: research, storylines, script, through the existing ledger and caps)
  and stores the outputs.
- The Mac does all heavy work: painting (FLUX inserts/props) and rendering (Kokoro voice, compositing, Whisper QA),
  and under `SCRIPT_PROVIDER=ollama` research, storylines and the script on its local model.
  It pulls jobs; nothing connects into the Mac. Jobs wait in the queue while the Mac is offline or asleep.
- One heavy job at a time on the Mac (a single queue; the worker claims one job, finishes it, then claims the next).

## Repository layout (one owner per directory, so lanes merge cleanly)

| Path | Owner | What |
|---|---|---|
| `webapp/` | B | FastAPI app (`webapp.app:app`), DB, queue, VPS runner, templates, static |
| `tests/webapp/` | B | unit tests (SQLite or a throwaway Postgres; no network, no paid calls) |
| `macworker/` | C | the Mac worker package (`python -m macworker`), job handlers, Keychain token |
| `deploy/` | C | `compose.yaml`, `Dockerfile.web`, `Caddyfile`, `.env.example`, `install.sh`, `backup.sh`, `rollback.sh`, `launchd/` plist + `install_worker.sh` |
| `tests/worker/` | C | worker unit tests (fake server, no FLUX) |
| `tests/e2e/` | D | end-to-end test (topic → MP4 through the HTTP API) with a `fake` mode and a `real` mode |
| `deploy/smoke_test.sh` | D | post-deploy smoke test |
| `pipeline/`, `scripts/`, `styles/`, `episodes/` | coordinator | the existing pipeline: lanes call it, they do not change it (ask the coordinator) |

The old `studio/`, root `compose.yaml` and root `Dockerfile` are a retired experiment: do not touch or reuse them.

## Configuration (environment; secrets only in `/srv/stick/.env` on the VPS, never in git)

| Variable | Used by | Meaning |
|---|---|---|
| `DATABASE_URL` | web | `postgresql+psycopg://stick:...@postgres:5432/stick` (tests may use `sqlite:///...`) |
| `STICK_DATA_DIR` | web | `/data`; episodes in `/data/episodes` (also exported as `STICK_EPISODES_DIR`), outputs in `/data/outputs/<slug>/` |
| `APP_USER`, `APP_PASSWORD_HASH` | web | the single login; hash = `scrypt$<salt hex>$<hash hex>` (n=2**14, r=8, p=1, dklen=32); `python -m webapp.hashpw` prints one |
| `SESSION_SECRET` | web | cookie signing key |
| `WORKER_TOKEN_SHA256` | web | sha256 hex of the Mac worker's bearer token (the VPS never stores the token) |
| `SCRIPT_PROVIDER` | web, runner | `ollama` (default): `pitch`/`script` are Mac jobs; `claude-code`: Mac jobs too (the Mac's own setting decides, item 23); `anthropic`: VPS runner jobs (paid). Both containers read it from the same env file |
| `ANTHROPIC_API_KEY` | web runner | the only paid key; needed only with `SCRIPT_PROVIDER=anthropic` |
| `PUBLIC_URL` | web, caddy | e.g. `https://187-127-179-129.sslip.io` (a real domain later) |
| `SPEND_PREAPPROVED_USD` | web | `0.50`: an Anthropic step that would take a video past this total stops and asks in the UI |
| `STICK_SERVER`, token in Keychain (`security ... -s stick-worker`) | macworker | where to pull jobs from |
| `OLLAMA_HOST` | macworker | default `http://localhost:11434` (checked with `GET /api/version` before every pitch/script job) |

## Data

Postgres tables (SQLAlchemy 2 Core or ORM, psycopg 3; migrations by a simple `CREATE TABLE IF NOT EXISTS` bootstrap is fine):

- `episodes`: `slug` (pk), `topic`, `niche`, `audience`, `state`, `created_at`, `updated_at`, `cost_usd`, `error`.
  The episode itself stays the pipeline's `episode.json` + `ledger.json` under `/data/episodes/<slug>/` (the
  pipeline's `store` reads/writes it; `STICK_EPISODES_DIR=/data/episodes`).
- `jobs`: `id`, `slug`, `kind` (`pitch`, `script` run on the Mac with `SCRIPT_PROVIDER=ollama`, on the VPS runner
  with `anthropic`; `paint`, `render` always run on the Mac), `status`
  (`queued`, `running`, `done`, `failed`), `payload` json, `result` json, `progress` json (`stage`, `pct`, `message`,
  `at`), `attempts`, `lease_until`, `created_at`, `started_at`, `finished_at`, `error`.
- `worker`: one row: `last_seen`, `host`, `version`, `busy_job_id`.
- `files`: `slug`, `name`, `kind` (`mp4`, `srt`, `qa`, `contact_sheet`, `scene_preview`), `size`, `created_at`.

Episode states: `researching` → `storylines` (gate) → `scripting` → `script` (gate: hook + approve) → `painting`
→ `scenes` (gate: approve new scenes, or redo one with a note) → `rendering` → `done` | `done_qa_failed` |
`failed` (with `error`; a retry button re-queues the failed job; paid steps, i.e. pitch/script under `anthropic`,
never retry on their own).
If painting finds nothing new, the episode goes straight to `rendering`.

## Gates (the user decides, unless the user started the video with Auto on)

- **storyline**: show `pipeline.story.view` content as cards (format, hook, pitch, conflict/stakes/twist/payoff,
  facts with links). Pick = the pipeline's storyline gate (rotation rule: a refused format shows the reason).
- **script**: show the script (`pipeline.review.script_view` content, laid out for a phone: beat cards with SAY,
  SHOW, HOST, sources), the three hook options as radio buttons, the story check and validation issues. Approve is
  disabled while `Episode.check` has errors. Approve = `pick_hook(n)` + the script gate, recorded `by='user (web)'`.
- **scenes**: thumbnails of every newly painted scene/prop (uploaded by the Mac), each with its beats and scene
  text. "Approve all" queues `render`; "Redo" on one scene takes a short note and queues `paint` with
  `payload.redo = {key: note}` (the Mac repaints that key as a round-2 art direction, `inserts.jobs(rnd, direction)`).
- Every approval writes the pipeline's own approval log entry.

## Auto mode (user 2026-11-13)

The New Video page has an **Auto** switch (`POST /episodes` field `auto=1`; `POST /api/episodes {"auto": true}`;
stored as `episodes.auto`). With it on, the topic goes to a finished MP4 with no stops. After every step
(`Service.after_step`) the gate it reached is decided by `pipeline/auto.py`, through the same gate code, logged with
`by='auto (web)'`:
- storyline: the first storyline the gate accepts (the rotation rule still applies);
- script: approved only if every check passes, trying the hooks in order; a beat whose base is not in the library
  becomes a painted scene of the same place (the paint step makes it). Before judging, auto fixes the local
  writer's mechanical slips (`auto.fix_script`): anchors the narration does not say, a split with no top scene, a
  picture with no kebab-case key (named from its scene or place), and a generic base (a living room) in a video set
  mostly in one real place (shown as that place, painted, with the host in it);
- scenes: accepted as painted.

When a rule cannot decide (a script that fails its checks, a scene that failed QA, every storyline refused) the episode
stays at that gate with `episodes.auto_note` saying why; the user acts there as usual, and auto carries on after. A
paid spend stop (`spend_approval`) is never passed automatically. The episode page shows a stepper (Research,
Storyline, Script, Paint, Render, Done) above the live job progress. Without Auto every gate waits for the user.

**Notifications (Web Push, `webapp/push.py`):** the phone is notified when a video is done (QA passed or not), when a
step fails, and when Auto stops at a gate. No account or paid service: the server signs with its own VAPID key pair
(`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` in `/srv/stick/.env`; `python -m webapp.push keys` makes one) and the
browser's push service delivers. `/sw.js` (service worker), `/manifest.webmanifest` (installable),
`GET /api/push/key`, `POST /api/push/subscribe|unsubscribe|test`. On an iPhone, add the site to the Home Screen and
turn notifications on from there (iOS 16.4+).

**Render again:** a finished episode (`done`, `done_qa_failed`) can be queued for a new render with the same approved
script and scenes (`POST /episodes/{slug}/render-again`, `/api/episodes/{slug}/render-again`), e.g. after renderer
fixes on the Mac.

## Topic understanding (user 2026-11-14)

The first step of every pitch, on the Mac's local model (`pipeline/understand.py`, cached). A topic typed casually
("how is crude oil naturally forming in th earth and not comig from the dinosaurs") becomes: the real question,
the claims inside it, the misconception a viewer likely holds, the most interesting true angle, a clean title and
slug, and the picture subjects that show the myth. Research searches the question and each claim; each claim is then
labelled supported, mainstream-disputed, fringe/false or unverified from the cited facts. A claim that comes back
unverified because no fact mentions it gets two targeted searches of its own on .edu/.gov pages (`story.claim_followup`;
"dinosaur myth crude oil origin" found only blogs, which are never cited, until it was searched there); new facts join
the research (`meta.claim_facts`, so the script step has them under the same ids) and the claim is checked again. It
never waits for the user.

- The episode moves from the typed slug to the clean one when the pitch comes back (`Service.rename_after_pitch`:
  row, jobs, files, spend approvals and folders); the typed slug stays in `episodes.renamed_from` and old links
  redirect (307). The page and the history show the clean title (`episodes.title`) with "Typed as: ..." under it.
- The storyline and script prompts get the interpretation (`understand.brief`): true claims are told straight; a
  myth or fringe idea is shown only as an idea. myth_vs_fact, mystery and revelation are offered first, and Auto picks
  one of them for a topic that corrects a myth. The script check fails a beat that states a disputed or false claim as
  fact without framing it.
- QA `story_match`: every painted scene must show what its beat says, and a picture of the myth must be marked as the
  myth on screen (fails; the self-repair loop adds the MYTH mark). A scene that shares no word with its beat or the
  topic is listed as a warning to look at, never repainted automatically.
- The episode page shows "What it understood": question, angle, misconception, each claim with its verdict.
- If the local model is down, the topic is used tidied, nothing is renamed, and the episode goes on.

## Fact-first writing (user 2026-11-14)

The facts are chosen before any narration is written (`pipeline/grounding.py`), so the fact check after writing
confirms instead of rewrites.

1. **Twist fact, in code.** After research and the claim checks, the fact that settles the topic's claim and names
   what the thing really is becomes the twist fact (crude oil: EIA's "remains of animals and plants (diatoms) ...
   before dinosaurs existed"). The storylines are pitched on it, and each storyline's twist is checked against it
   (else it becomes the fact as written). A sentence of a pitch, conflict, stakes or payoff that names a group of
   living things no fact names is cut. The model's own "angle" no longer reaches the writers.
2. **The plan.** The story roles come from the format and length (`skeleton`: hook, setup, problem, rising beats,
   twist, reveal, payoff, loop). The fact of every fact beat is assigned in code (`assign_facts`):
   - the twist beat gets the twist fact;
   - the problem gets the fact that breaks the myth;
   - the payoff gets the fact closest to the question;
   - the reveal gets the fact of how it works;
   - the clues get the most relevant facts left.

   Each fact is used once; near-duplicates and facts that share nothing with the topic are skipped. One local-model
   call writes each fact beat's **claim** in plain words and picks the **kind** of the other beats (question,
   transition, reaction, callback, myth). Those beats state no fact and cite nothing. Each claim gets the word check
   in code, then the yes/no judge against its fact's quote. After two repairs, a claim that still fails becomes the
   fact's own line from research. The plan is in `meta.fact_plan` and on the episode page.
3. **The outline** gets the plan, not the facts at large, and gives the pictures, the on-screen text and the hook
   options. The narration itself is chosen by **best-of-N** (`pipeline/bestof.py`, user 2026-11-15):
   - for each beat in order, one call writes 4 candidate lines in story voice from its claim and research (or its
     no-fact kind);
   - code drops a candidate whose claims fail the fact check, or that copies its research line, repeats another
     line, breaks the word rule or has the wrong length;
   - one more call ranks the survivors for vividness, rhythm and fit with the previous line.

   The hook and the loop get 6 candidates each, filled from shapes in `data/hooks.json` (25 hook shapes, 12 loop
   shapes, rotated per episode) and checked against the twist fact. A hook that argues against something nobody
   claims ("Stop calling it dinosaur poop") is refused. The loop must repeat a word of the hook. A beat with no
   survivor keeps its checked claim.

   User 2026-11-15 (the benchmark brief) added three filters, all in code:
   - **a hook opens the gap and never states the twist**: it may not name what the twist fact names (other than the
     myth's subjects), nor say most of the twist claim's words (`grounding.gives_away`). The writer sees the answer
     with "never say it", and the bank's shapes tease the answer instead of naming it;
   - **no two beats make the same claim**: each candidate's split claims are compared with every other beat's
     (`grounding.same_claim`: the shorter claim's content words, topic words aside, are almost all in the other).
     Only the payoff may restate the twist; the loop may echo the hook;
   - **the length is kept while writing**: no-fact lines may be short ("But there's a catch."), and the words they
     leave go to the fact beats still to come (`bestof.budget`), so the script reaches its length without padding;
   - **facts judged on topic** (bench run 1): a fact that shares a content word with the question is on topic; each
     other fact gets one yes/no call, "does it help answer the question?".
     Only those facts are told, and every one of them may be, whatever words it shares with the question. The ids
     of the rest are in `meta.fact_plan.off_topic`;
   - **no myth said as a fact**: a hook or loop that names the myth's words with no doubt in it ("It was boredom.")
     is refused when the judge says it states the misconception as true;
   - **beliefs only in the myth beat** (and the hook); a no-fact line counts as a claim for repeats; no two lines
     open with the same three words; no line starts mid-sentence or holds markdown; any line's numbers must be in
     its research;
   - **ranges stay as wide as the fact**: "thousands to millions of years" never becomes "thousands" or
     "millions", even when another cited fact says "millions" on its own, and a magnitude word or number the
     research does not have fails (`grounding.narrowed`). This runs
     in the claims check itself, so plan claims, best-of-N lines, rewrites, swaps and on-screen texts all get it.
4. **Labels from the claim** (user 2026-11-15). After staging, every on-screen text, split label, diagram label and
   label on a picture must use only words of its beat's narration or planned claim (plus "MYTH", "FACT" and other
   structure words), with no number the narration does not say, and at least one real word
   (`grounding.ground_labels`). A beat that states no fact shows only a phrase its narration says as it is. A text that does not
   is written again from them in one short call per beat, else dropped. So staging no longer invents labels for
   the fact check to remove. The counts are in `meta.label_grounding`.
5. **The writer model.** The fact plan's claims, the outline and the best-of-N candidate lines (hooks and loops
   too) use `OLLAMA_WRITER_MODEL`, which defaults to `OLLAMA_MODEL`. The judge, the claim splitter, the ranking,
   the labels and every other small call stay on `OLLAMA_MODEL` (qwen3.5:9b). On a 16 GB Mac the two models do not
   fit in memory together, so Ollama swaps them between calls. A bigger writer is judged on the benchmark
   (`bench/score.py`) before it becomes the default.
6. **Word precision.** When a fact names a group (diatoms, plankton, marine, organisms), a line uses that group's own
   words or a direct plain-English equivalent ("plankton", "tiny sea plants and animals", "microscopic"). It never
   uses a broader or different category ("bacteria", "microbes", "bugs"). This is a fixed table, checked in code on
   claims, narration and every on-screen text. The equivalents also go to the judge with the research. A group word
   that only marks a time ("the age of reptiles", "before dinosaurs existed") does not count.
7. **Source quality.** `script.source_quality` labels a page scientific (journals, science agencies), general, or
   simplified (pages for kids or students, course reports). Of two facts that say the same thing, the better
   source is told. A fact only a simplified page states is still used, with a note to phrase it with caution ("much
   of it", "most").
8. **Deeper research** (user 2026-11-15: "research depth is the bottleneck"). One short call splits the topic
   into 6 sub-questions: what it is, how it works, why it happens, a surprising detail, a number or scale, and its
   history or discovery (template ones when the model gives none). Each is searched on its own: the web, plus
   Wikipedia's reference links and OpenAlex abstracts. Up to 2 reputable pages per sub-question are read, each for up
   to 3 facts with that sub-question in mind. The facts are then taken in turn, one sub-question at a time, up to 14,
   so every angle is told before any gets a second fact. The counts are in `meta.research` (facts, per sub-question,
   the sub-questions) and the research notes. The target is 8-12 verified facts per topic.
9. **Length follows facts.** The plan counts the usable facts (on topic, near-duplicates dropped) and gives one
   fact beat to each, as many as the brief's length holds, plus the hook, setup, problem and loop. At most 3 beats
   besides the hook, setup and loop state no fact; extra empty middle beats are cut (`grounding.trim`). Thin facts
   make a shorter video, down to 30 s, never padding. The planned length (`plan.seconds`, from word estimates per
   beat) becomes the episode's length, so the outline, the word budget, the gate and the duration QA all use it. The
   brief's own length stays in `meta.length_plan`.
10. **The hook from the most surprising fact.** One call ranks the usable facts by surprise, and the hook is written
   from the top one. A hook is refused when it says a word of the twist fact's claim or quote that the topic,
   question and myth do not already say (`grounding.answer_words`: "stranger" in "from strangers to kin" gives
   "Your yawn came from a stranger" away). It is also refused when it points to no subject: no topic word or word of
   its fact, and the judge says it could open any video ("Believing this hides a secret.").
11. **Another app on the same Ollama.** Before each call, a model this pipeline did not ask for that is loaded on
   Ollama is waited out (at most 10 minutes, `OLLAMA_YIELD=0` turns it off). The wait is logged
   (`llm.CONTENTION`, and as "waited" in the benchmark). On 2026-11-15 another project's worker on this Mac used
   `ibm/granite3.3-guardian:8b`; the two models evicted each other for 25 minutes and one judge call took 863 s.
12. **Research degrades, not fails.** Each query starts at a different keyless engine (DuckDuckGo, Google, Bing,
   Yandex, Brave, Mojeek, Yahoo, Startpage). An engine that refuses goes to the back for five minutes, with backoff
   and jitter between tries. A `site:` query is retried without the operator. When every engine refuses, the same
   query from any episode of the last 30 days is used (the shared search cache). Wikipedia's API and OpenAlex's API
   are looked up from the start: Wikipedia only to find a topic's reference links, never cited itself; OpenAlex
   papers are cited by DOI. Both are keyless; OpenAlex shares a small free daily budget per IP address.

13. **Built around the answer (user 2026-11-15).** Topic understanding also writes the **core answer**: one plain
   sentence that answers the interpreted question ("Compound interest means your interest starts earning interest
   too"), and whether the question is about history. After research, the claims check is asked whether a fact backs
   it. When none does, two targeted searches are made for it (the claim follow-up's own search), and it is checked
   again. When still none does, the answer is written again from the facts themselves, citing them (one call). The
   result is `interpretation.core` = {text, fact_ids, how: supported, searched, from facts or unsupported}. The
   writers' brief names it, and the payoff stands on its fact.
14. **The twist from the core answer.** One call scores every fact on relevance to the core answer (1-5), surprise
   (1-5) and kind (what, how, why, effect, number, history, trivia), stored as `interpretation.fact_scores`. The twist
   is the most surprising of the most relevant facts (the top relevance, and one below it when the top is 5). It is
   never a side fact (history, trivia) unless the question is about history. The fact that backs the core answer is
   used only when no other fact is as relevant, because the payoff says it. With no judge, the code pick stays
   (`grounding.twist_fact`). The hook fact is the most surprising usable fact by the same scores (no extra call).
15. **Causal order and flow.** The reveal (how it works) now comes before the twist. The middle fact beats are
   ordered as a chain by their kind, from what it is to how it works to why to what it leads to
   (`grounding.chain_order`), then the twist, then the payoff. Each beat after the first is asked to follow from the
   one before it ("because", "so", "but", "which means"). After all lines are written, the flow judge scores every
   adjacent pair in one call. The second line of a pair scored 2 or lower is written again by best-of-N (at most
   3 lines), and the new line is kept only when the judge scores the pair higher. The log is under the script's
   best-of-N repairs ("flow b05: pair scored 1 ...").
16. **The core answer is said.** The payoff's candidates must say the core answer: a code check (two of its
   content words, one of them more than the topic's own name) and then the judge's yes/no. After the flow pass, and
   again after the fact check, the gate's story check "core answer stated" needs a reveal, twist or payoff line that
   says it. When none does, the payoff is written again by best-of-N, or becomes the core answer itself (which the
   research backs). The script gate fails a script with no line that says it.
17. **Hook giveaway by meaning.** The word match is replaced by one yes/no call per candidate hook: "does this hook
   reveal or strongly imply the answer, or any part of it?", asked with the core answer. On the 9B it judged 12 of 12
   labelled hooks right: "Your yawn came from closeness", "You caught that yawn from someone you love" and "Your car
   runs on ancient plankton" are refused; "Why does one yawn set off a whole room?" and "What is really in your fuel
   tank?" pass. The 9B is very sensitive to the prompt's wording: one wording with an example in it let every hook
   through. The word match stays only for a run with no judge.

18. **Whole-script best-of-N (user 2026-11-16).** "The line-by-line pipeline is over-constrained ... Simplify instead
   of adding checks." The fact-first outline stays: code picks the roles, the core answer, the twist and the one fact
   each beat says, and the outline names the single point each beat makes (`pipeline/whole.py`). The writer then
   writes 4 complete scripts, one call each, with a different hook shape and seed per script. Every line of every
   script goes through one list of checks: code first, and the judge only for a line that code passes. A script with
   no failing line survives. Survivors are scored on the judge's hook gap (1-5), the mean flow score of every pair of
   lines (1-5) and, in code, how close the script is to its length. The best one is kept. When no script survives,
   the one with the fewest failing lines is kept and only those lines are written again (best-of-N for that line,
   kept only if it passes). A pair scored 2 or lower has its second line written again, kept only if the pair scores
   higher. The one-line self-repair after the render (a word the voice does not say) uses the same checks.
   The old line-by-line writer (`bestof.write_all`) is no longer called.
19. **One fact, one beat; repeats by meaning.** Code gives each fact to exactly one beat. Two lines that say the same
   thing are found with a local embedding model (`nomic-embed-text`, Apache 2.0, on the same Ollama:
   `OLLAMA_EMBED_MODEL`; `pipeline/embed.py`). A pair repeats when its cosine is 0.78 or more, or 0.70 or more and
   still 0.20 or more once the script's shared subject is taken out (the vectors centred on the script's mean). Raw
   cosine alone cannot tell them apart, because every line is about the topic. The payoff may restate one line (the
   core answer, once). The myth beat, the hook against the payoff and the loop are never repeats. Without the
   embedding model, shared words stand in. Tuned on the 9b-core benchmark scripts and crude oil -8: there it flags
   the four lines that said "plankton was buried and heated into oil" and nothing else.
20. **Gentler fact check.** The claims check now rejects only a claim that adds something its fact does not make (a
   new cause, effect, thing, kind of living thing, place, time, number or certainty) or contradicts it. Plainer
   words, details left out, vivid verbs and what the fact plainly implies pass. Narrowed numbers and ranges still fail
   (in code). Tuned on the lines the 9b-core benchmark refused: "But time is the engine: longer periods mean way more
   earnings" (refused for omitting "savings account") now passes.
21. **No raw fallbacks.** No line is ever replaced by a research sentence or a stock line ("Now you know.", "But
   there is a catch.": a code check refuses them in every script and rewrite). When no script and no rewrite gives
   a line that passes, the beat is dropped if the video stays above 30 s and the story keeps its shape (never the hook,
   the problem, the twist, the payoff or the loop). Otherwise the line stays, and the log reports it as unresolved.
22. **Twist quality.** The fact scores also say whether a fact changes how the viewer understands the subject (a
   cause or a counter-intuitive result) and whether it corrects the myth. Two kinds are new: definition and list. A
   list of types, a definition or a name ("Kerogen is classified into three types": also caught in code by
   "classified", "types of", "is called") is never the twist. For a topic with a myth, the twist is the fact that
   corrects it, even when it is a history fact or backs the core answer ("formed ... before dinosaurs" for crude oil).
23. **The writer can be Claude Code** (user 2026-11-17, a comparison: **not the default**). With
   `SCRIPT_PROVIDER=claude-code` the script step's model calls go to headless Claude Code (`claude -p`, the Mac's
   subscription login, no API key; `pipeline/claude_code.py`). That covers the core answer (written again from the
   on-topic facts), the fact plan, the outline, the four whole scripts (drafted at once), the judge, the staging, the
   labels and the fact check. Research, facts, painting, voice and rendering stay local, and so do all the checks and
   the fact-first outline. The CLI gets no `ANTHROPIC_API_KEY` (the repo's `.env` has one, and with it the CLI would
   bill the API). It also runs with no tools, MCP servers, settings or hooks, no saved session and an empty working
   folder. The model is `CLAUDE_CODE_MODEL` (default `claude-sonnet-5-5`). Each call is in the ledger at $0.00 as
   `claude-code` with its seconds, and `meta.script.claude_code` holds the calls, the total and mean seconds and the
   slowest call. If Claude Code is not logged in or hits a usage limit (checked before the step with
   `claude auth status`, and on every call), the 9B pipeline writes the script from the start. The notes, the script
   card on the phone ("Claude Code was not available …") and a push notification say so.
   - On the Mac worker: add `SCRIPT_PROVIDER=claude-code` to `~/.config/stick-worker/config` and restart the worker.
     Remove the line to go back. The bridge passes on nothing else, so a paid provider is never used.
   - Benchmark: `bench/score.py --provider claude-code`. Its scoring judge stays the local `qwen3.5:9b`, so every
     writer is scored on the same yardstick.

## Fact check before the script gate (user 2026-11-14)

At the end of the script step on the Mac (`pipeline/entail.py`), every narrated sentence and every text on screen is
checked against the facts its beat cites. **Claims, not colour** (user 2026-11-15): one short call splits a sentence
into its atomic claims (fact or belief) and lists any that make no sense ("dinosaurs were sea creatures"). Only the
claims are checked, so metaphor and tone that assert nothing pass ("black liquid gold"). Word precision runs in code
on the line's own words that it asserts, then the yes/no judge checks each fact claim against the research. A beat
that cites nothing may hold no fact claim.

* **A sentence of a beat that cites facts** must say only what they say; leaving a detail out is fine. One that fails:
  1. is kept and re-cited when another fact says it (the candidates are picked in code by their words);
  2. else is rewritten (two versions in one call) and checked again; a version with a new number, a wrong group word,
     or that repeats another line is refused;
  3. else the beat says its planned claim (checked before writing);
  4. else its claim is dropped and the beat kept: the sentence is removed, or a one-sentence beat gets a short line
     that states no fact (never the payoff, twist or reveal), or the cited fact as research wrote it.
* **A beat that cites nothing** may hold only lines that state no fact. A line there that states one is cited to the
  fact that says it, else replaced by a line that states none, else dropped.
* **Every text on screen** (on-screen text, split labels, diagram items, label and pin effects, drawn text recipes)
  gets the same check against its beat's facts. A text that only marks the myth ("MYTH", "Myth: Dinosaurs") or only
  repeats words its narration says passes in code. A text that fails is rewritten short and checked again, else
  removed.

The **rewrite rate** (changed lines / checked lines) is logged per episode in `meta.fact_check.rate` and on the
episode page. Above 20 % means the fact-first plan failed for that episode.

Each change is in `meta.fact_check` and the job notes, and on the episode page ("Fact check"). Anchors the new line no
longer says are re-anchored. The self-repair's added sentences (duration) get the same check. `stick fact-check SLUG`
runs it, with the claim follow-up first, on an existing script (an Auto-approved script is re-stamped
`fact check (auto)`; one a person approved goes back to its gate).

## Self-repair after every render (user 2026-11-14)

After QA, `pipeline/repair.py` diagnoses each failed check, applies a fix and renders again (only the beats it
touched; the rest come from the clip cache), at most two rounds. Fixes are per-beat hints in
`episodes/<slug>/repairs.json`, applied in memory by the composer; the approved script is untouched except by the
duration and word fixes, which are logged in the approval log as `self-repair (QA duration)` / `(QA words)` and sent
back to the server.

| check | fix |
|---|---|
| words | the checker compares one canonical form (numbers, %, units, CO2, plurals); a word still unheard re-voices its beat once at 0.92x; still unheard after that, the local model picks a plainer synonym that keeps the meaning, the narration (so the captions), labels and anchors get it (`self-repair (QA words)`) and the beat is voiced again. The swapped line must pass the fact check and the word rule; slang ("rock goo") and split replacements ("make ... become") are refused, and then the whole line is written again by best-of-N without the word. A video under its length gets one short sentence added only to beats that cite facts, checked against those facts, for ranges and for repeating another beat (never to a question or a transition). A filler that opens a sentence ("Hmm", "Oh", "Well") is never swapped: when Whisper does not hear it, it is dropped at once ("Hmm. Is that right?" -> "Is that right?") and the beat voiced again |
| text | a label on the host: round 1 moves it (host to the other side and smaller, or an insert label away from the host), round 2 drops it; two texts on each other: only the less important one is dropped (a myth mark, then the beat's key term: its on-screen text or a label naming what the narration says) |
| duration | one short sentence from the beat's cited facts on the 1-2 thinnest middle beats (no new numbers) |
| story_match | a MYTH mark on a picture of the myth (a scene that may not show its beat is a warning to look at: a word test cannot judge a painting well enough to repaint it) |

**The layout manifest (user 2026-11-15).** At render time the runner writes `layout.json` next to the video: each
beat's texts as the renderer drew them, with the box where each settled and the seconds it was on screen
(`qa.layout_manifest`). QA reads that file, so it checks the exact final texts and positions, never the episode's
list before the drops. An overlap names its two texts as a list, with the beat, and `text.layout` lists each beat's
drawn texts. A text is named by the episode's own text it came from. Before this, a slam stacked on two lines was
named "Is that / actually right?". QA joined an overlapping pair with the same " / ", so crude oil -6's repair
dropped "Is that" and "-> Kerogen", texts the beats did not have; the slams stayed and failed QA again. An older
report's "A / B" is now cut where both sides are texts of the beat (`repair.split_pair`), and a drop always names a
text the beat has (`repair.spec_text`), including a text slam's own text.

What is still wrong after two rounds is listed in plain words on the phone ("Still wrong after the self-repair"),
with the repairs made under "Fixed automatically"; the episode is "Done, QA failed". qa.json carries `repairs`
(round, check, beat, cause, fix), `repair_rounds` and `still_wrong`. The script writer's word target follows Kokoro's
measured speaking rate (`pipeline/pacing.py`: the median of the latest renders, about 2.5 words per second).

## Paint never stops an episode (2026-11-14)

Every FLUX file name is sanitised and ends in `.png`, and every saved file is decoded before it counts
(`pipeline/paintsafe.py`). A scene or prop with no passing seed is painted again on a new seed, then from a simplified
prompt, then becomes a placeholder (a gradient in the scene's mood with what it would show; a card for a prop),
flagged `placeholder: true` in its library, badged on the scenes page and failed by QA's `placeholders` check so it
is never posted by accident. Redo it with a note to paint it again. Base candidates get the same file checks and one
retry on new seeds.

## HTTP API

Browser routes (session cookie, HTML; mobile-first, no build step, plain HTML + a little JS, polling every 3 s):
`GET/POST /login`, `POST /logout`, `GET /` (history: topic, state, cost, date, links), `GET /new`, `POST /episodes`,
`GET /episodes/{slug}` (renders the page for the current state, live progress incl. Mac worker status),
`GET /files/{slug}/{name}` (download; `Content-Disposition: attachment` with `?dl=1`).

JSON API (same session cookie; `POST /api/login` `{user, password}` sets it). D's e2e test uses only these:

| Method, path | Body | Response |
|---|---|---|
| `POST /api/login` | `{user, password}` | `{ok}` + cookie |
| `GET /api/health` | | `{ok, db, worker: {online, last_seen, busy}}` (no auth; no secrets) |
| `POST /api/episodes` | `{topic, niche?, audience?}` | `{slug}`; queues `pitch` |
| `GET /api/episodes` | | `[{slug, topic, state, cost_usd, created_at}]` |
| `GET /api/episodes/{slug}` | | `{slug, topic, state, cost_usd, error, job: {kind, status, progress}, worker: {...}, storylines?, script?, scenes?, files?}` |
| `POST /api/episodes/{slug}/storyline` | `{story: 1-3}` | `{ok}`; queues `script` |
| `POST /api/episodes/{slug}/script/approve` | `{hook: 1-3}` | `{ok}` or 409 `{errors}`; queues `paint` |
| `POST /api/episodes/{slug}/scenes/approve` | `{}` | `{ok}`; queues `render` |
| `POST /api/episodes/{slug}/scenes/redo` | `{key, note}` | `{ok}`; queues `paint` |
| `POST /api/episodes/{slug}/retry` | `{}` | re-queues the failed job |

Worker API (`Authorization: Bearer <token>`; sha256 must equal `WORKER_TOKEN_SHA256`; nothing else accepts it):

| Method, path | Body | Response |
|---|---|---|
| `POST /api/worker/heartbeat` | `{host, version, busy_job_id?}` | `{ok, queued: n, kinds: [...]}` (`kinds`: what this server hands the Mac) |
| `POST /api/worker/claim` | `{kinds: ["pitch","script","paint","render"]}` | 204 none, or `{job_id, kind, slug, payload, episode: <episode.json>, ledger: <ledger.json>}` (+ for pitch/script: `provider`, `cache: {name: text}` of `episodes/<slug>/cache/*.json`, and for pitch `others: {slug: stub episode.json}`); sets `running`, `lease_until = now + 15 min`. Kinds the provider does not route to the Mac are ignored |
| `POST /api/worker/jobs/{id}/progress` | `{stage, pct, message}` | `{ok}`; extends the lease |
| `POST /api/worker/jobs/{id}/files` | multipart `file`, form `name`, `kind` | `{ok}`; saved to `/data/outputs/<slug>/<name>` (names: `[a-z0-9._-]+` only). A pitch/script job uploads only `kind=cache` (at most 20 MB each), saved to `/data/episodes/<slug>/cache/<name>`, not listed as a download |
| `POST /api/worker/jobs/{id}/done` | `{result}` | `{ok}`; advances the episode state |
| `POST /api/worker/jobs/{id}/fail` | `{error}` | `{ok}`; episode `failed` |

A running Mac job whose lease expires goes back to `queued` (free work, which includes pitch/script under `ollama`;
at most 3 attempts). The worker counts as online if `last_seen` is under 2 minutes old.

Pitch/script result (Mac, `ollama`): `{notes: [...], episode: <episode.json>, ledger: <ledger.json>, provider}`;
the new `cache/` files are uploaded first. The server checks the episode (same slug; storylines after pitch, beats
after script), saves `episode.json`/`ledger.json` and moves the episode exactly as the runner does: `storylines`
after pitch, `script` after script (`cost_usd` from the ledger, $0). An episode it cannot use fails the job with the
reason (Retry re-runs it). The job's stored result is `{notes, ran_on: "mac", provider}`.

Paint result: `{new: [{key, kind: "scene"|"prop", name: "<key>.jpg", scene, beats}], failed: [{key, why}]}` (the
previews are uploaded first as `scene_preview` files). Render result: `{duration, qa_pass: bool, qa_summary}`;
files `<slug>.mp4`, `<slug>.srt`, `qa.json`, `contact_sheet.jpg`. `qa_pass` false → `done_qa_failed`.

## What the Mac worker runs (C)

In the user's `Stick_Figures` checkout on `pipeline-v2`, with its venvs:
- before a job: write the received `episode.json`/`ledger.json` to `episodes/<slug>/` (pull the latest `pipeline-v2`
  only when idle, never mid-job);
- `paint`: `stick inserts <slug> --generate`, `stick props <slug> --generate` (a `redo` payload repaints those keys
  as round 2 with the note as art direction, archiving round 1), previews as 720px JPEGs, upload, `done`;
- `render`: `.venv/bin/python scripts/motion_test/episode_compose.py <slug> --workers 4`, progress from its output
  (`bNN: frames ... s` lines, then QA), upload the files, `done` with `qa_pass` from `qa.json['all']`;
- `pitch` / `script` (`SCRIPT_PROVIDER=ollama`): check Ollama (`GET http://localhost:11434/api/version`; down →
  `fail` with "Ollama is not running on the Mac: brew services start ollama"), write the claimed episode, ledger,
  `cache/` and the `others` stubs into a scratch `episodes/` inside the job folder (the checkout's own `episodes/`
  is not touched), run `story.pitch` / `script.generate` there through `macworker/bridge.py` (the repo's `.venv`;
  `SCRIPT_PROVIDER=ollama`, an empty `ANTHROPIC_API_KEY`, a ledger that refuses any paid reservation), upload the
  new or changed `cache/` files, `done` with the episode and ledger;
- after every job (`macworker/cleanup.py`, never fails or delays the report): a finished `render` deletes its stale
  per-beat clips (files in `renders/sample/<slug>/shots/` that its `shots.txt` no longer lists and that are older than
  it; the listed clips are the render cache); `OLLAMA_MODEL` is unloaded (`keep_alive: 0`) when `/api/ps` shows it
  loaded; what was freed goes to `logs/cleanup.log`;
- `caffeinate -i -w <pid>` only while a job runs; the launchd agent (`KeepAlive`) polls every 30 s while idle.
- Never kills other processes; one job at a time; the token from the Keychain; logs in `~/Library/Logs/stick-worker.log`.

## Fake mode (tests; no money, no FLUX)

- `STICK_FAKE_ANTHROPIC=1` (B): the runner's pipeline calls get a fake client (`tests/pipeline_fixtures.FakeClient`
  with the fixtures' research, storyline and script responses) instead of `pipeline.script.make_client()`. Nothing
  paid is ever sent in this mode; the ledger records the fake usage as $0.
- `tests/e2e/fake_worker.py` (D): speaks the worker API like the Mac, with canned outputs (a 2 s ffmpeg `testsrc`
  MP4, an SRT, a passing `qa.json`, two preview JPEGs). The real Mac worker (C) has a `--once` mode for tests.
- Ports: Caddy's host ports are `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (default 80/443) in case the VPS already
  runs something on them; the web app listens on 8000 inside the network only.

## Out of scope for v1 (say so in the UI if hit)

- A script that needs a base the library does not have stops at the script gate with that message (the host format
  rarely does: scenes are painted inserts).
- Music (no-music is the default), 16:9, multiple users.

## The writing benchmark (user 2026-11-15)

"Stop fixing one video at a time": every writing change is judged on `bench/score.py`, not on one video.
`bench/topics.txt` holds 5 varied topics: crude oil (a myth), why we yawn, the fall of the Roman Republic, compound
interest, and why the sky is blue. For each topic the script runs auto mode up to the approved script, with no
painting or rendering. It uses the same functions as `stick make --auto` and the web app's Auto toggle, including
auto's fixes and its script gate. Research and the storyline pick are made once per topic
(`bench/work/upstream-<versions>/`, git-ignored; a new research, understanding or twist version makes them
again), so every run writes from the same facts and storyline. A new understanding or twist version on the same
research starts from the older snapshot's cache, so the searches and every unchanged call are replayed and the facts
stay the same. Each run writes into
its own copy (`bench/work/runs/<label>/`).

| score | how |
|---|---|
| hook (1-5) | the judge's fixed rubric for the gap the hook opens; minus 2 when it gives the answer away (the judge, or `grounding.gives_away` in code) |
| flow (1-5) | the judge's fixed rubric (each beat adds something new and follows on); minus 1 per repeated claim found in code |
| accuracy (0-1) | share of the final lines and on-screen texts that pass the claims check |
| rewrite (0-1) | the fact check's rewrite rate |
| clean (0/1) | auto reached an approved script with no manual step |
| seconds | the script stage's wall time (waits for another app's model included; "waited" says how long) |
| facts | facts the research found, and (in brackets) the usable ones the plan could tell |
| filler | beats that state no fact besides the hook, setup and loop (at most 3) |
| core (0/1) | the core answer is said in the reveal, twist or payoff: the gate's code check and the judge on the whole narration both say yes |
| overall (0-1) | mean of hook/5, flow/5, accuracy, 1 - rewrite, clean |

The judge is `OLLAMA_MODEL` (qwen3.5:9b) at temperature 0 with fixed prompts. Results are saved to
`bench/results/<date>.json` and printed next to the previous run. `--writer MODEL` tries another writer,
`--only crude,yawn` picks topics, and `--compare A B` prints two saved runs side by side.

## Archive (user 2026-11-15)

Episodes are archived, never deleted. `Service.archive(slug, by)` moves `data/episodes/<slug>` and
`data/outputs/<slug>` to `data/archive/<stamp>-<slug>/`, writes the row as it was to `row.json` there, and keeps the
row with the state `archived`, which hides it from the list. It is refused while a job of the episode is queued or
running.

