# Stage 2 — voice, reminders, links, lists

Implementation spec for `SPEC.md` §11 stage 2. `SPEC.md` stays the accepted
specification; this file says how its requirements land in the code that exists
after stage 1. Written 2026-09-11 for an autonomous overnight run (Dimitry's go:
stages 2, 4, 5; push to `main` after each).

## Objective

After this stage the bot is useful on its own (SPEC.md §11): a voice note on the
go becomes a note or a reminder, "напомни в четверг в 11" fires on Thursday at
11:00 even if the service was redeployed in between, a bare link is fetched and
titled, and "добавь в покупки молоко и батарейки" produces a list with tappable
checkboxes.

Requirements covered: FR-16…18 (voice), FR-19…23 (reminders), FR-11 and FR-12
(links), FR-47…52 (lists). DoD (§11): FR-16…23, FR-11, FR-47…50; a reminder
survives a redeploy. FR-12, FR-51, FR-52 are S-priority and included because they
are cheap once the M items exist.

## Assumptions (decided here, revisable)

1. **Transcription runs inline** in the same background thread as the rest of the
   update, not through `job_queue`. A 10-minute voice note transcribes in well
   under a minute; the thread already outlives the webhook response. §9's
   "worker crash mid-transcription → picked up again" is approximated by leaving
   the inbox row in `status='new'` with the error recorded, so a later sweep can
   retry. A queue-based worker is deferred until a second long-running job exists.
2. **Whisper is called over plain HTTPS** (`POST /v1/audio/transcriptions`,
   model `whisper-1`, `language=ru`) with `httpx`, which is already a dependency.
   No OpenAI SDK.
3. **Time is parsed by a hand-written Russian parser** (`sveta/core/timeparse.py`),
   no `dateparser` dependency: the supported forms are enumerable and every one
   of them is a unit test. Unknown phrase → the tool answers "не поняла время",
   nothing is created.
4. **The reminder tick is a daemon thread** inside the web process (§7: "the
   reminder tick lives inside `web`"), polling every 20 s with
   `FOR UPDATE SKIP LOCKED`. Two web replicas would not double-send; one replica
   restarting loses nothing because due rows stay `scheduled` in Postgres.
5. **Link fetch is synchronous**, 10 s timeout, 1 MB cap, after an SSRF check on
   the resolved address. Title from `<title>`, summary from
   `og:description` / `meta description` / first paragraph. **No model call** for
   FR-12 — "1–2 sentences of substance" comes from the page's own metadata.
6. **List checkboxes are inline buttons, one per line**, callback toggles the
   line and the message is edited in place (FR-49). Telegram allows 100 buttons;
   a list shows at most 30 lines with buttons, longer lists say so.
7. **Undo generalises**: `undo:n:<note_id>`, `undo:l:<list_item_id>`,
   `undo:r:<reminder_id>`. The stage-1 form `undo:<digits>` keeps meaning a note,
   so buttons under old messages still work.
8. **No schema change is needed.** `inbox_items.transcript`, `reminders`,
   `lists`, `list_items`, `links` exist since stage 0 with the right columns.
9. `OPENAI_API_KEY` joins `REQUIRED` (the spec says stage 2 needs it). It is
   already set in Railway under the alias `sveta_openai_api`.

## Tech stack

Python 3.13, FastAPI, psycopg2, `anthropic` 1.x, `httpx` (Whisper, link fetch),
stdlib `zoneinfo` / `html.parser` / `ipaddress`. No new dependencies.

## Commands

```
.venv/bin/python -m pytest -q                              # unit + integration, no network
SVETA_GOLDEN=1 ANTHROPIC_API_KEY=… .venv/bin/python -m pytest -m golden -q -s
git push origin main                                       # deploys sveta-web
```

## Project structure (additions)

```
sveta/core/timeparse.py     Russian time phrases → aware datetime in the user's tz
sveta/core/fetch.py         SSRF guard + bounded HTTP GET + title/summary extraction
sveta/core/transcribe.py    Telegram file download + Whisper call
sveta/jobs/__init__.py
sveta/jobs/reminder_tick.py due reminders → Telegram, FOR UPDATE SKIP LOCKED
sveta/tools/reminders.py    reminder_create, reminder_list, reminder_cancel
sveta/tools/lists.py        list_add, list_show, list_check, list_move
sveta/tools/links.py        link_save, link_fetch
tests/test_timeparse.py, test_fetch.py, test_voice.py, test_reminders.py,
tests/test_lists.py, test_links.py, test_tick.py; isolation cases live next to each tool's tests
tests/golden/scenarios.jsonl  +12 scenarios for the new tools
```

## Design by requirement

### Voice — FR-16, FR-17, FR-18

`bot._handle_message`, voice branch:

1. `duration > config.MAX_VOICE_SECONDS` → reply "Голосовое длиннее N минут — не
   беру, чтобы не жечь деньги. Скажи короче или текстом." Row marked `done`,
   **no `getFile`, no download** (FR-17).
2. Otherwise send the placeholder "Расшифровываю…" and keep its `message_id`.
3. `transcribe.telegram_voice(file_id)` → `getFile` → download `file_path` →
   Whisper. Empty transcript → "Не разобрала, повтори, пожалуйста." (§9), row
   `done`, no records.
4. Transcript stored in `inbox_items.transcript`; the text then goes through the
   **same** pipeline as a typed message (cheap path for a bare URL, otherwise the
   agent). The final reply **edits the placeholder** (FR-18) and is prefixed with
   the transcript in quotes so the user sees what was heard: `«…»\n\n<reply>`.
5. Any transport failure → row stays `new` with `error`, reply "Не смогла
   расшифровать — сохранила, попробую позже."

### Reminders — FR-19…FR-23

`timeparse.parse(phrase, now, tz) -> datetime | None`. Supported (all in the
user's tz, result timezone-aware):

- relative: `через N минут|часов|дней|недель`, `через полчаса`, `через час`
- day words: `сегодня`, `завтра`, `послезавтра`, `в понедельник … воскресенье`
  (next occurrence; if today and the time is still ahead — today), `через неделю`
- day parts: `утром` 09:00, `днём` 13:00, `вечером` 19:00, `ночью` 22:00
- clock: `в 11`, `в 11:30`, `в 9 утра`, `в 7 вечера`, `в 11 часов`
- dates: `25 сентября`, `25.09`, `15 числа`, optionally `в HH[:MM]`
- a bare clock time with no day → today if still ahead, else tomorrow
- a day with no time → 09:00

Tools (`sveta/tools/reminders.py`):

- `reminder_create(text, when)` — `when` is the user's phrase verbatim. Past →
  "Error: that time is in the past (…); nothing created" (FR-23). Duplicate
  (`dedup_key = lower(text)|fire_at ISO minute`) → "Already exists: #id at …"
  (FR-22). Success → `ctx.created_reminder_ids.append(id)` and a string with the
  exact local time, which the reply must echo (§6.2).
- `reminder_list()` — scheduled ones, soonest first, with ids.
- `reminder_cancel(reminder_id)` — status `cancelled`.

Delivery (`sveta/jobs/reminder_tick.py`): every 20 s,
`SELECT … FROM reminders WHERE status='scheduled' AND fire_at <= now() FOR
UPDATE SKIP LOCKED`, send "⏰ <text>" with buttons `сделано / +1 час / завтра`,
mark `sent` + `sent_at` in the same transaction. Chat id comes from a join with
`users`. Started from the FastAPI lifespan; a `SVETA_TICK_SECONDS=0` disables it
(tests, and any future cron-only role). FR-20 "within a minute" holds with a
20-second period. FR-21 callbacks: `rm:done:<id>` → `done`; `rm:snooze:<id>` →
`fire_at = now + 1h`, `scheduled`; `rm:tomorrow:<id>` → next day 09:00 in the
user's tz, `scheduled`; `undo:r:<id>` → `cancelled` (the button under the
creation reply).

### Links — FR-11, FR-12

`fetch.get(url)`: scheme must be http/https; host resolved with
`socket.getaddrinfo`; every resolved address must be public (no loopback,
private, link-local, multicast, reserved, `169.254.169.254`); redirects followed
at most 3 times with the same check on each hop; response body capped at 1 MB;
timeout 10 s. Returns `FetchResult(final_url, status, title, summary, error)`.

`link_save(url, comment)`: fetch → `links` row always (status or error recorded);
a `notes` row (`source` by domain, `source_ref=url`, body = title + comment) only
when the fetch succeeded (FR-11: "404 → recorded, no note created"). The bare-URL
cheap path in `bot.py` calls the same function — still no model call (FR-6).
`link_fetch(url)`: fetch and return title + summary without saving, for "что по
этой ссылке?" (FR-12).

### Lists — FR-47…FR-52

Names are free text; matching is case-insensitive on the trimmed name.
"Сегодня" and "Покупки" are ordinary lists created on first use like any other
(FR-47). A line is arbitrary text (FR-47).

Tools (`sveta/tools/lists.py`):

- `list_add(list_name, items[])` — creates the list if missing, appends lines in
  order, `ctx.created_list_item_ids` extended (undo), `ctx.render_list_id` set so
  the reply carries the checkbox keyboard (FR-48).
- `list_show(list_name, as_text)` — `list_name=""` → overview of all lists with
  counts; otherwise the list. `as_text=true` → plain text, no keyboard (FR-52).
  Otherwise `ctx.render_list_id` is set (FR-49).
- `list_check(list_name, item, checked)` — `item` is a line number or a text
  fragment; toggles `checked_at`.
- `list_move(item, from_list, to_list)` — moves a line, sets `moved_from`
  (FR-51).

Rendering (`bot._list_keyboard`): the message text is the list name and its
lines (`☐`/`☑` prefix); one inline button per line, label `☐ молоко` /
`☑ молоко`, callback `li:<item_id>`. Tap → toggle → `editMessageText` with the
re-rendered list on the same `message_id` (FR-49). Checked lines are kept in the
message, struck through by the prefix only (plain text, no parse_mode).

FR-50 is the brief's job (stage 4); this stage provides
`db.unchecked_list_items(user_id, "Сегодня")` for it.

### Bot wiring

- `ToolContext` gains `created_reminder_ids`, `created_list_item_ids`,
  `render_list_id`.
- `bot._keyboard` builds rows in this order: list checkboxes (if
  `render_list_id`), "✖︎ Не туда" for the last created record of any kind,
  "отменить" for a created reminder, then suggestions.
- `HELP` updated: voice, reminders, lists move from "пока не умею" to "умею".
- Callbacks: `li:`, `rm:`, generalised `undo:`.

### Persona

`playbooks/persona.md` gains three lines: pass the user's time phrase verbatim to
`reminder_create`; several items in one message → one `list_add` with several
items or several `note_save`; after a reminder is created, repeat the exact time
the tool returned. Golden set runs before (95%, baseline) and after.

## Code style

Same as stage 1: one tool per file, docstring names the FR, strict schemas, tools
return plain strings for the model, everything takes `UserScope`. Example:

```python
def _create(scope: UserScope, ctx: ToolContext, text: str, when: str) -> str:
    fire_at = timeparse.parse(when, now=datetime.now(tz), tz=tz)
    if fire_at is None:
        return f"Error: could not understand the time {when!r}. Ask the user to rephrase."
    if fire_at <= datetime.now(tz):
        return f"Error: {fmt(fire_at)} is in the past; nothing created (FR-23)."
```

## Testing strategy

`pytest`, no Postgres, no network. `tests/fakedb.py` gains lists, list_items,
reminders, links with the same `user_id` filtering as the SQL. Whisper, Telegram
file download and HTTP fetch are monkeypatched at the module boundary
(`transcribe._whisper`, `transcribe._download`, `fetch._http_get`).

Mandatory cases (SPEC.md §10): 40-minute voice → refused, zero downloads · link
to `169.254.169.254` and to `http://localhost` → rejected before any request ·
duplicate reminder · reminder in the past · cross-user leak for lists and
reminders · tick sends a due reminder once and not a future one · checkbox tap
edits in place · the parser table (≥30 phrases with a fixed `now`).

Golden: +12 scenarios (reminders with each time form, lists, link with comment,
"что у меня в покупках", "перенеси … в …"). Threshold stays 90%.

## Boundaries

- Always: run `pytest` before every commit; golden before and after any change to
  `persona.md` or a tool description; every tool has its own test; reply on every
  path.
- Ask first (left for the morning report, not done tonight): new tables or
  columns; new dependencies; any change to `SPEC.md`.
- Never: log a token, a key or a transcript; download a voice file over the
  limit; make an outbound request before the SSRF check; send anything to a chat
  that is not the user's own.

## Success criteria

- `pytest` green including the cases above.
- Golden ≥ 90% after the persona change.
- On Railway after the push: `/health` 200; a reminder created before a redeploy
  fires after it (checked in the morning: create one for +N minutes, redeploy or
  wait for the next push, watch it arrive).
- Manual acceptance in the morning (SPEC.md §14 item 3): one voice note, one
  "напомни …", one "добавь в покупки …", one link with a comment.

## Open questions (none block)

- Whether a transcript should be shown in full or only the first 200 characters
  when the note is long. Chosen: full, capped at the Telegram limit.
- Whether `list_add` on "Сегодня" from the morning should also create a reminder.
  Chosen: no; stage 4's brief and review cover it (FR-50).

---

# Implementation plan

Vertical slices, each leaving `pytest` green. Verification is `pytest -q` after
every task; golden after task 9; push to `main` after the review.

### Phase 1: foundations with no bot wiring

- **Task 1 — fakedb + ToolContext.** Add lists, list_items, reminders, links to
  `tests/fakedb.py` and the matching real functions to `db.py`; add the three
  `ToolContext` fields. Acceptance: fake and real signatures match; existing
  tests green. Files: `db.py`, `scope.py`, `tests/fakedb.py`. Size M.
- **Task 2 — timeparse.** `sveta/core/timeparse.py` + `tests/test_timeparse.py`
  with a fixed `now` and ≥30 phrases including past and unknown. Size S.
- **Task 3 — fetch with SSRF guard.** `sveta/core/fetch.py` + `tests/test_fetch.py`
  (metadata IP, localhost, private range, redirect to private, 1 MB cap, title
  and summary extraction). Size S.

### Checkpoint 1: `pytest -q` green, no bot behaviour changed.

### Phase 2: tools

- **Task 4 — reminders tools.** `reminder_create/list/cancel`, dedup, past
  refusal, `created_reminder_ids`. Tests + isolation case. Size S.
- **Task 5 — lists tools.** `list_add/show/check/move`, `render_list_id`.
  Tests incl. FR-49 toggle by number and by text, isolation case. Size S.
- **Task 6 — links tools.** `link_save/link_fetch`, note only on success.
  Tests with the fetch monkeypatched. Size S.
- Register all in `REGISTRY`; `test_every_tool_schema_is_strict` covers them.

### Checkpoint 2: tools callable through `agent.run` with the fake model.

### Phase 3: bot wiring (vertical slices)

- **Task 7 — lists in the chat.** Keyboard rendering, `li:` callback edits in
  place, generalised `undo:`. `tests/test_bot.py` cases. Size M.
- **Task 8 — reminders in the chat.** Cancel button under creation; tick module
  with `FOR UPDATE SKIP LOCKED`; lifespan starts the thread; `rm:` callbacks.
  `tests/test_tick.py` with the fake DB. Size M.
- **Task 9 — voice.** `transcribe.py`, the voice branch in `bot.py`, placeholder
  edit, duration refusal before download. `tests/test_voice.py`. Size M.
- **Task 10 — links cheap path + HELP + persona.** Bare URL → `link_save`; HELP
  text; persona lines; `OPENAI_API_KEY` in `REQUIRED`; run.sh unchanged. Size S.

### Checkpoint 3: `pytest -q` green; golden ≥ 90% (after persona edit); review.

### Phase 4: ship

- `/review` on the diff; fix what it finds.
- Commit, push to `main`, watch the Railway deploy log for "tick: started" and
  the webhook registration; `/health` 200.
- CLAUDE.md "Current state" updated; Notion stage-2 card → done with the golden
  number; morning acceptance list in the report.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Time parser misreads a phrase → wrong reminder time | High (silent) | The reply always echoes the parsed local time; cancel button under it; ≥30 parser tests |
| Tick thread dies silently | High | Thread wraps the loop in try/except with a log line per failure and keeps going; `/health` reports `tick_alive` |
| Whisper cost on long notes | Low | Duration refused before download at 600 s |
| Fetch used for SSRF | High | Resolve-then-check on every hop; tests for metadata and private ranges |
| Golden drops after persona edit | Medium | Baseline 95% recorded; revert the persona lines if < 90% and report |
| Push deploys a broken build overnight | Medium | Railway healthcheck keeps the old deployment on a failed `/health`; tests before push |
