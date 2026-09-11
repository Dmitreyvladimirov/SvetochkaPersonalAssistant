# Why Svetochka feels dumb, and what to fix

Written 2026-09-12, in response to the party-ticket failure. Grounded in
`sveta/core/agent.py`, `sveta/playbooks/persona.md`, `sveta/playbooks/trip.md`,
`sveta/playbooks/__init__.py`, every file in `sveta/tools/`, the relevant parts of
`sveta/core/google.py`, `tests/golden/scenarios.jsonl` and `tests/test_golden.py`.
Builds on `docs/research/2026-09-12-product-scenarios.md`.

## The failure, read in code

```python
# sveta/tools/mail.py, _search()
hits = google.search_mail(scope.user_id, query, limit=5)
...
if not hits:
    return f"No mail matches '{query}'. Say so honestly; suggest other words."
```

```python
# sveta/core/google.py, search_mail()
status, body = _get(f"{GMAIL_URL}/messages", params={"q": query, "maxResults": str(limit)}, headers=auth)
```

The model called `mail_search(query="билет вечеринка")` once. The tool does
**zero** query construction — the model's raw string goes straight into Gmail's
`q` parameter, which AND-matches both Russian words, in Russian only. "в эту
субботу" — the one piece of disambiguating information in the message — was
never turned into anything; there is no date argument on `mail_search` at all,
and nothing in `mail.py` calls `timeparse`. The tool returned an honest "no mail
matches," exactly as designed, and the model, exactly as instructed
(`persona.md`: "если инструмент ничего не нашёл — так и скажи"), reported the
failure and suggested — in text, to the user, as homework — the exact next
queries a competent human assistant would have just tried: платформа, организатор,
Bilettix, Eventbrite. **The model already has the knowledge. It never turned that
knowledge into a second tool call.** That gap — knowing the next move and not
making it — is the whole finding of this document.

## 1. Tool by tool: where giving up is possible, and whether the contract allows persistence

| Tool | A lazy call | A persistent call | Can the tool contract even express persistence? |
|---|---|---|---|
| `mail_search` | One query, the user's words verbatim, no date, one language. Exactly what happened. | Try synonyms across Russian/English/Hebrew, translate a date phrase into `after:`/`before:`, fall back to known sender domains, fall back to `has:attachment` + date only. | **No.** The schema has one field, `query` (free text), and `google.search_mail` has no date parameter, no OR-builder, no sender list. The model would have to hand-write Gmail search syntax itself to be persistent — nothing stops it, but nothing helps it either, and the persona never tells it Gmail syntax exists |
| `mail_read_body` | Gated by design (§6.2) — not a laziness axis | — | N/A, correct as-is |
| `note_search` | One `to_tsvector('russian', …)` full-text query (per SPEC.md §7.3); Postgres full-text is AND-by-default between terms — a paraphrase that doesn't share the exact stems returns nothing | Retry with fewer/different words, or fall back to `note_recent` (chronological, no query needed) when the topic is recent, or `fact_recall` when the topic is a person/thing rather than an idea | **Partially.** `note_recent` and `fact_recall` already exist as separate tools the model *could* chain to — this is closer to a persona gap than a missing tool. But `note_search` itself offers no broadening (no `OR`, no stemmed synonym try) |
| `note_recent` | Calling it once with a guessed `limit` | Widening `limit` (1–20) or dropping the `project` filter if a filtered call comes back empty | Contract allows it (`limit` is free), nothing in the persona says to widen it |
| `notes_propose` (dump) | N/A — a write, not a search | — | N/A |
| `fact_remember` / `fact_recall` | One `topic` string; `fact_recall` matches subject-or-object contains, case-insensitive substring per `stage5.md` | Trying the predicate side, or a shorter substring, if the full phrase misses | Contract is narrower than `note_search` (substring only, no full-text) — a paraphrase ("кто отвечает за бэкапы" vs. stored predicate "отвечает за инфру") can miss with no fallback offered |
| `link_save` / `link_fetch` | N/A — deterministic HTTP fetch, no judgment call to hedge on | — | N/A, this is the one search-adjacent tool with nothing to be lazy about |
| `list_add` / `list_show` / `list_check` / `list_move` | N/A — writes and reads by exact name; "лень" here looks like creating a near-duplicate list ("Покупки" vs "покупки") rather than search-hedging | Case/whitespace-insensitive matching (already implemented per `stage2.md`: "matching is case-insensitive on the trimmed name") | Already reasonably solid |
| `reminder_create` / `_list` / `_cancel` | N/A — deterministic date parsing, refuses cleanly (past date, duplicate) | — | Already reasonably solid; refusal reasons are specific, not a shrug |
| `calendar_query` | One `period` phrase through `google.range_for`; an unparseable compound/vague phrase ("на этой неделе или на следующей, не помню") returns `None` → the tool says "could not understand the period" and stops | Default to a wide window (e.g. 14–30 days) instead of erroring when the phrase doesn't parse — **reading is free (§6.2)**, there is no cost to showing too much, only to showing nothing | Contract already supports an empty/default period ("`period` narrows by title... or empty for the next 7 days" per the tool description) — the gap is that a *garbled* period is treated as an error instead of falling through to that same default |
| `calendar_create` | N/A — gated, correct as designed | — | N/A |
| `preference_set` / `_delete` / `memory_show` | N/A | — | N/A |
| `suggest` | The model offers "список мест", "проверить визу" as buttons instead of just doing the read half of those (e.g. `note_search` for visa info) before offering the button — see §3 | — | Working as designed; flagged in §3 as a place the *button* itself overpromises (already noted in the prior review, scenario #15) |

**The pattern:** every tool that reads external data with a free-text `query`
(`mail_search`, `note_search`, `fact_recall` in a weaker way) has a contract that
lets the model be lazy by default — pass exactly what the user said, once — and
offers it no structured way to be persistent instead. `calendar_query` is the
outlier: its contract already supports a sane default, and the bug is purely
that a parse failure doesn't fall through to it.

## 2. Search competence — the retry ladder

### What belongs in the tool vs. the prompt

The task's own framing is the right test: **can `mail_search` even express
"ticket OR билет OR כרטיס from any ticketing sender in the next 10 days"?**
Today, no — and that's the fix, not a smarter model. Put the ladder where it's
deterministic, cheap and unit-testable (`sveta/core/google.py`,
`sveta/tools/mail.py`), and leave the prompt only the part that's genuinely
judgment: *deciding the topic and the rough timeframe from what the user said*,
and *not stopping after one silent failure*.

```
Model's job (judgment):
  - read "найди билет на вечеринку в эту субботу" and produce:
      query = "билет вечеринка", date_phrase = "в эту субботу"
  - pass both to mail_search; never construct Gmail syntax itself

Tool's job (deterministic, inside mail_search / google.py):
  1. date_phrase → timeparse.parse() → after:/before: (same code path
     reminder_create and calendar_create already use — SPEC.md §6.1: dates are
     parsed by code, never by the model)
  2. query → a small static synonym table by domain, not a live translation
     call:
       "билет"/"ticket"/"tickets" → {"билет" "ticket" "tickets" "כרטיס" "כרטיסים"}
       "вечеринка"/"party"/"event" → {"вечеринка" "party" "event" "מסיבה"}
       "рейс"/"flight"/"перелёт"   → {"рейс" "перелёт" "flight" "טיסה"}
     (the same three or four domains already named in playbooks/__init__.py's
     TRIGGERS regex — this reuses knowledge already encoded once, instead of
     inventing a second copy of it in English-only prose)
  3. build candidate Gmail queries, most specific first, and try them IN ONE
     mail_search call, stopping at the first non-empty result:
       a. "{билет ticket כרטיס party вечеринка}" + after:/before:
       b. same, without the party/event word — pure date + ticket-domain terms
       c. from:(bilettix.com OR eventbrite.com OR ticketmaster.com OR
          go-out.co.il OR *.tixbee.com) + after:/before:  — a short, named
          list of the ticketing platforms actually used in Israel, not a
          generic guess
       d. has:attachment + after:/before: only — broadest useful fallback;
          e-tickets are almost always PDF attachments
  4. return either the first non-empty hit set, or an honest "tried 4
     phrasings across 3 languages for 12–14 Sep, nothing matched" — material
     the model can use to give a specific, not generic, "не нашла"
```

This removes the retry decision from the model entirely for the common case —
it cannot forget to be persistent, because persistence is now the tool's
default behaviour, not a hope. The model still needs a much smaller, genuinely
judgment-shaped rule for the cases the ladder can't cover (§3).

### Same ladder, other tools

- **`note_search`**: the deterministic fix is at the query layer, not a synonym
  table — switch Postgres from `plainto_tsquery` (strict AND, if that's what's
  in use) to trying `websearch_to_tsquery` or an explicit OR-fallback when the
  AND pass returns zero rows, before ever reaching the model. Cheap, no LLM
  involved, unit-testable with a fixture note and two phrasings of the same
  idea.
- **`calendar_query`**: deterministic fix only — an unparseable `period` falls
  through to the tool's own documented default (7 days) instead of erroring.
  One `if rng is None: rng = default_range()` change in `calendar.py::_query`.
- **`fact_recall`**: lower priority (facts are short, structured, few in
  number) — if it becomes a real gap, the fix is the same shape as notes:
  widen the substring match before giving up, not a model retry.

## 3. Asking versus doing

§6.2's boundary is about **write** actions ("Действие → только по нажатию").
Every case below is the model asking a question it could have answered by
calling one more **read** tool — free, silent, no card, nothing §6.2 gates.
Two concrete instances from the failures reported:

- Party ticket: it hedged with a question ("может, попробовать другие слова —
  Bilettix, Eventbrite?") instead of calling `mail_search` again with exactly
  those words.
- Colombia: it found one booking email and asked "это тот рейс, или билет в
  Колумбию — отдельная история?" instead of calling `mail_search` again (a
  second, broader query, or `mail_search` for the other leg/hotel) to find out
  for itself whether there was a second, related email — the user's own report
  says there were two emails (purchase confirmation + e-tickets); the agent
  stopped after finding the first kind.

Proposed persona rule (Russian, concrete enough to test as `min_calls` on the
relevant read tool in the golden set — see §4):

> Если для ответа не хватает одного факта, а есть ещё инструмент для чтения
> (mail_search, note_search, note_recent, calendar_query, fact_recall), который
> может его дать, — вызови этот инструмент, а не спрашивай у человека. Задавать
> вопрос вместо ещё одной попытки чтения — это лень, а не забота о человеке.
> Правило: если первый вызов инструмента для поиска вернул пусто или
> неоднозначно — прежде чем ответить вопросом или «не нашла», сделай минимум
> ещё одну попытку с другими словами, на другом языке или с более широким
> диапазоном дат/времени. Сдавайся только после этого — и в ответе скажи
> честно, что именно ты уже проверила («искала «билет», «ticket», «Bilettix»
> за эту субботу — не нашла»), а не просто «не нашла». Вопрос пользователю —
> только когда ответа физически нет ни в одном инструменте (например, он не
> сказал, о какой поездке речь, и подходящих писем/заметок несколько).

The last sentence matters as much as the first: this rule is not "never ask" —
it is "ask only after reading has genuinely been exhausted," which keeps FR-2's
and §8's honesty requirement ("nothing invented") intact. A rule that just says
"try harder" risks the model inventing a plausible-sounding answer instead of
asking; this one explicitly still allows — requires — an honest question when
the tools truly don't have the answer.

## 4. Golden scenarios (8–12), JSONL shape

A structural caveat first, because it changes what these scenarios can actually
prove: **`test_golden.py`'s `_check()` only inspects `r.tool_calls`, never
`r.reply` — the golden harness cannot verify what the final message says, only
which tools were called and with which exact-match arguments.** And the golden
scope (`UserScope(user_id=1, chat_id="111", ...)`) has no seeded row in
`get_oauth_token` for `tests/fakedb.py`'s `install()`, so every `mail_search` /
`calendar_query` call in the *current* golden run hits `google.NotConnected` —
"Google не подключён — /google" — before it ever reaches the query-building
logic proposed in §2. That means: scenarios that only check *tool choice* work
fine today (and already do — scenarios 61–65 pass at "not connected" too,
because the model still dutifully calls the tool per persona instruction).
Scenarios that need to check *query quality* or *retry behaviour with realistic
mail data* need two small, separate harness additions that this document flags
but does not build (scope says not to touch other files):
1. `tests/fakedb.py::install()` seeding a fake Google connection for the golden
   scope, with `sveta.core.google.search_mail` / `list_events` monkeypatched to
   serve fixture data (the same pattern already used for `fetch.get` in
   `test_golden_set`).
2. Optionally, an `expect_args_contains` key in `_check()` (substring, not
   exact match) — natural-language `query` strings will never be byte-identical
   across model runs, so today's exact-match `expect_args` can't check "the
   query mentions ticket and Saturday," only "the query is exactly X."

Scenarios below use only fields the harness already supports
(`expect_tools`, `expect_tools_any`, `forbid_tools`, `min_calls`), so they are
valid and runnable today (`test_golden_file_is_well_formed` will accept them);
each `note` says honestly what it can and cannot prove until the harness gets
the addition above.

```jsonl
{"message": "привет. найди мой билет на вечеринку в эту субботу", "expect_tools": ["mail_search"], "min_calls": {"mail_search": 2}, "forbid_tools": ["note_save"], "note": "the reported bug. min_calls=2 proves persistence today even against 'not connected'; proving the SECOND call is actually broader (language/sender/date) needs the fakedb+fixture addition above"}
{"message": "у меня были билеты в Колумбию — покажи все рейсы, не только один", "expect_tools": ["mail_search"], "min_calls": {"mail_search": 2}, "forbid_tools": ["calendar_create"], "note": "the Colombia case: the user said 'все рейсы', plural — one mail_search hit must not end the search when the phrasing implies more than one email exists"}
{"message": "найди счёт от подрядчика, что-то про дизайн, могло прийти на английском", "expect_tools": ["mail_search"], "min_calls": {"mail_search": 1}, "note": "user pre-empts the language ambiguity themselves; baseline sanity check that a single well-specified query still works, distinguishing 'the model is bad at search' from 'the model can't recover from ambiguity'"}
{"message": "когда встреча с юристом — на этой неделе или на следующей, не помню", "expect_tools": ["calendar_query"], "forbid_tools": ["note_save"], "note": "vague/compound period must fall through to calendar_query's own default window, not stop with 'не поняла период'"}
{"message": "что там с Артёмом по поводу встречи — уже назначили?", "expect_tools": ["calendar_query"], "min_calls": {"calendar_query": 1}, "note": "baseline: a name-filtered calendar_query call happens at all, not deferred to a clarifying question"}
{"message": "что я писал про запуск новой фичи в проде — не помню точных слов", "expect_tools_any": ["note_search", "note_recent"], "forbid_tools": ["note_save"], "note": "user explicitly signals a paraphrase mismatch risk; note_search alone with the literal phrase is expected to miss — a second, different read call (note_recent, or a reworded note_search) should follow"}
{"message": "кто у нас отвечает за бэкапы, что-то Костя вроде говорил", "expect_tools_any": ["fact_recall", "note_search"], "forbid_tools": ["note_save"], "note": "fact_recall's contract is substring-only per stage5.md; 'отвечает за бэкапы' vs a stored predicate like 'отвечает за инфру' may miss — the fallback to note_search matters here"}
{"message": "покажи, что писал по бюджету — ну ты знаешь, тот созвон на прошлой неделе", "expect_tools": ["note_search"], "min_calls": {"note_search": 1}, "forbid_tools": ["note_save"], "note": "the message is vague about WHO but concrete about WHAT ('бюджет') — a recoverable keyword exists; the rule from §3 says act on it rather than ask 'уточни, о ком речь'"}
{"message": "когда у меня самолёт в эту субботу, и что там по погоде в Ларнаке", "expect_tools_any": ["mail_search", "calendar_query"], "forbid_tools": ["note_save"], "note": "compound question; the mail/calendar half must be attempted even though weather has no tool — tests that a missing capability for HALF the ask doesn't suppress the half that IS answerable"}
{"message": "найди билет, я его точно кидал сюда в чат на прошлой неделе", "expect_tools_any": ["note_search", "mail_search"], "forbid_tools": ["note_save"], "note": "user names the wrong source on purpose (says 'chat' meaning 'forwarded to Svetochka') — tests that note_search is tried, since a forward often lands as a note (source=telegram_channel per the prior review's scenario #2), not just mail_search"}
```

## 5. Three changes, best effort-to-perceived-intelligence ratio

1. **Push the mail-search retry ladder into the tool, not the model** (§2).
   Touches: `sveta/tools/mail.py`, `sveta/core/google.py` (a
   `candidate_queries(query, date_phrase, tz)` pure function + `search_mail`
   trying them in order), `mail_search`'s schema gains a `date_phrase` field
   parsed the same way `reminder_create`'s `when` is. **Why this first:** it
   fixes both reported failures directly, it's deterministic (unit-testable
   with mocked HTTP in `test_mail.py`/`test_google.py`, zero model cost to
   verify), and it cannot regress from a prompt edit six weeks from now the way
   a persona-only fix can — the persistence is structural, not a request to the
   model's judgment. Effort: M.

2. **The persona persistence-and-honesty rule** (§3). Touches:
   `sveta/playbooks/persona.md`, one paragraph. **Why second, not first:** it's
   the cheapest possible change (no code, no schema) and it's the only fix that
   generalizes to tools #1 can't reach deterministically — `note_search`,
   `fact_recall`, cross-tool cases like the "forwarded to chat, not mail" golden
   scenario above. It's also the weakest guarantee of the three (a model can
   still choose not to follow it), which is exactly why #1 should carry the
   load for mail specifically rather than leaning on this alone. Effort: S.

3. **Seed a connected Google fixture in the golden harness** (§4). Touches:
   `tests/fakedb.py` (a fake `oauth_tokens` row for the golden scope) and
   `test_golden.py` (monkeypatch `google.search_mail`/`list_events` the way
   `fetch.get` is already monkeypatched). **Why third, and why it still belongs
   in the top three despite not touching user-facing behaviour at all:**
   without it, none of the ten scenarios above — or any future mail/calendar
   scenario — can ever prove more than "a tool got called." The party-ticket
   bug shipped and reached Dimitry specifically because the golden set had no
   way to catch "wrong query, technically a tool call happened." This is the
   change that stops the next version of this exact bug from reaching
   production silently again. Effort: S–M (mirrors an existing pattern in the
   same file, not a new one).

---

## Summary

1. The party-ticket bug is not a smart-vs-dumb model question: `mail.py::_search`
   passes the user's raw string straight to Gmail's `q` parameter with zero
   query construction — no synonyms, no language variants, no date, no sender
   list. The model called it once and got an honest "no matches."
2. The model already knew the right next moves (it *told the user* to try
   Bilettix/Eventbrite) — it just never turned that knowledge into a second
   tool call. The fix is making persistence structural, not teaching it facts
   it already has.
3. `calendar_query` is the one read tool whose contract already supports a
   sane default (7 days) — the bug there is narrower: an unparseable period
   errors instead of falling through to that default.
4. `note_search`'s Postgres full-text is AND-by-default; a paraphrase that
   doesn't share exact stems returns nothing with no fallback offered anywhere
   in the tool or the persona.
5. `fact_recall` is substring-only per its stage-5 design — narrower than
   `note_search`, with no broadening step either.
6. Recommended top fix: push a deterministic, testable query-expansion ladder
   into `mail_search`/`google.py` itself (language synonyms from the same
   domains `playbooks/__init__.py` already encodes, date-phrase parsing via
   the existing `timeparse` code path, a short named list of Israeli ticketing
   sender domains, a `has:attachment` + date-only last resort).
7. Second fix, cheap: one persona paragraph requiring at least one broadened
   retry before a "не нашла" or a clarifying question, and requiring the reply
   to say specifically what was tried — this is the backstop for tools #1
   can't reach (notes, facts, cross-tool cases).
8. §6.2's tap-to-confirm boundary is about writes; every hedge found here was
   a free read the model could have made instead of asking — the persona rule
   is scoped narrowly to that, not "never ask," so honesty (§8, "nothing
   invented") stays intact.
9. Structural finding about the test suite itself: `test_golden.py::_check()`
   only inspects `r.tool_calls`, never the reply text — it cannot currently
   verify anything about what Svetochka actually *said*, only which tools she
   called.
10. Second structural finding: the golden scope has no seeded Google
    connection, so every `mail_search`/`calendar_query` call in today's golden
    run hits "Google не подключён" before reaching any query logic — the
    party-ticket bug's exact failure mode was invisible to the existing 65
    scenarios by construction, not by bad luck.
11. Ten new golden scenarios are proposed in the file's JSONL shape, using only
    fields the harness already supports (`min_calls` for persistence,
    `expect_tools_any` for fallback-tool choice) — they're valid and runnable
    today, but several need the harness addition in point 10 to prove more
    than "a tool got called at all."
12. Recommended order: (1) the tool-side mail retry ladder — deterministic,
    fixes both reported bugs directly, unit-testable without spending on the
    model; (2) the persona persistence rule — cheapest, covers what the tool
    fix structurally can't; (3) the golden-harness fixture seeding — doesn't
    change behaviour but is what stops this exact class of bug from shipping
    invisibly again.
13. Both reported failures share one root cause, not two: a read tool with a
    free-text query and no structured way to broaden it, paired with a persona
    that says "be honest when you find nothing" but never says "try harder
    first." Fixing only the tool or only the persona leaves half the gap open.
14. This document does not propose a `note_edit`/`reminder_update`/delete-type
    fix — those are the previous review's territory
    (`2026-09-12-product-scenarios.md`); this one is scoped to search and
    read-tool persistence specifically, per the task.
15. Everything above is quoted from the current code, not inferred from the
    spec: `mail.py`, `google.py`, `agent.py`, `persona.md`,
    `playbooks/__init__.py`, `test_golden.py` and `fakedb.py` were all read to
    confirm the exact mechanism of each claim before writing it down.
