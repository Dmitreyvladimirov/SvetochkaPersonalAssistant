"""Playbooks are data (FR-44): a markdown file per situation, applied when a tool
returns an event of that type. Adding one is a file plus a trigger line below;
the core does not change (NFR-9). A file that does not exist is skipped with a
warning (§9)."""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DIR = Path(__file__).resolve().parent

# name → pattern over the text a tool produced (subject + sender for mail).
TRIGGERS = {
    "trip": re.compile(r"билет|перел[её]т|рейс|посадочн|boarding|flight|itinerary|e-?ticket|"
                       r"booking|бронировани|reservation|отел[ья]|hotel|поезд|train", re.IGNORECASE),
}


def load(name: str) -> str:
    path = _DIR / f"{name}.md"
    if not path.exists():
        logger.warning("playbook %s: file missing — skipped", name)
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        logger.warning("playbook %s: unreadable (%s) — skipped", name, e)
        return ""


def matching(text: str) -> list[str]:
    return [name for name, pattern in TRIGGERS.items() if pattern.search(text or "")]


def attach(text: str) -> str:
    """The playbook block a tool appends to its result when its output matches."""
    blocks = []
    for name in matching(text):
        body = load(name)
        if body:
            blocks.append(f"\n\nPlaybook «{name}» applies to this result:\n{body}")
    return "".join(blocks)
