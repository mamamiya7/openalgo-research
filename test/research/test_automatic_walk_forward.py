"""Real seeded Optuna refits with synthetic, explicitly bounded account evidence.

Synthetic outcomes isolate chronology and recovery; they do not establish edge,
fill realism or the financial correctness of the independently tested engines.
"""

import json
from copy import deepcopy

import pytest
from test_automatic_timing import prepared as timing_prepared

from research.automatic_protocol import WALK_FORWARD_VERSION, automatic_portfolio, compile_recipe
from research.automatic_walk_forward import checkpoint_rows, run
from research.connectors.optuna_portfolio import _fingerprint, _settings
from research.joint_stock_market import FEATURE_VERSION, markers
from research.report_contract import fingerprint, settings_identity

pytestmark = pytest.mark.timeout(300)


def synthetic_gate(evidence, bundle, hypothesis):
    """Chronology-test membership stub; no indicator correctness claim.

    The first full-loop acceptance test retains the real native gate. Its
    numerical/causal behavior is also covered in the dedicated joint-gate suite.
    """
    rows = markers(evidence["strategies"], hypothesis, bundle["id"])
    counts = dict.fromkeys(("allowed", "filtered", "unknown", "original_excluded", "pending"), 0)
    for row in rows:
        for signal in row["signals"]:
            counts["original_excluded" if signal.get("research_exclusion") else "allowed"] += 1
    receipt = {
        "version": FEATURE_VERSION,
        "calculation_version": FEATURE_VERSION,
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


class SyntheticPreparedGate:
    """Test-only capability with the same scoped release contract as native gates."""

    def __init__(self, bundle):
        self.source = bundle

    def apply(self, evidence, bundle, hypothesis):
        if self.source is None or bundle is not self.source:
            raise ValueError("Synthetic prepared context was released or its source changed")
        return synthetic_gate(evidence, bundle, hypothesis)

    def close(self):
        self.source = None

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()


def synthetic_prepare_gate(bundle):
    return SyntheticPreparedGate(bundle)


@pytest.fixture(autouse=True)
def isolate_membership_for_chronology_tests(request, monkeypatch):
    if (
        request.node.name
        != "test_four_seeded_fits_use_training_only_and_freeze_before_next_account"
    ):
        monkeypatch.setattr(
            "research.automatic_walk_forward.prepare_gate", synthetic_prepare_gate, raising=False
        )


def prepared():
    value = timing_prepared()
    value["portfolio"]["automatic_research"] = {"version": WALK_FORWARD_VERSION}
    value["portfolio"] = automatic_portfolio(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["automatic_recipe"] = compile_recipe(value)
    return value


class Stopped(Exception):
    pass


class AccountEngine:
    """Deterministic synthetic accounts, with actual full marks for each window."""

    def __init__(
        self,
        value,
        *,
        final_loss=False,
        later_loss=False,
        no_edge=False,
        infeasible_top=False,
        on_call=None,
    ):
        self.recipe = value["automatic_recipe"]
        self.final_loss = final_loss
        self.later_loss = later_loss
        self.no_edge = no_edge
        self.infeasible_top = infeasible_top
        self.on_call = on_call
        self.calls = []

    def __call__(self, rows, snapshot, capital, progress=None):
        first, last = snapshot["sessions"][0], snapshot["sessions"][-1]
        period = next(
            name
            for name, bounds in self.recipe["periods"].items()
            if bounds["from"] == first and bounds["to"] == last
        )
        settings = _settings(rows)
        condition = rows[0].get("entry_condition", {}).get("hypothesis", {})
        stock = condition.get("stock_hypothesis", condition).get("id", "none")
        cfg = rows[0]["config"]
        baseline = self.recipe["baseline"][0]["config"]
        profit = 5.0
        if stock == "ema_above_10":
            profit = 30 - 2 * abs(cfg["target_pct"] - 6)
        elif stock != "none":
            profit = 2.0
        risk = 2.0
        if self.infeasible_top:
            if stock == "none" and cfg["target_pct"] != baseline["target_pct"]:
                profit, risk = 100.0, 30.0
            elif stock == "ema_above_10":
                profit = 50 - abs(cfg["target_pct"] - 6)
        if self.final_loss and period == "final" and stock != "none":
            profit = -50.0
        if self.later_loss and period in ("check1", "check2", "check3") and stock != "none":
            profit = -40.0
        if self.no_edge:
            profit = 0.0
        profit -= cfg["cost_bps"] / 100
        timeline = snapshot.get("timeline", snapshot["sessions"])
        initial = float(capital)
        low, final = initial * (1 - risk / 100), initial * (1 + profit / 100)
        peak = initial
        curve = []
        for index, key in enumerate(timeline):
            equity = (
                initial if index == 0 else low + (final - low) * (index - 1) / (len(timeline) - 2)
            )
            peak = max(peak, equity)
            point = {
                "date": key[:10],
                "equity": equity,
                "cash": equity,
                "drawdown_pct": (peak - equity) / peak * 100,
                "open_positions": 0,
            }
            if "T" in key:
                point["timestamp"] = key
            curve.append(point)
        allowed = [signal for signal in rows[0]["signals"] if not signal.get("research_exclusion")]
        closed = min(10, len(allowed))
        call = {
            "period": period,
            "settings_id": settings_identity(settings),
            "stock_hypothesis": stock,
            "from": first,
            "to": last,
        }
        self.calls.append(call)
        if self.on_call:
            self.on_call(call)
        if progress:
            progress(1, 1)
        # Native engines return numerical settings; enrich() restores the exact
        # condition descriptor after the caller has checked numerical identity.
        native_settings = [
            {key: item for key, item in row.items() if key != "entry_condition"} for row in settings
        ]
        return {
            "strategies": native_settings,
            "config": {"initial_capital": initial},
            "summary": {
                "net_return_pct": profit,
                "max_drawdown_pct": max(point["drawdown_pct"] for point in curve),
                "closed_trades": closed,
            },
            "equity_curve": curve,
            "missing_marks": [],
            "ledger": [
                {"status": "closed", "entry_date": signal["date"]} for signal in allowed[:closed]
            ],
        }


def execute(value, engine, *, saved=None, stop=None, observe=None, on_checkpoint=None):
    latest = saved

    def checkpoint(state, counts):
        nonlocal latest
        latest = state  # run() already gives the publisher an independent copy.
        if on_checkpoint:
            on_checkpoint(state)
        if stop and stop(state):
            raise Stopped()

    try:
        result = run(
            value,
            evaluate=engine,
            saved=saved,
            checkpoint=checkpoint,
            progress=lambda *args: None,
            cancelled=lambda: None,
            defer_study_analysis=True,
            observe=observe,
        )
    except Stopped:
        return None, latest
    return result, latest


def financial_selection(state):
    """Compare decisions rather than full-source identity receipts across inputs."""
    return {
        name: {
            "trials": fit["trials"],
            "config_id": fit["selection"]["config_id"],
            "strategies": fit["selection"]["strategies"],
        }
        for name, fit in state["fits"].items()
    }


@pytest.mark.timeout(600)
def test_four_seeded_fits_use_training_only_and_freeze_before_next_account():
    value = prepared()
    original = deepcopy(value)
    latest = None
    events = []

    def saved(state):
        nonlocal latest
        latest = state

    def before_account(call):
        if call["period"].startswith("check"):
            name = "fit" + call["period"][-1]
            assert latest["fits"][name]["selection"] is not None
            assert len(latest["fits"][name]["trials"]) == 48
            bounds = value["automatic_recipe"]["periods"]
            assert bounds[name]["to"] < call["from"]
        if call["period"] == "final":
            assert latest["selection"] is not None
            assert len(latest["fits"]["search"]["trials"]) == 48

    engine = AccountEngine(value, on_call=before_account)
    result, state = execute(value, engine, on_checkpoint=saved, observe=events.append)
    assert value == original
    assert len(state["fits"]) == 4
    assert all(len(fit["trials"]) == 48 for fit in state["fits"].values())
    assert result["automatic_research"]["counts"]["proposals"] == 192
    assert len(engine.calls) == state["simulations"] <= state["attempts"] <= 320
    assert len(state["folds"]) == 3
    assert result["automatic_research"]["status"] == "supported"
    assert not state["selection"]["is_baseline"]
    # Optuna table and plots describe one final 48-proposal fit, never a pooled
    # study whose objective values came from different windows.
    assert len(result["experiment"]["trials"]) == 48
    assert result["experiment"]["counts"]["proposed"] == 48
    started = [event for event in events if event["kind"] == "proposal_started"]
    finished = [event for event in events if event["kind"] == "proposal_finished"]
    assert len(started) == len(finished) == 192
    assert [event["number"] for event in started] == list(range(192))
    from research.study_analysis import reconstruct_study

    study, _ = reconstruct_study(result["experiment"])
    assert [trial.value for trial in study.trials] == [
        row["value"] for row in state["fits"]["search"]["trials"]
    ]


def test_later_outcomes_cannot_change_first_fit_proposals_or_its_frozen_choice():
    value = prepared()
    _, reference = execute(value, AccountEngine(value))
    _, changed = execute(value, AccountEngine(value, later_loss=True))
    assert financial_selection(reference)["fit1"] == financial_selection(changed)["fit1"]
    assert reference["folds"][0]["summary"] != changed["folds"][0]["summary"]
    assert not changed["folds"][0]["eligible"]
    assert changed["selection"]["is_baseline"]


def test_final_losses_do_not_rerank_or_change_any_fit_or_development_selection():
    value = prepared()
    good, reference = execute(value, AccountEngine(value))
    bad, changed = execute(value, AccountEngine(value, final_loss=True))
    assert financial_selection(reference) == financial_selection(changed)
    assert reference["selection"] == changed["selection"]
    assert good["automatic_research"]["status"] == "supported"
    assert bad["automatic_research"]["status"] == "not_supported"
    assert bad["validation"]["result"]["summary"]["net_return_pct"] < 0


def test_pause_resume_preserves_financial_result_without_repeating_completed_accounts():
    value = prepared()
    control = AccountEngine(value)
    result, complete = execute(value, control)
    partial_engine = AccountEngine(value)
    stopped, partial = execute(
        value,
        partial_engine,
        stop=lambda state: (
            state["active_fit"] == "fit2" and len(state["fits"]["fit2"]["trials"]) == 17
        ),
    )
    assert stopped is None
    before = len(partial_engine.calls)
    resumed, state = execute(value, partial_engine, saved=partial)
    assert len(partial_engine.calls) == len(control.calls)
    assert state["simulations"] == complete["simulations"]
    assert resumed == result
    assert before == partial["simulations"]
    repeated, _ = execute(
        value,
        lambda *args, **kwargs: pytest.fail("Completed resume recalculated an account"),
        saved=state,
    )
    assert repeated == result
    assert checkpoint_rows(partial) == sorted(
        partial["fits"]["fit2"]["candidates"].values(),
        key=lambda row: (-row["score"], row["config_id"]),
    )
    forged = json.loads(json.dumps(state))
    forged["selection"]["is_baseline"] = not forged["selection"]["is_baseline"]
    forged["checkpoint_id"] = _fingerprint(
        {key: item for key, item in forged.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="selection|changed|frozen"):
        execute(
            value,
            lambda *args, **kwargs: pytest.fail("Forged completed selection recalculated"),
            saved=forged,
        )
    for slot in ("selected_report", "baseline_final_report", "final_report"):
        forged = json.loads(json.dumps(state))
        forged[slot]["summary"]["net_return_pct"] += 100
        forged["checkpoint_id"] = _fingerprint(
            {key: item for key, item in forged.items() if key != "checkpoint_id"}
        )
        with pytest.raises(ValueError, match="report differs"):
            execute(
                value,
                lambda *args, **kwargs: pytest.fail("Changed report recalculated"),
                saved=forged,
            )


def test_rehashed_completed_fold_cannot_override_its_recorded_account_evidence():
    value = prepared()
    _, partial = execute(value, AccountEngine(value), stop=lambda state: len(state["folds"]) == 1)
    # Real checkpoint publication serializes JSON and does not retain Python
    # object aliases between a fold summary and its original evaluation row.
    changed = json.loads(json.dumps(partial))
    changed["folds"][0]["summary"]["net_return_pct"] += 100
    changed["checkpoint_id"] = _fingerprint(
        {key: item for key, item in changed.items() if key != "checkpoint_id"}
    )
    with pytest.raises(ValueError, match="fold|evidence|changed"):
        execute(value, AccountEngine(value), saved=changed)


def test_no_edge_retains_unchanged_reference_and_does_not_claim_a_recommendation():
    value = prepared()
    result, state = execute(value, AccountEngine(value, no_edge=True))
    assert state["selection"]["is_baseline"]
    assert result["automatic_research"]["selected_is_baseline"]
    assert result["automatic_research"]["status"] != "supported"
    assert all(fit["selection"]["hypothesis"]["id"] == "none" for fit in state["fits"].values())
    assert result["validation"]["result"]["summary"] == state["baseline_final_report"]["summary"]


def test_high_score_risk_violation_does_not_hide_a_supported_training_runner_up():
    value = prepared()
    result, state = execute(value, AccountEngine(value, infeasible_top=True))
    for fit in state["fits"].values():
        ordered = sorted(
            fit["candidates"].values(), key=lambda row: (-row["score"], row["config_id"])
        )
        assert ordered[0]["summary"]["max_drawdown_pct"] > 25
        assert fit["selection"]["config_id"] != ordered[0]["config_id"]
        assert fit["selection"]["summary"]["max_drawdown_pct"] < 25
        assert fit["selection"]["hypothesis"]["stock_hypothesis"]["id"] == "ema_above_10"
    assert result["automatic_research"]["status"] == "supported"
