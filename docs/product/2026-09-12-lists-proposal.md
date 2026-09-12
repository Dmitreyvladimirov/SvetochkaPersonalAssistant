# Lists that know what day it is — proposal

Status: proposal, not accepted. Written 2026-09-12 against spec v0.10, revised the
same day after two corrections (history is not missing data; read paths should not
grow another narrow function).
Trigger: «Пришли список дел вчерашний» → «Вчерашнего списка не вижу.»

## Recommendation

Yesterday's list was never lost — `list_items.created_at` and `checked_at` have
been recording it since stage 2. What is missing is not storage and not a reader:
it is that **"Сегодня" is a name, not a day**, so nothing tells an item which day
it belongs to and nothing happens when a day ends. Fix that and almost everything
else follows from a query. Concretely: `lists.kind` (a column that already exists)
gains the value `day`, and only "Сегодня" gets it — Покупки, Большие покупки,
Книги stay exactly as they are, undated, never rolling, never expiring. Items on a
day list carry `due_on`, and once a day at 04:00 local (not midnight: he works past
it) the existing 15-minute cron runs one UPDATE that moves every still-open line to
the new day and adds the elapsed days to a `carried` counter. Carry-over is
automatic and silent — **zero taps in the morning**, which is the entire reason this
is not a second Todoist — and the cost is paid by ageing instead: from the second
day a line shows its age, and a line carried five days, or actively postponed three
times, moves itself into "Отложено" and is reported once with one «Вернуть» tap.
History needs no table and no reader: with `due_on` and `carried`, "what was on the
list on the 11th" is a `WHERE` clause, which is why this proposal adds **five
nullable columns, no new table, and no new tool**.

## What changes, in one table

| Today | After |
|---|---|
| "Сегодня" accumulates every ticked line ever; it only grows | A day's view is the day's work; yesterday's ticks stay in yesterday |
| Yesterday's unfinished lines sit unmarked among today's | Deliberately carried, and marked «2-й день» |
| No way to ask about a past day | A query over `due_on` / `carried`; no new function |
| "не сегодня" can only be expressed by ticking it as if done | Deferring and dropping are recorded as themselves |
| A line postponed nine times looks like one added this morning | It parks itself and Svetochka names the pattern |

## 1. What the correction changed

Two things were cut outright from the first draft, and they were the two heaviest:

- **A `day_logs` snapshot table.** Unnecessary. `created_at` and `checked_at`
  already date every line; adding `due_on` and `carried` makes the day an item
  belonged to a computable range rather than a stored blob. The one thing the
  snapshot bought that columns do not — perfect fidelity across deferrals — is
  named as a cost in §6 and deliberately not paid for.
- **A `when` argument on `list_show`.** Unnecessary if the read path becomes a
  query the model composes. "What was on the list yesterday", "what have I
  postponed three times", "what is still open from last week" and "what did I
  finish in August" are one capability, not four functions. §5 says which parts of
  this proposal depend on that direction and what the fallback is if it is not taken.

What survived unchanged: the day boundary, the roll, the ageing and parking
behaviour, the brief keyboard, the boundary with reminders, and every non-goal.

## 2. Is carry-over the same operation as `list_move`?

No, and the distinction is worth keeping.

- **A move changes which list a line is on.** A human does it, it says nothing
  about the line except that it was filed wrong, and `moved_from` records where it
  came from.
- **A carry changes which day a line is on, inside the same list.** A job does it,
  nobody decided anything, and it is the accumulation of these that is the signal.

Collapsing them would make every night look like a decision and destroy exactly the
signal the ageing is built on. But **parking is a move** — "Отложено" is an ordinary
list, `list_move` already moves things there, `moved_from` already records the
origin, and «Вернуть» is a move back. That reuse is taken; and it is `moved_from`
that identifies a line coming round for the second time, so no "how many laps"
counter is needed.

`moved_from` does, however, have no timestamp (`db.move_list_item` sets the pointer
only), so today a move is invisible to any question about the past. One column fixes
that — see §3.

## 3. The state that has to exist

> **Superseded in part by §11.** Under the query read path this shrinks to three
> columns plus an event table; `deferred` and `moved_at` stop being row state.

Five nullable columns on `list_items`. Each is justified against what
`created_at`, `checked_at` and `moved_from` already give.

```sql
-- lists.kind already exists (TEXT NOT NULL DEFAULT 'custom'); 'day' is a new value.

ALTER TABLE list_items
  ADD COLUMN due_on     DATE,          -- the day this line belongs to; NULL on custom lists
  ADD COLUMN carried    INTEGER NOT NULL DEFAULT 0,  -- days it has been carried forward
  ADD COLUMN deferred   INTEGER NOT NULL DEFAULT 0,  -- times a human postponed it
  ADD COLUMN dropped_at TIMESTAMPTZ,   -- "не буду" — decided against, which is not done
  ADD COLUMN moved_at   TIMESTAMPTZ;   -- when moved_from happened

CREATE INDEX IF NOT EXISTS list_items_day_idx ON list_items (user_id, list_id, due_on);
```

| Column | Why `created_at` / `checked_at` / `moved_from` do not cover it |
|---|---|
| `due_on` | `created_at` is when the line was written down. That stops being the day it belongs to the moment anything carries over or is scheduled ahead, and it can never be edited — it is an audit fact. Without a separate day there is no "tomorrow", no rollover and no expiry |
| `carried` | Derivable as `due_on - created_at::date` only while nothing has been deferred or parked. It is also what makes the past reconstructable: a line's contiguous life is `[due_on - carried, due_on]`, so no history table is needed |
| `deferred` | Nothing existing records that a **human** pushed a line. This is the difference between neglect and a decision, and it is the number behind "postponed nine times" |
| `dropped_at` | `checked_at` conflates "done" with "decided against". Without the split, every abandonment is recorded as work completed and the record lies in the direction that flatters him |
| `moved_at` | `moved_from` is a pointer with no time, so a parked line's earlier days are unanswerable. One timestamp makes a column that already exists actually usable by a query |

**`carried` counts days, not runs.** The roll is
`carried = carried + (today - due_on)`, so an outage that skips two nights still
produces the right number, and running the job twice in one day is a no-op because
nothing has `due_on < today` any more. Idempotency comes free from the predicate;
no lock, no marker row.

**Migration.** "Сегодня" → `kind='day'`; its open lines get `due_on = today`, its
ticked lines keep `due_on = checked_at::date`, `carried = 0` for everything — the
migration invents no history it cannot prove. Days before the deploy are answerable
only as far as `created_at` and `checked_at` allow, which for the trigger question
(«вчерашний») is enough from day one.

## 4. Requirements

Written in the style of SPEC §4, as behaviour rather than as functions. Next free id
is FR-66 (FR-64/65 are taken by spec 0.10).

### Changed

| ID | Requirement | P | Acceptance criterion |
|---|---|---|---|
| FR-47 | Named free-form lists | M | *(changed)* A line is arbitrary text with no required fields. "Сегодня" is `kind='day'` and its lines belong to a day; every other list is `kind='custom'`, undated, and untouched by rollover, ageing and expiry. Test: adding a line to "Покупки" and running the roll leaves it byte-identical |
| FR-50 | The day list in the brief and the review | M | *(changed)* Morning — the lines open for today, carried ones showing their age; evening — what is still open, said together with the fact that it carries over by itself, and no button unless something is about to park |

### New

| ID | Requirement | P | Acceptance criterion |
|---|---|---|---|
| FR-66 | A line belongs to a day | M | On the day list a line has a day of its own, defaulting to the day it was added; "сегодня" and "завтра" are different days inside one list, and no list is ever created per day. Test: `list_lists` never shows a dated list name |
| FR-67 | The day carries itself over | M | At the user's `day.rollover` time (default 04:00 local, ±15 min) every line still open on a past day becomes open today, and the number of days it has been carried grows by the days elapsed. Nothing is sent, nothing is asked, no tap is involved. Test: two open and one ticked line at 03:59 local → the same two open and nothing ticked in today's view at 04:20, with both rows still in the database; the job run twice changes nothing the second time |
| FR-68 | Any past day can be asked for | M | «пришли список дел вчерашний» is answered with the lines that were on the list that day and which of them were ticked — without a purpose-built reader for that question, and equally for «что я не закрыл на прошлой неделе» or «что я откладывал больше трёх раз». Test: the exact trigger phrase returns yesterday's lines with their ticks |
| FR-69 | The day boundary is computed by code, never by the model | M | Whatever composes a query about time is handed `today`, `yesterday` and `week_start` already resolved in the user's timezone with the 04:00 boundary applied (SPEC §6.1); it never derives a date from the words itself. Test: at 02:00 local, "сегодня" resolves to the previous calendar date |
| FR-70 | A line shows its age | S | A line carried two days or more renders as «☐ позвонить в банк · 4-й день»; a fresh or once-carried line renders plain. Test: the age appears on the third day and not before |
| FR-71 | Stale lines park themselves | M | A line carried `lists.stale_after` days (preference, default 5), or postponed three times, moves into "Отложено" during the roll — an ordinary list move, with its origin recorded. The morning brief says it in **one** line with a «Вернуть» tap that moves them back and clears the age. At most one such line a day, and none on a day when nothing parked |
| FR-72 | Postponing is not the same as ignoring | M | «перенеси на пятницу» sets that day, clears the carried count and increments the postponed count; the brief never shows a deliberately postponed line as "4-й день". A line coming round for the second time (its origin is "Отложено") is not parked silently: Svetochka says how many times it has come back and offers «Убрать» / «Это проект» |
| FR-73 | "Не буду" is recorded as itself | M | «не буду» / «убери» marks the line dropped, not ticked: it leaves the live view and the past shows it as abandoned, never as done. Test: a dropped line does not appear among finished work in any question about the day |
| FR-74 | Scheduling a line in chat | M | «перенеси звонок на пятницу», «не на этой неделе» (→ next Monday), «на завтра цветы» set the line's day, parsed by code inside the tool (§6.1); the reply repeats the date as the tool returned it. A line dated ahead is invisible in today's view and appears on its day |
| FR-75 | The brief's list is tappable | S | The morning brief renders the day's open lines as checkbox buttons on the brief message itself; a tap edits that message in place; the text carries one header line with the count instead of the lines. Test: the brief keyboard has one line button per open line plus 👍/👎 |
| FR-76 | Lists and reminders do not merge | M | A clock time makes a reminder («позвонить в банк в 11»); a day without a time makes a line («завтра позвонить в банк»); a person or a place makes a calendar event. A line that acquires a time is **offered** a reminder through `suggest`, never given one silently. Lines never recur — recurrence lives in `reminders.rrule`. Test: the three phrasings produce three different kinds of record |
| FR-77 | "Отложено" is read once a week | S | The Sunday evening review adds one line — «В «Отложено» 6 дел. Показать?» — with one tap. Never on other days, never when empty |
| FR-78 | A day the record cannot reconstruct is said, not guessed | M | Asked about a date the data cannot cover — before the migration, or before a line's last deferral — the answer says which part is unknown instead of presenting a partial day as complete. Test: a day preceding the deploy is answered as partial, naming the first fully recorded date |

## 5. What this depends on, and what it does not

**Depends on the query-tool direction being adopted:**

- FR-68 in the form written. It assumes the reasoning path can ask the user's own
  tables a question nobody anticipated. Without that direction, this proposal grows
  exactly one thing — a `when: string` argument on `list_show` — and FR-68 shrinks
  to yesterday and last week, with «что я откладывал больше трёх раз» unanswerable
  until someone writes that function too. That fallback is one property on one
  non-strict tool; it is the cheap consolation prize, not the plan.
- The claim of **zero new tools**, which holds either way, but for different reasons.

**Does not depend on it** — these are true whichever way the read path goes, because
they are state and scheduling, not reading:

- The `day` kind and the five columns (§3).
- The roll as a **cron step, not a model decision** (§6.1 keeps dates in code, and a
  job that runs at 04:00 whether or not anyone sent a message cannot be a tool call).
- Ageing, parking, «Вернуть», the weekly "Отложено" line.
- FR-74's write path: `list_add` and `list_move` each gain a `when` string parsed by
  code inside the tool. **Writes stay narrow and stay where the damage is
  containable.** A model composing an UPDATE over `list_items` is not in this
  proposal and should not be in any.
- The brief keyboard, and the boundary with reminders.

**Tool budget: 26 → 26 tools, 6 → 6 strict.** The only surface change is a `when`
property on `list_add` (strict) and `list_move` (not strict). Everything else is
callbacks, which cost no budget: «Вернуть», the brief's checkboxes, «Убрать / Это
проект», the Sunday tap.

**The one budget risk, named.** `list_add` is in `STRICT_TOOLS`, and the grammar
budget shrinks as the tool set grows (7 strict at 19 tools, 6 at 25, measured
2026-09-12). Adding a property to a strict schema grows the compiled grammar, the
alarm is a 400 "Schema is too complex" on the first agent call, and the golden run
that would catch it **cannot be run**: the Anthropic key hit its workspace spend cap
and regains access 2026-10-01 (CLAUDE.md). Ship that change with `list_add` already
swapped out of `STRICT_TOOLS` — it validates in Python like every other non-strict
tool and the count stays at 6 — or hold it until the cap lifts. Nothing else here
touches a strict schema.

Non-tool code touched: `db.list_items` (return `created_at`, `due_on`, `carried`,
`dropped_at` — today it selects five columns and the tool layer is blind to dates),
`db.unchecked_list_items` (filter by day), `db.move_list_item` (set `moved_at`),
`lists.render` (age suffix, date header), `jobs/brief.py` (the roll and the
keyboard), `core/timeparse.py` (day-granularity phrases and the 04:00 boundary).

## 6. What is being traded away

- **Deferring truncates history.** *(Withdrawn in §11 — an event log removes this
  cost, and under a query read path paying it was the wrong call.)* A line's
  reconstructable life is
  `[due_on - carried, due_on]`. Push a line from Tuesday to Friday and the record no
  longer knows it was on Tuesday's list. Asked about Tuesday, Svetochka answers the
  lines she can prove and says the day is partial (FR-78) rather than presenting it
  as whole. The exact alternative is an append-only `list_item_events` table; it is
  deliberately not built, because it is a table and a write path for a question —
  "what did a day I have since changed my mind about look like at the time" — that
  nobody has asked. If it turns out to matter, that table is the fix and it can be
  added without changing anything above it.
- **Automatic carry-over means the brief can show a dead line for five mornings.**
  That is the direct cost of a zero-tap morning; ageing is the compensation and
  `stale_after` is the dial. Five is chosen to span a working week and is a guess —
  there is no usage data on this behaviour, because the behaviour does not exist yet.
- **Weekends count as carried days.** A Friday line parks the following Wednesday.
  Not counting them requires a notion of a working day, which is a setting he would
  have to maintain — see non-goal 1.
- **`list_move` gets a third meaning** (move / reschedule / drop) to hold the tool
  count at 26. The model will sometimes reach for "tick it off" on «не буду»,
  recording an abandonment as work done. Mitigation is a persona line and a golden
  scenario; if it survives both, that is when a 27th tool earns its place, and not
  before.
- **Two counters exist** (`carried`, `deferred`). That is the most task-manager-shaped
  part of this. Neither is ever set by hand and he never sees more than one number at
  a time — «4-й день», «переносишь третий раз» — but they are state, and state drifts.

## 7. Dimitry's day

**Morning, 07:30.** The brief as it is now, except "Сегодня" is one header line and
the lines themselves are buttons under it:

```
Доброе утро.
Встречи:
11:00 созвон с Костей
Сегодня (4):
[☐ дожать оффер                    ]
[☐ позвонить в банк · 4-й день     ]
[☐ забрать посылку                 ]
[☐ ответить Оксане                 ]
2 дела ушли в «Отложено».  [Вернуть]
```

He does nothing. He taps things off during the day on that same message. The last
line appears only on a day when something actually parked; most days it is absent.

**Evening, 21:00.** «Не закрыто: 2 — перенесу на завтра сама.» No button, no
question, no ritual. If he wants to act he talks: «перенеси банк на пятницу», «с
посылкой не буду». If he says nothing, 04:00 handles it.

**Daily tap budget: zero.** Weekly: one, maybe, on the Sunday "Отложено" line. That
is the number this design optimises, and every mechanism above was chosen because it
does not raise it.

## 8. What this deliberately does not build

1. **No priorities, flags or stars.** A priority is a field he has to maintain and
   re-maintain. The day is the priority; ageing, not a field, is what says the list
   is too long.
2. **No clock times on lines, and no recurrence.** A time is a reminder; recurrence
   is `reminders.rrule`, which stays unused until something needs it. FR-76 keeps
   the two apart by test, not by convention.
3. **No sub-tasks, nesting, tags, projects or estimates.** FR-47 says a line is
   arbitrary text. It stays arbitrary text.
4. **No list per day.** One row in `lists`, dated lines.
5. **No morning triage.** No inbox to process, no «перенести всё на завтра?»
   confirmation, no review screen. Carry-over is automatic *because* the alternative
   is a chore, and a chore is what kills an assistant.
6. **No expiry outside the day list.** Покупки never ages. A shopping list that went
   stale would be a bug.
7. **No automatic deletion, ever.** Parking moves, dropping marks, carrying updates.
   The only hard delete stays the «Не туда» button, which exists for a mistake and
   not for a decision.
8. **No event-log table** (§6), **no snapshot table**, and no second place where the
   truth about a day might live.
9. **No stats, streaks or completion rates.** He would either game them or resent
   them, and neither makes a day go better.
10. **No sync to Notion, Todoist or Google Tasks.** Notes already mirror to Notion; a
    second integration is a second set of failure modes, and nothing in the trigger
    asked for one.
11. **No model-composed writes.** The read path may become general; the write path
    stays narrow, typed and gated per §6.2.

## 9. Order, and why

| # | Scope | Why this order | Est. |
|---|---|---|---|
| 1 | `day` kind, `due_on`, `carried`, the roll, `db.list_items` returning dates (FR-66, FR-67, FR-69) | Everything else reads this state; without it there is nothing to query. Touches no strict schema and no read tool, so it ships whatever happens to the query-tool direction and while the golden run is offline | ~5 h (est.) |
| 2 | Answering about past days (FR-68, FR-78) | The trigger case. Sequenced second only because it is the query direction's first real customer — if that direction is still being decided, this is the increment that waits, and the `list_show(when)` fallback is the hedge | ~3 h (est.) |
| 3 | Age, parking, the brief keyboard (FR-70, FR-71, FR-75) | What stops the list becoming a graveyard, but also the first thing that changes what he sees every morning — better after a week of real `carried` values exists to check the threshold of 5 against something | ~6 h (est.) |
| 4 | `when` on the write tools, dropped, the second lap (FR-72, FR-73, FR-74) | The only piece touching a strict schema, and its alarm is down until 2026-10-01 | ~5 h (est.) |
| 5 | Weekly "Отложено", second-lap buttons (FR-77, the tail of FR-72) | Lowest confidence: matters only if parking turns out to happen often, which nothing yet shows | ~3 h (est.) |

Estimates are estimates. Nothing here has been built or measured.

## 10. Open questions

Only the two where a wrong guess is expensive, because both change what is stored
and cannot be recovered from the data afterwards:

1. **Is «вчерашний список» the list, or the day?** This proposal answers about the
   day *list*. The other reading is a day journal — what was ticked, what notes were
   saved, which reminders fired, what was decided. That is a different product, and
   it is the one place where the cut snapshot table might come back, because a
   journal is a record of events and not a state you can reconstruct.
2. **Should «сделал X» for something never on the list be recorded?** If yes, the day
   list is a record of the day and question 1 tilts toward the journal. If no, it
   stays a list of intentions and off-list work is invisible. Same decision, asked
   from the other end.

Everything else is decided, not deferred: the 04:00 boundary, one "Отложено" list
rather than one per origin, no new tools, carry-over automatic. *(Question 1 is
largely closed in §11.5; "no new tables" became one event table — §11.2.)*

## 11. Reconciliation with the query read path

*Added after the architecture correction. §1–§10 were revised once already, so two
of the three points raised were answered in passing; the third — snapshot versus
event log — was not, and on it I was wrong.*

### 11.1 Does a past day need a dedicated reader? No.

Already resolved above: FR-68 is a behaviour requirement («пришли список дел
вчерашний» is answered, and so is «что я откладывал больше трёх раз»), FR-69 is
about who computes the date, and neither implies a tool. The `when` argument on
`list_show` survives only as the named fallback in §5 if the query direction is not
taken.

Nothing about rendering a past day needs code either, and there is a pleasant
accident in how the bot already works: the checkbox keyboard is built from
`ctx.render_list_id`, which only a **tool** sets. A day reconstructed by a query
therefore cannot come back tappable — and a tappable checkbox on yesterday would be
a lie, since tapping it would change today's state. The property we wanted comes
free from the architecture rather than from a rule someone has to remember.

### 11.2 Snapshot versus event log: the event log wins

I cut `day_logs` for the wrong reason. My argument was "nobody has asked for that
question" — which was only true under the old architecture, where each new question
costs a function and the set of questions is therefore the set someone anticipated.
If the read path becomes general, **the cost of a question drops to zero and the
only limit on what can be asked is what was recorded.** That inverts the calculus
completely, and the answer is not "no table", it is "a different table".

| | JSONB snapshot per day | Append-only event log |
|---|---|---|
| Storage | ~365 rows/user/year | ~1,500 rows/user/year (see below) — both are rounding errors; this axis decides nothing |
| Reconstructing a day | O(1) read, no logic | An as-of fold — the most error-prone SQL shape there is, and a wrong one yields a *plausible* wrong day, worse than "I don't know". Fixable, see 11.3 |
| After an outage | Broken: the roll did not run, so the day is missing or written later from a state that is no longer that day's | Untouched: events are written when the human acts, not by the cron. Nothing about the record depends on the scheduler having been up |
| Usable by a reasoning agent | Barely. One renderer understands the blob; "how many times did this line come back" means scanning every day's JSON and matching by *text*, because the blob has no line identity | Fully. Every row carries `list_item_id`, so identity survives and every such question is a `GROUP BY` |

Three axes to one, and the one is fixable. The event log wins and the snapshot
should not be built.

**What keeps it small: only discontinuities are events.** Carry-over is
deterministic — `due_on` and `carried` describe a contiguous run of days exactly —
so the nightly roll writes **no events at all**. Writing one row per open line per
night would be ~1,800 rows a year carrying zero information. Events are written only
where a human does something the state cannot replay: added, checked, unchecked,
dropped, restored, deferred (day A → day B, non-contiguous), moved or parked
(list A → list B). That is a handful a day, and it makes the table a record of
decisions, which is the interesting data anyway.

```sql
CREATE TABLE IF NOT EXISTS list_item_events (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    list_item_id INTEGER NOT NULL REFERENCES list_items(id) ON DELETE CASCADE,
    list_id      INTEGER NOT NULL REFERENCES lists(id),
    kind         TEXT NOT NULL,   -- added|checked|unchecked|dropped|restored|deferred|moved
    from_day     DATE, to_day  DATE,
    from_list_id INTEGER, to_list_id INTEGER,
    at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS list_item_events_item_idx ON list_item_events (user_id, list_item_id, at);
```

`ON DELETE CASCADE` is deliberate: «Не туда» means "this was never meant to exist",
so its history going with it is the consistent outcome, not a leak.

### 11.3 The fold is a view, not something the agent writes

The one axis where the snapshot wins is that reconstructing needs no logic. Neutralise
it in code, once: a view that flattens events plus current state into one row per
line per day it was live.

```sql
CREATE OR REPLACE VIEW list_item_days AS  -- (user_id, list_id, list_item_id, day, state, carried)
```

The agent queries `list_item_days` — a flat table with a `day` column, obvious to
anyone — and never composes an as-of fold itself. The view goes in the table
allowlist alongside the base tables. If expanding it over a year ever gets slow, it
becomes a materialised view refreshed by the same 04:00 job: a one-line change, no
schema move, nothing above it affected.

This is the general shape I would argue for beyond lists: **a query read path should
be given views that make the hard questions flat, not left to rediscover window
functions per question.** Every time a fold turns out to be subtle, that is a view,
not a tool.

### 11.4 What this does to §3 and §6

State shrinks from five columns to three, because the event log makes two of them
redundant:

| Column | Verdict |
|---|---|
| `due_on` | **Keep.** Not derivable; the scheduler and every live query read it |
| `carried` | **Keep**, as deliberate denormalisation. It is rendered on every line of every list display, and it means the common case — a line nobody has deferred — is answerable without touching the event table at all |
| `dropped_at` | **Keep.** A state, not a count; the live view filters on it every time |
| `deferred` | **Drop** — I disagree with keeping this one. It is read once a day by the parking rule over a handful of rows; a `COUNT` over events is cheap enough, and one fewer denormalised counter is one fewer thing that can drift. «переносишь третий раз» comes from the events |
| `moved_at` | **Drop.** It only existed to give `moved_from` a timestamp; the `moved` event has one |

And in §6, the **"deferring truncates history" cost is withdrawn** — that was the
price of having no table, and it is no longer being paid. FR-78 stays but narrows to
days before the migration, which are genuinely unknowable. A smaller cost replaces
it: `carried` is denormalised, so it can drift from the events; the events are
authoritative and it is rebuildable from them.

Non-goal 8 in §8 ("no event-log table") is **withdrawn**. "No snapshot table" stands,
and one new one is added: **no second renderer** — a past day is read through the
same view as every other historical question, not through a bespoke path.

### 11.5 Does the day-journal reading come free? Mostly, and it does not need this table

Open question 1 largely dissolves, but not because of the event log. Under a query
read path, "what happened on the 11th" is a UNION over timestamps that already
exist — `notes.created_at`, `reminders.sent_at`, `inbox_items`, `digests.for_date` —
composed on demand, needing no new storage at all. What the event log adds is only
the *list* part of that journal, and it adds precision: transitions, not just
creation.

So the wide reading is a query and an allowlist, not a product decision. What does
**not** come free is capture: «сделал X» for something never written down is
unanswerable no matter how good the read path is, because nothing recorded it. Open
question 2 therefore stands unchanged and is now the only one of the two that
matters — and it is a behaviour question (should she add-and-tick?), not a storage one.

### 11.6 Budget, and one thing that is not mine to decide

Collapsing the six pure reads (`note_search`, `note_recent`, `fact_recall`,
`memory_show`, `list_show`, `reminder_list`) into one query tool takes the registry
from 26 to 21, which moves the strict-schema budget in the favourable direction —
the measurements are 7 strict at 19 tools and 6 at 25 (2026-09-12), so a smaller set
should buy room back. **How much is unmeasured**, and the golden run that measures
it is down until 2026-10-01, so the `list_add` risk in §5 should still be treated as
live rather than assumed away. If the read tools collapse first, `list_add` can stay
strict and the risk disappears on its own — worth sequencing in that order for that
reason alone.

One flag, and then I will stop: a read path where the model composes queries over
the user's own tables is a security design, not a product one. Injection from a
fetched page or an email can steer a query, and the interesting failure is not the
query but the reply — data read under the user's own scope and then rendered into a
message. The controls listed (read-only, user_id forced, allowlist, timeout, LIMIT)
sound right to me and I have no standing to sign off on them; that belongs to
**security-auditor** before it ships, not to this document.

### 11.7 What changes in the order (§9)

One change, and it is the important one: **the event table moves into increment 1**,
alongside `due_on` and `carried`. Events not written are events lost — unlike a view
or a reader, this part cannot be added later and backfilled, and every day it is not
there is a day of decisions that will never be answerable. Increment 2 (answering
about past days) then becomes the view plus the allowlist entry, and no longer waits
on anything but the query direction itself.

Revised estimates for the two affected increments: increment 1 ~7 h (est., was ~5 —
the event writes attach to the existing write paths), increment 2 ~4 h (est., was
~3 — the view replaces the reader). Both still estimates; nothing is built.

---

*A parallel research note on how other products handle carry-over and expiry
(`docs/research/2026-09-12-todo-lists.md`) was not available when this was written.
Most likely to move on reading it: the `stale_after` default of 5, and whether a
single "Отложено" is the right destination — one constant and one list, both cheap
to change. The day boundary, the state model (§3 as amended by §11.4) and the write
path are not expected to move.*
