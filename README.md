# Svetochka — personal assistant

A personal assistant that lives in one Telegram chat: a single point of entry for
thoughts, links, voice notes and lists, with calendar, mail, reminders and a morning
brief behind it. Built as an agent with a closed set of tools; intelligence grows
through tools, playbooks and memory rather than by rewriting the core.

- `SPEC.md` — the accepted specification (requirements with acceptance criteria).
  Code is written strictly against it.
- `RESEARCH.md` — the research and reasoning the spec grew out of.
- `CLAUDE.md` — working notes for Claude Code sessions.

Status: stages 0–2, 4 and 5 deployed (2026-09-12) — notes, search, preferences,
suggestion buttons, voice, reminders, links, lists, morning brief and evening
review, RSS, dump proposals, facts. Stage 3 (Google) needs OAuth from Dimitry;
the Notion showcase (stage 5, FR-14) needs a token. Per-stage specs: `docs/stages/`.

Development: see `CLAUDE.md` → Local development. Everything a new session needs
(Railway ids, variable names, open operational items) is in that file; nothing
lives only in a chat.

Own Railway project `svetochka` with its own Postgres. Nothing is shared with any
other project.
