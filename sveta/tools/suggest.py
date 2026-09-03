"""suggest — the FR-43 tool. It does nothing except record what Svetochka would offer
to do next; the bot renders the entries as buttons. Nothing runs without a tap, and
the test for that is a row count."""
from sveta.core.scope import ToolContext, UserScope
from sveta.tools import Tool

MAX_SUGGESTIONS = 3


def _suggest(scope: UserScope, ctx: ToolContext, actions: list) -> str:
    cap = MAX_SUGGESTIONS
    try:
        cap = max(0, min(int(scope.pref("persona.proactivity", str(MAX_SUGGESTIONS))), MAX_SUGGESTIONS))
    except ValueError:
        pass
    kept = []
    for a in actions or []:
        label = str(a.get("label", "")).strip()[:40]
        instruction = str(a.get("instruction", "")).strip()
        if label and instruction:
            kept.append({"label": label, "instruction": instruction})
    ctx.suggestions = kept[:cap]
    if not ctx.suggestions:
        return "No suggestions recorded (user prefers none, or entries were empty)."
    return f"{len(ctx.suggestions)} suggestion(s) will be shown as buttons. Do not repeat them in the reply text."


SUGGEST = Tool(
    name="suggest",
    description=("Offer up to 3 follow-up actions as buttons under the reply — things the user "
                 "did not ask for but would plausibly want next (e.g. after finding a flight: "
                 "'список мест', 'проверить визу'). Each has a short button label and the "
                 "instruction Svetochka would execute if tapped. Nothing runs until the user taps."),
    input_schema={
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "Button text, ≤40 chars, in the user's language."},
                        "instruction": {"type": "string", "description": "What to do if tapped, phrased as a user request."},
                    },
                    "required": ["label", "instruction"],
                    "additionalProperties": False,
                },
            },
        },
    },
    fn=_suggest,
)
