"""Per-account durable reservations survive grouped evidence checkpoints."""

import json
from copy import deepcopy

import pytest
from test_automatic_decision_runtime import deciding  # noqa: F401
from test_automatic_design_runtime import designed  # noqa: F401
from test_available_history_runtime import available  # noqa: F401

from database.research_db import ResearchExperiment, ResearchJob, ResearchStore, ResearchWorker
from services.research_attempt_budget import (
    KEY,
    AttemptLeaseLost,
    attempt_reserver,
    merge_counts,
)

IDENTITY = "a" * 64
POLICY = "unchanged-full-budget-policy"


@pytest.fixture
def store(tmp_path):
    value = ResearchStore(tmp_path / "metadata")
    value.initialize()
    with value.sessions.begin() as db:
        db.get(ResearchWorker, 1).token = "worker"
        db.add(
            ResearchJob(
                id="job",
                owner="owner",
                source_id="source",
                config="{}",
                status="running",
                progress=0,
                created_at=1,
                updated_at=1,
                worker="worker",
            )
        )
        db.add(
            ResearchExperiment(
                job_id="job",
                kind="portfolio_optimize",
                specification="{}",
                identity=IDENTITY,
                counts=json.dumps({"activity": {"stage": "optimization"}}),
            )
        )
    try:
        yield value
    finally:
        value.close()


def reserver(store, attempts=0, simulations=0):
    return attempt_reserver(
        store,
        "owner",
        "job",
        "worker",
        IDENTITY,
        POLICY,
        checkpoint_attempts=attempts,
        checkpoint_simulations=simulations,
    )


def counts(store):
    with store.sessions() as db:
        return json.loads(db.get(ResearchExperiment, "job").counts)


def rewrite(store, value):
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, "job").counts = json.dumps(value)


def test_every_reservation_is_committed_and_preserves_display_counts(store):
    reserve = reserver(store)
    for attempt in range(5):
        assert reserve(attempt, attempt, 2, 512) == attempt + 1
        assert counts(store)[KEY]["attempts"] == attempt + 1
        assert counts(store)["activity"] == {"stage": "optimization"}
    assert counts(store)[KEY]["checkpoint_attempts"] == 0


def test_crash_resume_consumes_lost_reservations_instead_of_resetting_budget(store):
    reserve = reserver(store)
    for attempt in range(5):
        reserve(attempt, attempt, 2, 512)
    # No full evidence checkpoint was published. All five admissions remain
    # charged even though their calculations must be reconstructed after crash.
    resumed = reserver(store)
    assert resumed(0, 0, 2, 512) == 6
    assert resumed(6, 1, 2, 512) == 7
    assert counts(store)[KEY]["attempts"] == 7


def test_checkpoint_merge_preserves_reservations_and_advances_verified_watermark(store):
    reserve = reserver(store)
    for attempt in range(10):
        reserve(attempt, attempt, 2, 512)
    with store.sessions.begin() as db:
        receipt = db.get(ResearchExperiment, "job")
        replacement = merge_counts(
            receipt.counts,
            {"completed": 8, "total": 512},
            checkpoint_state={"attempts": 8, "simulations": 8},
        )
        receipt.counts = json.dumps(replacement)
    result = counts(store)
    assert result[KEY]["attempts"] == 10
    assert result[KEY]["checkpoint_attempts"] == 8
    assert result[KEY]["checkpoint_simulations"] == 8
    assert reserver(store, 8, 8)(8, 8, 2, 512) == 11
    assert merge_counts(result, {"activity": {"stage": "resume"}})[KEY] == result[KEY]


def test_legacy_checkpoint_migrates_once_without_losing_prior_attempts(store):
    reserve = reserver(store, 21, 20)
    assert reserve(21, 20, 2, 512) == 22
    assert counts(store)[KEY]["checkpoint_attempts"] == 21
    updated = counts(store)
    updated.pop(KEY)
    rewrite(store, updated)
    with pytest.raises(ValueError, match="disappeared"):
        reserve(22, 21, 2, 512)


def test_final_pair_reservation_and_total_cap_unchanged(store):
    reserve = reserver(store, 510, 510)
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(510, 510, 2, 512)
    assert reserve(510, 510, 0, 512) == 511
    assert reserve(511, 511, 0, 512) == 512
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(512, 512, 0, 512)


def test_lost_attempt_cap_charges_uncheckpointed_work_after_crash(store):
    reserve = reserver(store)
    for attempt in range(64):
        assert reserve(attempt, 0, 2, 512) == attempt + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserver(store)(0, 0, 2, 512)


@pytest.mark.parametrize(
    "field, value",
    [
        ("attempts", True),
        ("attempts", 513),
        ("version", "future-version"),
        ("identity", "b" * 64),
        ("policy", "changed"),
        ("limit", 511),
        ("checkpoint_attempts", 2),
        ("checkpoint_simulations", -1),
    ],
)
def test_malformed_or_rebound_journal_rejected(store, field, value):
    reserve = reserver(store)
    reserve(0, 0, 2, 512)
    modified = counts(store)
    modified[KEY][field] = value
    rewrite(store, modified)
    with pytest.raises(ValueError):
        reserve(1, 1, 2, 512)


def test_counter_and_checkpoint_cannot_roll_back_or_be_overwritten(store):
    reserve = reserver(store)
    reserve(0, 0, 2, 512)
    reserve(1, 1, 2, 512)
    existing = counts(store)
    changed = deepcopy(existing)
    changed[KEY]["attempts"] = 1
    rewrite(store, changed)
    with pytest.raises(ValueError, match="rolled back"):
        reserve(2, 2, 2, 512)
    with pytest.raises(ValueError, match="replace"):
        merge_counts(existing, changed)
    with pytest.raises(ValueError, match="Only calculation"):
        merge_counts({}, existing)
    checkpointed = merge_counts(existing, {}, checkpoint_state={"attempts": 2, "simulations": 2})
    with pytest.raises(ValueError, match="rolled back"):
        merge_counts(checkpointed, {}, checkpoint_state={"attempts": 1, "simulations": 1})
    with pytest.raises(ValueError, match="rolled back"):
        merge_counts(existing, {}, checkpoint_state={"attempts": 3, "simulations": 2})


@pytest.mark.parametrize(
    "target, field, value",
    [
        ("job", "status", "cancelling"),
        ("job", "worker", "successor"),
        ("job", "owner", "other-owner"),
        ("lease", "token", "successor"),
    ],
)
def test_every_increment_is_owner_job_and_lease_fenced(store, target, field, value):
    reserve = reserver(store)
    reserve(0, 0, 2, 512)
    with store.sessions.begin() as db:
        row = db.get(ResearchJob, "job") if target == "job" else db.get(ResearchWorker, 1)
        setattr(row, field, value)
    with pytest.raises(AttemptLeaseLost):
        reserve(1, 1, 2, 512)
    assert counts(store)[KEY]["attempts"] == 1


@pytest.mark.parametrize(
    "arguments",
    [
        (True, 0, 2, 512),
        (0, True, 2, 512),
        (0, 0, 1, 512),
        (0, 0, 2, 511),
        (0, 1, 2, 512),
    ],
)
def test_invalid_callback_accounting_never_reserves(store, arguments):
    with pytest.raises(ValueError):
        reserver(store)(*arguments)
    assert KEY not in counts(store)


@pytest.mark.parametrize("reserved", [3, 4, 5])
def test_capital_and_final_reserves_hold_back_slots_without_expanding_total(store, reserved):
    before = 512 - reserved - 1
    reserve = reserver(store, before, before)
    assert reserve(before, before, reserved, 512) == before + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(before + 1, before + 1, reserved, 512)
    # The reserved work uses the same journal, never a new budget.
    for attempt in range(before + 1, 512):
        assert reserve(attempt, attempt, 0, 512) == attempt + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(512, 512, 0, 512)
    assert counts(store)[KEY]["attempts"] == 512


@pytest.mark.parametrize("reserved", [True, 1, 6, 64, -1, 4.0])
def test_unregistered_reserves_never_create_or_change_a_budget(store, reserved):
    with pytest.raises(ValueError, match="accounting"):
        reserver(store)(0, 0, reserved, 512)
    assert KEY not in counts(store)


@pytest.mark.parametrize(
    "mode,period,expected",
    [
        ("chronological", "fit1", 2),
        ("historical_only", "search", 2),
        ("chronological", "final", 0),
        ("exploratory_only", "search", 0),
    ],
)
def test_legacy_phase_reservations_are_unchanged(mode, period, expected):
    from research.automatic_walk_forward import _account_reserve

    assert _account_reserve({"history_availability": {"mode": mode}}, period) == expected


@pytest.mark.parametrize(
    "mode,capital_accounts,period,expected",
    [
        ("historical_only", 3, "fit1", 5),
        ("historical_only", 3, "search", 5),
        ("historical_only", 3, "final", 3),
        ("exploratory_only", 4, "search", 4),
        ("historical_only", 3, "planning", 0),
        ("exploratory_only", 4, "planning", 0),
    ],
)
def test_new_phase_reserves_only_accounts_not_yet_run(mode, capital_accounts, period, expected):
    from research.automatic_walk_forward import _account_reserve

    recipe = {
        "history_availability": {"mode": mode},
        "capital_planning": {"version": "research-capital-planning-v1"},
        "budget": {"max_capital_planning_accounts": capital_accounts},
    }
    assert (
        _account_reserve(
            recipe, "search" if period == "planning" else period, planning=period == "planning"
        )
        == expected
    )


def test_strict_planning_uses_all_reserved_final_slots_and_never_account_513(store):
    from research.automatic_walk_forward import _account_reserve

    recipe = {
        "history_availability": {"mode": "historical_only"},
        "capital_planning": {"version": "research-capital-planning-v1"},
        "budget": {"max_capital_planning_accounts": 3},
    }
    reserve = reserver(store, 509, 509)
    # Final checks are complete. All three planning accounts use the same
    # durable total, including the previously unreachable accounts 511/512.
    for attempt in range(509, 512):
        held = _account_reserve(recipe, "search", planning=True)
        assert held == 0
        assert reserve(attempt, attempt, held, 512) == attempt + 1
        assert counts(store)[KEY]["attempts"] == attempt + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(512, 512, _account_reserve(recipe, "search", planning=True), 512)


def test_planning_cannot_spend_slots_without_a_recorded_planning_programme():
    from research.automatic_walk_forward import _account_reserve

    with pytest.raises(ValueError, match="recorded account reservation"):
        _account_reserve({}, "search", planning=True)


def decision_request(store):
    automatic = {
        "version": "automatic-hypotheses-v16",
        "history_policy": "available_history",
        "design_policy": "automatic_design_v1",
        "decision_policy": "automatic_decision_v1",
    }
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, "job").specification = json.dumps(
            {"portfolio": {"automatic_research": automatic}}
        )
    return automatic


def test_decision_worker_first_durable_account_holds_exact_thirteen_future_slots(store):
    from research.automatic_walk_forward import _account_reserve

    decision_request(store)
    recipe = {
        "history_availability": {"mode": "exploratory_only"},
        "capital_planning": {"version": "research-capital-planning-v2"},
        "automatic_decision": {"version": "research-automatic-decision-v1"},
        "budget": {"max_capital_planning_accounts": 4, "max_decision_accounts": 9},
    }
    reserved = _account_reserve(recipe, "fit1")
    assert reserved == 13
    reserve = reserver(store)
    assert reserve(0, 0, reserved, 512) == 1
    assert counts(store)[KEY]["attempts"] == 1
    # Preserve the actual worker's durable-journal handshake across a new
    # callback after checkpoint publication, rather than resetting attempts.
    current = counts(store)
    rewrite(store, merge_counts(current, {}, checkpoint_state={"attempts": 1, "simulations": 1}))
    assert reserver(store, 1, 1)(1, 1, reserved, 512) == 2


def test_decision_reserve_boundary_releases_only_its_own_slots_and_still_rejects_513(store):
    decision_request(store)
    reserve = reserver(store, 498, 498)
    assert reserve(498, 498, 13, 512) == 499
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(499, 499, 13, 512)
    # Nine decision accounts run with four capital slots retained.
    for attempt in range(499, 508):
        assert reserve(attempt, attempt, 4, 512) == attempt + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(508, 508, 4, 512)
    for attempt in range(508, 512):
        assert reserve(attempt, attempt, 0, 512) == attempt + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(512, 512, 0, 512)


@pytest.mark.parametrize("field", ["version", "history_policy", "design_policy", "decision_policy"])
def test_thirteen_reserved_accounts_require_the_complete_frozen_decision_request(store, field):
    automatic = decision_request(store)
    automatic.pop(field)
    with store.sessions.begin() as db:
        db.get(ResearchExperiment, "job").specification = json.dumps(
            {"portfolio": {"automatic_research": automatic}}
        )
    with pytest.raises(ValueError, match="frozen research policy"):
        reserver(store)(0, 0, 13, 512)
    assert KEY not in counts(store)


def test_thirteen_reserved_accounts_do_not_apply_to_legacy_or_lost_attempt_overflow(store):
    with pytest.raises(ValueError, match="frozen research policy"):
        reserver(store)(0, 0, 13, 512)
    assert KEY not in counts(store)
    decision_request(store)
    reserve = reserver(store)
    for attempt in range(64):
        assert reserve(attempt, 0, 13, 512) == attempt + 1
    with pytest.raises(ValueError, match="calculation limit"):
        reserve(64, 0, 13, 512)


def test_decision_service_durably_reserves_first_account_before_entering_engine(
    store, request, monkeypatch
):
    from test_batched_walk_forward_recovery import Delivery, execute

    decision_request(store)
    decision_inputs = request.getfixturevalue("deciding")
    delivery = Delivery(store, batch=True)

    class FirstAccountReached(Exception):
        pass

    def stop_after_durable_admission(*args, **kwargs):
        assert counts(store)[KEY]["attempts"] == 1
        assert delivery.reservations == [1]
        raise FirstAccountReached()

    with pytest.raises(FirstAccountReached):
        execute(decision_inputs, stop_after_durable_admission, delivery, monkeypatch)
    assert counts(store)[KEY]["attempts"] == 1
    assert counts(store)[KEY]["checkpoint_simulations"] == 0
