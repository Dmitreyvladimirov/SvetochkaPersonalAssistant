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
- Deploy is `bash run.sh`, role chosen by `SERVICE_TYPE` (see `SPEC.md` §7).
- Models: `claude-sonnet-5` for the agent and the brief, `claude-haiku-4-5` for
  the cheap path. IDs without date suffixes.

## Current state

- Spec v0.6. Stage 0 done 2026-09-03: `run.sh`/`railway.toml`, `sveta/core/config.py`
  (validate_secrets, alias names accepted), `sveta/core/db.py` (20-table schema as a
  pure function, idempotent `init_db`, `seed_users`), `sveta/core/app.py` (`/health`
  with a DB ping, 503 when the database is down). 21 tests.
- Next: stage 1 — webhook, inbox, agent loop with `UserScope`, note/search tools,
  `suggest`, preferences. Not started without an explicit go.
- Local run: `pip install -r requirements.txt && pytest`; the app needs the
  variables of `SPEC.md` §12 (a `.env` in the repo root is read, never committed).
