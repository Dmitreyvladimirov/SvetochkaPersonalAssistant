# Stage 3 — Google OAuth, calendar, mail, "trip" playbook (+ FR-14 Notion showcase)

Implementation spec for `SPEC.md` §11 stage 3, plus the deferred FR-14 from stage
5, started 2026-09-12 with Dimitry online for the OAuth and token steps. DoD
(§11): FR-24…FR-27, FR-44; S-13 end to end. FR-14: a note appears in Notion;
Notion failing does not prevent the Postgres save.

## Assumptions

1. **OAuth is done from the chat, not from a laptop script.** `/google` sends an
   authorisation link; Google redirects to
   `GET /oauth/google/callback` on `sveta-web`; the code is exchanged and the
   refresh token stored in `oauth_tokens` under Fernet (§8), one row per
   `(user_id, provider, account_email)`. The `state` parameter is an HMAC of the
   user id with `SVETA_TOKEN_KEY`, so a callback cannot attach a Google account to
   another user. Multi-user by construction: a second user runs `/google` too.
2. **Google is called over REST with `httpx`**, no Google SDK: three endpoints
   (token, Calendar events list/insert, Gmail messages list/get). Scopes:
   `calendar.events` and `gmail.readonly` (§8 minimal scopes).
3. **`cryptography` becomes a dependency.** Fernet is what the spec names for
   tokens and mail bodies (§8) and `SVETA_TOKEN_KEY` already exists for it; there
   is no stdlib Fernet. Flagged in the morning report as the one new dependency.
4. **Confirmation cards (§6.2) get their mechanism now.** A tool marked
   `needs_confirmation` never runs from the agent loop: the loop records
   `(tool, args, label)` in `ctx.pending`, returns "proposed, waiting for the tap"
   to the model, and the bot renders one button per pending action. The pending
   actions are stored on the inbox row (`suggestions` JSONB, `kind: "confirm"`),
   like FR-43 suggestions and FR-13 proposals. Tap → `cf:<item_id>:<n>` → claimed
   by update_id → the tool runs **by code**. `calendar_create` replies with the
   event; `mail_read_body` feeds the decrypted body into one more agent turn that
   answers the original question. An injection can propose a wrong event; it
   cannot press the button (§6.1).
5. **Mail bodies reach the model only after a tap.** `mail_search` returns
   subject, sender, date, snippet and the Gmail id, and stores each hit in
   `mail_messages` with the body encrypted; `mail_read_body(gmail_id)` is
   confirmation-gated and decrypts for one turn. Nothing of the body is logged.
6. **The trip playbook is data.** `sveta/playbooks/trip.md`. `mail_search`
   tags a hit as a trip when subject or sender matches a ticket/flight/booking
   pattern (Russian and English), and appends the playbook text to its result
   under "Playbook: trip". The agent then suggests (FR-43) "список мест",
   "проверить визу", "что собрать" — S-13. Adding another playbook is a markdown
   file plus one pattern line in `playbooks/__init__.py`; no core change (NFR-9).
7. **Calendar writes are always gated** (FR-25): the tool parses the date by
   code (`timeparse`), the card shows the parsed date and time; without a tap no
   event. FR-26 (mirror a reminder into the calendar) is offered as a suggestion
   under a created reminder when Google is connected, never automatic.
8. **The brief gains two sections** (FR-29, FR-30): "Встречи" from today's
   calendar events, "Поездки" from trip-tagged mail with a date in the next 14
   days. Google down → sections absent, a log line, the brief still goes out.
9. **Notion showcase (FR-14) is OAuth per user** (Dimitry's call, 2026-09-12:
   "более профессионально и в дальнейшем удобнее"). A *public* Notion
   integration (`NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET` in Railway, redirect
   `GET /oauth/notion/callback`); `/notion` sends the consent link, the user
   picks the pages on Notion's screen, the access token (Notion tokens do not
   expire) lands encrypted in `oauth_tokens` with provider `notion`. When the
   consent shared exactly one database it becomes the target automatically
   (preference `notion.notes_db`); otherwise `/notion <link>` picks one.
   `NOTION_TOKEN` remains a deployment-wide fallback (an internal integration)
   for a user without an OAuth row. On every `note_save` / `link_save` the note
   is mirrored as a page (title, body, project, source, url, created) in a
   background thread with that user's token; the page id lands in
   `notes.notion_page_id`. A Notion error is logged and the note stays in
   Postgres; no retry queue in v1.
10. **No schema change.** `oauth_tokens`, `mail_messages`, `notes.notion_page_id`
    exist since stage 0.

## Structure (additions)

```
sveta/core/crypto.py        Fernet encrypt/decrypt with SVETA_TOKEN_KEY
sveta/core/google.py        OAuth URL/exchange/refresh, calendar and gmail REST
sveta/core/notion.py        one call: create a page in a database
sveta/playbooks/__init__.py playbook loader + trigger patterns
sveta/playbooks/trip.md
sveta/tools/calendar.py     calendar_query, calendar_create (gated)
sveta/tools/mail.py         mail_search, mail_read_body (gated)
app.py                      GET /oauth/google/callback
bot.py                      /google, /notion, cf: callbacks, pending cards
agent.py                    needs_confirmation → ctx.pending
jobs/brief.py               meetings and trips sections
tests/test_google.py, test_calendar.py, test_mail.py, test_confirm.py,
tests/test_notion.py, test_playbooks.py
```

## Requirements → behaviour

- **FR-24** "что у меня завтра" → `calendar_query(range)`; `range` is a phrase
  parsed by code ("завтра", "на этой неделе", "в четверг"); returns up to 20
  events with local times and links. "когда встреча с Артёмом" → the next one and
  the one after (S-4) — the tool takes an optional `query` filter on summary.
- **FR-25** "поставь встречу с Костей в четверг в 15" → the loop records
  `calendar_create(title, when, duration_min)`; the card says "Создать событие:
  Встреча с Костей — чт 18.09 15:00–16:00"; tap → event; no tap → nothing.
- **FR-27** "найди билет на вечеринку типа Synergy" → `mail_search(query)` →
  Gmail `q`; hits stored encrypted; reply names date, sender, subject.
- **FR-28** `mail_messages.body_enc` is Fernet; a dump without the key is inert.
- **FR-29 / FR-30** brief sections.
- **FR-44 / S-13** "когда у меня самолёт?" → `mail_search` tags the ticket →
  playbook appended → suggestions под ответом; a tap runs the instruction
  through the agent as today.
- **FR-39** Google 401 on refresh → `oauth_tokens.last_error`, the chat says
  "календарь отвалился, переавторизуйся: /google" (§9); other work continues.
- **FR-14** note → Notion page; failure logged; `/notion` shows the state.

## Tests

Google and Notion HTTP faked at the module boundary (`google._post`, `google._get`,
`notion._post`). Cases: state HMAC rejects a foreign user; refresh token stored
encrypted and decryptable only with the key; 401 on refresh → last_error + the
chat message; calendar_query time range in the user's tz; calendar_create never
runs from the loop, runs once from the tap, a second tap says so;
mail_read_body gated the same way; body never in logs (caplog grep for a marker
string); trip pattern → playbook in the tool result; brief sections present only
when Google answers; Notion failure does not fail note_save; `/notion` with a
non-database link refused. Golden: +6 (calendar query, calendar create
proposal, mail search, trip, "поставь напоминание и в календарь").

## Needed from Dimitry (in progress)

`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`
in Railway; Calendar and Gmail APIs enabled; both redirect URIs registered
(`/oauth/google/callback`, `/oauth/notion/callback`); the Notion integration is
*public*. Then `/google` and `/notion` in the chat, and the consent screens.
