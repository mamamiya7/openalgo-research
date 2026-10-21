from copy import deepcopy

import pytest
from test_portfolio_execution_features import prepare, source

from research import execution_features as atr
from research import portfolio_allocation as allocation
from research import portfolio_execution_features as features
from research.connectors.vectorbt_portfolio import evaluate
from research.execution_policy import normalize_policy
from research.portfolio_constraint_evidence import assess

pytestmark = pytest.mark.timeout(120)


def account(symbols=("AAA", "BBB", "CCC"), mode=allocation.MODE, minute=False):
    evidence, context, records = source(symbols=symbols, quantity=100000, minute=minute)
    if minute:
        snapshot = evidence["snapshot"]
        first = min(signal["date"] for row in evidence["strategies"] for signal in row["signals"])
        start = snapshot["sessions"].index(first)
        snapshot["sessions"] = snapshot["sessions"][start : start + 2]
        snapshot["session_hours"] = {
            day: snapshot["session_hours"][day] for day in snapshot["sessions"]
        }
        snapshot["timeline"] = [
            key for key in snapshot["timeline"] if key[:10] in snapshot["sessions"]
        ]
        snapshot["provenance"]["temporal_version"] = "minute-open-v1"
        snapshot["bars"] = {
            symbol: {
                key: {**deepcopy(record["evidence"]["bars"][key[:10]]), "timestamp": key}
                for key in snapshot["timeline"]
            }
            for symbol, record in context["series"].items()
        }
    for strategy in evidence["strategies"]:
        policy = strategy["execution_policy"]
        policy.update(
            version="research-execution-aware-v5", capacity_mode="disabled", allocation_mode=mode
        )
        policy["risk_limits"].update(
            max_correlated_exposure_pct=100,
            max_symbol_exposure_pct=100,
            max_trade_risk_pct=100,
            max_total_stop_risk_pct=100,
        )
        strategy["execution_policy"] = normalize_policy(policy)
        strategy["config"]["order_size_pct"] = 100
    prepared = prepare(evidence, context, records)
    attached = features.attach(atr.attach(prepared))
    return prepared, attached, context, records


def test_groups_singular_related_stocks_and_preserves_total_budget():
    prepared, _, _, _ = account()
    point = next(iter(prepared["portfolio_execution_features"]["allocation"].values()))
    assert point["clusters"] == [["AAA", "BBB"], ["CCC"]]
    assert point["weights"] == pytest.approx({"AAA": 0.25, "BBB": 0.25, "CCC": 0.5})
    assert sum(point["weights"].values()) == pytest.approx(1)


def test_genuine_fills_clip_whole_portfolio_and_audit_detects_tamper():
    prepared, attached, _, _ = account()
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert report["policy_version"] == "vectorbt-joint-causal-bars-v6"
    filled = [row for row in report["ledger"] if row["quantity"]]
    assert len(filled) == 3
    for row in filled:
        detail = row["execution_details"]
        assert row["quantity"] <= detail["allocation_symbol_capacity_shares"] + 1e-9
        assert row["quantity"] <= detail["allocation_cluster_capacity_shares"] + 1e-9
    audit = assess(report, prepared["portfolio_execution_features"])
    assert audit["status"] == "supported", audit
    changed = deepcopy(report)
    next(row for row in changed["ledger"] if row["quantity"])["execution_details"][
        "allocation_weight"
    ] = 0.99
    assert assess(changed, prepared["portfolio_execution_features"])["status"] != "supported"


def test_missing_and_constant_symbols_are_unavailable_not_zero_risk():
    prepared, attached, _, _ = account(("AAA", "EEE"))
    point = next(iter(prepared["portfolio_execution_features"]["allocation"].values()))
    assert point["unavailable"] == ["EEE"] and point["weights"] == {"AAA": 1}
    signals = attached["strategies"][0]["signals"]
    assert (
        "allocation_unavailable"
        in next(s for s in signals if s["symbol"] == "EEE")["research_exclusion"]
    )
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert not any(row["quantity"] for row in report["ledger"] if row["symbol"] == "EEE")


def test_feature_is_frozen_on_resume_and_changed_evidence_rejected():
    prepared, attached, context, _ = account()
    assert features.prepare(prepared, context) == prepared
    assert features.attach(attached) == attached
    changed = deepcopy(prepared)
    point = next(iter(changed["portfolio_execution_features"]["allocation"].values()))
    point["weights"]["AAA"] = 0.9
    with pytest.raises(ValueError):
        features.attach(changed)


def test_future_prices_cannot_change_prior_allocation():
    from test_stock_market_context import refreeze, resign

    prepared, _, context, records = account()
    day = next(iter(prepared["portfolio_execution_features"]["allocation"]))
    expected = prepared["portfolio_execution_features"]["allocation"][day]
    evidence = {
        k: deepcopy(v)
        for k, v in prepared.items()
        if k not in ("execution_features", "portfolio_execution_features")
    }
    changed = deepcopy(context)
    for record in changed["series"].values():
        for date, bar in record["evidence"]["bars"].items():
            if date >= day:
                bar.update(open=900, high=901, low=899, close=900)
        refreeze(record)
    resign(changed)
    evidence["automatic_stock_market_context"] = changed
    observed = prepare(evidence, changed, records)
    assert observed["portfolio_execution_features"]["allocation"][day] == expected


def test_original_control_does_not_apply_new_caps():
    _, attached, _, _ = account(mode="original")
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert all(
        "allocation_weight" not in row.get("execution_details", {}) for row in report["ledger"]
    )


def test_unavailable_held_symbol_fails_closed():
    prepared, _, _, _ = account()
    point = next(iter(prepared["portfolio_execution_features"]["allocation"].values()))
    with pytest.raises(ValueError, match="held symbol"):
        allocation.validate(
            point,
            symbol="AAA",
            entry_session=point["entry_session"],
            decision_at=point["entry_session"] + "T09:15:00+05:30",
            required_symbols=["MISSING"],
        )


def test_inverse_volatility_reduces_weight_of_riskier_related_symbol():
    from research.portfolio_execution_features import _returns

    prepared, _, context, _ = account(("AAA", "BBB"))
    day = next(iter(prepared["portfolio_execution_features"]["allocation"]))
    returns = {symbol: _returns(record, day) for symbol, record in context["series"].items()}
    returns["BBB"]["returns"] = [value * 2 for value in returns["BBB"]["returns"]]
    point = allocation.build(returns, day)
    assert point["weights"] == pytest.approx({"AAA": 2 / 3, "BBB": 1 / 3})


@pytest.mark.parametrize("fees,turnover", [(10, 100), (0, 10)])
def test_duplicate_buys_share_allocation_and_keep_turnover_friction_limits(fees, turnover):
    prepared, attached, _, _ = account(("AAA", "BBB"))
    for row in attached["strategies"]:
        row["signals"] *= 2
        row["config"].update(cost_bps=fees, slippage_bps=fees)
        row["execution_policy"]["risk_limits"]["max_daily_buy_turnover_pct"] = turnover
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    audit = assess(report, prepared["portfolio_execution_features"])
    assert audit["status"] == "supported", audit
    for row in report["ledger"]:
        if row["quantity"]:
            details = row["execution_details"]
            assert row["quantity"] <= details["allocation_symbol_capacity_shares"] + 1e-9
            assert row["quantity"] <= details["daily_buy_capacity_shares"] + 1e-9


@pytest.mark.parametrize("mutation", ["stale", "future", "naive", "source", "extra"])
def test_allocation_validator_rejects_resealed_boundary_changes(mutation):
    from research.portfolio import fingerprint

    prepared, _, _, _ = account()
    point = deepcopy(next(iter(prepared["portfolio_execution_features"]["allocation"].values())))
    day = point["entry_session"]
    if mutation == "stale":
        point["entry_session"] = "2020-01-01"
    elif mutation == "future":
        point["available_at"] = day + "T10:00:00+05:30"
    elif mutation == "naive":
        point["available_at"] = day + "T09:00:00"
    elif mutation == "source":
        point["source_id"] = "invalid"
    else:
        point["extra"] = True
    point["id"] = fingerprint({k: v for k, v in point.items() if k != "id"})
    with pytest.raises(ValueError):
        allocation.validate(
            point, symbol="AAA", entry_session=day, decision_at=day + "T09:15:00+05:30"
        )


def test_independent_opening_marks_reject_consistently_understated_held_exposure():
    prepared, attached, _, _ = account(("AAA", "BBB"))
    attached["strategies"][0]["signals"] *= 2
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert assess(report, prepared["portfolio_execution_features"])["status"] == "supported"
    changed = deepcopy(report)
    row = next(
        row
        for row in changed["ledger"]
        if row["quantity"] and row["execution_details"]["allocation_cluster_before"] > 0
    )
    details = row["execution_details"]
    details["allocation_cluster_before"] = 0
    details["allocation_cluster_capacity_shares"] = (
        details["opening_equity"] / row["entry_raw_price"]
    )
    result = assess(changed, prepared["portfolio_execution_features"])
    assert (
        result["status"] == "invalid"
        and "constraint_allocation_held_marks_mismatch" in result["reasons"]
    )


def test_intraday_changed_entry_clock_has_independent_frozen_marks():
    prepared, attached, _, _ = account(("AAA", "BBB"), minute=True)
    for strategy in attached["strategies"]:
        strategy["config"]["entry_time"] = "09:30"
    # Reattach from the admitted timing union after selecting another clock.
    attached = features.attach(atr.attach(attached))
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    result = assess(report, prepared["portfolio_execution_features"])
    assert result["status"] == "supported", result
    assert any(row["quantity"] for row in report["ledger"])


def test_regularized_correlation_forms_transitive_concentration_groups():
    from math import pi, sin

    from research.portfolio_execution_features import _returns

    prepared, _, context, _ = account()
    day = next(iter(prepared["portfolio_execution_features"]["allocation"]))
    records = {symbol: _returns(record, day) for symbol, record in context["series"].items()}
    for symbol, phase in zip(("AAA", "BBB", "CCC"), (0, 26, 52), strict=True):
        records[symbol]["returns"] = [
            sin(2 * pi * i / 60 + phase * pi / 180) * 0.01 for i in range(60)
        ]
    point = allocation.build(records, day)
    assert point["clusters"] == [["AAA", "BBB", "CCC"]]
    assert point["weights"] == pytest.approx({"AAA": 1 / 3, "BBB": 1 / 3, "CCC": 1 / 3})


def test_shared_volume_capacity_still_caps_new_allocation_fills():
    prepared, attached, _, _ = account(("AAA", "BBB"))
    for row in attached["strategies"]:
        row["execution_policy"]["capacity_mode"] = "prior20_broker_reported"
        row["execution_policy"]["risk_limits"]["max_daily_volume_participation_pct"] = 0.001
    attached = features.attach(attached)
    attached["strategies"][0]["signals"] *= 2
    report = evaluate(attached["strategies"], attached["snapshot"], 10000)
    assert sum(row["quantity"] for row in report["ledger"]) == 2
    assert assess(report, prepared["portfolio_execution_features"])["status"] == "supported"
    assert all(
        row["bought_shares"] == 1 for row in report["execution"]["capacity"]["daily_symbols"]
    )
