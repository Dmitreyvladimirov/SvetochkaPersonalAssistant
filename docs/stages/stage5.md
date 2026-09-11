# Stage 5 — dumps, corrections, memory of facts (Notion showcase deferred)

Implementation spec for `SPEC.md` §11 stage 5, written 2026-09-11 in the same
overnight run as stages 2 and 4. DoD (§11): FR-13…FR-15, FR-45. **FR-14 (the
Notion showcase) is deferred**: it needs a Notion internal token and shared pages
from Dimitry (§12); the code path is left as a `notion_page_id` column that
already exists on `notes`. The three other requirements are in scope.

## Objective

- **FR-13** — a message with three or more separate thoughts is split into
  records and shown for confirmation; nothing is lost, nothing is written until
  the tap.
- **FR-15** — the "Не туда" button produces a "did → should have" pair: the
  user's next message answers "куда это на самом деле", and the last N pairs go
  into the prompt as examples, so the same mistake is not repeated.
- **FR-45** — facts with a validity window: "Света сменила врача" closes the old
  fact and opens a new one; the agent answers with the current one.

## Assumptions

1. **A dump proposal is a suggestion of a special kind.** The model calls
   `notes_propose(items)`; the bot renders the items in the reply with one
   button "✓ Сохранить все N" (`dp:<inbox_item_id>`). The proposal is stored in
   the existing `inbox_items.suggestions` JSONB as entries with `kind: "note"`,
   so the tap saves them by code with no second model call and **no schema
   change**. Ordinary suggestions (FR-43) keep `kind: "instruction"` (absent =
   instruction, for rows written before this stage).
2. **The threshold is the model's judgement, guided by the persona**: three or
   more distinct thoughts → propose; one or two → save directly as before (the
   golden scenarios for two thoughts expect `note_save` ×2 and stay as they are).
3. **The "should have" half of a correction is the next message.** After "Не
   туда" the bot already asks "Скажи, куда это на самом деле — запомню". The next
   text from the user within 15 minutes fills `corrections.should_have` **and**
   is still processed normally (that message usually is the corrected
   instruction: "это был список покупок"). One open correction at a time; a
   newer undo replaces the pending one.
4. **Corrections reach the prompt** as the last 5 pairs with a `should_have`,
   under "Прошлые поправки (учитывай)" in `agent.system_prompt` (FR-15 acceptance:
   "edits go into the prompt as examples"). Pairs without `should_have` stay in
   `/memory` only.
5. **Facts are subject / predicate / object with a window.** `fact_remember`
   closes every open fact with the same `(subject, predicate)` (sets `valid_to`)
   and inserts the new one; `fact_recall(topic)` returns open facts whose subject
   or object contains the topic (case-insensitive), plus closed ones marked
   "(до <date>)" when the user asks about history. "Что ты про меня помнишь?"
   (`memory_show`) lists open facts too.
6. **"Запомни, что Костя отвечает за инфру" is a fact, not a note.** The golden
   scenario is updated to accept either (`expect_tools_any`), because both are
   defensible; the persona nudges towards `fact_remember` for "кто / что / где /
   когда" statements about people, places and things, and `note_save` for
   thoughts and ideas.
7. **No schema change.** `facts` and `corrections` exist since stage 0 with the
   needed columns.

## Design by requirement

### FR-13 — the dump

Tool `notes_propose(items: [{body, project}])` (file `sveta/tools/dump.py`):
validates 2–20 non-empty bodies, writes them to `ctx.proposal`, returns "N
records proposed; they will be shown with a confirm button — do not save them
yourself". The bot (`_keyboard`) appends the proposal to `suggestions` with
`kind: "note"` and renders the button; the reply text lists the records with
numbers (the tool's return string already contains the list so the model can
echo it). Callback `dp:<item_id>` → every `kind: "note"` entry → `db.create_note`
→ reply "Сохранила N заметок" with one "Не туда" for the last one. A second tap
finds the entries marked `saved: true` and says so.

### FR-15 — corrections

- `db.add_correction` unchanged; `db.open_correction(user_id, max_age_minutes)`
  returns the newest correction without `should_have` inside the window;
  `db.fill_correction(user_id, id, should_have)`.
- `bot._handle_message`: before the pipeline, if a text message arrives and an
  open correction exists, fill it with the text (first 200 characters). The
  message then continues normally.
- `agent.system_prompt`: `db.recent_corrections(user_id, 5)` filtered to pairs
  with `should_have` → a block of `- сделала: … → надо было: …` lines.
- `/memory` already lists corrections.

### FR-45 — facts

`sveta/tools/facts.py`: `fact_remember(subject, predicate, object)`,
`fact_recall(topic)`. Persona: "Факты о людях, местах, вещах (кто за что
отвечает, чей врач, где что лежит) — fact_remember; мысли и идеи — note_save.
Прежде чем ответить на вопрос о человеке или вещи — fact_recall." `memory_show`
adds the open facts. Isolation test: user B recalls nothing of user A's facts.

## Testing

`tests/test_dump.py` (proposal → button → saved by tap, nothing saved without
the tap, second tap is idempotent, empty/one-item proposals refused),
`tests/test_corrections.py` (undo then a message fills should_have and is still
processed; the window expires; the prompt carries the pair), `tests/test_facts.py`
(window closes the old fact; recall by subject and by object; history marked;
isolation). Golden: +6 scenarios (a three-thought dump → `notes_propose`; two
facts; two recalls; a correction phrase). Threshold 90%.

## Out of scope tonight

FR-14 Notion showcase (token from Dimitry). The Instagram importer (§11 note:
"can be pulled into stage 5") stays in stage 7.
