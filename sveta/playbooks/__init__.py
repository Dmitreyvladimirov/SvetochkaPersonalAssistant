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
    # Anchored so "Onboarding", "Training", "constrained", "Hotel California" and
    # "Booking.com: 15% off" stay out; the brief's trips section uses the same matcher.
    "trip": re.compile(
        r"(?<![\w-])(?:билет\w*|перел[её]т\w*|рейс\w?|посадочн\w*|авиа\w*|бронировани\w*|"
        r"boarding pass|flight|itinerary|e-?ticket|booking confirmation|booking confirmed|"
        r"hotel (?:booking|reservation|confirmation)|train ticket|check-in)(?![\w-])",
        re.IGNORECASE),
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
