"""Finite v9 admission over real seeded Optuna and synthetic account histories.

These account and membership stubs isolate orchestration, chronology and exact
recovery. Numerical feature correctness is tested by the native expanded gates;
none of the deliberately favorable synthetic returns establish a trading edge.
"""

from copy import deepcopy
from unittest.mock import patch

import pytest
from test_automatic_walk_forward import AccountEngine, execute, synthetic_prepare_gate
from test_automatic_walk_forward import prepared as legacy_prepared

from research import expanded_stock_market, joint_stock_market
from research.automatic_hypotheses import checkpoint_rows, policy_for_version
from research.automatic_protocol import (
    METHOD_VERSION,
    WALK_FORWARD_VERSION,
    automatic_portfolio,
    compile_recipe,
)
from research.automatic_walk_forward import FITS, METHOD_PROBES, _screening_program, run
from research.connectors.optuna_portfolio import _fingerprint, _prepare
from research.report_contract import fingerprint

pytestmark = pytest.mark.timeout(300)


def prepared():
    """Export a frozen expanded fixture for isolated service integration tests."""
    value = legacy_prepared()
    value["portfolio"]["automatic_research"] = {"version": METHOD_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["automatic_recipe"] = compile_recipe(value)
    return value


class SyntheticPreparedMethodGate:
    """Test-only v9 capability; never substitutes for numerical gate validation."""

    def __init__(self, bundle):
        self.source = bundle

    def apply(self, evidence, bundle, hypothesis):
        if self.source is None or self.source is not bundle:
            raise ValueError("Synthetic expanded context was released or its source changed")
        rows = expanded_stock_market.markers(evidence["strategies"], hypothesis, bundle["id"])
        counts = dict.fromkeys(
            ("allowed", "filtered", "unknown", "original_excluded", "pending"), 0
        )
        for row in rows:
            for signal in row["signals"]:
                counts["original_excluded" if signal.get("research_exclusion") else "allowed"] += 1
        receipt = {
            "version": expanded_stock_market.FEATURE_VERSION,
            "calculation_version": expanded_stock_market.FEATURE_VERSION,
            "hypothesis": deepcopy(hypothesis),
            "bundle_id": bundle["id"],
            "counts": counts,
            "execution_inputs_id": fingerprint(
                {
                    key: evidence[key]
                    for key in ("portfolio", "strategies", "signals", "snapshot", "versions")
                }
            ),
            "conditioned_strategies_id": fingerprint(rows),
        }
        return rows, {**receipt, "id": fingerprint(receipt)}

    def close(self):
        self.source = None

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()


def synthetic_prepare_method_gate(bundle):
    return SyntheticPreparedMethodGate(bundle)


@pytest.fixture(autouse=True)
def isolate_membership(monkeypatch):
    monkeypatch.setattr(
        "research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate
    )
    monkeypatch.setattr("research.automatic_walk_forward.prepare_gate", synthetic_prepare_gate)


@pytest.fixture(scope="module")
def completed():
    value = prepared()
    original = deepcopy(value)
    events = []
    latest = None

    def checkpoint(state):
        nonlocal latest
        latest = state

    def account(call):
        if call["period"].startswith("check"):
            fit = "fit" + call["period"][-1]
            assert latest["fits"][fit]["selection"] is not None
            assert len(latest["fits"][fit]["trials"]) == 48
            assert value["automatic_recipe"]["periods"][fit]["to"] < call["from"]
        if call["period"] == "final":
            assert latest["selection"] is not None
            assert len(latest["fits"]["search"]["trials"]) == 48

    engine = AccountEngine(value, on_call=account)
    with patch(
        "research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate
    ):
        result, state = execute(value, engine, observe=events.append, on_checkpoint=checkpoint)
    assert value == original
    return value, result, state, engine.calls, events


def test_every_fit_asks_new_methods_within_fixed_budget_and_keeps_final_fit_separate(completed):
    value, result, state, calls, events = completed
    assert state["version"] == result["automatic_research"]["version"] == METHOD_VERSION
    assert len(state["fits"]) == 4
    assert len(state["folds"]) == 3
    assert len(calls) == state["simulations"] <= state["attempts"] <= 320
    assert result["automatic_research"]["counts"]["proposals"] == 192
    assert len(result["experiment"]["trials"]) == 48
    assert result["experiment"]["counts"]["proposed"] == 48
    assert result["experiment"]["optimizer"]["adapter_version"] == METHOD_VERSION
    assert len(result["experiment"]["search_space"]["axes"]["hypothesis"]["choices"]) == 108
    assert {item["stock_hypothesis"] for item in calls} >= {
        identifier.removesuffix("__all") for identifier in METHOD_PROBES
    }
    legacy = value["automatic_recipe"]["hypothesis_catalog"][:92]
    _, axes = _prepare(value["strategies"])
    old_screens = _screening_program(
        WALK_FORWARD_VERSION, legacy, axes, value["automatic_recipe"]["timing_research"]
    )
    for fit in state["fits"].values():
        trials = fit["trials"]
        assert len(trials) == 48
        assert [row["params"]["hypothesis"] for row in trials[:32]] == [
            row["hypothesis"] for row in old_screens[:32]
        ]
        assert tuple(row["params"]["hypothesis"] for row in trials[32:36]) == METHOD_PROBES
        assert sum(row["phase"] == "joint_refinement" for row in trials) >= 8
        for row in fit["candidates"].values():
            if row["hypothesis"]["id"] != "none":
                assert row["strategies"][0]["entry_condition"]["version"] == (
                    expanded_stock_market.ENTRY_VERSION
                )
    started = [row for row in events if row["kind"] == "proposal_started"]
    finished = [row for row in events if row["kind"] == "proposal_finished"]
    assert len(started) == len(finished) == 192
    assert [row["number"] for row in started] == list(range(192))
    assert [row["fit_period"] for row in started] == [name for name in FITS for _ in range(48)]
    from research.study_analysis import reconstruct_study

    study, _ = reconstruct_study(result["experiment"])
    assert len(study.trials) == 48
    assert [trial.value for trial in study.trials] == [
        row["value"] for row in state["fits"]["search"]["trials"]
    ]
    assert len(study.trials[0].distributions["hypothesis"].choices) == 108


def test_pause_after_new_probe_replays_exactly_without_repeating_accounts(completed):
    value, result, complete, control_calls, _ = completed
    engine = AccountEngine(value)
    stopped, partial = execute(
        value,
        engine,
        stop=lambda state: (
            state["active_fit"] == "fit1" and len(state["fits"]["fit1"]["trials"]) == 33
        ),
    )
    assert stopped is None
    assert partial["fits"]["fit1"]["trials"][-1]["params"]["hypothesis"] == METHOD_PROBES[0]
    assert checkpoint_rows(partial) == sorted(
        partial["fits"]["fit1"]["candidates"].values(),
        key=lambda row: (-row["score"], row["config_id"]),
    )
    assert len(engine.calls) == partial["simulations"]
    resumed, state = execute(value, engine, saved=partial)
    assert resumed == result
    assert state == complete
    assert engine.calls == control_calls
    repeated, repeated_state = execute(
        value,
        lambda *args, **kwargs: pytest.fail("Completed v9 resume recalculated an account"),
        saved=state,
    )
    assert repeated == result
    assert repeated_state == state


def test_version_mismatch_and_catalogue_tampering_fail_before_accounting(completed):
    value, _, complete, _, _ = completed
    wrong_version = deepcopy(complete)
    wrong_version["version"] = WALK_FORWARD_VERSION
    wrong_version["checkpoint_id"] = _fingerprint(
        {key: item for key, item in wrong_version.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="checkpoint changed"):
        execute(
            value,
            lambda *args, **kwargs: pytest.fail("Wrong-version checkpoint ran accounting"),
            saved=wrong_version,
        )
    changed = deepcopy(value)
    changed["automatic_recipe"]["hypothesis_catalog"][-1]["stock_hypothesis"]["threshold"] = 999
    with pytest.raises(ValueError, match="recipe changed"):
        execute(changed, lambda *args, **kwargs: pytest.fail("Changed catalogue ran accounting"))


def test_targeted_admission_reserves_four_clock_probes_and_eight_refinements():
    axes = {"strategy.target_pct": {"min": 2, "max": 10, "step": 2}}
    timing = {"strategies": []}
    for index in range(4):
        identifier = f"s{index}"
        branches = [{"id": name} for name in ("original", "early", "late")]
        timing["strategies"].append({"id": identifier, "branches": branches})
        axes[f"{identifier}.timing_choice"] = {
            "type": "categorical",
            "choices": [row["id"] for row in branches],
        }
    new = _screening_program(METHOD_VERSION, expanded_stock_market.catalog(), axes, timing)
    old = _screening_program(WALK_FORWARD_VERSION, joint_stock_market.catalog(), axes, timing)
    assert len(new) == len(old) == 40
    assert new[:32] == old[:32]
    assert tuple(row["hypothesis"] for row in new[32:36]) == METHOD_PROBES
    assert new[36:] == old[32:36]
    assert all(
        row[name] == "original"
        for row in new[:36]
        for name in axes
        if name.endswith(".timing_choice")
    )
    assert 48 - len(new) == 8
    for entries in (expanded_stock_market.catalog()[:-1], expanded_stock_market.catalog() * 2):
        with pytest.raises(ValueError, match="fixed bounds"):
            _screening_program(METHOD_VERSION, entries, axes, timing)
    missing = expanded_stock_market.catalog()
    missing[92]["id"] = "unsupported_replacement"
    with pytest.raises(ValueError, match="lost a targeted method"):
        _screening_program(METHOD_VERSION, missing, axes, timing)


def test_expanded_policy_does_not_mutate_legacy_or_other_callers():
    old = policy_for_version(WALK_FORWARD_VERSION)
    new = policy_for_version(METHOD_VERSION)
    assert old["max_hypotheses"] == 92
    assert new["max_hypotheses"] == 108
    assert old["max_families"] == new["max_families"] == 32
    assert new["condition_version"] == expanded_stock_market.ENTRY_VERSION
    assert new["calculation_version"] == expanded_stock_market.FEATURE_VERSION
    new["max_hypotheses"] = 1000
    assert policy_for_version(METHOD_VERSION)["max_hypotheses"] == 108
    assert policy_for_version(WALK_FORWARD_VERSION) == old


def test_expanded_context_is_released_on_failure_and_not_prepared_after_cancellation(monkeypatch):
    contexts = []

    def prepare(bundle):
        context = SyntheticPreparedMethodGate(bundle)
        contexts.append(context)
        return context

    monkeypatch.setattr("research.automatic_walk_forward.prepare_method_gate", prepare)
    value = prepared()

    def failed_account(*args, **kwargs):
        raise RuntimeError("Isolated native account interruption")

    with pytest.raises(RuntimeError, match="account interruption"):
        execute(value, failed_account)
    assert len(contexts) == 1 and contexts[0].source is None

    def cancelled():
        raise RuntimeError("Already cancelled")

    with pytest.raises(RuntimeError, match="Already cancelled"):
        run(
            value,
            evaluate=failed_account,
            checkpoint=lambda *args: pytest.fail("Cancelled run published progress"),
            progress=lambda *args: None,
            cancelled=cancelled,
        )
    assert len(contexts) == 1


def test_legacy_v8_complete_evidence_result_and_checkpoint_remain_byte_exact():
    value = legacy_prepared()
    result, state = execute(value, AccountEngine(value))
    # Captured before generalising the runner. This covers exact v8 source,
    # seeded proposals, descriptors, account receipts, final choice and resume.
    assert fingerprint(value) == "6b94f5475b90e5ba61908229a7372e53dfcc4c4269155d0679bb61bbc1ed11b7"
    assert fingerprint(result) == "06a63d187578e94f9c10f43bf7dd6e5ab22c7b5ef637a8974c64f9249de68e94"
    assert fingerprint(state) == "97c70e4cb635e95a45cd102e5da35336f32fe89d518e081e0d0427cde64e7fad"
