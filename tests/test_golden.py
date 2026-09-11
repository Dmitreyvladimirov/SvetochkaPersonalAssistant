"""The golden set (SPEC.md §10): scenarios against the REAL model. Costs money.

Run explicitly, before and after any prompt or playbook change:

    SVETA_GOLDEN=1 ANTHROPIC_API_KEY=... pytest -m golden -q

Skipped otherwise. The threshold is 90%; the run prints every miss so a prompt
change can be judged on what it broke, not on a single number.
"""
import json
import os
from pathlib import Path

import pytest

from sveta.core import agent, config
from sveta.core.scope import UserScope
from tests.fakedb import install

SCENARIOS = [json.loads(line) for line in
             (Path(__file__).parent / "golden" / "scenarios.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
THRESHOLD = 0.90


def test_golden_file_is_well_formed():
    """Runs in CI for free: every scenario names only real tools and has a message."""
    from sveta.tools import REGISTRY
    names = {t.name for t in REGISTRY}
    assert len(SCENARIOS) >= 40
    for s in SCENARIOS:
        assert s["message"].strip()
        for key in ("expect_tools", "forbid_tools", "expect_tools_any"):
            for t in s.get(key, []):
                assert t in names, (s["message"], t)


def _check(scenario: dict, calls: list[tuple[str, dict]]) -> list[str]:
    names = [n for n, _ in calls]
    misses = []
    for t in scenario.get("expect_tools", []):
        if t not in names:
            misses.append(f"expected {t}")
    if scenario.get("expect_tools_any") and not any(t in names for t in scenario["expect_tools_any"]):
        misses.append(f"expected one of {scenario['expect_tools_any']}")
    for t in scenario.get("forbid_tools", []):
        if t in names:
            misses.append(f"forbidden {t}")
    prefix = scenario.get("forbid_tool_prefix")
    if prefix and any(n.startswith(prefix) for n in names):
        misses.append(f"forbidden prefix {prefix}")
    for t, n in scenario.get("min_calls", {}).items():
        if names.count(t) < n:
            misses.append(f"{t} called {names.count(t)}x, wanted ≥{n}")
    for t, want in scenario.get("expect_args", {}).items():
        got = [a for n, a in calls if n == t]
        if not any(all(str(a.get(k, "")).lower() == str(v).lower() for k, v in want.items()) for a in got):
            misses.append(f"{t} args {want} not found in {got}")
    return misses


@pytest.mark.golden
@pytest.mark.skipif(not os.environ.get("SVETA_GOLDEN"), reason="set SVETA_GOLDEN=1 to spend money")
def test_golden_set(monkeypatch):
    install(monkeypatch)
    from sveta.core import config, fetch, llm
    # FR-38's per-user daily limit is not a property under test: 60+ scenarios on
    # Sonnet cost ~$1.6, above the production default of $1.50.
    monkeypatch.setattr(config, "DAILY_USD_LIMIT", 100.0)
    # The golden set measures tool choice, not the network: every URL "opens".
    monkeypatch.setattr(fetch, "get", lambda url: fetch.FetchResult(url=url, final_url=url, status=200,
                                                                    title="Page", summary="Summary."))
    client = llm.client()
    scope = UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem")
    failures = []
    for s in SCENARIOS:
        r = agent.run(scope, s["message"], client=client, purpose="golden")
        misses = _check(s, r.tool_calls)
        if misses:
            failures.append((s["message"], misses, [n for n, _ in r.tool_calls]))
    score = 1 - len(failures) / len(SCENARIOS)
    report = "\n".join(f"- {m!r}: {why} (called {called})" for m, why, called in failures)
    print(f"\nGolden: {score:.0%} ({len(SCENARIOS) - len(failures)}/{len(SCENARIOS)}) on {config.AGENT_MODEL}\n{report}")
    assert score >= THRESHOLD, f"golden {score:.0%} < {THRESHOLD:.0%}\n{report}"
