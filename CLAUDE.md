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
  cron services come in stage 4. Postgres in the same project;
  `DATABASE_URL` is `${{Postgres.DATABASE_URL}}`.
- Railway ids (for the Railway MCP / CLI; not secrets):
  workspace `7e4d263c-df13-4b2b-9a17-5ce022a8643d`,
  project `28e32030-e8fc-4fa2-8d1d-c18a30f22720`,
  environment `production` `8d1e8981-1b3f-4c36-b75e-bc8320d5bb37`,
  service `sveta-web` `0ff001e5-0635-410c-b580-09c622554050`,
  service `Postgres` `4a33d3c8-631c-48ff-9763-ac4317b66ef8`.
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

## Open operational items (as of 2026-09-09)

None of these is code. All three block or degrade production until Dimitry acts
in Railway / the Anthropic Console:

1. **Agent calls fail with 400.** The Anthropic key in `sveta_anthropic` is
   identity-linked; the API demands `anthropic-workspace-id` on every request.
   Either add `SVETA_ANTHROPIC_WORKSPACE_ID` (Console → Settings → Workspaces,
   id `wrkspc_…`) or replace the key with a workspace-scoped one. Until then every
   free-text message gets "Не смогла разобрать — модель не ответила" and the
   inbox row stays `failed`. `/ping`, `/stats`, `/help` work (no model).
2. **Rotate `SVETA_WEBHOOK_SECRET`.** The first deployment carried the secret in
   the webhook URL and Railway's HTTP log recorded it. Set a new value; startup
   re-registers the webhook.
3. **Golden set not yet run** against the real model (stage 1 DoD). Command above.
   Do not edit `playbooks/persona.md` or tool descriptions before a baseline run.

After 1 and 2: send the bot a free-text message and confirm in the deploy logs
that the agent loop completed and a row landed in `llm_call`.

## Current state

- Spec v0.6. Stage 0 live 2026-09-03: skeleton, 20-table schema, `/health`, first
  user seeded from `SVETA_ALLOWED_CHAT_IDS`.
- Stage 1 pushed 2026-09-03: webhook, inbox, agent loop (`sveta/core/agent.py`) over
  the closed tool set in `sveta/tools/`, notes + search, preferences, `suggest`
  buttons, "Не туда" undo. Voice, reminders, lists → stage 2.
- The Telegram webhook registers itself on startup from `RAILWAY_PUBLIC_DOMAIN` +
  `SVETA_WEBHOOK_SECRET` (`app._register_webhook`); nobody pastes the bot token.
- Tests never touch Postgres or the model: `tests/fakedb.py` filters by `user_id`
  exactly where the SQL does, `tests/fakellm.py` scripts the model.
