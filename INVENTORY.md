# Inventory — the other Светочка pieces (local, not committed)

**Not tracked in git on purpose:** this repo is public, and the table below holds
Notion data-source IDs, local paths and machine details. If the repo becomes private,
this can be committed as-is.

`SPEC.md` describes the Telegram assistant being built. This file describes everything
*else* that already answers to the name "Светочка" or does her job today, scattered
across the machine and Notion. It exists to answer one question: what folds into the
assistant, and what stays where it is.

## The name collision — decide before stage 2

Two different things are called Светочка right now:

1. **Секретарь-Светочка** — a Claude Code subagent (`~/.claude/agents/secretary.md`)
   plus a set of rules in the global `~/.claude/CLAUDE.md`. Runs only inside an open
   Claude Code session. Captures ideas → Notion Backlog DB, logs days → `SESSION_LOG.md`,
   produces the weekly digest.
2. **Светочка the assistant** — this repo. Telegram-first, Railway-hosted, own Postgres,
   closed tool set, multi-user by construction.

They overlap heavily in intent (capture, memory, brief) and not at all in
implementation. Options: fold #1's jobs into #2 as tools; keep #1 as the
inside-the-terminal face of the same assistant; or rename one.

## 1. Secretary persona and rules (candidate to fold in)

| Item | Location |
|---|---|
| Secretary agent definition | `~/.claude/agents/secretary.md` |
| Nickname rule ("Светочка"/"Ирочка" = Secretary) | `~/.claude/CLAUDE.md` |
| "залогируй день" → session log | `~/.claude/CLAUDE.md` |
| "дай недельную сводку" → 7-day digest | `~/.claude/CLAUDE.md` |
| Idea capture → Notion Backlog DB | `~/.claude/CLAUDE.md` → *Projects Hub* |

## 2. Memory and state that already exists

| Item | Location | Note |
|---|---|---|
| Claude auto-memory | `~/.claude/projects/-Users-DimaKu-Documents-Coding/memory/` | ~29 files + `MEMORY.md` index |
| Session log | `../SESSION_LOG.md` | ~99 KB, newest on top |
| Legacy goals tracker | `../NORTH_STAR.md` | ~31 KB, frozen 2026-08-19 |
| Notion **Projects** DB | data source `a76cf86f-259b-46db-a360-d862d7236dd3` | one row per project |
| Notion **Backlog** DB | data source `bb48cde2-f54f-4d43-9332-b000a884b49d` | cross-project backlog |
| Notion Projects Hub page | `3c17a85b-5b20-81ca-9c3c-fb96b00615fb` | built under "Личная обвязка" |
| Notion Projects row **Svetochka** | page `3d87a85b-5b20-8159-986f-f9b7a29df0aa` | created 2026-09-11; embedded "Бэклог проекта" board; first backlog item = multi-provider BYOK |
| Per-project docs | `<project>/{STATUS,ROADMAP,IDEAS,WORKLOG}.md` | standard set since 2026-07-04 |

Four stores, no written contract for which is canonical for what. If the assistant is
going to have memory (`SPEC.md`), that contract has to exist.

## 3. Automation already running headless — one job, and it is dead

`com.dimaku.notion-project-sync` — daily 12:04, `claude -p` → Notion Projects Hub.
Files: `../.notion-sync/` (`run-sync.sh`, `sync-prompt.md`, logs) and
`~/Library/LaunchAgents/com.dimaku.notion-project-sync.plist`.

**11 consecutive failures.** Every line of `../.notion-sync/launchd.log`:

```
/bin/zsh: can't open input file: /Users/DimaKu/Documents/Coding/.notion-sync/run-sync.sh
```

The script exists and is executable. Cause looks like macOS TCC: a launchd *user agent*
gets no access to `~/Documents`, so the spawned `zsh` cannot read the script — while
`launchd` itself, which owns the log file, still writes the log. Last successful run:
**2026-08-19**, so the Notion Projects DB is ~2.5 weeks stale.

Fixes, cheapest first: (1) move `run-sync.sh` and its log out of `~/Documents`, e.g.
`~/.local/bin/`; (2) grant Full Disk Access to `/bin/zsh` — works, but hands that to
every shell script, not worth it; (3) retire launchd and run the job on Railway
alongside the assistant. Confirm before fixing — option 3 may make it moot.

The `sync-prompt.md` itself is a good, working example of a scheduled Светочка job and
worth keeping whatever the runtime turns out to be.

## 4. Input channels that exist

| Item | Location | State |
|---|---|---|
| `telegram-notes` skill — CEO bot, text/voice/video → daily notes | `~/.claude/skills/telegram-notes/` | closest existing inbox; overlaps this repo's stage 2 |
| Telegram MCP plugin | plugin `telegram` | failed to connect 2026-09-06 |
| Whisper API rule | global `CLAUDE.md` | always the API, never a local model |
| Anchor routine (usage-limit workaround) | cloud routine `trig_015hSG7RucQWU8RQ9ug2mi5E` | built ~2026-07-12 |

## 5. Hosting notes

- Railway project `svetochka` + own Postgres — the assistant's home (`CLAUDE.md`).
- `../CloudInfra` — an older, unstarted plan to move agents onto a DigitalOcean VM
  (~$29/mo CLI-only, ~$58/mo with a GUI browser). Still the only written answer for the
  **local** half Dimitry wants (reading personal messengers). Either it becomes that, or
  it gets retired.
- `../ContentFactory` — n8n on Railway, live and proven; a precedent, not a dependency.

## 6. Explicitly not part of Светочка

`JobScraper`, `ResumeBuilder`, `resumebuilder-cloud`, `rag-recruiting`,
`gaya-automation`, `ContentFactory`, `danetka`, `ticket_alert_bot`, `DirotIsraelBot`.
Things she should know about and report on — not absorb.
