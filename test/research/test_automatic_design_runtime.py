"""Full automatic design orchestration with controlled native-shaped accounts.

Real seeded proposals exercise admission, capital planning and exact recovery.
The controlled economics do not qualify financial performance or native fills.
"""

from copy import deepcopy

import pytest
from test_automatic_walk_forward import execute
from test_available_history_runtime import (
    available,  # noqa: F401
    install_controlled_gate_and_account_audit,
)
from test_priority_research import PriorityAccountEngine

from research.automatic_protocol import compile_recipe
from research.automatic_research import _settings as numerical_settings
from research.available_history_protocol import entry_bins, opportunity_calendar
from research.capital_planning import settings_for
from research.connectors.optuna_portfolio import _fingerprint
from research.connectors.vectorbt_portfolio import _schedule
from research.execution_features import prepare as prepare_atr
from research.portfolio import execution_versions, normalize
from research.portfolio_execution_features import prepare as prepare_portfolio
from research.report_contract import settings_identity

pytestmark = pytest.mark.timeout(300)


@pytest.fixture(scope="module")
def designed(request):
    value = deepcopy(request.getfixturevalue("available"))
    value["portfolio"]["automatic_research"]["design_policy"] = "automatic_design_v1"
    value["portfolio"] = normalize(value["portfolio"])
    for row, canonical in zip(value["strategies"], value["portfolio"]["strategies"], strict=True):
        row.update(deepcopy(canonical))
    value["versions"] = execution_versions(value["portfolio"])
    for name in ("automatic_recipe", "execution_features", "portfolio_execution_features"):
        value.pop(name, None)
    context = value["automatic_stock_market_context"]
    value = prepare_portfolio(prepare_atr(value, context), context)
    value["automatic_recipe"] = compile_recipe(value)
    return value


class NativeShapeAccountEngine(PriorityAccountEngine):
    """Preserve controlled economics but use actual scheduled entry identities."""

    def __init__(self, value, before_account):
        super().__init__(value)
        self.before_account = before_account

    def __call__(self, rows, snapshot, capital, progress=None):
        self.before_account(rows, snapshot, capital)
        report = super().__call__(rows, snapshot, capital, progress)
        minute = snapshot["provenance"]["interval"] == "1m"
        timeline = snapshot["timeline" if minute else "sessions"]
        index = {key: position for position, key in enumerate(timeline)}
        scheduled = []
        for strategy in rows:
            for signal in strategy["signals"]:
                entry, reason = _schedule(
                    signal, snapshot, strategy["config"], timeline, index, minute
                )
                if entry >= 0 and not reason:
                    scheduled.append((signal, timeline[entry]))
        count = report["summary"]["closed_trades"]
        report["summary"]["initial_capital"] = float(capital)
        report["ledger"] = [
            {
                "status": "closed",
                "entry_date": entered[:10],
                **({"entry_timestamp": entered} if minute else {}),
                "symbol": signal["symbol"],
                "pnl": 10.0,
                "quantity": 1,
                "execution_details": {"requested_quantity": 1, "constraints": []},
            }
            for signal, entered in scheduled[:count]
        ]
        assert len(report["ledger"]) == count <= 10
        self.calls[-1].update(
            capital=float(capital),
            settings=deepcopy(report["strategies"]),
            scheduled_entry_dates=[row["entry_date"] for row in report["ledger"]],
        )
        return report


@pytest.fixture(autouse=True)
def controlled(monkeypatch):
    install_controlled_gate_and_account_audit(monkeypatch)


@pytest.fixture(scope="module")
def completed_design(request, designed):
    patch = pytest.MonkeyPatch()
    install_controlled_gate_and_account_audit(patch)
    request.addfinalizer(patch.undo)
    latest = None
    capital_freezes = []
    events = []

    def checkpoint(state):
        nonlocal latest
        latest = state

    def before_account(rows, snapshot, capital):
        if latest and latest.get("capital_planning") is not None:
            assert sum(len(fit["trials"]) for fit in latest["fits"].values()) == 408
            assert latest["selection"] is not None
            capital_freezes.append(deepcopy(latest["selection"]))

    engine = NativeShapeAccountEngine(designed, before_account)
    result, state = execute(
        designed,
        engine,
        observe=events.append,
        on_checkpoint=checkpoint,
    )
    return result, state, engine.calls, events, capital_freezes


def test_full_budget_uses_new_temporal_plan_and_rejects_inadequate_candidates(
    designed, completed_design
):
    result, state, calls, events, _ = completed_design
    recipe, findings = designed["automatic_recipe"], result["automatic_research"]
    assert recipe["history_availability"]["fit_prefixes"] == {
        "fit1": 142,
        "fit2": 148,
        "fit3": 154,
        "search": 160,
    }
    assert recipe["baseline"][0]["config"]["hold_sessions"] == 45
    assert recipe["ranges"][0]["search"]["hold_sessions"]["max"] == 82
    assert findings["counts"]["proposals"] == 408
    assert state["simulations"] == len(calls) <= state["attempts"] <= 512
    assert [event["number"] for event in events if event["kind"] == "proposal_started"] == list(
        range(408)
    )
    assert all(len(fit["trials"]) == 68 for fit in state["fits"].values())
    assert all(
        not candidate["automatic_design"]["qualified"]
        for fit in state["fits"].values()
        for candidate in fit["candidates"].values()
    )
    assert findings["headline"] == "No reliable improvement found"
    assert findings["selected_is_baseline"] and state["selection"]["is_baseline"]
    assert findings["automatic_design"]["is_baseline_reference"]
    assert findings["automatic_design"]["candidates_passing_design"] == 0
    assert findings["independently_validated"] is False
    assert findings["final"] is None and findings["checks"] == []


def test_selected_entries_are_actual_common_calendar_dates(designed, completed_design):
    from research.automatic_protocol import slice_period

    result, state, calls, _, _ = completed_design
    search = slice_period(designed, "search")
    dates = opportunity_calendar(search)
    selected = result["automatic_design"]
    assert all(row["entry_date"] in dates for row in result["ledger"])
    assert selected["calendar_bins"] == state["selection"]["automatic_design"]["calendar_bins"]
    assert [
        {key: item[key] for key in ("name", "from", "to", "sessions")}
        for item in selected["calendar_bins"]
    ] == entry_bins(dates)
    # The old controlled engine reported signal dates. The wrapper must use the
    # next actual session instead, matching native scheduling and frozen bins.
    first_signal = next(
        signal
        for signal in search["strategies"][0]["signals"]
        if not signal.get("research_exclusion")
    )
    assert result["ledger"][0]["entry_date"] > first_signal["date"]
    assert all(
        call["scheduled_entry_dates"]
        for call in calls
        if call["capital"] != designed["portfolio"]["capital"]
    )


def test_capital_planning_runs_only_after_selection_without_changing_it(designed, completed_design):
    result, state, calls, _, frozen = completed_design
    retained = state["capital_planning"]
    plan = retained["plan"]
    findings = result["automatic_research"]["capital_planning"]
    assert (
        1
        <= len(retained["accounts"])
        <= designed["automatic_recipe"]["budget"]["max_capital_planning_accounts"]
        <= 4
    )
    assert frozen and all(item == state["selection"] for item in frozen)
    assert findings["independently_validated"] is False
    assert plan["selected_config_id"] == state["selection"]["config_id"]
    assert settings_identity(result["strategies"]) == state["selection"]["config_id"]
    assert result["summary"]["initial_capital"] == designed["portfolio"]["capital"]
    for index, item in enumerate(retained["accounts"]):
        amount = plan["candidate_capitals"][index]
        expected = settings_for(
            amount, plan["baseline"], plan["selected"], original_capital=plan["original_capital"]
        )
        assert numerical_settings(item["report"]["strategies"]) == numerical_settings(expected)
        assert item["report"]["summary"]["initial_capital"] == amount
        assert item["receipt"]["capital"] == amount
    assert len(calls) == state["simulations"]


def test_completed_design_resume_runs_no_additional_accounts(designed, completed_design):
    expected, complete, _, _, _ = completed_design
    result, state = execute(
        designed, lambda *a, **k: pytest.fail("Completed resume ran an account"), saved=complete
    )
    assert result == expected and state == complete


def reseal(state):
    state["checkpoint_id"] = _fingerprint(
        {key: value for key, value in state.items() if key != "checkpoint_id"}
    )


@pytest.mark.parametrize("change", ["symbol", "pnl", "entry_date", "design_receipt"])
def test_saved_selected_ledger_and_design_tampering_fails_without_accounts(
    designed, completed_design, change
):
    _, complete, _, _, _ = completed_design
    state = deepcopy(complete)
    report = state["selected_report"]
    if change == "symbol":
        report["ledger"][0]["symbol"] = "CHANGED"
    elif change == "pnl":
        report["ledger"][0]["pnl"] += 1
    elif change == "entry_date":
        report["ledger"][0]["entry_date"] = "2026-12-31"
    else:
        report["automatic_design"]["active_bins"] += 1
    reseal(state)
    with pytest.raises(ValueError, match="changed|outside|different"):
        execute(
            designed, lambda *a, **k: pytest.fail("Tampered report ran an account"), saved=state
        )


@pytest.mark.parametrize("change", ["plan", "receipt", "report", "ledger"])
def test_saved_capital_planning_tampering_fails_without_accounts(
    designed, completed_design, change
):
    _, complete, _, _, _ = completed_design
    state = deepcopy(complete)
    retained = state["capital_planning"]
    if change == "plan":
        retained["plan"]["seed_capital"] += 1000
    elif change == "receipt":
        retained["accounts"][0]["receipt"]["capital"] += 1000
    elif change == "report":
        retained["accounts"][0]["report"]["summary"]["initial_capital"] += 1
    else:
        retained["accounts"][0]["report"]["ledger"][0]["quantity"] += 1
    reseal(state)
    with pytest.raises(ValueError, match="changed|differ|disagree"):
        execute(
            designed,
            lambda *a, **k: pytest.fail("Tampered capital plan ran an account"),
            saved=state,
        )
