"""Real seeded proposals over available history; controlled account economics.

These tests qualify orchestration, exact recovery and honest evidence labels.
Native gate, holding-boundary and account numerical correctness have separate
regression suites; the favorable controlled accounts here establish no edge.
"""

from copy import deepcopy

import pytest
from test_automatic_trailing import prepared as trailing_prepared
from test_automatic_walk_forward import Stopped, execute
from test_expanded_walk_forward import synthetic_prepare_method_gate
from test_priority_research import PriorityAccountEngine

from research.automatic_protocol import compile_recipe
from research.automatic_walk_forward import run
from research.connectors.optuna_portfolio import _fingerprint
from research.execution_features import prepare as prepare_atr
from research.execution_policy import normalize_limits, normalize_policy
from research.portfolio import execution_versions, normalize
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.report_contract import fingerprint
from research.research_mandate import build as build_mandate

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(scope="module")
def available():
    value = trailing_prepared(sessions=160)
    portfolio = value["portfolio"]
    portfolio["automatic_research"] = {
        "version": "automatic-hypotheses-v16",
        "history_policy": "available_history",
    }
    limits = normalize_limits({"version": "research-risk-limits-v3"})
    portfolio["risk_limits"] = limits
    for row in portfolio["strategies"]:
        row["config"]["hold_sessions"] = 45
        row["execution_policy"] = normalize_policy(
            {
                "version": "research-execution-aware-v5",
                "risk_limits": limits,
                "allocation_mode": "original",
                "capacity_mode": "disabled",
                "correlation_mode": "prior60_positive_080",
            }
        )
    value["portfolio"] = normalize(portfolio)
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    for key in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(key, None)
    context = value["automatic_stock_market_context"]
    value = prepare_portfolio(prepare_atr(value, context), context)
    value["automatic_recipe"] = compile_recipe(value)
    assert value["automatic_recipe"]["history_availability"]["fit_prefixes"] == {
        "fit1": 103,
        "fit2": 122,
        "fit3": 141,
        "search": 160,
    }
    return value


@pytest.mark.parametrize("period", ["selection", "both"])
def test_available_history_replay_retains_search_without_inventing_final(available, period):
    from research.automatic_protocol import slice_period
    from research.portfolio_validation import partition

    original = deepcopy(available)
    earlier, later, info = partition(available, prepare_period=period)
    assert earlier == slice_period(available, "search")
    assert later is None and info["independently_validated"] is False
    assert info["train_from"] == available["automatic_recipe"]["periods"]["search"]["from"]
    assert info["train_to"] == available["automatic_recipe"]["periods"]["search"]["to"]
    assert "test_from" not in info and "test_to" not in info
    assert available == original


def test_available_history_replay_rejects_nonexistent_later_period(available):
    from research.portfolio_validation import partition

    with pytest.raises(ValueError, match="no independent later period"):
        partition(available, prepare_period="evaluation")


def install_controlled_gate_and_account_audit(monkeypatch):
    monkeypatch.setattr(
        "research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate
    )

    def audit(report, features, _identity_check):
        body = {
            "version": "research-portfolio-constraint-evidence-v1",
            "status": "supported",
            "feature_id": features["id"],
            "report_evidence_id": fingerprint(
                {
                    key: report[key]
                    for key in ("summary", "equity_curve", "evaluation_basis", "strategies")
                }
            ),
            "unavailable_entries": 0,
            "scope": "caller_admitted_development_account",
            "qualification": "recorded_constraints_and_native_fills_only",
        }
        return {**body, "id": fingerprint(body)}

    monkeypatch.setattr("research.portfolio_constraint_evidence._assess", audit)


@pytest.fixture(autouse=True)
def controlled_gate_and_account_audit(monkeypatch):
    install_controlled_gate_and_account_audit(monkeypatch)


@pytest.fixture(scope="module")
def completed(request, available):
    # Module fixtures cannot consume function monkeypatch; retain the same
    # explicit test-only gate/audit setup for this one full programme.
    patch = pytest.MonkeyPatch()
    install_controlled_gate_and_account_audit(patch)
    request.addfinalizer(patch.undo)
    engine = PriorityAccountEngine(available)
    events = []
    state = None

    def checkpoint(snapshot, counts):
        nonlocal state
        state = snapshot

    result = run(
        available,
        evaluate=engine,
        checkpoint=checkpoint,
        progress=lambda *args: None,
        cancelled=lambda: None,
        admit_final=lambda *args: pytest.fail("Available history invented a final reservation"),
        defer_study_analysis=True,
        observe=events.append,
    )
    return result, state, engine.calls, events


def test_all_408_proposals_complete_without_inventing_a_final_period(available, completed):
    result, state, calls, events = completed
    findings = result["automatic_research"]
    assert findings["headline"] == "Research completed on available history"
    assert findings["status"] == "inconclusive" and findings["final"] is None
    assert findings["checks"] == [] and findings["independently_validated"] is False
    assert "walk_forward" not in findings and "validation" not in result
    assert findings["counts"]["proposals"] == 408
    assert state["simulations"] == len(calls) <= state["attempts"] <= 512
    assert set(state["fits"]) == {"fit1", "fit2", "fit3", "search", "search_seed1", "search_seed2"}
    assert all(len(value["trials"]) == 68 for value in state["fits"].values())
    assert all(call["period"] in ("fit1", "fit2", "fit3", "search") for call in calls)
    assert state["folds"] == [] and state["final_report"] is None
    assert state["baseline_final_report"] is None
    assert findings["selected_config_id"] == state["fits"]["search"]["selection"]["config_id"]
    assert [event["number"] for event in events if event["kind"] == "proposal_started"] == list(
        range(408)
    )
    assert result["portfolio"]["strategies"][0]["config"]["hold_sessions"] == 45
    assert result["automatic_recipe"] == available["automatic_recipe"]
    assert findings["recommendation_checks"]["qualified"] is False
    repeats = findings["available_history"]["seed_comparisons"]
    assert [item["sampler_seed"] for item in repeats] == [0, 1, 2]
    assert all(item["independently_validated"] is False for item in repeats)


def test_completed_resume_replays_proposals_without_more_accounts(available, completed):
    expected, state, _, _ = completed
    repeated, saved = execute(
        available,
        lambda *args, **kwargs: pytest.fail("Completed resume repeated native accounting"),
        saved=state,
    )
    assert repeated == expected and saved == state


def test_partial_seed_resume_preserves_primary_selection_and_every_proposal(available, completed):
    expected, complete, expected_calls, _ = completed
    engine = PriorityAccountEngine(available)
    _, partial = execute(
        available,
        engine,
        stop=lambda state: (
            state["active_fit"] == "search_seed1"
            and len(state["fits"]["search_seed1"]["trials"]) == 3
        ),
    )
    assert partial["selection"] is None
    resumed, state = execute(available, engine, saved=partial)
    assert resumed == expected and state == complete and engine.calls == expected_calls


@pytest.mark.parametrize("change", ["proposal", "selection", "report"])
def test_tampered_available_history_checkpoint_cannot_run_more_accounts(
    available, completed, change
):
    _, state, _, _ = completed
    changed = deepcopy(state)
    if change == "proposal":
        changed["fits"]["search_seed1"]["trials"][0]["value"] += 1
    elif change == "selection":
        changed["selection"]["config_id"] = "0" * 64
    else:
        changed["selected_report"]["summary"]["net_return_pct"] += 1
    changed["checkpoint_id"] = _fingerprint(
        {key: value for key, value in changed.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="changed|different|replay"):
        execute(
            available,
            lambda *args, **kwargs: pytest.fail("Tampered checkpoint ran an account"),
            saved=changed,
        )


def test_cancellation_remains_live_and_never_admits_final(available):
    with pytest.raises(Stopped):
        run(
            available,
            evaluate=lambda *args, **kwargs: pytest.fail("Cancelled research ran an account"),
            checkpoint=lambda *args: None,
            progress=lambda *args: None,
            cancelled=lambda: (_ for _ in ()).throw(Stopped()),
            admit_final=lambda *args: pytest.fail("Available history admitted a final period"),
            defer_study_analysis=True,
        )


def test_available_history_mandate_keeps_metadata_and_claims_no_reserved_final(available):
    mandate = build_mandate(available, inputs_artifact="a" * 64)
    assert (
        mandate["validation"]["history_availability"]
        == available["automatic_recipe"]["history_availability"]
    )
    assert mandate["validation"]["reservation_owner"] == "not_required_exploratory"
    assert mandate["validation"]["reservation_status"] == "no_independent_period_available"
    assert mandate["supported_promise"]["validated_recommendation_possible"] is False
    assert all(item["role"] == "exploratory_selection" for item in mandate["validation"]["periods"])


def test_available_history_keeps_full_attempt_allowance_without_phantom_final_reserve(available):
    reservations = []

    def reserve(attempts, completed, final_reserve, maximum):
        reservations.append((attempts, completed, final_reserve, maximum))
        raise Stopped()

    with pytest.raises(Stopped):
        run(
            available,
            evaluate=lambda *args, **kwargs: pytest.fail("Reservation must precede the account"),
            checkpoint=lambda *args: None,
            progress=lambda *args: None,
            cancelled=lambda: None,
            reserve_attempt=reserve,
            defer_study_analysis=True,
        )
    assert reservations == [(0, 0, 0, 512)]


@pytest.mark.parametrize("adaptive", [False, True])
def test_historical_copy_changes_only_for_available_history_contract(adaptive):
    from research.automatic_walk_forward import _historical_result

    value = {
        "automatic_research": {
            "status": "supported",
            "recipe": {"history_availability": {"mode": "historical_only"}} if adaptive else {},
            "final": {"supports": True, "reasons": []},
        },
        "validation": {},
        "experiment": {"findings": ["prior headline"]},
    }
    result = _historical_result(value)
    if adaptive:
        assert "New data is required" not in result["automatic_research"]["evidence_scope"]
        assert result["experiment"]["next_action"] == "Review and save the historical setup"
    else:
        assert "New data is required" in result["automatic_research"]["evidence_scope"]
        assert "use genuinely new data" in result["experiment"]["next_action"]
