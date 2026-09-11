# CLAUDE.md

Working notes for Claude Code sessions on Svetochka.

## Read first

- `SPEC.md` — the accepted specification. Every change is checked against it; if
  a change needs the spec to move, the spec moves first, in its own commit.
- `RESEARCH.md` — why the spec says what it says. Reference, not instructions.
- Stages, estimates and Definition of Done: `SPEC.md` §11. Work goes stage by
  stage; a stage is not started without Dimitry's explicit go.

## Standing rules

- All documentation committed to git is in English. Bot-facing strings and quoted
  user messages stay in their original language (Russian) — they are data.
- The model never acts directly: it can only call the declared tools (`SPEC.md`
  §6.1). Anything with an outside effect goes through a confirmation card
  (§6.2). Dates are parsed by code inside tools, never by the model.
- Deduplicate on `tg_update_id` before any model call. Save the raw incoming item
  before any processing. Reply to every incoming item, including on failure.
- A foreign chat_id gets silence, not a refusal.
- Secrets live only in the Railway environment; never in the repo, never in chat.
- Multi-user by construction from stage 0: every personal table has `user_id`,
  every tool takes a `UserScope` and never a raw chat_id (`SPEC.md` §6.1, §7.3,
  NFR-10). v1 registers one user; a second is an INSERT, not a schema change.
- No shared infrastructure with JobScraper: own Railway project, own Postgres, no
  variable references across projects. Patterns are copied, not imported.

## Infrastructure

- Railway project `svetochka` (own workspace project, created 2026-09-03):
  service `sveta-web` (webhook + agent + reminder tick); `digest` and `ingest`
  cron services since stage 4 (2026-09-12). Postgres in the same project;
  `DATABASE_URL` is `${{Postgres.DATABASE_URL}}`.
- Railway ids (for the Railway MCP / CLI; not secrets):
  workspace `7e4d263c-df13-4b2b-9a17-5ce022a8643d`,
  project `28e32030-e8fc-4fa2-8d1d-c18a30f22720`,
  environment `production` `8d1e8981-1b3f-4c36-b75e-bc8320d5bb37`,
  service `sveta-web` `0ff001e5-0635-410c-b580-09c622554050`,
  service `Postgres` `4a33d3c8-631c-48ff-9763-ac4317b66ef8`,
  cron service `digest` `e422f9c8-015d-45e5-af1c-104527387da0` (`*/15 * * * *`,
  `SERVICE_TYPE=digest`, variables as references to `sveta-web` and `Postgres`),
  cron service `ingest` `7426adc2-d2f7-46c7-9ea6-34a6ec60453f` (`0 */3 * * *`,
  `SERVICE_TYPE=ingest`). Created 2026-09-12; deploy settings (healthcheck, cron,
  start command) live on the services — Railway config files are deprecated.
  Public domain: `sveta-web-production.up.railway.app`; webhook path `/tg/webhook`.
- Deploy is `bash run.sh`, role chosen by `SERVICE_TYPE` (see `SPEC.md` §7).
  Pushing to `main` deploys; `railway.toml` sets the healthcheck to `/health`.
- Environment variables as actually entered in Railway on 2026-09-03 — three of
  them under non-canonical names, which `sveta/core/config.py` accepts as aliases
  (canonical name wins if both are set):
  `sveta_telegram_token` (= `SVETA_TELEGRAM_TOKEN`), `sveta_anthropic`
  (= `ANTHROPIC_API_KEY`), `sveta_openai_api` (= `OPENAI_API_KEY`), plus
  `SVETA_ALLOWED_CHAT_IDS`, `SVETA_WEBHOOK_SECRET`, `SVETA_TOKEN_KEY`, `SVETA_TZ`,
  `DATABASE_URL`. Optional: `SVETA_ANTHROPIC_WORKSPACE_ID` (see open items).
  Stage 3 (2026-09-12): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (an OAuth "Web
  application" client in GCP project `sodium-wall-331321`, redirect URI
  `https://sveta-web-production.up.railway.app/oauth/google/callback`, Calendar and
  Gmail APIs enabled); `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET` (a *public* Notion
  integration, redirect `…/oauth/notion/callback`); `NOTION_TOKEN` is an optional
  fallback (internal integration). Tokens per user live encrypted in `oauth_tokens`,
  landed by `/connect` → consent → callback; nobody pastes a token anywhere.
- Models: `claude-sonnet-5` for the agent and the brief, `claude-haiku-4-5` for
  the cheap path. IDs without date suffixes.
- Telegram bot: `@dmitreyvladimirovic_bot`. Only Dimitry's chat is registered.

## Local development

```
git clone git@github.com:Dmitreyvladimirov/SvetochkaPersonalAssistant.git
cd SvetochkaPersonalAssistant
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q            # never touches Postgres or the model
```

Running the service locally needs a `.env` (gitignored) with the variables above
and a reachable Postgres; `uvicorn sveta.core.app:app --reload`. Without
`RAILWAY_PUBLIC_DOMAIN` the webhook is not registered, so a local run cannot
receive Telegram traffic — that is intended; test the agent through `pytest` and
the golden set instead:

```
SVETA_GOLDEN=1 ANTHROPIC_API_KEY=... python -m pytest -m golden -q -s
```

(with `SVETA_ANTHROPIC_WORKSPACE_ID=...` too if the key is identity-linked).
Costs cents; prints the pass rate and every miss; threshold 90%.

Logs and deployments: Railway dashboard, or the Railway MCP with the ids above.
The public domain is not reachable from Claude Code on the web (egress proxy);
use the MCP for `/health` and logs from there.

## Operational log

- 2026-09-11: all three stage-1 operational items closed.
  1. The Anthropic key in `sveta_anthropic` was replaced with a single-workspace
     key (Console → API Keys, workspace selected at creation). No
     `SVETA_ANTHROPIC_WORKSPACE_ID` needed; the header code path stays for
     multi-workspace keys. First live call after the change: `POST /v1/messages`
     200 OK at 01:23 UTC.
  2. `SVETA_WEBHOOK_SECRET` rotated in Railway; the redeploy at 01:30 UTC
     re-registered the webhook.
  3. **Golden set baseline: 95% (38/40) on `claude-sonnet-5`**, 3 min 42 s.
     Both misses are the same shape — a message with two intents where the model
     only did one (`две мысли: … ` saved nothing; `запиши идею, потом покажи…`
     searched but did not save). Judge any persona/tool-description change
     against this baseline, not against a single number.
- Golden-set gotcha: `tests/conftest.py` sets `ANTHROPIC_API_KEY=test-key` as a
  default, and the canonical name wins over the `sveta_anthropic` alias. Export
  the real key under `ANTHROPIC_API_KEY` (a `.env` copied from Railway is not
  enough on its own).

## Current state

- Spec v0.6. Stage 0 live 2026-09-03: skeleton, 20-table schema, `/health`, first
  user seeded from `SVETA_ALLOWED_CHAT_IDS`.
- Stage 1 closed 2026-09-11 (golden 95%): webhook, inbox, agent loop
  (`sveta/core/agent.py`) over the closed tool set in `sveta/tools/`, notes + search,
  preferences, `suggest` buttons, "Не туда" undo.
- **Stages 2, 4 and 5 coded and pushed in the overnight run of 2026-09-11/12**
  (Dimitry's go: "стадии 2, 4, 5, push в main после каждой"). Per-stage specs with
  the assumptions taken: `docs/stages/stage2.md`, `stage4.md`, `stage5.md`.
  - Stage 2: voice (Whisper over HTTPS, duration refused before download),
    reminders (`sveta/core/timeparse.py` parses Russian time by code; the tick is a
    daemon thread in the web process, `FOR UPDATE SKIP LOCKED`), links (SSRF-guarded
    `fetch.py`, bare URL on the cheap path), lists with one checkbox button per line.
  - Stage 4: `digest` and `ingest` roles in `run.sh` (`sveta/jobs/digest.py`,
    `ingest.py`); brief and evening review per user in the user's tz, one per day;
    RSS 2.0/Atom with the stdlib; `/rss` commands; FR-39 alerts for a dead key or an
    empty credit balance.
  - Stage 5: `notes_propose` (dump → one-tap save), corrections' "should have" from
    the next message and into the prompt, `fact_remember` / `fact_recall` with a
    validity window. **FR-14 Notion showcase deferred** — needs a Notion internal
    token from Dimitry.
- **Stage 3 coded 2026-09-12 with Dimitry online** (`docs/stages/stage3.md`):
  OAuth for Google and Notion from the chat (`/connect` with URL buttons; the
  `state` is a one-shot HMAC nonce), calendar read and gated write, mail search
  with bodies encrypted at rest and a gated `mail_read_body`, the confirmation-card
  mechanism (§6.2) with atomic taps, the trip playbook as data, notes mirrored to
  the Notion database "Светочка · Заметки" (created under the project page in
  Notion). New dependency: `cryptography` (Fernet, §8). Reviewed; the review's
  criticals and importants are fixed. Live check needs the four OAuth variables
  in Railway and Dimitry's two consents.
- **Tickets end to end (2026-09-12, after stage 3):** attachments from Gmail into
  the chat (`mail_send_attachment`, documents and images only), flight extraction
  by the cheap model into structured legs with real durations and airport time
  zones (`mail_extract_trip`, `sveta/core/airports.py`) — the ticket body never
  reaches the agent model — `calendar_create` takes an optional `tz`, and up to
  eight confirmation cards plus "✓✓ Всё сразу" for a batch of calendar events.
  Reviewed; no criticals, eight important items fixed in `b9f77c0`.
- **Research, 2026-09-12:** `docs/research/2026-09-12-product-scenarios.md` (20
  scenarios, an integrations table with exact Google scopes and verification
  impact, a five-increment order, and what not to build) and
  `docs/research/2026-09-12-qa-gaps.md` (coverage matrix, §9/§10 gaps, the ten
  tests to add next). Everything actionable is in the Notion backlog. The three
  cheapest items already done: Hebrew ticket subjects, a redelivery guard on every
  state-changing tap, and naming the Google scopes a consent left out.
- **The strict-schema budget moves with the tool count.** Seven strict tools at 19
  tools, six at 25 (`sveta/tools/__init__.py`, `STRICT_TOOLS`). Over the budget,
  every agent call answers 400 "Schema is too complex". The golden run is the alarm.
- **Not done in the run, needs Dimitry (morning list):**
  1. ~~Anthropic credit balance~~ — topped up by Dimitry during the run; the
     probe passed at 07:20 UTC. FR-39 now names an empty balance in the chat.
  2. **Golden set: 97% (63/65) on `claude-sonnet-5`** after the ticket work and its
     review fixes (2026-09-12 15:10 UTC; 98% (64/65) on the run before them, 97%
     (61/63) after stage 3, 96% (55/57) after stages 2–5). The misses are the
     stage-1 pair — a message with two intents — and they come and go between runs
     at the same threshold; treat a single run as ±1 scenario, not as a trend. A run costs ~$1.6 and
     is exempt from the per-user daily limit inside the test. Both misses are the stage-1 pair (a two-intent message
     where the model does one thing). The first run after the top-up found a
     production bug: with 19 strict tools the API answers 400 "Schema is too
     complex" — the budget is seven strict tools (`sveta/tools/__init__.py`,
     `STRICT_TOOLS`). Fixed and pushed before the number above was measured.
  3. Railway cron services `digest` and `ingest` were created during the run (ids
     above). Check their first deploy logs in the morning: `digest` logs
     "nothing due" or one line per user; `ingest` logs "0 source(s) polled" until
     feeds are added with `/rss add`.
  4. Manual acceptance (SPEC.md §14 item 3): one voice note, one "напомни …", one
     "добавь в покупки …", one link with a comment, one three-thought dump,
     "запомни, что …", `/rss add <feed>`.
- The Telegram webhook registers itself on startup from `RAILWAY_PUBLIC_DOMAIN` +
  `SVETA_WEBHOOK_SECRET` (`app._register_webhook`); nobody pastes the bot token.
- Tests never touch Postgres or the model: `tests/fakedb.py` filters by `user_id`
  exactly where the SQL does, `tests/fakellm.py` scripts the model. 229 tests.
