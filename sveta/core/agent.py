"""The agent loop (SPEC.md §6.1).

One message in, one reply out, with tool calls in between. The loop is manual rather
than the SDK's tool runner on purpose: it needs a hard cap on iterations, a refusal
to repeat an identical call, a confirmation gate, per-call cost accounting and
injection of a fake client in tests — all things that sit more naturally around a
`while` than inside a runner's hooks.

Safety lives here and in the registry: the model can name only registered tools
(FR-5); an unknown name becomes an error result, never an action; a repeated
identical call is refused (§9); more than MAX_STEPS tool rounds ends the loop with
whatever was gathered (§9).
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sveta.core import config, llm
from sveta.core.providers import ToolOutput
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, UnknownTool, definitions, run as run_tool

logger = logging.getLogger(__name__)

MAX_STEPS = 8
# The keyboard shows this many confirmation cards; a batch must never run an
# action the user never saw (review).
MAX_PENDING = 8
_PERSONA = Path(__file__).resolve().parents[1] / "playbooks" / "persona.md"


@dataclass
class AgentResult:
    reply: str
    ctx: ToolContext
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    steps: int = 0
    truncated: bool = False


# The prompt is built in two halves so the constant one can be cached. SPEC.md
# §7.1 costed the whole assistant on "system prompt and schemas cached": the tool
# schemas and the persona are ~5 100 tokens resent on every step of every message,
# about 80% of each bill (measured 2026-09-12). Where the breakpoint physically
# goes is the provider's business — Anthropic marks it, OpenAI caches the prefix
# by itself — so this module only guarantees the halves are in the right order.


def system_blocks(scope: UserScope) -> list[dict]:
    """[the persona, cached] + [this user's preferences, corrections and tz].

    The split is what makes caching work: the persona is identical for everyone
    and every call, so it stays a stable prefix, while the tail that changes when
    the user sets a preference sits after the breakpoint and invalidates nothing.
    """
    persona = (_PERSONA.read_text(encoding="utf-8") if _PERSONA.exists() else "").strip()
    lines = []
    prefs = scope.preferences
    if prefs:
        lines.append("Предпочтения пользователя (действуют всегда):")
        lines += [f"- {k}: {v}" for k, v in sorted(prefs.items())]
    corrections = _corrections(scope)
    if corrections:
        lines.append("\nПрошлые поправки пользователя (учитывай, не повторяй ошибку):")
        lines += [f"- сделала: {c['did']} → надо было: {c['should_have']}" for c in corrections]
    lines.append(f"\nЧасовой пояс пользователя: {scope.tz}.")
    blocks = []
    if persona:
        # An empty block is rejected by the API, and a missing persona file is
        # survivable (the loop still works), so the block is dropped, not emptied.
        blocks.append({"type": "text", "text": persona})
    blocks.append({"type": "text", "text": "\n".join(lines)})
    return blocks


def system_prompt(scope: UserScope) -> str:
    """The same prompt as one string, for tests and for reading it in a log."""
    return "\n\n".join(b["text"] for b in system_blocks(scope) if b["text"])


def _corrections(scope: UserScope) -> list[dict]:
    """FR-15: the last pairs that have both halves. Best-effort — a failed read
    must not stop a reply."""
    from sveta.core import db
    try:
        return [c for c in db.recent_corrections(scope.user_id, limit=5) if c.get("should_have")]
    except Exception:  # noqa: BLE001
        logger.exception("agent: corrections load failed")
        return []


HISTORY_CHARS = 2000


def _trim(text: str) -> str:
    """Cut long enough to be useful, and say so when cutting — a reply that stops
    mid-sentence reads to the model as a reply that said that much and no more."""
    text = text or ""
    return text if len(text) <= HISTORY_CHARS else text[:HISTORY_CHARS].rstrip() + " […]"


def _history_messages(history: list[dict]) -> list[dict]:
    """The recent conversation, oldest first (FR-64). A file is an exchange like
    any other, labelled so the model knows the text is what was read off it and
    not something the user typed, and each reply carries what that exchange found
    (FR-65) so a follow-up can build on it instead of searching again."""
    out = []
    for h in history:
        said, replied = h.get("raw_text"), h.get("reply_text")
        if not said or not replied:
            continue
        kind = h.get("kind")
        if kind in ("photo", "document"):
            name = h.get("file_name") or ("фото" if kind == "photo" else "файл")
            said = f"[прислал {name}, на нём:] {said}"
        found = ((h.get("context") or {}).get("found") or [])
        if found:
            replied += "\n[в этом сообщении найдено: " + "; ".join(found) + "]"
        out.append({"role": "user", "content": _trim(said)})
        out.append({"role": "assistant", "content": _trim(replied)})
    return out


def _tool_result(call_id: str, content: str, is_error: bool = False) -> ToolOutput:
    return ToolOutput(call_id=call_id, content=content, is_error=is_error)


def run(scope: UserScope, text: str, *, inbox_item_id: int | None = None,
        history: list[dict] | None = None, client=None, tools=None,
        purpose: str = "agent") -> AgentResult:
    """Never raises for model-side or tool-side trouble; raises llm.BudgetExceeded
    before spending anything if the user's daily limit is gone."""
    tools = tools if tools is not None else REGISTRY
    ctx = ToolContext(inbox_item_id=inbox_item_id)
    result = AgentResult(reply="", ctx=ctx)

    llm.check_budget(scope.user_id)
    client = client or llm.client()

    messages = _history_messages(history or [])
    messages.append({"role": "user", "content": text[:config.MAX_MESSAGE_CHARS]})
    tool_defs = definitions(tools)
    seen_calls: set[str] = set()
    final_text_parts: list[str] = []

    for step in range(MAX_STEPS + 1):
        if step == MAX_STEPS:
            result.truncated = True
            logger.warning("agent: hit MAX_STEPS for item %s", inbox_item_id)
            break

        with llm.Timer() as t:
            answer = llm.complete(client, model=config.AGENT_MODEL, max_tokens=1500,
                                  system=system_blocks(scope), messages=messages,
                                  tools=tool_defs, effort="low")
        llm_db_record(scope, inbox_item_id, purpose, answer.usage, t.ms)
        result.steps += 1

        tool_uses = answer.tool_calls
        final_text_parts = [answer.text] if answer.text else final_text_parts

        if not tool_uses:
            break

        messages.extend(answer.turn)
        results = []
        for tu in tool_uses:
            args = tu.args
            result.tool_calls.append((tu.name, args))
            key = tu.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
            if key in seen_calls:
                results.append(_tool_result(tu.id, "Error: this exact call was already made in this message; use its result.", True))
                continue
            seen_calls.add(key)
            gated = next((t for t in tools if t.name == tu.name and t.needs_confirmation), None)
            if gated is not None:
                # §6.2: an outside effect happens only on a tap. The tool's
                # `describe(scope, args)` renders the card label and validates the
                # arguments; the call itself is stored, not run.
                try:
                    label = gated.describe(scope, args) if gated.describe else f"{tu.name} {args}"
                except Exception as e:  # noqa: BLE001
                    results.append(_tool_result(tu.id, f"Error: {tu.name} refused: {str(e)[:300]}", True))
                    continue
                if label.startswith("Error"):
                    results.append(_tool_result(tu.id, label, True))
                    continue
                import uuid
                if len(ctx.pending) >= MAX_PENDING:
                    results.append(_tool_result(
                        tu.id, f"Error: already {MAX_PENDING} actions are waiting for the user's tap; "
                               "tell them to confirm those first.", True))
                    continue
                ctx.pending.append({"kind": "confirm", "pid": uuid.uuid4().hex[:12], "tool": tu.name,
                                    "args": args, "label": label})
                results.append(_tool_result(
                    tu.id, f"Proposed, waiting for the user's tap: {label}. A confirm button will appear "
                           "under your reply. Do NOT say it is done; say what will happen when they tap."))
                continue
            try:
                out = run_tool(tools, tu.name, scope, ctx, args)
            except UnknownTool:
                logger.warning("agent: model called unknown tool %r", tu.name)
                out = f"Error: no tool named {tu.name!r}. Available: {', '.join(t.name for t in tools)}."
                results.append(_tool_result(tu.id, out, True))
                continue
            except TypeError as e:
                results.append(_tool_result(tu.id, f"Error: bad arguments for {tu.name}: {e}", True))
                continue
            except Exception as e:  # noqa: BLE001 — the model gets the error as text (§9)
                logger.exception("agent: tool %s failed", tu.name)
                results.append(_tool_result(tu.id, f"Error: {tu.name} failed: {str(e)[:300]}", True))
                continue
            results.append(_tool_result(tu.id, out))
        messages.extend(llm.tool_results(client, results))

    reply = "\n".join(final_text_parts).strip()
    if result.truncated and not reply:
        reply = "Запуталась и остановилась. Вот что успела — проверь, пожалуйста."
    if not reply:
        reply = "Готово."
    result.reply = reply
    return result


def llm_db_record(scope: UserScope, inbox_item_id, purpose, usage, latency_ms) -> None:
    from sveta.core import db
    db.record_llm_call(scope.user_id, inbox_item_id, purpose, config.AGENT_MODEL, usage,
                       llm.price(config.AGENT_MODEL, usage), latency_ms)
