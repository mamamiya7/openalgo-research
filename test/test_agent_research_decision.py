"""State-driven research decisions without a provider or weakened finance gates."""

import json
from types import SimpleNamespace

import pytest

from services.agent import research_decision as decisions


def evidence(*remedies, missing=False):
    return {
        "status": "planning",
        "diagnosis": {"permitted_remedies": list(remedies), "missing_evidence": missing},
        "remaining_accounts": 511,
        "goal": "Preserve native validation and original settings unless a remedy qualifies",
    }


def test_native_funding_priority_does_not_claim_a_validated_result():
    chosen = decisions.native_decision(evidence("lower_turnover", "funding_and_size"))
    assert chosen["remedy"] == "funding_and_size"
    assert chosen["provider_called"] is False
    assert "validated" not in chosen
    assert decisions.native_decision(evidence("lower_turnover")) is None
    assert decisions.native_decision(evidence("lower_turnover", "temporal_stability")) is None


def test_concrete_model_choice_is_bound_to_registered_settings_and_failure_feedback():
    value = {
        **evidence("lower_turnover"),
        "experiment_options": [
            {
                "id": "a" * 64,
                "remedy": "lower_turnover",
                "change": {"hold_sessions": 23},
                "expected_effect": "Two recorded trades may change; native validation still required",
            }
        ],
    }
    chosen = {
        "remedy": "lower_turnover",
        "candidate_id": "a" * 64,
        "reason": "47 had no economic effect; test an option intersecting observed exits",
    }
    assert decisions.validate_decision(chosen, value) == chosen
    for invalid in (
        {"remedy": "lower_turnover", "reason": "Blind adjacent point"},
        {**chosen, "candidate_id": "b" * 64},
        {**chosen, "settings": {"hold_sessions": 999}},
        {**chosen, "remedy": "reduce_concentration"},
    ):
        with pytest.raises(ValueError):
            decisions.validate_decision(invalid, value)
    assert (
        decisions.validate_decision(
            {"remedy": "stop", "candidate_id": None, "reason": "No justified effect"}, value
        )["candidate_id"]
        is None
    )


def test_native_concrete_funding_pins_option_without_a_provider_call():
    value = {
        **evidence("funding_and_size"),
        "experiment_options": [{"id": "f" * 64, "remedy": "funding_and_size"}],
    }
    selected = decisions.native_decision(value)
    assert selected["candidate_id"] == "f" * 64 and selected["provider_called"] is False


def test_real_builder_pins_the_resolved_model_in_server_only_context(monkeypatch):
    from services.agent import builder
    from services.agent.tools import ToolContext

    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent"] = kwargs

    monkeypatch.setattr(builder, "_require_agno", lambda: (FakeAgent, None, None))
    monkeypatch.setattr(
        builder,
        "resolve_model",
        lambda requested: SimpleNamespace(id=7, display_name="Pinned", supports_vision=False),
    )
    monkeypatch.setattr(builder, "build_model", lambda *a, **kw: object())
    monkeypatch.setattr(builder, "session_db", lambda: None)
    monkeypatch.setattr(
        builder,
        "build_session_state",
        lambda c: {"trading_enabled": False, "analyzer_mode": False, "mcp_enabled": False},
    )
    monkeypatch.setattr(builder.prompts, "build_system_prompt", lambda **kw: "Bounded test")
    monkeypatch.setattr(builder.settings, "get_system_prompt_override", lambda: None)
    monkeypatch.setattr(builder, "_mcp_section", lambda *a: None)
    monkeypatch.setattr(builder, "tool_factory", lambda c: captured.setdefault("context", c))
    store = object()
    original = ToolContext(
        api_key="private",
        user_id="alice",
        extras={"research_store": store, "research_model_id": 99},
    )
    builder.build_agent(original, model_id=7)
    assert captured["context"].extras["research_model_id"] == 7
    assert captured["context"].extras["research_store"] is store
    assert original.extras["research_model_id"] == 99
    assert "research_model_id" not in captured["agent"]["session_state"]


@pytest.mark.parametrize("case", [evidence(), evidence("funding_and_size", missing=True)])
def test_missing_or_fixed_evidence_stops_without_a_provider(case, monkeypatch):
    from services.agent import builder

    monkeypatch.setattr(
        builder, "resolve_model", lambda *a: pytest.fail("No model for a native stop")
    )
    chosen = decisions.select_decision(case, 1)
    assert chosen["remedy"] == "stop"
    assert chosen["provider_called"] is False


@pytest.mark.parametrize(
    "value",
    [
        {"remedy": "arbitrary_code", "reason": "Run this"},
        {"remedy": "funding_and_size", "reason": "", "capital": 1000000},
        {"remedy": "funding_and_size", "reason": "Add capital"},
        {"remedy": "lower_turnover", "reason": "x" * 1001},
        {"remedy": True, "reason": "Not a named remedy"},
        {"reason": "I will run a test"},
    ],
)
def test_unregistered_and_arbitrary_decisions_cannot_be_executed(value):
    with pytest.raises(ValueError):
        decisions.validate_decision(value, evidence("lower_turnover"))


@pytest.mark.parametrize("routes", [["invented"], ["lower_turnover"] * 2, "lower_turnover", None])
def test_invalid_server_catalogue_fails_closed(routes):
    with pytest.raises(ValueError):
        decisions.native_decision({"diagnosis": {"permitted_remedies": routes}})


class Events:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def __iter__(self):
        return iter(self.rows)

    def close(self):
        self.closed = True


@pytest.fixture
def provider(monkeypatch):
    from services.agent import builder

    trace = {}
    model = SimpleNamespace(request_params={"other": "preserved"})
    resolved = SimpleNamespace(id=7, litellm_id="chatgpt/test-model")
    trace.update(model=model, resolved=resolved)

    def resolve(identifier):
        trace["requested"] = identifier
        return resolved

    class FakeAgent:
        def __init__(self, **kwargs):
            trace["agent"] = kwargs

        def run(self, prompt, **kwargs):
            trace["prompt"] = prompt
            trace["run_options"] = kwargs
            trace["runs"] = trace.get("runs", 0) + 1
            return trace["events"]

    monkeypatch.setattr(builder, "resolve_model", resolve)
    monkeypatch.setattr(builder, "build_model", lambda *a, **kw: model)
    monkeypatch.setattr(builder, "_require_agno", lambda: (FakeAgent, None, None))
    trace["events"] = Events(
        [
            SimpleNamespace(event="RunContent", content='{"remedy":"lower_'),
            SimpleNamespace(
                event="RunContent", content='turnover","reason":"Cost failure supports this test"}'
            ),
            SimpleNamespace(event="ModelRequestCompleted", input_tokens=500, output_tokens=24),
            SimpleNamespace(
                event="RunCompleted",
                content='{"remedy":"lower_turnover","reason":"Cost failure supports this test"}',
            ),
        ]
    )
    return trace


def test_one_real_provider_boundary_returns_a_validated_typed_choice_and_closes(provider):
    result = decisions.select_decision(evidence("lower_turnover"), 7)
    assert result == {
        "remedy": "lower_turnover",
        "reason": "Cost failure supports this test",
        "selection": "model",
        "provider_called": True,
        "model_id": 7,
        "model": "chatgpt/test-model",
        "usage": {"completion_calls": 1, "input_tokens": 500, "output_tokens": 24},
    }
    assert provider["requested"] == 7
    assert provider["runs"] == 1
    assert provider["agent"]["tools"] == []
    assert provider["agent"]["add_history_to_context"] is False
    assert provider["agent"]["retries"] == 0
    assert "db" not in provider["agent"]
    assert provider["model"].max_tokens == decisions.MAX_OUTPUT_TOKENS
    assert provider["model"].request_params == {
        "other": "preserved",
        "timeout": 30,
        "num_retries": 0,
    }
    assert provider["events"].closed
    assert json.loads(provider["prompt"])["diagnosis"]["permitted_remedies"] == ["lower_turnover"]


@pytest.mark.parametrize(
    "content",
    [
        "I will run it now",
        '```json\n{"remedy":"stop","reason":"No"}\n```',
        '{"remedy":"lower_turnover","reason":"Test","settings":{}}',
    ],
)
def test_prose_and_arbitrary_model_settings_fail_once_without_tool_execution(provider, content):
    provider["events"] = Events([SimpleNamespace(event="RunCompleted", content=content)])
    with pytest.raises(ValueError):
        decisions.select_decision(evidence("lower_turnover"), 7)
    assert provider["runs"] == 1
    assert provider["events"].closed


def test_response_limit_closes_stream_without_more_model_requests(provider):
    provider["events"] = Events([SimpleNamespace(event="RunContent", content="x" * 4001)])
    with pytest.raises(ValueError, match="response exceeded"):
        decisions.select_decision(evidence("lower_turnover"), 7)
    assert provider["events"].closed
    assert provider["runs"] == 1


def test_complete_evidence_and_model_row_are_checked_before_upstream(provider):
    oversized = {**evidence("lower_turnover"), "facts": "x" * decisions.MAX_PROMPT_CHARS}
    with pytest.raises(ValueError, match="context allowance"):
        decisions.select_decision(oversized, 7)
    assert "requested" not in provider
    provider["resolved"].id = 8
    with pytest.raises(ValueError, match="model changed"):
        decisions.select_decision(evidence("lower_turnover"), 7)
    assert "runs" not in provider


def test_provider_error_does_not_return_raw_error_or_retry(provider):
    provider["events"] = Events(
        [SimpleNamespace(event="RunError", content="Bearer private-credential")]
    )
    with pytest.raises(ValueError, match="did not complete") as caught:
        decisions.select_decision(evidence("lower_turnover"), 7)
    assert "private-credential" not in str(caught.value)
    assert provider["events"].closed
    assert provider["runs"] == 1


def test_execute_uses_fresh_owner_bound_permission_then_native_propose_and_advance(monkeypatch):
    from services import research_supervisor as supervisor

    calls = []
    store = object()
    monkeypatch.setattr(
        supervisor,
        "status",
        lambda *a, **kw: calls.append(("status", a, kw)) or evidence("lower_turnover"),
    )
    monkeypatch.setattr(
        supervisor, "propose", lambda *a, **kw: calls.append(("propose", a, kw)) or {}
    )
    monkeypatch.setattr(
        supervisor,
        "advance",
        lambda *a, **kw: calls.append(("advance", a, kw)) or {"status": "running"},
    )
    result = decisions.execute_plan(
        store,
        "alice",
        "plan",
        conversation_id=9,
        decision={"remedy": "lower_turnover", "reason": "Cost evidence"},
    )
    assert result["status"] == "running"
    assert [row[0] for row in calls] == ["status", "propose", "advance"]
    assert calls[1] == (
        "propose",
        (store, "alice", "plan", "lower_turnover", "Cost evidence"),
        {"conversation_id": 9},
    )
    assert all(row[2]["conversation_id"] == 9 for row in calls)


def test_obsolete_decision_and_explicit_stop_never_submit_or_open_a_new_grant(monkeypatch):
    from services import research_supervisor as supervisor

    monkeypatch.setattr(supervisor, "status", lambda *a, **kw: evidence())
    monkeypatch.setattr(supervisor, "propose", lambda *a, **kw: pytest.fail("No proposal"))
    monkeypatch.setattr(supervisor, "advance", lambda *a, **kw: pytest.fail("No advance"))
    monkeypatch.setattr(supervisor, "open_supervisor", lambda *a, **kw: pytest.fail("No new grant"))
    with pytest.raises(ValueError, match="outside"):
        decisions.execute_plan(
            object(),
            "alice",
            "plan",
            conversation_id=9,
            decision={"remedy": "lower_turnover", "reason": "Obsolete"},
        )
    stopped = decisions.execute_plan(
        object(),
        "alice",
        "plan",
        conversation_id=9,
        decision={"remedy": "stop", "reason": "No permitted remedy"},
    )
    assert stopped["explicit_stop"]


@pytest.mark.parametrize("status", ["running", "accepted", "saved", "stopped"])
def test_duplicate_completion_cannot_submit_into_active_or_terminal_plan(monkeypatch, status):
    from services import research_supervisor as supervisor

    monkeypatch.setattr(
        supervisor, "status", lambda *a, **kw: {**evidence("lower_turnover"), "status": status}
    )
    monkeypatch.setattr(
        supervisor, "propose", lambda *a, **kw: pytest.fail("No duplicate proposal")
    )
    result = decisions.execute_plan(
        object(),
        "alice",
        "plan",
        conversation_id=9,
        decision={"remedy": "lower_turnover", "reason": "Test"},
    )
    assert result["status"] == status
