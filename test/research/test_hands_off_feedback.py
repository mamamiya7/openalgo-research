"""Pure saved-checkpoint projection cases; no model, scanner or backtest runs."""

import json
from copy import deepcopy

import pytest

from research import hands_off_feedback as feedback
from research.report_contract import settings_identity


def strategy(holding=2, stop=2):
    return {
        "id": "scan",
        "name": "Saved scan",
        "allocation_pct": 100,
        "config": {
            "initial_capital": 100000,
            "hold_sessions": holding,
            "stop_pct": stop,
            "target_pct": 8,
            "order_size_pct": 3,
            "trailing_enabled": False,
            "trailing_pct": 0,
            "cost_bps": 10,
            "slippage_bps": 5,
        },
        "execution_policy": {
            "exit_mode": "percent",
            "sizing": "allocation",
            "risk_limits": {"max_open_positions": 10},
        },
    }


def account(rows, number, score=-2):
    return {
        "config_id": settings_identity(rows),
        "trial_number": number,
        "strategies": deepcopy(rows),
        "score": score,
        "summary": {
            "net_return_pct": score,
            "max_drawdown_pct": 3,
            "closed_trades": 18,
            "unfunded_pending": 4,
            "equity_curve": ["must not enter model context"],
        },
        "automatic_design": {
            "qualified": False,
            "reasons": ["Too few trades"],
            "closed_trades": 18,
            "active_bins": 1,
        },
        "research_diagnostics": {
            "exit_outcomes": {"maximum_holding": 18},
            "skipped_reasons": {"insufficient_capital": 4},
            "maximum_observed_holding_sessions": 2,
            "complete_signal_count_in_fit": 40,
        },
        "ledger": ["must not enter model context"],
    }


def append(current, rows, source="ai_domain", score=-2):
    number = len(current["trials"])
    config_id = settings_identity(rows)
    reused = config_id in current["candidates"]
    current["candidates"].setdefault(config_id, account(rows, number, score))
    current["trials"].append(
        {
            "number": number,
            "config_id": config_id,
            "state": "complete",
            "reused": reused,
            "value": current["candidates"][config_id]["score"],
            "proposal_source": source,
        }
    )


@pytest.fixture
def case():
    current = {"trials": [], "candidates": {}}
    append(current, [strategy(9)], "approved_domain", score=10)
    append(current, [strategy(10)], "approved_domain", score=9)
    for hold in (2, 4, 6):
        for stop in (2, 4):
            append(current, [strategy(hold, stop)], score=-hold - stop)
    append(current, [strategy(2, 2)])
    plan = {
        "id": "plan1",
        "action": "search",
        "method": "holding_and_exits",
        "hypothesis": "Shorter holding and exits may reduce losses",
        "reason": "Observe every complete setting, including losses",
        "domains": {
            "scan": {
                "hold_sessions": {"min": 2, "max": 6, "step": 2},
                "stop_pct": {"min": 2, "max": 4, "step": 2},
            }
        },
        "strategies": [
            {**strategy(), "search": {"hold_sessions": {"min": 2, "max": 6, "step": 2}}}
        ],
    }
    records = [{"fit": "search", "index": 2, "phase": "completed", "plan": plan}]
    return current, records


def test_every_distinct_bad_configuration_reaches_feedback_not_only_winning_screen(case):
    current, records = case
    before = deepcopy(case)
    result = feedback.build_feedback(current, records, 9)
    block = result["blocks"][0]
    assert block["completed_proposals"] == 7
    assert block["distinct_configurations"] == 6
    assert block["reused_proposals"] == 1 and block["fresh_proposals"] == 6
    assert len(block["configurations"]) == 6
    assert all(row["score"] < 0 for row in block["configurations"])
    assert block["configuration_changes"] == 5
    assert block["configurations"][0]["proposal_indices"] == [2, 8]
    assert block["configurations"][0]["proposal_sources"] == {"ai_domain": [2, 8]}
    assert {row["config_id"] for row in block["configurations"]} == {
        t["config_id"] for t in current["trials"][2:9]
    }
    for row in block["configurations"]:
        assert row["strategies"] == current["candidates"][row["config_id"]]["strategies"]
        assert row["summary"]["unfunded_pending"] == 4
        assert row["research_diagnostics"]["skipped_reasons"] == [
            {"reason": "insufficient_capital", "count": 4}
        ]
        assert row["automatic_design"]["qualified"] is False
    assert "equity_curve" not in json.dumps(result) and '"ledger"' not in json.dumps(result)
    assert case == before


def test_complete_cartesian_proof_uses_complete_configuration_and_counts_distinct_tuples(case):
    result = feedback.build_feedback(*case, 9)
    grid = result["blocks"][0]["grid"]
    assert grid["cartesian_size"] == 6
    assert grid["distinct_tested_tuples"] == 6
    assert grid["untested_count"] == 0 and grid["exhausted"] is True
    assert grid["tested_values"] == {"scan.hold_sessions": [2, 4, 6], "scan.stop_pct": [2, 4]}


def test_long_native_skipped_reasons_preserve_complete_text_counts_and_all_accounts(case):
    current, records = case
    reason = (
        "Native portfolio risk admission rejected the order because "
        + "the permitted capital allocation was unavailable. " * 4
    )
    assert 160 < len(reason) < 1000
    for candidate in current["candidates"].values():
        candidate["research_diagnostics"]["skipped_reasons"] = {
            reason: 7,
            "insufficient_capital": 4,
        }
    before = deepcopy(current)
    result = feedback.build_feedback(current, records, 9)
    rows = result["blocks"][0]["configurations"]
    assert len(rows) == 6
    for row in rows:
        assert row["research_diagnostics"]["skipped_reasons"] == [
            {"reason": reason, "count": 7},
            {"reason": "insufficient_capital", "count": 4},
        ]
        assert row["research_diagnostics"]["exit_outcomes"] == {"maximum_holding": 18}
    assert current == before
    with pytest.raises(ValueError, match="invalid field"):
        feedback._small({reason: 7})


@pytest.mark.parametrize(
    "histogram", [{"x" * 1001: 1}, {"normal reason": -1}, {"normal reason": True}]
)
def test_skipped_reason_projection_remains_bounded_and_fail_closed(case, histogram):
    current, records = case
    key = current["trials"][2]["config_id"]
    current["candidates"][key]["research_diagnostics"]["skipped_reasons"] = histogram
    with pytest.raises(ValueError, match="bounded complete text and counts"):
        feedback.build_feedback(current, records, 9)


def test_marginal_axis_coverage_does_not_prove_cartesian_exhaustion(case):
    current, records = case
    # One point from each holding value reaches every marginal axis value but
    # leaves half the two-dimensional combinations genuinely untested.
    current["trials"] = current["trials"][:2]
    for hold, stop in ((2, 2), (4, 4), (6, 2)):
        append(current, [strategy(hold, stop)])
        current["candidates"][settings_identity([strategy(hold, stop)])]["trial_number"] = min(
            2, len(current["trials"]) - 1
        )
    grid = feedback.build_feedback(current, records, 5)["blocks"][0]["grid"]
    assert grid["distinct_tested_tuples"] == 3
    assert grid["untested_count"] == 3 and grid["exhausted"] is False


def test_saved_failure_shape_reports_three_measured_configs_and_six_untested_pairs():
    # Regression shape from the read-only full-scan-feedback evidence: 28
    # proposals repeated three diagonal pairs in a nine-point model domain.
    current = {"trials": [], "candidates": {}}
    for i in range(11):
        append(current, [strategy(60 + i)], "approved_domain")
    rows = [strategy(34, 22.5), strategy(22, 15), strategy(46, 30)]
    sequence = [0, 0, 1, 0, 2, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]
    for i in sequence:
        append(current, [rows[i]], score=-i - 1)
    records = [
        {
            "fit": "search",
            "index": 11,
            "phase": "completed",
            "plan": {
                "action": "search",
                "strategies": [rows[2]],
                "domains": {
                    "scan": {
                        "hold_sessions": {"min": 22, "max": 46, "step": 12},
                        "stop_pct": {"min": 15, "max": 30, "step": 7.5},
                    }
                },
            },
        }
    ]
    block = feedback.build_feedback(current, records, 39)["blocks"][0]
    assert block["completed_proposals"] == 28
    assert block["distinct_configurations"] == 3
    assert block["reused_proposals"] == 25
    assert [len(row["proposal_indices"]) for row in block["configurations"]] == [3, 2, 23]
    assert block["grid"]["cartesian_size"] == 9
    assert block["grid"]["distinct_tested_tuples"] == 3
    assert block["grid"]["untested_count"] == 6
    assert block["grid"]["exhausted"] is False


def test_broader_approved_domain_fill_is_reported_without_claiming_it_was_the_ai_grid(case):
    current, records = case
    broader = [strategy(12, 8)]
    broader[0]["execution_policy"]["sizing"] = "stop_risk"
    append(current, broader, "approved_domain", score=-10)
    block = feedback.build_feedback(current, records, 10)["blocks"][0]
    assert block["distinct_configurations"] == 7
    assert block["grid"]["matching_configurations"] == 6
    assert block["grid"]["outside_controlled_domain"] == 1
    assert block["grid"]["exhausted"] is True
    assert 12 not in block["grid"]["controlled_tested_values"]["scan.hold_sessions"]
    assert block["configurations"][-1]["proposal_sources"] == {"approved_domain": [9]}
    assert any(
        c["path"].endswith("sizing") for c in block["configurations"][-1]["changes_from_anchor"]
    )


def test_changed_frozen_controls_cannot_prove_tested_cartesian_points(case):
    current, records = case
    changed = deepcopy(current)
    changed["trials"] = changed["trials"][:2]
    changed["candidates"] = {
        k: v for k, v in changed["candidates"].items() if v["trial_number"] < 2
    }
    for hold in (2, 4, 6):
        for stop in (2, 4):
            rows = [strategy(hold, stop)]
            rows[0]["config"]["cost_bps"] = 99
            append(changed, rows)
    grid = feedback.build_feedback(changed, records, 8)["blocks"][0]["grid"]
    assert grid["matching_configurations"] == 0
    assert grid["untested_count"] is None and grid["exhausted"] is None


def test_each_plan_block_ends_at_next_boundary_not_current_prefix(case):
    current, records = case
    second = deepcopy(records[0])
    second.update(index=5)
    second["plan"]["id"] = "plan2"
    result = feedback.build_feedback(current, [second, *records], 9)
    assert [
        (b["start"], b["end_exclusive"], b["completed_proposals"]) for b in result["blocks"]
    ] == [(2, 5, 3), (5, 9, 4)]


def test_no_future_accounts_or_later_fit_plans_leak(case):
    current, records = case
    expected = feedback.build_feedback(current, records, 9)
    append(current, [strategy(50)], score=999)
    later = {"fit": "fit3", "index": 10, "phase": "requesting", "plan": {"reason": "FUTURE SECRET"}}
    assert feedback.build_feedback(current, [*records, later], 9) == expected
    with pytest.raises(ValueError, match="different fits"):
        feedback.build_feedback(current, [*records, {**records[0], "fit": "fit2", "index": 4}], 9)


@pytest.mark.parametrize(
    "damage",
    [
        "missing_trial",
        "incomplete",
        "missing_candidate",
        "identity",
        "score",
        "future_candidate",
        "duplicate",
    ],
)
def test_missing_or_inconsistent_accounts_fail_closed(case, damage):
    current, records = deepcopy(case)
    trial = current["trials"][3]
    candidate = current["candidates"][trial["config_id"]]
    if damage == "missing_trial":
        current["trials"].pop(3)
    elif damage == "incomplete":
        trial["state"] = "running"
    elif damage == "missing_candidate":
        current["candidates"].pop(trial["config_id"])
    elif damage == "identity":
        candidate["strategies"][0]["config"]["hold_sessions"] = 999
    elif damage == "score":
        trial["value"] = 999
    elif damage == "future_candidate":
        candidate["trial_number"] = 10
    elif damage == "duplicate":
        current["trials"].append(deepcopy(trial))
    with pytest.raises(ValueError):
        feedback.build_feedback(current, records, 9)


def test_defer_reports_all_native_search_outcomes_without_claiming_an_ai_grid(case):
    current, records = case
    records[0]["plan"] = {
        "action": "defer",
        "method": None,
        "domains": {},
        "reason": "Keep approved search",
    }
    block = feedback.build_feedback(current, records, 9)["blocks"][0]
    assert len(block["configurations"]) == 6
    assert block["grid"]["proof"] == "not_a_frozen_numeric_search"
    assert block["grid"]["exhausted"] is None
    assert block["configuration_changes"] is None


def test_oversized_feedback_keeps_every_outcome_and_points_to_exact_saved_configs(
    case, monkeypatch
):
    current, records = case
    # A legal bounded policy field makes repeated complete settings expensive;
    # all actual outcomes still fit when repeated settings use saved references.
    for row in current["candidates"].values():
        row["strategies"][0]["name"] = "Saved scan " + "x" * 600
    records[0]["plan"]["strategies"][0]["name"] = "Saved scan " + "x" * 600
    remap = {}
    for row in current["candidates"].values():
        old = row["config_id"]
        row["config_id"] = settings_identity(row["strategies"])
        remap[old] = row["config_id"]
    current["candidates"] = {r["config_id"]: r for r in current["candidates"].values()}
    for trial in current["trials"]:
        trial["config_id"] = remap[trial["config_id"]]
    full = feedback.build_feedback(current, records, 9)
    full_size = len(json.dumps(full, separators=(",", ":")).encode())
    monkeypatch.setattr(feedback, "MAX_BYTES", full_size - 500)
    with pytest.raises(ValueError, match="saved checkpoint/artifact"):
        feedback.build_feedback(current, records, 9)
    current["feedback_evidence_ref"] = {
        "checkpoint": "a" * 64,
        "fit": "search",
        "path": "calculation.fits.search",
    }
    compact = feedback.build_feedback(current, records, 9)
    assert compact["rows_complete"] is True and compact["omitted_rows"] == 0
    assert compact["configuration_details_complete"] is False
    actual = compact["blocks"][0]["configurations"]
    assert len(actual) == 6
    assert [r["summary"] for r in actual] == [
        r["summary"] for r in full["blocks"][0]["configurations"]
    ]
    assert compact["blocks"][0]["configuration_encoding"] == "anchor_indexed_changes"
    assert all(r["configuration_ref"]["config_id"] in current["candidates"] for r in actual)
    assert len(json.dumps(compact, separators=(",", ":")).encode()) <= feedback.MAX_BYTES


def test_unbounded_input_fails_without_dropping_bad_results(case, monkeypatch):
    current, records = case
    current["feedback_evidence_ref"] = {"checkpoint": "a" * 64}
    monkeypatch.setattr(feedback, "MAX_BYTES", 100)
    with pytest.raises(ValueError, match="aggregate exceeds"):
        feedback.build_feedback(current, records, 9)


def test_realistic_long_reason_history_keeps_every_outcome_and_lossless_measured_anchor_deltas():
    current = {"trials": [], "candidates": {}}
    append(current, [strategy(60)], "approved_domain")
    append(current, [strategy(61)], "approved_domain")
    reason = "Native admission rejected this proposed order because the next trade would exceed the reviewed aggregate stop-risk exposure and allocation capacity; retain the exact per-order explanation in the checkpoint. "
    for holding in range(2, 30):
        append(current, [strategy(holding)], "approved_domain", score=-holding)
        candidate = current["candidates"][current["trials"][-1]["config_id"]]
        candidate["research_diagnostics"]["skipped_reasons"] = {
            reason + f" Native reason category {i}.": 20 - i for i in range(16)
        }
        candidate["summary"].update(
            net_pnl=-holding * 100, accepted_trades=18, skipped_trades=200, profit_factor=0.4
        )
    current["feedback_evidence_ref"] = {
        "checkpoint": "a" * 64,
        "fit": "search",
        "path": "calculation.fits.search",
    }
    records = [
        {
            "fit": "search",
            "index": 2,
            "phase": "completed",
            "plan": {
                "action": "defer",
                "method": None,
                "domains": {},
                "reason": "Continue the reviewed native search",
            },
        }
    ]
    original = deepcopy(current)
    result = feedback.build_feedback(current, records, 30)
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= feedback.MAX_BYTES
    assert result["rows_complete"] is True and result["omitted_rows"] == 0
    assert result["diagnostics_complete"] is False
    assert result["configuration_reconstructable"] is True
    block = result["blocks"][0]
    assert block["anchor_source"] == "measured_configuration"
    assert block["anchor_config_id"] == current["trials"][2]["config_id"]
    assert len(block["configurations"]) == 28
    for row in block["configurations"]:
        actual = current["candidates"][row["config_id"]]
        assert row["score"] == actual["score"]

        def metrics(value):
            return (
                dict(zip(result["metric_schemas"][value["schema"]], value["values"], strict=True))
                if "schema" in value
                else value
            )

        assert metrics(row["summary"]) == {
            k: v for k, v in actual["summary"].items() if k in feedback.SUMMARY
        }
        design = metrics(row["automatic_design"])
        assert design["qualified"] == actual["automatic_design"]["qualified"]
        assert design["closed_trades"] == actual["automatic_design"]["closed_trades"]
        assert design["active_bins"] == actual["automatic_design"]["active_bins"]
        assert [result["text_catalog"][r["text_ref"]] for r in design["reasons"]] == actual[
            "automatic_design"
        ]["reasons"]
        diagnostics = metrics(row["research_diagnostics"])
        dominant = diagnostics["skipped_reasons"][0]
        assert result["text_catalog"][dominant["text_ref"]] == reason + " Native reason category 0."
        assert dominant["count"] == 20
        assert diagnostics["additional_skipped_reasons"]["distinct_reasons"] == 15
        assert diagnostics["additional_skipped_reasons"]["total_count"] == sum(range(5, 20))
        assert diagnostics["diagnostics_complete"] is False
        assert "not zero occurrences" in result["diagnostic_omission_notice"]
        decoded = {r["id"]: deepcopy(r) for r in block["anchor_strategies"]}
        for path_index, value_index in row["configuration_changes"]:
            parts = block["configuration_paths"][path_index].split(".")
            target = decoded
            for part in parts[:-1]:
                target = target[part]
            if value_index == -1:
                del target[parts[-1]]
            else:
                target[parts[-1]] = deepcopy(block["configuration_values"][value_index])
        assert list(decoded.values()) == actual["strategies"]
        assert settings_identity(list(decoded.values())) == row["config_id"]
    assert current == original


def test_57_distinct_outcomes_are_retained_within_bound_using_shared_complete_configs():
    current = {"trials": [], "candidates": {}}
    for holding in range(2, 70):
        append(current, [strategy(holding)], "approved_domain", score=-holding)
    current["feedback_evidence_ref"] = {
        "checkpoint": "a" * 64,
        "fit": "search",
        "path": "calculation.fits.search",
    }
    records = [
        {
            "fit": "search",
            "index": 11,
            "phase": "completed",
            "plan": {
                "action": "defer",
                "domains": {},
                "reason": "Saved native outcomes",
            },
        }
    ]
    result = feedback.build_feedback(current, records, 68)
    assert result["rows_complete"] is True and result["omitted_rows"] == 0
    assert len(result["blocks"][0]["configurations"]) == 57
    assert len(json.dumps(result, separators=(",", ":")).encode()) <= feedback.MAX_BYTES
    assert {r["config_id"] for r in result["blocks"][0]["configurations"]} == {
        t["config_id"] for t in current["trials"][11:]
    }


def test_provider_prompt_guard_counts_compact_exact_evidence_before_resolving_model(monkeypatch):
    from research.engine import validate_config
    from research.hands_off_plan import build_context
    from services.agent import builder, research_planning

    row = strategy()
    row["config"] = validate_config(row["config"])
    context = build_context(
        [row],
        {"scan": {"hold_sessions": [2, 4]}},
        signal_id="a" * 64,
        style="positional",
        risk_limits=row["execution_policy"]["risk_limits"],
    )
    evidence = {
        "planning_context": context,
        "feedback": {"explanation": "Exact recorded outcome " * 20},
    }
    observed = []
    original_dumps = json.dumps

    def serialize(value, **kwargs):
        if value is evidence:
            observed.append(kwargs)
        return original_dumps(value, **kwargs)

    def model_boundary(_):
        raise RuntimeError("Local model boundary; no provider call")

    monkeypatch.setattr(research_planning.json, "dumps", serialize)
    monkeypatch.setattr(builder, "resolve_model", model_boundary)
    with pytest.raises(RuntimeError, match="Local model boundary"):
        research_planning.select_plan(evidence, 1)
    assert observed[0]["separators"] == (",", ":")
    assert (
        len(original_dumps(evidence, **observed[0])) + len(research_planning._INSTRUCTIONS)
        < research_planning.MAX_PROMPT_CHARS
    )
    evidence["feedback"]["explanation"] = "x" * research_planning.MAX_PROMPT_CHARS
    with pytest.raises(ValueError, match="context allowance"):
        research_planning.select_plan(evidence, 1)
