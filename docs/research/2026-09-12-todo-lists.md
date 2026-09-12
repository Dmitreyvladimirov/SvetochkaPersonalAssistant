# To-do lists and the day problem

Research, 2026-09-12. Triggered by a live failure; the deliverable is a
recommendation a product manager can act on, not code.

The failure:

```
Дима:      Пришли список дел вчерашний
Светочка:  Вчерашнего списка не вижу. Есть «Сегодня», «Покупки» и «Большие покупки».
           Какой прислать?
```

The answer was correct. There is no yesterday in the system to send.

---

## 0. What the current model actually is

Read before forming an opinion (`sveta/tools/lists.py`, `sveta/core/db.py`,
`sveta/jobs/brief.py`, `SPEC.md` §4.3, §6.1, §6.2, §7.3):

- `lists` has `UNIQUE (user_id, name)`. **"Сегодня" is one row, forever.** It is not
  a day; it is a string that happens to mean one.
- `list_items` has `text`, `position`, `checked_at`, `moved_from`, `created_at`.
  There is **no date on a line** and no notion of which day a line belongs to.
- `db.list_items()` returns everything, checked and unchecked, ordered by
  `position`. `lists.render()` prints ☑ for checked. **Nothing ever leaves.**
  "Сегодня" grows monotonically until the 30-button cap
  (`lists.MAX_BUTTON_LINES`) truncates the keyboard.
- `db.unchecked_list_items(user_id, "Сегодня")` — the brief and the evening review
  read open lines from a **hardcoded list name** (FR-50).
- The only age signal that exists today is `created_at` (and `checked_at`), and
  nothing reads either.

So three distinct things are broken, and they are worth separating because they
have different fixes:

1. **No history.** "Вчерашний список" is unanswerable — but the data to answer it
   is almost there (`created_at`, `checked_at`), unused.
2. **No expiry and no age.** A line written on 3 September is indistinguishable
   from one written this morning. Checked lines never stop taking a button row.
3. **No movement.** There is no way to say "перенеси на завтра". `list_move` moves
   between *lists*, which is a different axis.

---

## 1. How real products solve the day problem

Concretely: what happens to an unfinished task at midnight?

| Product | Is "today" a view or a container? | At midnight | Carry-over | "Carried N times" signal | What the user must do each morning |
|---|---|---|---|---|---|
| **Things 3** | **View.** The item holds a start date; Today is everything whose date has arrived | Nothing runs | Implicit — the task never left Today | **None.** Things deliberately shows no overdue badge on start dates | Nothing required. "If you aren't able to finish everything, you can easily reschedule the remaining items for another day" — manual, optional |
| **Apple Reminders** | **View.** Today is a *smart list*, literally a saved query: "due today **and** overdue" | Nothing runs | Implicit — overdue items sort to the top of the same view | Only "overdue" as a binary, shown red. No count. Apple confirmed the relative date range always includes today + everything past due, and calls this a limitation, not a bug | Nothing required |
| **Todoist** | **View.** Today is a filter over due dates, with an Overdue section above it | Nothing runs | Implicit — the task goes red and lands in **Overdue** | Binary red, no count. A bulk **Reschedule** action sits next to the Overdue header | Nothing required; the bulk Reschedule exists because most people do want to sweep |
| **TickTick** | **View.** Today is a built-in smart list | Nothing runs | Implicit — overdue tasks appear in the Today smart list | Binary. A **"Plan Your Day"** flow walks overdue + today's tasks one at a time | Optional ritual ("Plan Your Day") |
| **Amazing Marvin** | **View**, with dates on the item | Nothing runs | Implicit | **Yes, explicitly — the strongest in the field.** "Procrastination Count": the *first* date a task was ever scheduled is recorded, and one `!` is prepended per day of delay (display modes: one-per-day, numeric `!6`, tiered `!`/`!!`/`!!!` at 1-10/11-20/20+). Orange, turning **red after 3 days**. Only completing the task clears it — unscheduling does not reset the count. Plus a separate "Stale" threshold (days since creation) and a "Most Procrastinated Tasks" screen grouped by how long each has been put off | Nothing required; the marks do the nagging |
| **Microsoft To Do** | **Container.** My Day is a real, separate bucket you put things into | **My Day is wiped every night** | **Refused.** Unfinished items stay in their real lists and come back as *suggestions*, not as carried tasks | The suggestion pane is the signal — an item that keeps reappearing there is visibly stale | Open Suggestions and re-pick the day's items. Explicit, every morning |
| **Sunsama** | **Container.** A day is a real list that tasks are placed on and moved between | **"All tasks automatically roll over to the next day's task list if they are left incomplete at midnight"** | Automatic, plus an optional prompt at planning time to choose | **Auto-archive after N consecutive rollovers** (threshold configurable, can be disabled). A pink badge on the archive icon + a message saying how many moved | Daily Planning ritual: review what carried over, estimate each task, drag onto the calendar. Evening: Daily Shutdown reviews what did and didn't get done |
| **Bullet Journal (paper)** | **Container**, unavoidably — a day is a physical page | Nothing runs — paper | **Refused, by construction.** At month's end you walk every unfinished task and either rewrite it into the new month (mark the old `>`), push it to the Future Log (`<`), or strike it out | Rewriting *is* the count: a task rewritten three months running is visibly a task you have rewritten three times | The migration ritual. Carroll's argument is that the friction is the feature — if a task is not worth the seconds it takes to copy forward, that is the signal to drop it |

### 1.1. View or container — the live decision

This is the column that decides the work, so it is worth stating flatly:
**every product that can answer "what was yesterday?" treats today as a view.**
The container products cannot answer it at all without extra machinery — Microsoft
To Do wipes My Day and keeps no record of what was on it; Sunsama has to write a
rollover into the next day's list and archive after N of them, and its archive
panel exists *because* the container shape loses the history that a view shape
gets for free.

Svetochka is currently a **container** ("Сегодня" is a bucket you put things in)
with none of the machinery that makes containers survivable. That is the whole
bug in one sentence.

And the view option is nearly free here, which is the part worth noticing:
`list_items` already has `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` and
`checked_at TIMESTAMPTZ`. **Every line ever added to "Сегодня" is still in the
table, with the day it arrived and the moment it was ticked.** Nothing was lost.
What is missing is three things, none of them data:

1. `db.list_items()` does not even `SELECT created_at` — the column is fetched
   nowhere and read by nothing.
2. No query filters by day.
3. "Сегодня" is a name, so there is nothing for a day filter to mean.

Yesterday's list is an unasked question, not lost data. That changes the cost of
the recommendation in §5 from "add a feature" to "start reading columns we
already write" — see §5.2 for how far `created_at` alone gets us and where it
stops being enough.

### 1.2. What do users do with history once it exists?

Asked because a feature nobody opens after the first week is worth knowing about
before building it. **The honest answer: I found no usage data.** What exists is
vendor documentation and practitioner writing, and it is weak:

- Todoist ships an activity log, a completed-tasks view, a Productivity view and
  Karma, and positions them for the GTD **weekly review** — "identify patterns,
  adjust estimations". That is a vendor describing an intended use, not evidence
  of one.
- The recurring *user-voiced* reasons in issue trackers and reviews are narrower
  and more believable than the vendor framing: estimating a recurring piece of
  work from how long the last one took, and retrieving a note attached to a task
  that has since been completed. Both are **lookups**, not browsing.
- The counter-pattern is equally common: most apps hide completed tasks by
  default and treat them as clutter.

The useful asymmetry: **storing** history costs nothing here (it is already
stored), while **building a surface to browse it** is the part nobody opens. So
the conclusion is not "build a history feature" — it is "be able to answer a
history question when asked, and do not build a place to go and look". Дима
asked for yesterday's list unprompted during ordinary use, which is worth more
as evidence than anything in this bullet list; but he asked *a question*, he did
not ask for an archive.

**What the table actually says.** Two families, not seven designs:

- **Sticky** (Things, Apple, Todoist, TickTick, Marvin). Nothing moves at midnight.
  The task carries its own date; "today" is a *query*, not a container. Carry-over
  is free and invisible; the cost is a Today list that silently fills with debris,
  which is exactly why Todoist grew a bulk Reschedule button and Marvin grew a
  procrastination counter.
- **Ritual** (Microsoft To Do, Sunsama, Bullet Journal). The day is a container
  that gets rebuilt. Nothing is lost — the task falls back to a pool — but it must
  be re-chosen to be on today's plan. The cost is a daily obligation.

Three observations that matter more than the family split:

1. **Nobody deletes.** Not one product in this set destroys an unfinished task at a
   day boundary. Sunsama's auto-archive is the closest, and it archives to a
   visible panel with a badge and a count, not to nothing.
2. **Everyone keeps the original date.** Sticky products can show "overdue" only
   because the item remembers the day it was *for*. Marvin can show a count only
   because it remembers the **first** date it was ever scheduled — not the latest.
   This is the single cheapest structural decision on this list.
3. **The count is rare.** Only Marvin surfaces "carried N times", and Marvin is a
   niche, heavily-configurable app aimed at people with ADHD. The mainstream apps
   show a binary red. That is evidence that the count is *not* obviously the right
   default, not evidence that it works.

---

## 2. Task rot

**What is actually documented, and what is just folklore.** This section is thinner
than it looks like it should be, and I would rather say so than dress it up.

**Documented (product behavior, verifiable):**

- Sunsama ships an auto-archive threshold on repeated rollovers. Its existence is
  an admission by a product built entirely around automatic carry-over that
  automatic carry-over produces a pile that has to be swept. That is the most
  useful single data point here, because it is a *revealed* design decision rather
  than an opinion.
- Marvin ships a procrastination count, a staleness threshold (days since
  creation, configurable), a "Most Procrastinated Tasks" screen, and a
  "Procrastination Wizard". A vendor building four features against one problem is
  evidence the problem is real; it is not evidence any of the four works.
- Microsoft To Do refuses carry-over outright and re-offers unfinished items as
  suggestions. Two large vendors landing on opposite defaults (Microsoft: wipe and
  re-offer; Sunsama: roll over automatically) means there is **no settled answer**.

**Research (weak, and old):**

- Bellotti et al., *What a to-do: studies of task management towards the design of
  a personal task list manager*, CHI 2004. Qualitative. The relevant findings:
  only a minority of to-do reminders ever appear in a list at all; a list serves
  partly to convey *the amount* of work rather than to be executed; and "to-dos
  don't all get done — people procrastinate about some and deem some of lower
  importance". That last point is the useful one: **a permanently-unfinished task
  is a normal state, not an error state.** A design that treats every uncrossed
  line as a failure is fighting the actual behavior.
- I could not find anything empirical comparing automatic carry-over to a daily
  reset on completion rates. I looked. If such a study exists, I did not find it,
  and I am not going to infer a number. **Any claim in either direction is
  practitioner opinion.**

**Task bankruptcy** — declaring the list dead and starting over — is a real and
widely-described practice (Todoist's own blog recommends it; Fast Company, Clive
Thompson in Forge, and others describe doing it), borrowed from email bankruptcy.
The consistent reported mechanism is avoidance: people stop opening the app
because they know what is waiting. The consistent reported claim is that "the
important stuff bubbles back up". **There is no evidence for that claim beyond
self-report**, and it is exactly the kind of claim that survives because the
people for whom it failed do not write blog posts. Treat it as a valid *escape
hatch to offer*, not as a strategy to schedule.

**The one defensible synthesis:** the failure mode of automatic carry-over is not
that tasks are lost — it is that the list stops being read. Whatever Svetochka
does, the test is not "did the task survive" but "does Дима still open the
list at the end of the week". That is measurable here (does he tap the checkboxes;
does he 👍 the brief) and it is the metric worth watching.

---

## 3. What the chat shape changes

Svetochka is not an app with a screen you open. This changes more than it looks.

**Patterns from §1 that cannot work here:**

- **A Today *view* that you scroll to.** There is no view. A list message is a
  message; it scrolls away within an hour of normal chat. Every product above
  assumes a persistent surface that the user *returns to*; Svetochka has none.
  Every "nothing required each morning" cell in the table silently assumes the
  user will open the app. Дима will not open anything.
- **Bulk reschedule by multi-select.** Todoist's Overdue-header sweep needs
  selection UI. The nearest chat equivalent is one button that does the whole
  sweep, which is a much blunter instrument.
- **Rewriting as friction (BuJo).** In chat, retyping a task is *cheap and normal*
  — it is how everything else already works. The friction that makes migration
  meaningful on paper does not exist here. You cannot import the ritual by
  importing its mechanics; you would have to invent friction artificially, which
  is a bad idea.
- **A per-line "reschedule" control.** Telegram gives one inline keyboard, capped
  at 100 buttons and already at 30 lines here. A date picker per line is not
  available; a second button per line halves the usable list length.

**Patterns that work *better* here than in an app:**

- **The ritual family, but pushed.** Microsoft To Do's and Sunsama's problem is
  that the ritual only happens if the user opens the app. Svetochka already sends
  a morning brief and an evening review, per user, in the user's timezone, once a
  day, idempotently (`sveta/jobs/brief.py`, `digests` UNIQUE). **The daily ritual
  is already built and already delivered.** This is the single biggest asset in
  this design and it is currently spending itself on a flat list of ☐ lines.
- **One question a day.** A chat can ask about *one* thing and get an answer.
  Sunsama's planning ritual asks about everything at once because it has a screen;
  Svetochka should ask about the worst single item. BuJo's migration, compressed
  to one line per day, is a genuinely better fit for chat than for paper.
- **Taps are free and unambiguous.** A checkbox tap already works and already
  edits the message in place (`_toggle_list_line`). Buttons attached to the
  *evening review* are the highest-value real estate in the product: it is the one
  message a day that arrives on its own and is read while it is fresh.
- **Recency is the interface.** In chat, the most recent message wins. A list is
  "current" if it was sent recently, not if it is filtered correctly. This argues
  for regenerating the day's list as a *new message* each morning rather than
  editing an old one — and for `edit_reply_markup` staying what it is: an in-place
  toggle on a message the user is currently looking at.

**One thing to verify before relying on it:** the Telegram Bot API's documented
48-hour cap is on *deleting* a message; editing a bot's own message appears to be
uncapped, but sources conflict. Nothing below depends on editing a message older
than a day — deliberately — but if a design ever does, test it first.

---

## 4. Recurring tasks

"Каждый понедельник" is a third thing, and the products agree on where the line
sits more than they agree on anything else in this document.

**What the products do:**

- **Todoist**: completing a recurring task generates the next occurrence, and
  **only future dates are scheduled** — a recurring task you ignored for three
  weeks does not produce three overdue copies. `every` counts from the original
  date; `every!` counts from the actual completion date. "Postpone" moves to the
  next occurrence; "reschedule" to a chosen date; neither touches the rule.
- **Apple Reminders / Things / TickTick**: same shape — one live instance at a
  time, the rule regenerates it.

**The boundary in one sentence:** a **reminder interrupts you at a moment**; a
**to-do waits for you in a place**. "Каждый понедельник" is ambiguous precisely
because it names a moment but usually means a place — the user wants it to *be in
Monday's list*, not to buzz at 09:00 on Monday.

**For Svetochka:**

- The `rrule` column on `reminders` is unused and should stay unused for now.
  Recurring *reminders* are a well-understood feature that nobody has asked for.
- Once a list line carries a date (§5), a recurring line is a small addition —
  an `rrule` on `list_items` and a tick that materialises the next `due_on` —
  **and it should not be built until Дима asks for it twice.** It cannot be built
  well before dates exist, and it is not what broke today.
- The **non-pile-up rule is the part to copy**: regenerate at most one live
  instance. A recurring line that produces four unfinished Mondays is the
  graveyard mechanism in its purest form.

---

## 5. Recommendation

Shaped to the constraints: **no new tools** (26 registered, 6 strict, the API
answers 400 "Schema is too complex" past the budget), dates parsed by code, a card
for anything with an outside effect, the 15-minute cron already in place,
everything scoped per user.

### 5.1. Put the date on the line, not on the list

Two columns on `list_items`:

- `due_on DATE` — the day this line is *for*. NULL means undated (a shopping item
  is not a to-do; "Покупки" lines stay NULL and nothing changes for them).
- `first_due_on DATE` — the **first** day it was ever for. Never updated after
  insert. This is Marvin's "first schedule date", and it is the only reason a
  carry count can ever be computed. Copying the *latest* date instead is the
  mistake that makes age unrecoverable.

Everything else follows from these two columns:

- **"Сегодня"** stops being a name and becomes a query: open lines with
  `due_on <= today`. Carry-over is implicit and nothing runs at midnight, so
  **no scheduled job can ever lose a line** (the sticky family's one real
  advantage, and it matters more here than in an app because there is no screen
  on which to notice a loss).
- **"Вчерашний список"** becomes answerable, including what was ticked off:
  `due_on = yesterday`, checked and unchecked. That is the reported failure,
  closed.
- **Age is free**: `today - first_due_on` is the carry count.
- **The keyboard stops growing.** A checked line stays visible for the rest of
  *its own* day — seeing what you did is the point of the evening review — and
  drops off the keyboard the next day while staying in the database. The 30-button
  cap stops being a live concern.

Migration: backfill `due_on = created_at::date` for lines in a list named
"Сегодня", NULL elsewhere; `first_due_on = due_on`. `CREATE TABLE IF NOT EXISTS`
+ `ADD COLUMN IF NOT EXISTS`, the existing pattern — no Alembic.

Keep the physical "Сегодня" row as the default bucket for dated lines so
`find_list`, `list_move` and every existing test keep working. Membership of
"today" is decided by `due_on`, not by which list a line sits in — which also
means a "Покупки" line the user *does* date ("купи молоко сегодня") correctly
shows up in the brief without being moved.

### 5.2. Reshape three tools, add none

`sveta/core/timeparse.py` already parses "вчера", "завтра", "послезавтра",
weekdays and explicit dates in Russian, by code. A date-only wrapper over
`_day()` is small. So:

- `list_add(list_name, items, **when**)` — `when` is a free-text Russian phrase
  parsed by the tool; empty means undated. "добавь в сегодня позвонить в банк" →
  `when="сегодня"`. `list_add` is already strict; adding an optional string does
  not change the strict-tool count.
- `list_show(list_name, as_text, **when**)` — `when="вчера"` answers the failing
  message. Empty keeps today's behavior.
- `list_move(item, from_list, to_list, **when**)` — moving is already the
  "change where this line lives" tool; a day is another axis of where. `to_list`
  empty + `when="завтра"` = "перенеси на завтра" without touching the list.
  This is the reshape that avoids a fifth list tool.

That is **zero new tools and three added optional string arguments**. `list_check`
is untouched.

Cheaper fallback if even that is too much: `created_at`/`checked_at` already allow
"what was open yesterday" to be computed with **no schema change at all** —
`created_at < end_of_yesterday AND (checked_at IS NULL OR checked_at >=
start_of_yesterday)`. It answers the literal failing question and nothing else: it
cannot express "на завтра", cannot distinguish a line added Monday *for* Friday,
and gives a carry count that is really an age-since-typing. Worth knowing it
exists; not worth shipping instead.

### 5.3. Spend the daily ritual, which is already built

`brief.py` currently reads `unchecked_list_items(user_id, "Сегодня")` and prints
flat ☐ lines. With dates it should instead read open lines with `due_on <= today`
across lists, and:

- **Morning brief**: today's lines, with an age marker on anything carried. Follow
  the mainstream, not Marvin: a **binary** marker for a line older than a
  threshold, not a per-day count. `☐ позвонить в банк · 4-й день` on the lines
  that earned it, nothing on the rest. Marvin's escalating `!!!` is the most
  interesting design in §1 and also the one with the least evidence and the worst
  fit for a ten-line message.
- **Evening review**: the unfinished lines, plus **one row of buttons** —
  `Перенести всё на завтра` / `Разобрать`. Moving a line within Svetochka's own
  database is not an outside effect (§6.2 puts list lines in the "silently, with
  «Не туда»" row), so these are plain UI, not confirmation cards. This gives
  Sunsama's shutdown ritual — the feature its users most consistently credit —
  for the cost of one keyboard row on a message that is already being sent.
- **The one question a day.** When a line crosses the age threshold, the evening
  review asks about **the single oldest one**, and only that one:
  `«Позвонить в банк» висит 6 дней. Оставить / На конкретный день / Выбросить.`
  This is the BuJo migration compressed to a scale a chat can carry. It is also
  the only place in this design where anything gets deleted, and it is deleted by
  a tap, never by a job.

Thresholds are preferences (`preferences` already exists, §6.4's pattern): the age
marker threshold and whether the nightly question is asked at all. Default them,
do not ask.

### 5.4. What to measure

Not "how many lines were completed". Measure whether the surface stays alive:
checkbox taps per week, 👍/👎 on the evening review, and the ratio of lines that
are ever ticked to lines that are ever added. If the third number falls over a
month, the list has become a graveyard regardless of what the other two say.

---

## 6. The architecture fork: an agent that queries vs. a catalogue of functions

Raised by Дима pushing back on the architecture: we answer each new question by
writing another narrow function, there are 26, and "пришли вчерашний список"
failed only because no function existed for that question. The direction he wants
is an agent that can look at its own data and reason, with narrow functions as the
exception.

This is a real fork, it is worth taking seriously, and one part of his argument is
simply correct — stated up front because the rest of this section is qualifications:

**A query-capable agent would probably have answered the failing message.**
`created_at` is in the table. A model with read access would have written
something like `SELECT text, checked_at FROM list_items WHERE list_id = … AND
created_at::date = CURRENT_DATE - 1` and returned a plausible, probably correct
answer, with no code written by anyone. That is the strongest point in the
argument and it should not be talked around.

### 6.1. What is actually known about giving a model query access

The benchmark picture, in order of relevance:

| Benchmark | Shape | Result |
|---|---|---|
| **Spider 1.0** | Small, clean, academic schemas | GPT-4o **86.6%**; o1-preview **91.2%** |
| **BIRD** | Larger, dirtier, with "external knowledge" | Best systems **~76-82%** (XiYan-SQL 75.63%, top listed 81.95%); **human experts 92.96%** |
| **Spider 2.0** | Real enterprise: 632 tasks, databases often **1000+ columns**, BigQuery/Snowflake | GPT-4o **10.1%**; o1-preview **17.0%**; best reported **~23.4-23.8%** |

The 86.6% → 10.1% collapse for the *same model* between Spider 1.0 and Spider 2.0
is the number most often quoted as proof that text-to-SQL does not work. It is
also the number most likely to mislead us here, and the fair reading matters:

**Svetochka is a Spider 1.0 database, not a Spider 2.0 one.** Twenty tables, all
designed in-house in the last two weeks, documented in `SPEC.md` §7.3, with
consistent naming (`user_id` everywhere, `*_at` for timestamps), one user's rows,
no dbt lineage, no thousand-column warehouse tables, no disputed business
definitions of "revenue". MotherDuck's analysis of BIRD points the same way: with
clean data models and realistic grading, models reached **94-95% using only the
schema** — and adding column comments moved the average by **+1.1pp on train and
+0.2pp on test**, helping the hardest schemas by ~9pp while *hurting* intuitive
ones by ~3pp. Their conclusion — "good data modeling *is* the semantic layer" —
argues that a clean small schema is most of what a model needs.

So: **the case for query access against this specific database is much stronger
than the enterprise headline numbers suggest.** I went looking for evidence that
would let me dismiss the idea and did not find it.

One caveat that cuts across all of the above: the benchmarks themselves are soft.
The FLEX work found BIRD's execution accuracy agrees with human expert judgment
only **62%** of the time, and re-grading shifted relative performance by −3% to
+31% and ranks by up to three positions. A CIDR 2026 paper is titled, without
hedging, *Text-to-SQL Benchmarks are Broken*. Treat every number in the table as
±10pp.

### 6.2. Where it breaks down, concretely

The failure modes, ordered by how much they matter *here* rather than in general:

1. **Silence.** This is the whole problem and everything else is a detail. A bad
   generated query does not raise; it returns rows of the right shape that are
   wrong, and the person who asked usually cannot tell. The circulating
   breakdown — **81.2% of failures at the schema/semantic level vs 18.8% syntax**,
   with `WRONG_FILTER` the single largest category at **54.6%** — says the errors
   are concentrated exactly where they are invisible. *(Caveat: these percentages
   travel through vendor and practitioner posts; I could not trace them to a
   primary paper. Treat the ordering as real and the decimals as decoration.)*
2. **Dates and timezones.** Named in every list of semantic failures, and the one
   that is disqualifying here. `SPEC.md` forbids the model from parsing dates
   precisely because "вчера" at 00:30 Asia/Jerusalem is a coin flip, and
   `sveta/core/timeparse.py` exists to do it in code. Handing the model SQL hands
   the date arithmetic straight back to it — `CURRENT_DATE - 1` is evaluated in
   the *server's* timezone, not the user's. This is not a hypothetical; it is the
   standing rule being reversed by the back door.
3. **`WHERE user_id = %s`.** `SPEC.md` §6.1 says multi-user isolation is "the one
   place where isolation is guaranteed by construction rather than by discipline"
   — every tool takes a `UserScope` and has no other way to address data. A
   generated query that omits the predicate, or that joins a table and forgets it
   on one side, converts a construction guarantee into a discipline guarantee
   enforced by a language model. Given that `WRONG_FILTER` is the most common
   failure category in the literature, this is the strongest single argument
   against raw SQL **in this system specifically**.
4. **Prompt injection reach.** §8 already reasons about what reaches the agent: a
   forwarded message or a fetched page can steer the model, but it "cannot call a
   non-existent tool and cannot bypass a confirmation card". Raw read access
   deletes that sentence. The blast radius of a successful injection goes from
   "one wrong declared tool call" to "any SELECT against any table" — including
   `mail_messages` and `oauth_tokens`, which is why they are encrypted at rest in
   the first place.
5. **Non-determinism.** The same question produces a different query on different
   runs. In BI an analyst notices; in a personal chat, "сколько дел я закрыл на
   прошлой неделе" answering 7 on Monday and 9 on Tuesday is indistinguishable
   from a memory the user misremembers. This quietly corrodes trust in a way that
   is hard to debug because there is no ground truth to compare against.
6. **Wrong joins / fan-out.** Real, and the standard example (a duplicated join
   inflating a total by 30%). Low risk here: the interesting queries are
   single-table over `list_items`.
7. **Cost and runaway scans.** Mostly irrelevant at personal volume — one user's
   rows, hundreds of them. Listed only to note that the most-cited enterprise
   objection does not apply.

**On the sources:** the loudest voices in this literature are Omni (sells a
semantic layer) and Atlan (sells a catalogue), writing articles whose conclusion
is "text-to-SQL fails, buy a semantic layer". That is precisely the shape of
self-serving content, and the volume of writing far exceeds the volume of
measurement. The benchmark numbers in §6.1 are real; the failure-mode percentages
are practitioner folklore with a decimal point. I would not make a decision on the
folklore alone — but points 2, 3 and 4 above do not depend on it, because they
follow from this system's own written invariants.

### 6.3. Which to-do mechanisms collapse into queries, and which do not

Testing the suspicion that "history and most views collapse into queries, while
rollover, expiry and a postponement count remain real state". **It survives, but
it needs sharpening in one place.**

**Collapses into a query** — no state, no column, nothing to write:

- History. "Вчерашний список", "что я закрыл на прошлой неделе", "когда я это
  добавил" — all `created_at`/`checked_at`, all already stored.
- Every view in §1's table. Today, overdue, per-list, "what is still open" — the
  entire sticky-family UI is filters.
- Age and staleness. `now - created_at`. Marvin's "Most Procrastinated Tasks"
  screen is `ORDER BY` with a threshold.
- "Сегодня" itself, once it is a date rather than a bucket.

**Does not collapse — genuinely irreducible state:**

- **`due_on`: the day a line is *for*.** This is the core of it, and it is worth
  stating as a principle because it generalises past to-do lists: **a query can
  recover what happened; it cannot recover what was meant.** "Позвонить в банк"
  typed on Friday *for Monday* and typed on Friday *for Friday* produce identical
  rows. `created_at` records when the user spoke; nothing records what the user
  said the line was for. No amount of reasoning over the existing table gets that
  back, because it was never written down. This is exactly why the failing
  message is a weaker example than it looks: it happened to be answerable from
  `created_at`, and the *next* question ("перенеси на завтра", "что у меня на
  четверг") is not.
- **Rollover and expiry**, in the sense of §6.4 below — though the §5 design
  deliberately needs neither.

**The sharpening:** the postponement count is *not* irreducible state. If every
movement of a line is **recorded as an event**, the count is `SELECT count(*)`.
What must exist is not a counter but a written record of the mutation —
`list_items.moved_from` shows the schema's author already had this instinct for
list-to-list moves. So the correct form of the requirement is: **movements and
intent must be written down; summaries and views must not be.** `first_due_on`
from §5.1 is the cheap denormalised form of that record, and it is the right
trade at this size — but if a movement log ever exists, the column is redundant
and should go.

### 6.4. What still has to be a scheduled job

A reasoning agent has no clock. It runs when a message arrives; midnight in the
user's timezone passes in silence, and on a quiet Sunday nothing runs for
thirty hours. Anything that must happen *at a time* is a job, permanently, and no
improvement in model capability changes that.

The sharper point, though, is how short that list becomes under the §5 design:

- **Nothing has to happen at midnight.** This is the main practical advantage of
  the view shape over the container shape, restated in operational terms: Things,
  Apple, Todoist, TickTick and Marvin all run **zero** midnight jobs, because a
  task that carries its own date needs no push to survive the boundary. Sunsama
  runs a rollover only because its days are containers. Adopting the view shape
  removes a job that would otherwise need to be idempotent, timezone-correct, and
  safe to re-run after a Railway restart — three ways to lose data that simply
  never get created.
- **The morning brief and evening review stay jobs**, and they already are:
  `sveta/jobs/digest.py` on a `*/15 * * * *` cron, per user in the user's own
  timezone, at-most-once via `UNIQUE (user_id, kind, for_date)` on `digests`.
  Nothing new is needed.
- **The reminder tick stays a job** — already a daemon thread with `FOR UPDATE
  SKIP LOCKED`.
- **The aging question from §5.3 rides on the evening review**, not on its own
  job. It is a question asked inside a message that is already being sent, so it
  inherits the idempotency that message already has.

And the pattern for what a job is allowed to delegate to the model is already
written in `brief.py` and should not be loosened: data is gathered
deterministically, the model may only rephrase, the deterministic template is
"both the fallback and the ceiling", and `_dropped_facts()` re-checks in code that
every clock time and URL survived. A scheduled job that asks a model *what to do*
rather than *how to say it* is a job that can silently do nothing at 03:00 and
tell no one.

### 6.5. Where this leaves the fork

The fork is not binary, and the honest resolution keeps most of what Дима is
asking for:

1. **Adopt the direction, reject the mechanism.** The rule worth writing down is
   *prefer widening an existing tool's parameters to adding a new tool*. "Пришли
   вчерашний список" should be `list_show(when="вчера")`, never `list_yesterday()`.
   §5.2 is that rule applied: three optional arguments, zero new tools, and the
   space of answerable questions grows by a lot more than one. The instinct that
   26 narrow functions is the wrong trajectory is correct; the fix is fewer,
   wider tools, not zero tools.
2. **The model passes parameters; code builds the query.** A date *phrase* goes
   to `timeparse.py`; `user_id` is bound by `UserScope` in Python and is never
   something the model can omit. This is what a semantic layer is in the
   analytics literature — a small number of governed query shapes — arrived at
   from the same pressure, and it preserves all four of this system's written
   invariants at once.
3. **A read-only SQL escape hatch is the interesting open question, and the
   answer today is no.** It would genuinely cover unanticipated questions, which
   is the real complaint. It also puts every table within reach of a prompt
   injection and moves `user_id` scoping from construction to discipline. What
   would change the answer: a dedicated Postgres role with read-only access to a
   *view* that exposes only the current user's rows (so the isolation is back in
   the schema, not in the generated SQL), an allow-list of tables that excludes
   `mail_messages` and `oauth_tokens`, a `STATEMENT_TIMEOUT`, and the generated
   SQL shown in the reply so a wrong answer is at least auditable. That is a
   stage of its own, and it should be proposed on its own evidence rather than
   smuggled in as a to-do-list fix.
4. **The tool-count objection deserves a straight answer, and this system already
   has the measurement.** The literature does support it in general: tool-choice
   accuracy on BFCL dropped from 93.1% (2.2 tools shown) to 87.1% (fixed 5), and
   on medium-difficulty queries 76.8% → 60.9%; an adaptive agent matched a
   fixed-50 baseline at 7.4 tools; practitioners report degradation past 15-20
   tools, and one retrieval benchmark reports 13.62% → 43% from showing a relevant
   subset. **But Svetochka's own golden set measures 97% (63/65) at 26 tools**,
   and the two documented misses are two-intent messages, not tool-selection
   errors. The constraint that actually bites here is the *strict-schema grammar
   budget* — a hard 400 "Schema is too complex", which is about schema
   compilation, not about reasoning. On this system's own evidence, 26 tools is
   not currently degrading tool choice. The argument for consolidation is a good
   one; it is not an emergency, and the golden run is the instrument that would
   tell us if it became one.
5. **The failing message was not a tool-selection failure.** The model chose
   `list_show`, called it correctly, and reported accurately that no such list
   exists. No catalogue size and no reasoning improvement would have helped,
   because "вчера" was not a property of anything in the database. It was a
   **data-model** gap wearing a capability gap's clothes — which is the argument
   for §5.1 and, incidentally, the best evidence in this document that the next
   such failure is likelier to be fixed by a column than by an architecture.

## 7. What would be a mistake here, and why

Options considered and rejected, with the reason each was rejected rather than a
verdict.

**Deleting or auto-archiving unfinished lines at midnight.** No product in §1 does
this. Sunsama comes closest and archives to a *visible panel with a badge and a
count* — a surface Telegram does not have. In chat, an archived line is
indistinguishable from a lost one: nothing shows the badge, so the user learns
that Svetochka silently eats things. That lesson is unrecoverable and would
poison notes and reminders too, not just lists.

**A new list row per day — "Сегодня 2026-09-11", "Сегодня 2026-09-12".** The
obvious reading of "how do tasks move between days", and wrong. `UNIQUE (user_id,
name)` makes each day a separate list, so `list_show` with no name — the overview
— returns a wall of dates; a checkbox keyboard is scoped to one list, so a line
moved to tomorrow leaves the message you are looking at; `find_list` is
case-insensitive name matching, which will start matching the wrong day. The date
belongs on the item because a date is a property of the work, not a filing
cabinet. Every product in §1 that can show "overdue" does it this way.

**A dedicated `todo_*` tool family separate from lists.** Conceptually the
cleanest: to-dos and shopping lists really are different things. It costs four or
five new tools against a 26-tool ceiling with a measured 400 "Schema is too
complex" past the strict budget, and it splits "добавь в покупки" from "добавь в
сегодня" into two vocabularies the model has to choose between — a new class of
miss on a golden set that already loses points to two-intent messages. A `due_on`
column buys the same behavior for one migration.

**Letting the model decide what "вчера" means.** Prohibited by §6.2 and by the
spec's standing rule, and for a good reason that is easy to forget: the model does
not know the user's timezone boundary, so "вчера" at 00:30 Asia/Jerusalem is a
coin flip. `timeparse.py` exists precisely for this. The `when` arguments above
are *strings the tool parses*, never dates the model computes.

**Marvin's escalating procrastination count as the default.** It is the most
thought-through mechanism found, and I still would not ship it as the default. The
count only works in a dense visual list where `!!!` is a glance; in a ten-line
message capped by `brief.MAX_LINES` it costs characters on every line to convey
what a binary marker conveys on the few lines that need it. Marvin is also a
configurability-maximalist app for a specific audience — its shipping a feature is
weak evidence the feature works. Ship the binary; the column (`first_due_on`)
keeps the count available if Дима asks for it.

**Making the user do a morning planning ritual (Microsoft To Do / Sunsama
proper).** Both require the user to open a surface and re-pick the day. There is
no surface. Forcing the ritual into chat means Svetochka asks a question every
morning before she has earned the right to, and an unanswered morning question
means the day has no list at all. The evening review is the right place for the
ritual because the information is fresh and the cost of ignoring it is zero —
implicit carry-over already happened.

**Auto-converting to-dos into reminders so they "can't be forgotten".** This is
the tempting move when a list has no teeth, and it converts a quiet list into a
notification stream. A reminder interrupts; a to-do waits (§4). Interrupting Дима
about a line he wrote down to *not* have to think about is the fastest way to make
him stop writing lines down.

**Answering each new question with a new narrow function.** Named explicitly
because it is the trajectory we are actually on and because it is how this
document could easily be misread. `list_yesterday()` would have closed today's
bug and bought nothing: "что у меня на четверг" needs another function, "перенеси
на завтра" another. Each one costs grammar budget, blurs the semantic boundary
between neighbouring tools, and leaves the data model exactly as unable to
represent a day as it was before. See §6.5 — widen a parameter, do not add a
name.

**Giving the agent raw SQL to avoid writing functions.** The most tempting
version of the fix and the one with the largest blast radius: it hands date
arithmetic back to the model that §6.2 of the spec removed it from, converts
`user_id` isolation from a construction guarantee into a model-discipline
guarantee against a literature where wrong filters are the most common failure,
and widens a prompt injection from "one wrong declared tool call" to "any SELECT
against any table". §6.5 lists what would have to be true to revisit it.

**Recurring list lines now.** Correct eventually, wrong first: it cannot be built
before `due_on` exists, it is not what broke, and done carelessly it manufactures
exactly the graveyard this document is about (four unfinished Mondays). If it is
built later, copy Todoist's rule — at most one live instance, future dates only.

---

## 8. Where this is thin

Said plainly rather than papered over:

- **No empirical comparison of carry-over vs. daily reset exists that I could
  find.** Everything in §2 is product behavior, practitioner writing, or one
  qualitative CHI paper from 2004. The recommendation in §5 is an argument from
  the constraints of this system, not from evidence about users in general.
- **Things 3's exact behavior for a past-dated to-do is inferred**, not quoted.
  Culture Code's own documentation states the Upcoming→Today rule and says you can
  reschedule what you did not finish, but never states what happens if you do
  nothing. The inference (it stays in Today) is consistent with both statements
  and with how the app is described everywhere, but it is an inference.
- **Sunsama's default auto-archive threshold is unverified.** The setting exists
  and is adjustable and disableable; I did not find the shipped default.
- **Todoist's Overdue section placement** is assembled from two help articles that
  each reference it obliquely; the behavior is right, the exact UI wording is not
  worth quoting.
- **The Telegram edit-window question** (§3) is genuinely unclear in the sources —
  48 hours is documented for deletion, editing own messages appears uncapped.
  Nothing recommended here depends on it; verify before anything does.
- **No usage data on history features** (§1.2). Vendor documentation describes an
  intended use (the GTD weekly review); issue trackers show narrower real uses
  (estimating from a previous instance, retrieving a note on a completed task).
  Nobody publishes retention numbers for an archive view. The recommendation
  there — answer history questions, do not build a place to browse history — is
  reasoning from the asymmetry between storage cost and surface cost, not from
  measurement.
- **The text-to-SQL failure percentages in §6.2 are folklore with a decimal
  point.** "81.2% schema-level vs 18.8% syntax" and "WRONG_FILTER 54.6%"
  circulate through vendor and practitioner posts; I could not trace either to a
  primary paper. The *ordering* (semantic errors dominate syntax errors) is
  consistent across every source and is safe to rely on; the numbers are not.
- **The text-to-SQL literature is commercially motivated.** The most-cited
  "text-to-SQL fails" articles are published by companies selling semantic layers
  and catalogues. The benchmark results (Spider 1.0/2.0, BIRD) are independent
  and academic; almost everything written *about* them is not. §6.2's arguments
  2-4 were deliberately built on this system's own written invariants rather than
  on that literature, for exactly this reason.
- **Benchmark grading is itself contested** — FLEX found BIRD's execution
  accuracy agrees with human experts only 62% of the time, with re-grading
  shifting relative performance −3% to +31%. Every number in §6.1 should be read
  ±10pp.
- **The tool-count literature was not measured on anything resembling Svetochka.**
  BFCL and ToolBench use 370 and 3,251 tools; the "15-20 tools" threshold is
  practitioner report, not measurement. This system's golden set (97% at 26
  tools) is better evidence about this system than any of it, and it is a single
  run at ±1 scenario.

---

## Sources

- [Things — An In-Depth Look at Today, Upcoming, Anytime, and Someday](https://culturedcode.com/things/support/articles/4001304/)
- [Things — Scheduling To-Dos](https://culturedcode.com/things/support/articles/2803579/)
- [Apple — Use Smart Lists in Reminders on iPhone](https://support.apple.com/guide/iphone/use-smart-lists-iphe882772ed/ios)
- [Apple — View reminder lists on Mac](https://support.apple.com/guide/reminders/view-reminder-lists-remnd854fc47/mac)
- [Todoist — Schedule a date and time for your tasks](https://www.todoist.com/help/articles/introduction-to-dates-and-time-q7VobO)
- [Todoist — Introduction to recurring dates](https://www.todoist.com/help/articles/introduction-to-recurring-dates-YUYVJJAV)
- [Todoist — I Declare To-Do List Bankruptcy! (and you should too)](https://www.todoist.com/inspiration/todo-list-bankruptcy)
- [TickTick — 20 Lesser-Known TickTick Features (Plan Your Day)](https://blog.ticktick.com/2020/12/08/20-lesser-known-ticktick-features/)
- [Microsoft — My Day and suggestions](https://support.microsoft.com/en-us/office/my-day-and-suggestions-fc09a1b9-0854-4906-b166-f480ee97a139)
- [Sunsama — Task rollover and recurring tasks: the basics](https://help.sunsama.com/docs/getting-started/basics/task-rollover-and-recurring-tasks-the-basics)
- [Sunsama — Daily Planning](https://help.sunsama.com/docs/usage-guides/daily-planning/)
- [Sunsama — Daily Planning and Shutdown](https://www.sunsama.com/features/daily-planning-and-shutdown)
- [Amazing Marvin — Procrastination Count](https://help.amazingmarvin.com/en/articles/1950154-procrastination-count)
- [Amazing Marvin — Most procrastinated tasks](https://help.amazingmarvin.com/en/articles/3733206-most-procrastinated-tasks)
- [Amazing Marvin — Staleness Warning](https://help.amazingmarvin.com/en/articles/1950228-staleness-warning)
- [Bellotti et al., *What a to-do: studies of task management towards the design of a personal task list manager*, CHI 2004](https://www.researchgate.net/publication/221518959_What_a_to-do_studies_of_task_management_towards_the_design_of_a_personal_task_list_manager)
- [Bullet Journal symbols and migration — key guide](https://www.copychars.com/blog/bullet-journal-symbols)
- [Fast Company — How declaring a 'to-do list bankruptcy' made me more productive](https://www.fastcompany.com/90383750/how-to-manage-an-overflowing-to-do-list)
- [Todoist — View completed tasks](https://www.todoist.com/help/todoist/features/view-completed-tasks-in-todoist-J19h2s) and [View the activity log](https://www.todoist.com/help/articles/view-the-activity-log-oOra6D)

On the architecture fork (§6):

- [Spider 2.0: Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows (arXiv 2411.07763, ICLR 2025)](https://arxiv.org/abs/2411.07763) and the [Spider 2.0 site](https://spider2-sql.github.io/)
- [BIRD-SQL benchmark](https://bird-bench.github.io/) and [leaderboard scores](https://benchmarklist.com/benchmarks/bird_sql/)
- [Text-to-SQL Benchmarks are Broken: An In-Depth Analysis of Annotation Errors (CIDR 2026)](https://www.vldb.org/cidrdb/papers/2026/p5-jin.pdf)
- [MotherDuck — Your Data Model Is the Semantic Layer](https://motherduck.com/blog/bird-bench-and-data-models/)
- [Omni — Why text-to-SQL fails](https://omni.co/blog/why-text-to-sql-fails) *(vendor: sells a semantic layer)*
- [Text-to-SQL in Production: Why Correct SQL Is the Easy Part](https://tianpan.co/blog/2026/04/10/text-to-sql-failure-modes-production) *(practitioner)*
- [How Many Tools Should an LLM Agent See? A Chance-Corrected Answer (arXiv 2605.24660)](https://arxiv.org/html/2605.24660v1)
- [MCP Tool Overload: Why More Tools Make Your Agent Worse](https://dev.to/thedailyagent/mcp-tool-overload-why-more-tools-make-your-agent-worse-5a49) *(practitioner)*
- [Clive Thompson — Declaring 'To-Do List Bankruptcy'](https://forge.medium.com/declaring-to-do-list-bankruptcy-9a05f4b40de2)
