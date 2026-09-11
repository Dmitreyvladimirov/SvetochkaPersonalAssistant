# Stage 4 — RSS, morning brief, evening review, budget

Implementation spec for `SPEC.md` §11 stage 4, written 2026-09-11 in the same
overnight run as stage 2 (Dimitry's go covers stages 2, 4, 5). DoD (§11): FR-30,
FR-31, FR-38, FR-39; the brief is ≤10 lines; the cron in dry-run prints the brief
to the log. FR-32, FR-33, FR-34 (S/C) are included where they cost little.

## Objective

Every morning at the user's brief time Svetochka sends one message with what
matters today, built from data she already holds: reminders due today, what is
still hanging from yesterday, the unchecked lines of "Сегодня", and 1–3 links
from the user's own RSS feeds. In the evening she asks what is still open. Both
are per user, idempotent per day, and skip what is empty (FR-31).

## Assumptions

1. **One `digest` cron every 15 minutes, not one per user time.** Railway crons
   run at fixed UTC times and users have their own tz and preferences. The job
   runs `*/15 * * * *`, and for every active user checks whether the user's
   local time is inside the 15-minute window that starts at their `brief.time`
   (default 07:30) or `review.time` (default 21:00). `digests` has
   `UNIQUE (user_id, kind, for_date)`, so a second run in the same window is a
   no-op. Precision is ±15 minutes; NFR-1 does not cover the brief.
2. **`ingest` cron every 3 hours** polls every enabled source. RSS 2.0 and Atom
   are parsed with the stdlib `xml.etree`; **no feedparser dependency**. Items
   are deduplicated by `(user_id, external_id)` where `external_id` is the guid
   / atom id, or the link when the feed has none. The feed body goes through
   the same SSRF guard and 1 MB cap as links (`fetch.get_bytes`).
3. **Composition uses the model once** (§6.1: "Brief — Sonnet, one call over
   deterministically gathered data") with a hard instruction of ≤10 lines, and
   the code re-checks: more than 10 lines → the deterministic template is sent
   instead. Model unavailable or over budget → the deterministic template is
   sent (§9: a failure is visible, never silent; the brief still arrives).
   `SVETA_BRIEF_MODEL` defaults to the agent model.
4. **Feeds are managed by cheap-path commands**, not tools: `/rss` lists,
   `/rss add <url>`, `/rss rm <n>`. The closed tool set stays as specified in
   §6.1; feed management is administration, not conversation.
5. **FR-39 is implemented for the credentials that exist today**: an Anthropic
   401, a "credit balance too low" 400, and an OpenAI 401 each produce a
   specific message in the chat instead of the generic "модель не ответила",
   and the brief job sends the same alert once per day instead of the brief.
   Google and Notion join in their stages.
6. **"Overdue" means reminders that were delivered before today and are neither
   done nor rescheduled** (`status='sent'`). Meetings (stage 3) and unanswered
   messages (stage 6) are absent sections, skipped by FR-31.
7. **No schema change.** `digests`, `sources`, `source_items`, `llm_call` exist
   since stage 0 with the needed columns; the reaction (FR-32) is stored inside
   `digests.payload`.

## Project structure (additions)

```
sveta/jobs/brief.py        gather → compose → send; kinds "brief" and "review"
sveta/jobs/digest.py       the cron entry point (SERVICE_TYPE=digest): one pass over users
sveta/jobs/rss.py          feed fetch + parse (RSS 2.0, Atom) + upsert
sveta/jobs/ingest.py       the cron entry point (SERVICE_TYPE=ingest)
run.sh                     digest and ingest roles
tests/test_brief.py, test_rss.py, test_digest_job.py, test_alerts.py
```

## Design by requirement

### FR-30 / FR-31 / FR-50 — the morning brief

`brief.gather(user, kind, now)` returns sections as lists of plain strings, in
the user's tz:

| Section (brief) | Source | Line |
|---|---|---|
| Напоминания сегодня | `reminders` scheduled with `fire_at` on the local date | `11:00 позвонить в банк` |
| Висит со вчера | `reminders` with `status='sent'`, sent before today | `вчера: оплатить счёт` |
| Сегодня | unchecked lines of the "Сегодня" list (FR-50) | `☐ позвонить маме` |
| Почитать | up to 3 `source_items` published in the last 24 h, newest first (FR-33) | `Title — url` |

Empty sections are absent (FR-31). If every section is empty the brief is not
sent and the job logs it; the `digests` row is still written so the window does
not retry.

`brief.compose(sections, prefs)`: the deterministic template is the fallback and
the ceiling; the model gets the same sections and the persona knobs
(`greeting`, `brevity`, `address`) and is asked for ≤10 lines, plain text, no
markdown, nothing invented. The reply is line-counted; >10 lines or an error →
template. Cost is recorded in `llm_call` with purpose `brief` and in
`digests.cost_usd`.

Buttons under the brief (FR-32): `👍` / `👎` → `dg:up:<id>` / `dg:down:<id>`
→ `digests.payload.reaction`, answered with a toast, nothing else happens.

### FR-34 — the evening review

Same job, `kind="review"`, default 21:00 (`review.time` preference). Sections:
"Не закрыто" (reminders delivered today and yesterday still `sent`), "Сегодня
осталось" (unchecked "Сегодня" lines). Nothing open → no message.

### FR-33 — RSS

`rss.parse(body: bytes, base_url) -> list[Item]` handles RSS 2.0 (`channel/item`)
and Atom (`feed/entry`); dates via `email.utils.parsedate_to_datetime` and
`datetime.fromisoformat` (with `Z`). `rss.poll(source)` fetches with
`fetch.get_bytes`, upserts with `db.upsert_source_items`, records `last_polled_at`
and `last_error`. A feed that fails three polls in a row is still polled (there
is no auto-disable; the user sees the error in `/rss`).

`/rss` (cheap path): lists sources with their last poll and error; `/rss add
<url>` checks the URL with the SSRF guard, fetches once, requires at least one
parsed item, then stores the source; `/rss rm <n>` deletes by list position.

### FR-38 — budget

Already present (`llm.check_budget`, `/stats`). Stage 4 makes the brief obey it
(the fallback template is free) and extends `/stats` with the month-to-date
total. The daily limit stays per user.

### FR-39 — alert on dead authorisation

`llm.classify_error(exc) -> str | None` maps an Anthropic `AuthenticationError`
or a `BadRequestError` mentioning credit balance to a user-facing sentence
("Ключ Anthropic не работает: закончился кредит. Пополни в Console — до тех пор
я не отвечаю на свободный текст."). `bot._run_agent` uses it in place of the
generic failure text; `transcribe` does the same for OpenAI 401. The digest job
sends the alert instead of a brief, once per day (a `digests` row with
`kind="alert"`).

### Cron entry points

`python -m sveta.jobs.digest [--dry-run] [--now ISO]` and `python -m
sveta.jobs.ingest [--dry-run]`. Both validate config, open the database, iterate
`db.active_users()`, log one line per user, and exit 0 even if one user failed
(the failure is logged; the next run retries). `--dry-run` prints what would be
sent and writes nothing — the §10 smoke item.

`run.sh`: `digest)` and `ingest)` roles run those modules. Railway: two services
from the same repo with `SERVICE_TYPE` set, cron `*/15 * * * *` and
`0 */3 * * *`, no healthcheck, no public domain. Created tonight via the Railway
MCP if permitted, otherwise listed as a morning step.

## Testing strategy

Fake DB gains `digests`, `sources`, `source_items`, `active_users`,
`reminders_on_date`, `reminders_overdue`. Cases: each section appears only when
non-empty; ≤10 lines from the template with maximal data; the model's overlong
reply is replaced by the template; the window logic (before, inside, after, and
a second run inside); idempotence via the UNIQUE; per-user tz; RSS 2.0 and Atom
fixtures; dedup on a second poll; `/rss add` refuses a private address; the
credit-balance 400 becomes the FR-39 message; the review sends nothing when
nothing is open.

## Boundaries

As stage 2. Additionally: the digest job never sends to a chat that is not the
user's own (`users.telegram_chat_id`); the ingest job makes no request before
the SSRF check; neither job runs the reminder tick.

## Success criteria

- `pytest` green; `python -m sveta.jobs.digest --dry-run` prints a brief for the
  seeded user against a real database (checked on Railway in the morning).
- After the push: `/rss add` of a real feed works from the chat; the next
  ingest run populates `source_items`; the next brief window sends the brief.
- Golden set unchanged (no persona or tool-description change in this stage).
