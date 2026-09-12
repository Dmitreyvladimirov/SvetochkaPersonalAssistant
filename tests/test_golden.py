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


KEYS = {"message", "expect_tools", "expect_tools_any", "forbid_tools", "forbid_tool_prefix",
        "min_calls", "expect_args", "expect_reply", "forbid_reply", "smoke"}

# A full run is 81 real messages on the agent model. That is the right price
# before a release and the wrong one for the tenth prompt tweak of an evening:
# eight full runs on 2026-09-11 cost ~$18 and took the production key down with
# them. SVETA_GOLDEN=smoke runs one scenario per thing that can break.
SMOKE = [s for s in SCENARIOS if s.get("smoke")]


def test_golden_file_is_well_formed():
    """Runs in CI for free: every scenario names only real tools, has a message and
    uses no key the harness would silently ignore."""
    from sveta.tools import REGISTRY
    names = {t.name for t in REGISTRY}
    assert len(SCENARIOS) >= 40
    # Small enough to run on every prompt change, wide enough to be worth running.
    assert 8 <= len(SMOKE) <= 20, len(SMOKE)
    assert {s["message"] for s in SMOKE} <= {s["message"] for s in SCENARIOS}
    for s in SCENARIOS:
        assert s["message"].strip()
        assert set(s) <= KEYS, (s["message"], set(s) - KEYS)
        for key in ("expect_tools", "forbid_tools", "expect_tools_any"):
            for t in s.get(key, []):
                assert t in names, (s["message"], t)
        for key in ("min_calls", "expect_args"):
            for t in s.get(key, {}):
                assert t in names, (s["message"], t)


def test_the_golden_world_answers_the_tools(monkeypatch):
    """The fixture itself, for free: without it every mail and calendar scenario
    would answer 'Google не подключён' before reaching any query logic, and the
    whole class of search failures would stay invisible (found 2026-09-12)."""
    from sveta.core.scope import ToolContext
    from sveta.tools import REGISTRY, run
    from tests.golden import world
    fake = install(monkeypatch)
    fake.add_user("111", tz="Asia/Jerusalem")
    world.install(monkeypatch)
    from sveta.tools import mail as mail_tool
    monkeypatch.setattr(mail_tool, "_plan_queries",
                        lambda scope, ctx, what, when: (["Bogota OR BOG", "ticket"], ["Bogota", "BOG"]))
    scope = UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem")
    out = run(REGISTRY, "mail_search", scope, ToolContext(), {"what": "билеты в Колумбию", "when": ""})
    assert "[m-col]" in out and "7PVOQO" in out and "📎 eticket_7PVOQO.pdf" in out
    assert "Google не подключён" not in out
    events = run(REGISTRY, "calendar_query", scope, ToolContext(), {"period": "", "query": ""})
    assert "Созвон с Костей" in events
    # Behind, and spelled the other way: the ladder has to climb to reach it.
    past = run(REGISTRY, "calendar_query", scope, ToolContext(), {"period": "", "query": "приём"})
    assert "Приём у врача" in past
    # The user's own data, worded so the strict pass misses and the broad one hits.
    notes = run(REGISTRY, "note_search", scope, ToolContext(),
                {"query": "онбординг для новых пользователей", "source": ""})
    assert "через шаблоны" in notes
    assert "инфру" in run(REGISTRY, "fact_recall", scope, ToolContext(),
                                       {"topic": "Костя", "include_history": False})
    assert "батарейки" in run(REGISTRY, "list_show", scope, ToolContext(),
                              {"list_name": "Покупки", "as_text": True})


# Errors that make every remaining scenario meaningless: the run stops on these
# rather than reporting 60 identical failures or losing what it already paid for.
TERMINAL = ("usage limits", "credit balance", "invalid x-api-key", "authentication_error",
            "permission_error", "not_found_error",
            # OpenAI's wording for the same three things
            "incorrect api key", "invalid_api_key", "exceeded your current quota",
            "insufficient_quota", "model_not_found")


def _is_terminal(e: Exception) -> bool:
    text = str(e).lower()
    return any(marker in text for marker in TERMINAL)


def _check(scenario: dict, calls: list[tuple[str, dict]], reply: str = "") -> list[str]:
    """Tool choice *and* what was said: a reply can offer the wrong ticket or hand
    the searching back to the user while calling exactly the right tools."""
    names = [n for n, _ in calls]
    misses = []
    lowered = (reply or "").lower()
    for phrase in scenario.get("expect_reply", []):
        if phrase.lower() not in lowered:
            misses.append(f"reply lacks {phrase!r}")
    for phrase in scenario.get("forbid_reply", []):
        if phrase.lower() in lowered:
            misses.append(f"reply contains {phrase!r}")
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
    scenarios = SMOKE if os.environ.get("SVETA_GOLDEN") == "smoke" else SCENARIOS
    fake = install(monkeypatch)
    fake.add_user("111", tz="Asia/Jerusalem")
    from sveta.core import config, fetch, llm
    # FR-38's per-user daily limit is not a property under test: 60+ scenarios on
    # Sonnet cost ~$1.6, above the production default of $1.50.
    monkeypatch.setattr(config, "DAILY_USD_LIMIT", 100.0)
    # The golden set measures tool choice, not the network: every URL "opens".
    monkeypatch.setattr(fetch, "get", lambda url: fetch.FetchResult(url=url, final_url=url, status=200,
                                                                    title="Page", summary="Summary."))
    # A connected Google with a small awkward mailbox: without it every mail and
    # calendar scenario stops at "не подключён" and proves nothing.
    from tests.golden import world
    world.install(monkeypatch)
    client = llm.client()
    scope = UserScope(user_id=1, chat_id="111", tz="Asia/Jerusalem")
    failures = []
    ran = 0
    stopped = ""
    for s in scenarios:
        try:
            r = agent.run(scope, s["message"], client=client, purpose="golden")
        except Exception as e:  # noqa: BLE001
            # A run costs real money scenario by scenario. An exhausted key or a
            # revoked one used to raise out of the loop and throw away every
            # scenario already paid for; now the run stops and still reports.
            if _is_terminal(e):
                stopped = f"{type(e).__name__}: {str(e)[:200]}"
                break
            failures.append((s["message"], [f"raised {type(e).__name__}: {str(e)[:120]}"], ""))
            ran += 1
            continue
        ran += 1
        misses = _check(s, r.tool_calls, r.reply)
        if misses:
            # The reply itself, not only the verdict: a miss on wording cannot be
            # judged without seeing what she actually said, and re-running to find
            # out costs real money (2026-09-12).
            failures.append((s["message"], misses,
                             f"{[n for n, _ in r.tool_calls]} said: {r.reply[:400]!r}"))
        print(f"{'x' if misses else '.'}", end="", flush=True)
    report = "\n".join(f"- {m!r}\n    {why}\n    {called}" for m, why, called in failures)
    if stopped:
        print(f"\nGolden stopped after {ran}/{len(scenarios)} scenarios: {stopped}\n{report}")
        pytest.fail(f"the credential gave out after {ran}/{len(scenarios)} scenarios — "
                    f"nothing is proven about the rest.\n{stopped}\n{report}")
    score = 1 - len(failures) / ran
    kind = "smoke" if scenarios is SMOKE else "full"
    print(f"\nGolden ({kind}): {score:.0%} ({ran - len(failures)}/{ran}) "
          f"on {config.AGENT_MODEL}\n{report}")
    assert score >= THRESHOLD, f"golden {score:.0%} < {THRESHOLD:.0%}\n{report}"


def test_a_dead_credential_is_recognised_before_the_run_burns_more():
    """The exact message the Anthropic API returns when a workspace's spend cap is
    reached (seen 2026-09-11). A run that keeps going past this pays nothing and
    proves nothing."""
    assert _is_terminal(Exception(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'You have reached your specified API usage limits. "
        "You will regain access on 2026-10-01 at 00:00 UTC.'}}"))
    assert _is_terminal(Exception("401 authentication_error: invalid x-api-key"))
    assert not _is_terminal(Exception("500 overloaded_error"))


def test_the_smoke_set_covers_every_class_of_failure_we_have_seen():
    """It is only worth running often if a regression cannot hide from it: each of
    these is a distinct way Svetochka has actually gone wrong."""
    tools = {t for s in SMOKE for t in s.get("expect_tools", []) + s.get("expect_tools_any", [])}
    for needed in ("note_save", "note_search", "reminder_create", "list_add",
                   "mail_search", "calendar_query", "preference_set", "notes_propose"):
        assert needed in tools, needed
    assert any(s.get("forbid_reply") for s in SMOKE)        # the wrong-hit class
    assert any(s.get("expect_reply") for s in SMOKE)        # the did-it-find-it class
    assert any(s.get("forbid_tool_prefix") for s in SMOKE)  # prompt injection


def test_a_bad_openai_key_stops_the_run_too():
    """The first OpenAI run burned every scenario against a 401 because TERMINAL
    only knew Anthropic's wording (2026-09-12)."""
    assert _is_terminal(Exception(
        'Error code: 401 - {\'error\': {\'message\': \'Incorrect API key provided: sk-proj***\', '
        '\'code\': \'invalid_api_key\'}}'))
    assert _is_terminal(Exception("429 - You exceeded your current quota"))
    assert not _is_terminal(Exception("429 - Rate limit reached, please retry"))
