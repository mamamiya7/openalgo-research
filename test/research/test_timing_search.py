"""Bounded timing choices prepare one causal union and fair frozen source cohort."""

from copy import deepcopy

import pytest

from research.data import NATIVE_CALENDAR_BASIS
from research.portfolio import normalize
from research.timing_search import (
    _seal,
    build_union,
    freeze_cohort,
    materialize,
    propose,
    validate_union,
)

DAYS = ["2026-01-05", "2026-01-06", "2026-01-07"]


def calendar():
    return {
        "sessions": DAYS,
        "session_hours": {day: {"open": "09:15", "close": "15:30"} for day in DAYS},
        "provenance": {"exchange": "NSE", "calendar_basis": NATIVE_CALENDAR_BASIS},
    }


def source(*, config=None, times=("09:15:30", "15:20:00"), minute=True, count=1):
    configs = (
        {
            "trade_horizon": "intraday",
            "hold_sessions": 1,
            "cost_bps": 5,
            "order_size_pct": 25,
            "target_pct": 50,
            "stop_pct": 20,
            **(config or {}),
        }
        if minute
        else {"hold_sessions": 1}
    )
    portfolio = normalize(
        {
            "name": "Timing qualification",
            "engine": "vectorbt",
            "capital": 10000,
            "strategies": [
                {
                    "id": f"s{index}",
                    "name": f"Strategy {index}",
                    "source_id": "a" * 32,
                    "allocation_pct": 100 / count,
                    "config": configs,
                }
                for index in range(count)
            ],
        }
    )
    rows = []
    for row in portfolio["strategies"]:
        rows.append(
            {
                **deepcopy(row),
                "signals": [
                    {
                        "symbol": "AAA",
                        "date": DAYS[0],
                        "row": i + 2,
                        **({"timestamp": f"{DAYS[0]}T{time}+05:30"} if minute else {}),
                    }
                    for i, time in enumerate(times)
                ],
            }
        )
    return {
        "portfolio": portfolio,
        "strategies": rows,
        "signals": [
            dict(signal, strategy_id=row["id"]) for row in rows for signal in row["signals"]
        ],
        "versions": {"engine": "vectorbt", "policy": "controlled native geometry"},
    }


def frozen(value, native=None):
    native = native or calendar()
    descriptor = propose(value["portfolio"], value["strategies"], native)
    plan = build_union(value["portfolio"], value["strategies"], native, descriptor)
    assert plan["status"] == "ready"
    snapshot = {
        "sessions": plan["sessions"],
        "timeline": plan["timeline"],
        "session_hours": plan["session_hours"],
        "required_timestamps": plan["required_timestamps"],
        "bars": {
            symbol: {
                slot: {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5} for slot in slots
            }
            for symbol, slots in plan["required_timestamps"].items()
        },
        "provenance": {
            "provider": "controlled-timing-test",
            "synthetic": True,
            "exchange": "NSE",
            "interval": "1m",
            "temporal_version": "minute-open-v1",
            "adjustment_basis": "synthetic",
            "calendar_basis": NATIVE_CALENDAR_BASIS,
        },
    }
    return {**value, "snapshot": snapshot, "timing_descriptor": descriptor, "timing_union": plan}


def select(value, branch="original"):
    return {row["id"]: branch for row in value["strategies"]}


def test_daily_scope_stays_daily_and_unprepared_native_calendar_stays_pending():
    value = source(minute=False)
    receipt = propose(value["portfolio"], value["strategies"], calendar())
    assert receipt["status"] == "not_applicable" and receipt["interval"] == "D"
    assert receipt["strategies"][0]["branches"] == [
        {"id": "original", "label": "Original timing", "changes": {}}
    ]
    timed = source()
    assert propose(timed["portfolio"], timed["strategies"])["status"] == "pending_calendar"


def test_native_hours_create_four_bounded_policy_branches_and_exact_original():
    value = source(config={"entry_time": None, "exit_time": None})
    before = deepcopy(value)
    receipt = propose(value["portfolio"], value["strategies"], calendar())
    branches = receipt["strategies"][0]["branches"]
    assert [branch["id"] for branch in branches] == [
        "original",
        "entry_0930",
        "entry_1015",
        "exit_1515",
    ]
    assert branches[0]["changes"] == {}
    assert all(set(branch["changes"]) <= {"entry_time", "exit_time"} for branch in branches)
    assert value == before


def test_alternatives_that_do_not_change_any_observed_entry_or_deadline_are_removed():
    value = source(config={"hold_minutes": 2}, times=("14:30:00",))
    receipt = propose(value["portfolio"], value["strategies"], calendar())
    assert receipt["status"] == "not_applicable"
    assert len(receipt["strategies"][0]["branches"]) == 1
    assert len(receipt["strategies"][0]["removed"]) == 3


def test_union_equals_native_windows_for_every_registered_branch_and_no_prices_are_used():
    from research.connectors.vectorbt_portfolio import _deadline, _schedule

    value = frozen(source(config={"hold_minutes": 2}))
    plan, descriptor = value["timing_union"], value["timing_descriptor"]
    timeline, snapshot = plan["timeline"], value["snapshot"]
    index = {slot: i for i, slot in enumerate(timeline)}
    required = set()
    original = value["strategies"][0]
    for branch in descriptor["strategies"][0]["branches"]:
        cfg = {**original["config"], **branch["changes"]}
        for signal in original["signals"]:
            entry, reason = _schedule(signal, snapshot, cfg, timeline, index, True)
            if reason or entry < 0:
                continue
            end = _deadline(entry, snapshot, cfg, timeline, True)
            required.update(timeline[entry : end + 1])
    assert set(plan["required_timestamps"]["AAA"]) == required
    assert plan["counts"]["required_candles"] == len(required)
    assert (
        validate_union(value["portfolio"], value["strategies"], calendar(), descriptor, plan)
        == plan
    )


def test_only_candidate_price_gap_is_common_excluded_but_source_rows_remain_in_every_branch():
    value = frozen(source(config={"hold_minutes": 2}))
    before = deepcopy(value)
    missing = DAYS[0] + "T10:16:00+05:30"
    del value["snapshot"]["bars"]["AAA"][missing]
    cohort = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    assert cohort["counts"]["common_excluded"] == 1
    assert cohort["counts"]["eligible"] == 1
    assert cohort["exclusions"][0]["branch_id"] == "entry_1015"
    for branch in value["timing_descriptor"]["strategies"][0]["branches"]:
        staged = materialize(value, cohort, select(value, branch["id"]))
        assert len(staged["strategies"][0]["signals"]) == 2
        assert missing in staged["strategies"][0]["signals"][0]["research_exclusion"]
        assert "research_exclusion" not in staged["strategies"][0]["signals"][1]
    assert value["strategies"] == before["strategies"]


def test_intentional_exit_clock_filter_is_branch_specific_and_never_erases_baseline_opportunity():
    value = frozen(source())
    cohort = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    assert cohort["counts"]["eligible"] == 2 and cohort["counts"]["common_excluded"] == 0
    assert cohort["branch_counts"]["s0"]["exit_1515"]["rule_filtered"] == 1
    original = materialize(value, cohort, select(value))
    exit_branch = materialize(value, cohort, select(value, "exit_1515"))
    assert original["strategies"][0]["signals"] == exit_branch["strategies"][0]["signals"]
    assert all(
        "research_exclusion" not in signal for signal in original["strategies"][0]["signals"]
    )
    from research.connectors.vectorbt_portfolio import evaluate

    baseline = evaluate(**{key: original[key] for key in ("strategies", "snapshot", "capital")})
    filtered = evaluate(**{key: exit_branch[key] for key in ("strategies", "snapshot", "capital")})
    assert baseline["summary"]["closed_trades"] == 2
    assert filtered["summary"]["closed_trades"] == 1
    assert filtered["ledger"][1]["status"] == "excluded"


def test_special_session_clocks_use_native_hours_and_finite_tail_never_fabricates_complete_data():
    value = source(
        config={"trade_horizon": "multiday", "hold_minutes": 4000, "hold_sessions": 10},
        times=("09:15:00",),
    )
    native = calendar()
    native["session_hours"][DAYS[0]] = {"open": "13:45", "close": "14:45"}
    plan = build_union(value["portfolio"], value["strategies"], native)
    assert plan["status"] == "pending_calendar"
    assert plan["required_timestamps"] == {}
    assert DAYS[0] in plan["reason"] and "AAA" in plan["reason"]


@pytest.mark.parametrize("horizon,minutes", [("intraday", 10), ("multiday", 1500)])
def test_complete_special_session_union_matches_the_native_engine_for_every_clock(horizon, minutes):
    from research.connectors.vectorbt_portfolio import _deadline, _schedule

    native = calendar()
    native["session_hours"] = {day: {"open": "13:45", "close": "14:45"} for day in DAYS}
    value = frozen(
        source(
            config={"trade_horizon": horizon, "hold_minutes": minutes},
            times=("13:45:30", "14:35:00"),
        ),
        native,
    )
    plan, snapshot = value["timing_union"], value["snapshot"]
    branches = value["timing_descriptor"]["strategies"][0]["branches"]
    assert "entry_1400" in {branch["id"] for branch in branches}
    assert all("13:45" <= slot[11:16] < "14:45" for slot in plan["timeline"])
    index = {slot: i for i, slot in enumerate(plan["timeline"])}
    for item in plan["opportunities"]:
        signal = value["strategies"][0]["signals"][item["signal_index"]]
        for branch in branches:
            cfg = {**value["strategies"][0]["config"], **branch["changes"]}
            entry, reason = _schedule(signal, snapshot, cfg, plan["timeline"], index, True)
            window = item["windows"][branch["id"]]
            if reason:
                assert window["state"] == "rule_filtered" and window["reason"] == reason
            else:
                deadline = _deadline(entry, snapshot, cfg, plan["timeline"], True)
                assert window == {
                    "state": "scheduled",
                    "entry": plan["timeline"][entry],
                    "deadline": plan["timeline"][deadline],
                }


def test_only_recorded_calendar_hours_are_frozen_and_extra_metadata_does_not_change_union():
    value = source(config={"hold_minutes": 2})
    expected = build_union(value["portfolio"], value["strategies"], calendar())
    extra = calendar()
    extra["session_hours"]["2099-01-01"] = {
        "open": "made-up",
        "close": "made-up",
        "private": "unused" * 10000,
    }
    extra["session_hours"][DAYS[0]]["unrelated"] = {"private": "unused" * 10000}
    assert build_union(value["portfolio"], value["strategies"], extra) == expected
    assert set(expected["session_hours"]) == set(DAYS)


def test_portfolio_dates_conditions_and_frozen_strategy_settings_are_bound():
    value = source()
    expected = propose(value["portfolio"], value["strategies"], calendar())
    for key, setting in (
        ("date_from", DAYS[0]),
        ("date_to", DAYS[-1]),
        ("entry_conditions", {"version": "frozen"}),
    ):
        changed = deepcopy(value)
        changed["portfolio"][key] = setting
        assert (
            propose(changed["portfolio"], changed["strategies"], calendar())["inputs_id"]
            != expected["inputs_id"]
        )
    for key, setting in (
        ("allocation_pct", 50),
        ("config", {}),
        ("search", {"hold_minutes": {"min": 1, "max": 2}}),
    ):
        changed = deepcopy(value)
        changed["portfolio"]["strategies"][0][key] = setting
        with pytest.raises(ValueError, match="frozen portfolio"):
            propose(changed["portfolio"], changed["strategies"], calendar())


def test_observation_filtered_by_every_clock_is_counted_separately_from_data_loss():
    value = frozen(source(config={"exit_time": "15:15"}))
    cohort = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    assert cohort["counts"] == {
        "eligible": 1,
        "common_excluded": 0,
        "original_excluded": 0,
        "all_rule_filtered": 1,
    }
    assert all("research_exclusion" not in signal for signal in cohort["strategies"][0]["signals"])


@pytest.mark.parametrize("count", [2, 8])
def test_independent_branches_share_one_frozen_cash_account_and_retain_all_financial_rules(count):
    value = frozen(source(config={"hold_minutes": 2}, count=count))
    before = deepcopy(value)
    cohort = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    selection = {
        row["id"]: "original" if index % 2 == 0 else "entry_0930"
        for index, row in enumerate(value["strategies"])
    }
    staged = materialize(value, cohort, selection)
    assert staged["capital"] == 10000 and len(staged["strategies"]) == count
    for original, candidate in zip(value["strategies"], staged["strategies"], strict=True):
        assert original["allocation_pct"] == candidate["allocation_pct"]
        for key, setting in original["config"].items():
            if key not in {"entry_time", "exit_time"}:
                assert candidate["config"][key] == setting
    assert value == before


def test_modified_rehashed_contracts_and_cohort_cannot_change_registered_settings_or_evidence():
    value = frozen(source(config={"hold_minutes": 2}))
    descriptor, plan = deepcopy(value["timing_descriptor"]), deepcopy(value["timing_union"])
    descriptor["strategies"][0]["branches"][1]["changes"]["order_size_pct"] = 100
    descriptor = _seal({key: item for key, item in descriptor.items() if key != "id"})
    with pytest.raises(ValueError, match="frozen inputs"):
        build_union(value["portfolio"], value["strategies"], calendar(), descriptor)
    plan["required_timestamps"]["AAA"] = []
    plan = _seal({key: item for key, item in plan.items() if key != "id"})
    with pytest.raises(ValueError, match="union changed"):
        validate_union(
            value["portfolio"], value["strategies"], calendar(), value["timing_descriptor"], plan
        )
    cohort = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    cohort["counts"]["eligible"] = 400
    cohort = _seal({key: item for key, item in cohort.items() if key != "id"})
    with pytest.raises(ValueError, match="cohort changed"):
        materialize(value, cohort, select(value))
    with pytest.raises(ValueError, match="every strategy"):
        materialize(
            value, freeze_cohort(value, value["timing_descriptor"], value["timing_union"]), {}
        )


def test_context_identity_preserves_chosen_condition_and_source_digests():
    value = source()
    original = propose(value["portfolio"], value["strategies"], calendar())
    for field in ("entry_condition", "original_csv_sha256", "original_signals_sha256"):
        changed = deepcopy(value)
        changed["strategies"][0][field] = (
            {"version": "registered-condition"} if field == "entry_condition" else "f" * 64
        )
        assert (
            propose(changed["portfolio"], changed["strategies"], calendar())["inputs_id"]
            != original["inputs_id"]
        )
    value["strategies"][0]["exchange"] = "BSE"
    with pytest.raises(ValueError, match="NSE"):
        propose(value["portfolio"], value["strategies"], calendar())


def test_native_matrix_limit_is_checked_before_materializing_a_huge_timeline(monkeypatch):
    import research.timing_search as module

    value = source()
    monkeypatch.setattr(module, "MAX_MATRIX_CELLS", 10)
    with pytest.raises(ValueError, match="matrix bound"):
        build_union(value["portfolio"], value["strategies"], calendar())


def test_no_price_complete_opportunity_blocks_calculation_and_keeps_all_evidence():
    value = frozen(source(config={"hold_minutes": 2}, times=("09:15:00",)))
    value["snapshot"]["bars"]["AAA"] = {}
    cohort = freeze_cohort(value, value["timing_descriptor"], value["timing_union"])
    assert cohort["counts"]["eligible"] == 0 and cohort["counts"]["common_excluded"] == 1
    with pytest.raises(ValueError, match="No shared price-complete"):
        materialize(value, cohort, select(value))
