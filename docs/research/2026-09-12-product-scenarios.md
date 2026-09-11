# Product scenarios and integrations after the ticket fix

Written 2026-09-12. Grounded in `SPEC.md` (v0.6), `docs/stages/stage2.md`…`stage5.md`,
`docs/stages/acceptance-2-4-5.md`, `CLAUDE.md` "Current state", and the tool set in
`sveta/tools/` and `sveta/core/bot.py` as they exist today — before the in-flight fix
for attachments, Haiku trip extraction, the "create all N" card and airport time
zones. That fix is out of scope here; the scenarios below are what breaks *next*,
once it ships.

Two facts from reading the code, not from the spec, that shape everything below:

- `sveta/core/bot.py` reads only `message["voice"|"audio"]` and
  `message["text"|"caption"]`. A `photo` or `document` sent straight into the chat
  has no branch at all today — the caption (if any) gets processed as a bare note,
  the file itself is silently dropped. This is a bigger gap than mail attachments,
  because Telegram-native sharing is Dima's primary habit (§0 of `SPEC.md`).
- The tool registry (`sveta/tools/__init__.py`) has 22 tools but only 7 can be
  `strict` — the API refuses the request past that budget ("Schema is too
  complex", found and fixed 2026-09-12). All 7 slots are taken:
  `reminder_create`, `list_add`, `list_check`, `calendar_create`, `link_save`,
  `note_save`, `fact_remember`. **Any new write tool below needs a decision about
  which existing tool gives up its strict slot**, not just a green light to add one.

---

## 1. Scenarios the spec does not cover

Format per row: user story in Russian as Dima would type or say it → what happens
today (concretely, which code path) → what's missing → priority → effort.

Priority: **P0** blocks daily use in the first week · **P1** hit within the first
month · **P2** real but can wait.

### A. Telegram-native sharing (not mail, not a URL)

| # | User story | Today | Missing | P | Effort |
|---|---|---|---|---|---|
| 1 | «Вот скан посадочного» (photo, no caption) | Silently dropped — no `photo` branch in `bot.py`. Bot replies nothing or, if there's a caption, answers only the caption | Photo/document intake as a first-class message kind, independent of mail | **P0** | M |
| 2 | Forwards a Telegram channel post (not a link — Telegram forwards carry no URL) | `message.get("text")` catches the forwarded text, so it saves as an ordinary note with no distinguishing `source`; `source_ref` is empty, so FR-46 ("every note knows its source") is silently violated for this path | Detect `forward_from_chat` / `forward_from` and set `source="telegram_channel"`, `source_ref` = a stable forward reference | **P1** | S |
| 3 | Sends a photo of a whiteboard / a printed doc / a receipt and expects text back later | Same as #1 — dropped | OCR/structuring via a vision-capable model call (Sonnet, not Haiku — image quality varies) triggered the same way the mail-PDF extraction will be, just from a different intake path | **P1** | M |
| 4 | «Скинь мне сюда файл» after finding something — the reverse direction, bot → chat | Not a chat command bug, a missing capability: nothing in the tool set sends a file into the chat, only text and buttons | `sendDocument` support in the Telegram client wrapper, plus a way for a tool result to carry a file reference | **P0** (this is literally the ticket-PDF scenario that triggered the current fix — flagging it because the fix as scoped covers mail-sourced PDFs; a file the bot itself produces or re-sends from Drive needs the same `sendDocument` primitive) | S |

### B. Family logistics (wife Sveta, daughter)

| # | User story | Today | Missing | P | Effort |
|---|---|---|---|---|---|
| 5 | «Напомни Свете забрать дочку» | The agent has no way to know this isn't "напомни *мне*" — nothing in the persona or tool descriptions tells it Svetochka can only ever message Dima's own chat (NFR-8). Best case it creates a reminder that fires in *Dima's* chat with the wrong implied recipient; worst case it's ambiguous and the model guesses | A persona line making the boundary explicit in the reply, not just enforced by code: "напомню *тебе* про Свету", never silently reinterpreted as a message to her | **P0** | S (prompt only) |
| 6 | «Что у нас с садиком на этой неделе» — a recurring family logistics item (pickup schedule, activities) | No concept of a recurring event; `calendar_query` reads whatever is on the single connected Google Calendar. If daycare schedule lives in Sveta's calendar or a shared family calendar, it's invisible | A second calendar source (see §2, "a shared/secondary calendar") — a real scope decision, not just a tool | **P1** | M (once scoped) |
| 7 | «Каждый понедельник напомни оплатить садик» | `reminder_create` is one-shot by construction (`dedup_key = text + fire_at ISO minute`); there is no recurrence field anywhere in the `reminders` table or tool | A `recurrence` column/tool argument (weekly/monthly) and tick logic that re-schedules on fire instead of just marking `sent` | **P1** | M |

### C. Work as a PM

| # | User story | Today | Missing | P | Effort |
|---|---|---|---|---|---|
| 8 | «Что мы обсуждали с Костей в прошлый раз» | `entities` / `entity_links` tables exist in the schema (§7.3) but **no tool reads or writes them** — `note_search` is full-text only, with no person filter | Either extend `note_search` with a `person` argument backed by `entity_links`, or a new `entity_notes(name)` tool; and something has to populate `entity_links` in the first place (auto-link on `note_save`/`fact_remember`, or manual) | **P1** | M |
| 9 | Dump after a call with named owners and deadlines: «после созвона: Костя — бэкапы до пятницы, я — обновить деку» | `notes_propose` (FR-13) splits into flat notes with no owner/deadline structure; a per-line deadline is lost unless Dima separately says "напомни" | Either richer fields on `notes_propose` items (owner, due) or an explicit nudge in the persona to also call `reminder_create` for lines with a date — the latter is cheap and matches how `dump.py` already works | **P1** | S |
| 10 | «Скинь мне ссылку на доку по онбордингу» (a Google Doc, not a public page) | `link_fetch`/`link_save` scrape `<title>` and `og:description` over plain HTTP; a Docs/Sheets/Slides URL requires auth to even load, so the fetch gets a login wall and a useless title | Needs the Drive/Docs API (with the user's OAuth token) to resolve title and access, not the generic scraper | **P1** | S, once Drive scope exists (§2) |
| 11 | «Поставь встречу с командой, добавь Meet» | `calendar_create` creates a plain event; no `conferenceData` field is ever sent | One parameter added to the existing tool and to `google.create_event` — **no new OAuth scope**, `calendar.events` already covers `conferenceDataVersion=1` | **P0** (cheapest real win in this whole document) | S |

### D. Israel specifics

| # | User story | Today | Missing | P | Effort |
|---|---|---|---|---|---|
| 12 | Mail search / trip extraction on a Hebrew ticket confirmation (many Israeli airlines and agencies mail in Hebrew, RTL, sometimes mixed with English flight codes) | `mail.py`'s trip-tagging pattern match (`playbooks/__init__.py`) is pattern-based on subject/sender text — unclear whether the patterns include Hebrew keywords (билет/рейс/booking are Russian/English only per `RESEARCH.md`/`trip.md`). Untested against Hebrew subjects | Add Hebrew ticket/flight keywords to the trigger patterns; explicitly test the in-flight Haiku extraction fix against a Hebrew-subject email before calling it done, not just the two-email Colombia case | **P0** — this directly risks the fix in flight right now, flagging it for that work, not for later | S (test + pattern list) |
| 13 | «Не планируй мне встречи в пятницу вечером» / brief silently proposing something on Shabbat or a chag | No concept of Israeli holidays or Shabbat anywhere in `timeparse.py` or `brief.py` | A static Hebcal-sourced table (holidays + candle-lighting times per year) consulted by `calendar_create`'s confirmation card ("это суббота — всё равно ставить?") and by the brief | **P2** | M |
| 14 | «Когда лучше звонить маме в Россию» — cross-timezone family, not just cross-timezone flights | `timeparse` and `calendar_query` are single-tz (`scope.tz`) by design; the in-flight fix adds per-leg airport tz for flights specifically, not a general "person X is in tz Y" fact | Not urgent to generalize now — `facts` could hold a per-person tz once `fact_remember` supports it, but this is speculative until it comes up in real traffic | **P2** | S once facts support it |

### E. Travel, beyond the ticket fix

| # | User story | Today | Missing | P | Effort |
|---|---|---|---|---|---|
| 15 | «Проверь визу» (a trip playbook suggestion button that already exists) | `trip.md` literally says the tool for this is `note_search` — i.e. it searches Dima's *own* past notes for visa info. If he's never written one down, the honest answer is always "не нашла", forever. The suggestion button promises something the bot cannot deliver | Either a small static visa-requirements table (Israeli passport × destination — a short, maintainable list, not a live API) or rewrite the playbook to stop offering a button that can't work | **P1** | S (either fix is cheap; leaving it as-is is the bad option) |
| 16 | Airline reschedules a flight — new confirmation email lands | No re-extraction path: `mail_search` finds the new email as a new hit; nothing links it to the calendar event or reminder already created from the original ticket, so Dima now has two conflicting calendar entries and no signal either is stale | Out of scope for the current fix but worth a persona line: when a trip-tagged mail matches an existing calendar event by rough date, say so and ask before creating a duplicate | **P1** | S (persona + a `calendar_query` call before `calendar_create` in the trip flow) |
| 17 | «Что я обычно беру в поездку» — reusing a packing list across trips | `list_add` creates a fresh "Собрать в поездку" list every time per `trip.md`; nothing carries lines forward from the last trip | A list template/duplicate feature — genuinely nice-to-have, not a blocker | **P2** | S |

### F. Retrieval, editing, correction UX

| # | User story | Today | Missing | P | Effort |
|---|---|---|---|---|---|
| 18 | «Это не молоко, а сыр» — fixing a typo/wrong item, not a misfiled record | The only correction path is "Не туда" (FR-15), which is about *where* something went, not *what* it says; there is no `note_edit` or `list_item_edit` tool anywhere in the registry | A small `note_edit`/`list_item_edit` tool, silent with an undo like the rest of §6.2's "silently, with undo" row | **P0** — this is a daily-use papercut, not an edge case | S |
| 19 | «Удали ту заметку про парковку» | No `*_delete` tool exists anywhere — and `SPEC.md` §10 has a mandatory negative test that an injected "удали все заметки" must **not** find a `*_delete` call to make. A legitimate user request for a single, named delete currently has nowhere to go | A narrowly scoped, confirmation-gated `note_archive`/`list_item_remove` (soft-delete, not a hard `DELETE`, and never bulk) — this needs a deliberate decision so it doesn't reopen the injection-safety guarantee the test exists to protect | **P1** | S, but **needs a product decision on the confirmation/scope boundary before coding**, see §4 |
| 20 | «Перенеси то напоминание на завтра» for a reminder that hasn't fired yet | The only reschedule buttons (`+1 час` / `завтра`) live on a *delivered* reminder (`rm:snooze`, `rm:tomorrow` in the tick's keyboard); a still-`scheduled` reminder can only be cancelled and re-created from scratch, losing the original wording if Dima doesn't remember it | A `reminder_update` tool for the scheduled (not yet sent) state | **P1** | S |

---

## 2. Integrations to add

Everything below shares one constraint worth stating plainly: **the current Google
OAuth client is the personal one at `sodium-wall-331321`, granted `calendar.events`
and `gmail.readonly` today.** `gmail.readonly` is a Google **restricted** scope —
restricted scopes require a CASA security assessment to verify for an app serving
external users; a personal, single-user app can instead stay in the Cloud
Console's "Testing" publish status with Dima added as a test user, which needs no
verification at all but caps refresh-token life and the test-user list at 100 (a
non-issue at one user). Whichever mode it's actually in today should be confirmed
before adding anything new — SPEC.md §12 says "already In production," which is
hard to reconcile with an *unverified* restricted scope working reliably; that
line is worth double-checking against the actual Cloud Console screen, not taken
on faith. **Recommendation for every new scope below: prefer the least-sensitive
scope that does the job**, because sensitive/restricted scopes are what forces the
verification conversation, not the number of scopes.

| Candidate | Unlocks | API / scope | Verification impact | Recommendation |
|---|---|---|---|---|
| **Drive — write only** | scenario #4 (send a file into the chat via Drive), scenario #10 (Docs/Sheets titles), the ticket-PDF fix's natural next step (mirror a found ticket PDF to Drive) | Drive API v3, scope `drive.file` (access limited to files the app itself created or the user explicitly opened with it) | **None** — `drive.file` is not sensitive or restricted; no verification needed at any publish status | **Add now**, alongside the ticket fix — it's the same OAuth re-consent trip Dima has to make anyway when the fix ships (a scope can't be added to an existing refresh token, per SPEC.md §12) |
| **Drive — read/search all files** | "find that doc I made last year" style search across all of Drive, not just what Svetochka created | `drive.readonly` (sensitive) or full `drive` (restricted) | `drive.readonly` needs verification (no CASA); full `drive` needs verification + CASA | **Not now.** No scenario above needs it — #10 only needs to resolve a title for a link Dima already has, which `drive.file` combined with a link-share (Drive lets a file be "opened with" the app) can approximate. Revisit only if a real "search all my Drive" request shows up |
| **Google Meet via Calendar `conferenceData`** | scenario #11 | Existing `calendar.events` scope, `conferenceDataVersion=1` parameter on `events.insert` | **None** — no new scope | **Add now** — cheapest item in this whole document, one parameter |
| **Contacts / People API** | resolving "Костя" to a real email for a calendar invite | `contacts.readonly` (sensitive) | Needs verification | **No, not yet.** `calendar_create` today never adds attendees (SPEC.md has no requirement that it does) — this scope has no consumer until that feature is explicitly decided, and inviting other people raises its own §6.2 boundary question (see §4) |
| **Google Tasks** | a "task with a due date" object | `tasks` (non-sensitive) | None | **No.** SPEC.md's own open question #2 (§13) already decided lists cover this ground ("fewer entities, closer to Apple Notes"); adding Tasks reopens a decision that was deliberately closed, and duplicates Lists |
| **Google Maps / Places** | "сколько ехать до аэропорта", airport transfer suggestions in the trip playbook | Maps Platform (Directions/Places), **API key, not user OAuth** | **None** — this isn't a consent-screen scope at all | **Next stage**, cheap and no OAuth complexity; pairs naturally with the trip playbook's existing "что собрать" suggestion pattern |
| **Google Photos** | "найди мои фото из Колумбии" | Photos Library API | Google locked this down through 2025: broad read (`photoslibrary.readonly`) is now restricted and mostly replaced by the Picker API, which requires the user to hand-pick photos in a Google-hosted UI each time — it cannot power a background search | **No.** Not technically feasible as "search my photos from the bot" any more, and photos of a daughter are exactly the kind of data §8's privacy table is written to keep minimal | Recommendation: **no** |
| **Google Sheets/Docs (write)** | spend tracking, structured exports | Sheets/Docs API, would ride on `drive.file` for app-created files | None beyond `drive.file` | **No, not now.** SPEC.md already decided Notion is the showcase and Postgres is truth (§0 "Decisions taken," 09-01); a second showcase in Sheets duplicates that decision without a stated reason to reopen it |
| **Telegram file upload → Drive** | scenario #1, #4: a file shared into the chat gets both saved locally and optionally mirrored to Drive | Telegram Bot API `getFile`/`sendDocument` (already partly used for voice) + Drive `drive.file` | Same as Drive-write above | **Add now**, it's the Telegram-native counterpart of the mail-attachment fix already in flight and closes the bigger gap (§1, scenario #1 is P0) |
| WhatsApp | — | — | — | **Out of scope**, SPEC.md §1, fixed |
| Todoist | — | — | — | **Out of scope**, SPEC.md §1, fixed |
| Apple Calendar / Notes | — | — | — | **Stages 6–7** per SPEC.md §11, not now |

---

## 3. Proposed order (continuing from FR-58)

Five increments, ordered so each is buildable and testable on its own and the
riskiest/cheapest-to-verify items come first. All FR numbers are proposals for
Dimitry to accept into `SPEC.md`, not yet-accepted requirements.

**Increment 1 — close the Telegram-native intake gap (pairs with the ticket fix, same OAuth trip)**
- FR-59: photo/document sent directly into the chat is saved, not dropped; a
  document caption is treated as a comment on it, matching `link_save`'s pattern.
- FR-60: an image that looks like a document (boarding pass, receipt, screenshot
  of text) is OCR'd/structured the same way the in-flight fix does for mail PDFs.
- Tools: `file_save(file_ref, comment)` — Postgres row + Drive mirror behind
  `drive.file`; extend the existing extraction path (from the ticket fix) to be
  callable from this intake, not just from mail.
- Needs from Dimitry: nothing new to grant beyond what the ticket fix already
  needs — Drive re-consent can happen in the same sitting as the mail-attachment
  OAuth refresh.

**Increment 2 — daily-use papercuts (cheapest value, no integrations)**
- FR-61: `note_edit` / `list_item_edit` — fix wording without going through
  "Не туда".
- FR-62: `reminder_update` — reschedule a still-scheduled reminder without
  re-typing it.
- FR-63: Meet on `calendar_create` (`with_meet: bool` → `conferenceData`).
- Tools: `note_edit`, `reminder_update`; one-line change to `calendar_create`
  and `google.create_event` for Meet. **All three write tools compete for the
  7-slot strict budget** — recommend retiring `list_check` from `STRICT_TOOLS`
  (checking a box is the lowest-cost mistake in the whole tool set — worst case
  is a wrong checkbox, trivially undone by tapping again) to make room.
- Needs from Dimitry: a go on which existing strict tool gives up its slot, plus
  a persona-only fix (no code) for scenario #5, "напомни Свете" — cheap enough
  to fold into this increment even though it's not a tool.

**Increment 3 — entity-linked recall for work**
- FR-64: `note_search`/`fact_recall` gain an optional `person` filter backed by
  `entity_links` (tables already exist since stage 0, unused by any tool today).
- Tools: extend `note_search`'s schema, or a new `entity_notes(name)` tool if
  keeping `note_search`'s schema simple is preferred.
- Needs from Dimitry: a call on how `entity_links` gets populated — auto-linked
  by the model on `note_save`/`fact_remember` (more coverage, more model
  judgment to get wrong) vs. only ever set explicitly ("это про Костю") — this
  is a real product tradeoff, not an implementation detail, and belongs in
  `interview-me` with Dimitry before Increment 3 starts.

**Increment 4 — the trip playbook stops overpromising**
- FR-65: replace the "проверь визу" suggestion's `note_search`-only path with a
  small static visa-requirements table (Israeli passport × common destinations)
  or remove the button if Dimitry would rather it not exist than answer wrong.
- FR-66: before `calendar_create` fires from the trip flow, `calendar_query` the
  rough date first and flag a likely duplicate (a rescheduled flight scenario,
  #16) instead of silently creating a second event.
- Needs from Dimitry: the destinations list for the visa table (or the decision
  to drop the button instead).

**Increment 5 — Israel calendar awareness**
- FR-67: a static Hebcal-derived holiday/Shabbat table consulted by
  `calendar_create`'s confirmation card and by the brief.
- FR-68: Hebrew keywords added to the trip-tagging pattern in
  `playbooks/__init__.py` — flagged as urgent enough to fold into the ticket
  fix itself (scenario #12) rather than wait for this increment, but the
  general Shabbat/holiday awareness is genuinely a later increment.
- Needs from Dimitry: confirm a yearly-refreshed static table is acceptable
  (no live API, no OAuth) — this is a data-maintenance commitment, not a
  one-time build.

---

## 4. What NOT to build, and why

- **Sending or replying to email as Dima.** No FR asks for it, and it's the
  email-domain analogue of NFR-8 ("Svetochka never writes to other people's
  chats as Dima") even though NFR-8's literal text is about Telegram — the same
  reasoning applies with more force to email, which reaches people who never
  opted into a bot's involvement at all.
- **Adding attendees / sending calendar invites to other people.** §6.2's table
  says "anything visible to other people — only on a tap," but an invite is a
  bigger step than that: it's an email Google sends to a named third party on
  Dima's behalf. A tap on Dima's side authorizes an event *for himself*, not a
  notification to someone else — that needs its own explicit spec line before
  any Contacts-API scope is even worth requesting, not just a confirmation card
  reused from `calendar_create`.
- **Full inbox triage / auto-labeling / background email scanning.** §8 is
  explicit: "an email body never reaches the model without an explicit
  request." Anything that scans the inbox proactively (to summarize, to flag,
  to auto-file) reaches for bodies without Dima asking in the moment, which is
  the exact rule this line exists to block — and separately, it's the kind of
  standing administration §3 already rejects ("if Svetochka needs
  administration, she has lost to a notepad").
- **Google Photos search.** Covered in §2 — no longer cleanly possible via API,
  and photos of a child are precisely the data §8's minimal-scope table exists
  to keep out of an unverified app's reach.
- **Google Tasks as a new object type.** SPEC.md §13 already closed this
  question in favor of lists; building Tasks now reopens a decision without a
  new reason to.
- **A merged/shared view into Sveta's calendar or notes by default.** If Sveta
  ever becomes a second Svetochka user, NFR-10 (tenant isolation by
  construction) says her data gets her own `user_id` and her own `UserScope` —
  not read access folded into Dima's. A "family view" is a real, separate
  product decision (who can see whom, opt-in per direction) and should not be
  approximated by quietly sharing one Google account's calendar between two
  people's reminders.
- **Reading Sveta's or the daughter's personal chats/messages.** Stage 6's
  account-reading (Telethon) is scoped to Dima's own chats with an explicit
  allowlist (FR-35); reading anyone else's messages, even a spouse's, for
  "family logistics" is not what that stage is for and isn't proposed anywhere
  above.
- **A general `*_delete` tool.** Scenario #19 is real, but SPEC.md §10's
  mandatory negative test exists specifically to guarantee no delete path is
  reachable by prompt injection. Increment 2's `note_edit` covers most of the
  real daily friction (wrong text, not "shouldn't exist at all"); a genuine
  delete/archive tool needs its own confirmation-gating design reviewed against
  that test before it's added, not a quick addition alongside edits.

---

## Summary

1. `sveta/core/bot.py` has no branch for `photo` or `document` messages at all —
   anything shared as a file rather than mail or a URL is silently dropped
   today. This is a bigger, more frequent gap than the mail-attachment case
   currently being fixed, because sharing straight into Telegram is Dima's main
   habit.
2. Two tool-set gaps cause daily friction independent of any integration:
   there is no `note_edit`/`list_item_edit` (only the misfiling-focused "Не
   туда") and no `reminder_update` for a still-scheduled reminder.
3. `entities`/`entity_links` tables exist since stage 0 but no tool reads or
   writes them — "what did we discuss with Костя" has no path today.
4. The trip playbook's "проверь визу" suggestion button calls `note_search`,
   which can only find visa info Dima already wrote down himself — it currently
   promises something it structurally cannot deliver.
5. Google Meet is a one-parameter addition to the existing `calendar_create` —
   `calendar.events` already covers `conferenceData`; no new OAuth scope, no
   verification question, the cheapest real win in this review.
6. Drive-write (`drive.file` scope) is the right integration to add now: it is
   not a sensitive or restricted scope, needs no Google verification at any
   publish status, and unlocks both "send me the file in chat" and "save this
   to Drive."
7. Drive-*read-everything*, Contacts, and Google Tasks are recommended against
   for now — each is either a sensitive/restricted scope with no scenario that
   needs it yet, or duplicates a decision SPEC.md already made deliberately
   (Tasks vs. Lists).
8. Google Photos search is not currently buildable the way the ask implies:
   Google restricted broad photo-library read access through 2025 in favor of
   a manual Picker UI, which cannot power background search.
9. The Hebrew-subject risk (scenario #12) should be tested against the
   in-flight ticket-extraction fix *before* that work is called done, not
   deferred to a later increment — Israeli airline/agency mail is routinely
   Hebrew and the current trip-tagging patterns look Russian/English-only.
10. "Напомни Свете" needs a one-line persona fix now, not a new tool: the
    autonomy boundary (Svetochka never messages anyone but Dima) is enforced in
    code, but nothing tells the model to say so honestly in the reply instead
    of silently reinterpreting the request.
11. Adding calendar attendees/invites is explicitly flagged as **not** covered
    by the existing "tap to confirm" pattern — inviting a third party notifies
    them, which is a bigger boundary than anything §6.2 currently authorizes.
12. A `*_delete` tool is a real, unmet need (scenario #19) but must not be
    built as a quick add-on: SPEC.md §10 has a mandatory injection test
    guaranteeing no delete path exists today, and any delete tool has to be
    designed against that test, not alongside `note_edit`.
13. If Sveta ever becomes a second user, NFR-10 requires her own `UserScope`,
    not a shared view folded into Dima's account — a "family view" is a
    separate product decision that should be made explicitly, not approximated.
14. The tool registry's strict-schema budget (7 of 7 slots already used) means
    every new write tool proposed here needs an explicit decision about which
    existing tool gives up its slot — recommended: `list_check`, the lowest-
    stakes mistake in the current strict set.
15. Proposed order: (1) Telegram-native file intake, riding the same OAuth
    re-consent the ticket fix already needs; (2) note/reminder editing plus
    Meet, cheap and integration-free; (3) entity-linked recall, pending a
    product call on auto- vs. explicit linking; (4) trip playbook honesty
    fixes; (5) Israel calendar awareness, deferring the Hebrew-pattern part
    into the current fix rather than this increment.
