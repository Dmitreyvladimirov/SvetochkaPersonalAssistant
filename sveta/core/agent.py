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
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import REGISTRY, UnknownTool, definitions, run as run_tool

logger = logging.getLogger(__name__)

MAX_STEPS = 8
_PERSONA = Path(__file__).resolve().parents[1] / "playbooks" / "persona.md"


@dataclass
class AgentResult:
    reply: str
    ctx: ToolContext
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    steps: int = 0
    truncated: bool = False


def system_prompt(scope: UserScope) -> str:
    persona = _PERSONA.read_text(encoding="utf-8") if _PERSONA.exists() else ""
    prefs = scope.preferences
    lines = [persona.strip()]
    if prefs:
        lines.append("\nПредпочтения пользователя (действуют всегда):")
        lines += [f"- {k}: {v}" for k, v in sorted(prefs.items())]
    corrections = _corrections(scope)
    if corrections:
        lines.append("\nПрошлые поправки пользователя (учитывай, не повторяй ошибку):")
        lines += [f"- сделала: {c['did']} → надо было: {c['should_have']}" for c in corrections]
    lines.append(f"\nЧасовой пояс пользователя: {scope.tz}.")
    return "\n".join(lines)


def _corrections(scope: UserScope) -> list[dict]:
    """FR-15: the last pairs that have both halves. Best-effort — a failed read
    must not stop a reply."""
    from sveta.core import db
    try:
        return [c for c in db.recent_corrections(scope.user_id, limit=5) if c.get("should_have")]
    except Exception:  # noqa: BLE001
        logger.exception("agent: corrections load failed")
        return []


def _history_messages(history: list[dict]) -> list[dict]:
    out = []
    for h in history:
        if h.get("raw_text") and h.get("reply_text"):
            out.append({"role": "user", "content": h["raw_text"][:2000]})
            out.append({"role": "assistant", "content": h["reply_text"][:2000]})
    return out


def _tool_result(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


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
            response = client.messages.create(
                model=config.AGENT_MODEL,
                max_tokens=1500,
                system=system_prompt(scope),
                messages=messages,
                tools=tool_defs,
                output_config={"effort": "low"},
            )
        usage = llm.usage_of(response)
        llm_db_record(scope, inbox_item_id, purpose, usage, t.ms)
        result.steps += 1

        text_parts = [b.text for b in response.content if b.type == "text" and b.text.strip()]
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        final_text_parts = text_parts or final_text_parts

        if not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            args = tu.input if isinstance(tu.input, dict) else {}
            result.tool_calls.append((tu.name, args))
            key = tu.name + json.dumps(args, sort_keys=True, ensure_ascii=False)
            if key in seen_calls:
                results.append(_tool_result(tu.id, "Error: this exact call was already made in this message; use its result.", True))
                continue
            seen_calls.add(key)
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
        messages.append({"role": "user", "content": results})

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
