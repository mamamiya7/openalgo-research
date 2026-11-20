"""Provider transport contracts; actual model acceptance is qualified separately."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from test_hands_off_plan import context, proposal

from services.agent import research_planning as planner


class Events:
    def __init__(self, values):
        self.values = iter(values)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.values)

    def close(self):
        self.closed = True


def provider(monkeypatch, values, *, model_id=7, model_name="openai/configured"):
    from services.agent import builder

    stream = Events(values)
    capture = {"calls": 0, "stream": stream}
    model = SimpleNamespace(request_params={"existing": "kept"})
    monkeypatch.setattr(
        builder,
        "resolve_model",
        lambda requested: SimpleNamespace(id=model_id, litellm_id=model_name),
    )
    monkeypatch.setattr(builder, "build_model", lambda *a, **kw: model)

    class Agent:
        def __init__(self, **kwargs):
            capture["agent"] = kwargs

        def run(self, value, **kwargs):
            capture["calls"] += 1
            capture["input"] = json.loads(value)
            capture["run"] = kwargs
            return stream

    monkeypatch.setattr(builder, "_require_agno", lambda: (Agent, None, None))
    capture["model"] = model
    return capture


def completed(value):
    return [
        SimpleNamespace(event="ModelRequestCompleted", input_tokens=45, output_tokens=90),
        SimpleNamespace(event="RunCompleted", content=json.dumps(value)),
    ]


def test_model_hypothesis_is_compiled_before_execution_and_stream_is_closed(monkeypatch):
    capture = provider(monkeypatch, completed(proposal()))
    evidence = {"planning_context": context(), "development_feedback": {"failed": "cost drag"}}
    result = planner.select_plan(evidence, 7)
    assert result["provider_called"] is True and result["selection"] == "model"
    assert result["usage"] == {"completion_calls": 1, "input_tokens": 45, "output_tokens": 90}
    assert result["domains"]["scan"]["hold_sessions"]["max"] == 8
    assert capture["input"] == evidence
    assert capture["calls"] == 1 and capture["stream"].closed
    assert capture["agent"]["tools"] == []
    assert capture["agent"]["add_history_to_context"] is False
    assert capture["model"].retries == 0
    assert capture["model"].request_params["num_retries"] == 0
    assert capture["model"].request_params["existing"] == "kept"


def test_model_stop_is_first_class_and_does_not_get_a_fallback_search(monkeypatch):
    capture = provider(monkeypatch, completed(proposal(action="stop", method=None, domains={})))
    result = planner.select_plan({"planning_context": context()}, 7)
    assert result["action"] == "stop" and "strategies" not in result
    assert capture["calls"] == 1 and capture["stream"].closed


def test_model_defer_is_explicit_and_preserves_training_scope(monkeypatch):
    capture = provider(monkeypatch, completed(proposal(action="defer", method=None, domains={})))
    result = planner.select_plan(
        {
            "planning_context": context(),
            "source_structure": {"signals": 68, "stocks": 5, "dates": 45},
        },
        7,
    )
    assert result["action"] == "defer" and result["decision_scope"] == "training_block"
    assert "strategies" not in result and result["domains"] == {}
    assert capture["calls"] == 1 and capture["stream"].closed


@pytest.mark.parametrize(
    "content", ["not json", '{"version":NaN}', '{"action":"search","action":"stop"}']
)
def test_invalid_json_stops_without_retry_and_releases_stream(monkeypatch, content):
    capture = provider(monkeypatch, [SimpleNamespace(event="RunCompleted", content=content)])
    with pytest.raises(ValueError, match="required research plan JSON"):
        planner.select_plan({"planning_context": context()}, 7)
    assert capture["calls"] == 1 and capture["stream"].closed


def test_out_of_contract_model_change_is_not_returned_as_executable(monkeypatch):
    capture = provider(
        monkeypatch, completed({**proposal(), "risk_limits": {"max_open_positions": 1000}})
    )
    with pytest.raises(ValueError, match="exact versioned"):
        planner.select_plan({"planning_context": context()}, 7)
    assert capture["stream"].closed


def test_provider_error_keeps_sensitive_exception_text_out_of_result(monkeypatch):
    capture = provider(
        monkeypatch, [SimpleNamespace(event="RunError", content="secret=DO_NOT_LEAK")]
    )
    with pytest.raises(ValueError) as error:
        planner.select_plan({"planning_context": context()}, 7)
    assert "DO_NOT_LEAK" not in str(error.value)
    assert capture["stream"].closed and capture["calls"] == 1


def test_raw_stream_exception_is_sanitized_and_closes_the_stream(monkeypatch):
    capture = provider(monkeypatch, [])

    def fails():
        raise RuntimeError("authorization=DO_NOT_LEAK")
        yield

    capture["stream"].values = fails()
    with pytest.raises(ValueError) as error:
        planner.select_plan({"planning_context": context()}, 7)
    assert "DO_NOT_LEAK" not in str(error.value)
    assert capture["stream"].closed


def test_oversized_context_and_changed_authority_fail_before_credentials(monkeypatch):
    from services.agent import builder

    monkeypatch.setattr(
        builder, "resolve_model", lambda *a: pytest.fail("No provider for invalid authority")
    )
    with pytest.raises(ValueError, match="context allowance"):
        planner.select_plan(
            {"planning_context": context(), "feedback": "x" * planner.MAX_PROMPT_CHARS}, 7
        )
    invalid = context()
    invalid["budget"]["proposals"] = 3
    with pytest.raises(ValueError, match="context changed"):
        planner.select_plan({"planning_context": invalid}, 7)


def test_model_response_limit_closes_stream_even_before_completion(monkeypatch):
    capture = provider(
        monkeypatch,
        [SimpleNamespace(event="RunContent", content="x" * (planner.MAX_RESPONSE_CHARS + 1))],
    )
    with pytest.raises(ValueError, match="response exceeded"):
        planner.select_plan({"planning_context": context()}, 7)
    assert capture["stream"].closed


def test_expired_subscription_fails_without_starting_a_model_run(monkeypatch):
    from services.agent import chatgpt_oauth

    capture = provider(monkeypatch, completed(proposal()), model_name="chatgpt/configured")
    monkeypatch.setattr(chatgpt_oauth, "status", lambda: {"access_token_expired": True})
    monkeypatch.setattr(
        chatgpt_oauth, "refresh_access_token", lambda: (False, "private provider details")
    )
    with pytest.raises(planner.DecisionAuthenticationExpired, match="needs sign-in"):
        planner.select_plan({"planning_context": context()}, 7)
    assert capture["calls"] == 0


def test_changed_resolved_model_is_never_silently_substituted(monkeypatch):
    capture = provider(monkeypatch, completed(proposal()), model_id=8)
    with pytest.raises(ValueError, match="model changed"):
        planner.select_plan({"planning_context": context()}, 7)
    assert capture["calls"] == 0


def test_completed_domain_rejection_is_durable_safe_and_preserves_exact_response_digest(
    monkeypatch,
):
    invalid = proposal(domains={"scan": {"hold_sessions": {"min": 2, "max": 10, "step": 2}}})
    capture = provider(monkeypatch, completed(invalid))
    with pytest.raises(planner.CompletedPlanRejection, match="outside the frozen") as caught:
        planner.select_plan({"planning_context": context()}, 7)
    receipt = caught.value.receipt
    assert capture["stream"].closed and capture["calls"] == 1
    assert receipt["version"] == "completed-plan-rejection-v1"
    assert receipt["reason_code"] == "invalid_plan"
    assert receipt["response_sha256"] == hashlib.sha256(json.dumps(invalid).encode()).hexdigest()
    assert receipt["proposal"]["domains"]["scan"]["hold_sessions"]["max"] == 10
    assert "hypothesis" not in receipt["proposal"]
    assert receipt["provenance"]["provider_completed"] is True
    assert receipt["provenance"]["stream_closed"] is True
    assert receipt["provenance"]["usage"]["completion_calls"] == 1


def test_completed_unknown_fields_are_not_copied_into_recovery_feedback(monkeypatch):
    invalid = {**proposal(), "credential": "DO_NOT_PERSIST"}
    provider(monkeypatch, completed(invalid))
    with pytest.raises(planner.CompletedPlanRejection) as caught:
        planner.select_plan({"planning_context": context()}, 7)
    assert "DO_NOT_PERSIST" not in json.dumps(caught.value.receipt)
    assert "credential" not in caught.value.receipt["proposal"]


def test_completed_invalid_json_is_known_rejection_without_copying_response_text(monkeypatch):
    provider(monkeypatch, [SimpleNamespace(event="RunCompleted", content="not json: PRIVATE")])
    with pytest.raises(planner.CompletedPlanRejection) as caught:
        planner.select_plan({"planning_context": context()}, 7)
    assert caught.value.receipt["reason_code"] == "invalid_json"
    assert caught.value.receipt["proposal"] is None
    assert "PRIVATE" not in json.dumps(caught.value.receipt)


def test_eof_without_completion_is_uncertain_even_when_fragments_look_valid(monkeypatch):
    capture = provider(
        monkeypatch, [SimpleNamespace(event="RunContent", content=json.dumps(proposal()))]
    )
    with pytest.raises(planner.PlanningResponseError) as caught:
        planner.select_plan({"planning_context": context()}, 7)
    assert not isinstance(caught.value, planner.CompletedPlanRejection)
    assert capture["stream"].closed


def test_completed_stream_close_failure_remains_uncertain_and_sanitized(monkeypatch):
    capture = provider(monkeypatch, completed({"wrong": True}))

    def broken_close():
        raise RuntimeError("DO_NOT_PERSIST")

    capture["stream"].close = broken_close
    with pytest.raises(planner.PlanningResponseError) as caught:
        planner.select_plan({"planning_context": context()}, 7)
    assert not isinstance(caught.value, planner.CompletedPlanRejection)
    assert "DO_NOT_PERSIST" not in str(caught.value)
