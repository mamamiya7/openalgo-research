"""Frozen opportunity identity reuse; synthetic accounts, no broker access.

The controller's owned account function is exercised before the first proposal,
then stopped deliberately. This isolates reuse without an expensive search.
Account economics and method membership are existing orchestration stubs, not
evidence of execution correctness or a profitable trading strategy.
"""

import inspect
from collections import Counter
from copy import deepcopy

import pytest
from test_expanded_walk_forward import synthetic_prepare_method_gate
from test_priority_research import PriorityAccountEngine
from test_turnover_integration import prepared

from research import automatic_walk_forward as controller
from research.evaluation_basis import build_evaluation_basis


pytestmark = pytest.mark.timeout(90)


@pytest.fixture(scope="module")
def completed():
    value = prepared()
    original = deepcopy(value)
    windows = {}
    created = Counter()
    opportunity_builds = Counter()
    current_builds = []
    reports = []
    caches = []
    original_slice = controller.slice_period

    def slice_period(evidence, period):
        result = original_slice(evidence, period)
        windows[period] = result
        created[period] += 1
        return result

    def basis(evidence, *, period):
        name = next((name for name, item in windows.items() if item is evidence), None)
        if name is None:
            current_builds.append(period)
        else:
            opportunity_builds[name] += 1
        return build_evaluation_basis(evidence, period=period)

    class StopAfterAccounts(Exception):
        pass

    def retain(report, *, period, settings, hypothesis, **context):
        # Keep the prepared gate open while exercising the real owned account
        # function across cache hits, stress, eviction and a rebuilt window.
        frame = inspect.currentframe()
        try:
            simulate = frame.f_back.f_locals["simulate"]
        finally:
            del frame
        owned = dict(zip(simulate.__code__.co_freevars, simulate.__closure__, strict=True))

        def check(item, name):
            assert item["research_opportunity_basis"] == build_evaluation_basis(
                windows[name], period="selection"
            )
            reports.append(item)
            resident = owned["window"].cell_contents
            window_state = dict(
                zip(resident.__code__.co_freevars, resident.__closure__, strict=True)
            )
            caches.append(
                (
                    set(window_state["windows"].cell_contents),
                    set(owned["opportunity_bases"].cell_contents),
                )
            )

        check(report, period)
        accounts = (("fit1", True), ("fit2", False), ("check1", False), ("fit1", False))
        for name, stress in accounts:
            check(simulate(settings, hypothesis, name, stress), name)
        raise StopAfterAccounts

    engine = PriorityAccountEngine(value)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(controller, "slice_period", slice_period)
        patch.setattr(controller, "build_evaluation_basis", basis)
        patch.setattr(controller, "prepare_method_gate", synthetic_prepare_method_gate)
        with pytest.raises(StopAfterAccounts):
            controller.run(
                value,
                evaluate=engine,
                checkpoint=lambda *args: None,
                progress=lambda *args: None,
                cancelled=lambda: None,
                defer_study_analysis=True,
                retain_evaluation=retain,
            )
    assert value == original
    return engine.calls, reports, created, opportunity_builds, current_builds, caches


def test_uncached_opportunity_identity_matches_every_account_without_reusing_current_basis(completed):
    calls, reports, created, builds, current, _ = completed
    assert len(reports) == len(calls) == 5
    assert len(current) == len(calls)
    assert set(current) == {"selection"}
    assert reports[0]["evaluation_basis"] != reports[1]["evaluation_basis"]
    assert reports[0]["research_opportunity_basis"] == reports[1]["research_opportunity_basis"]
    assert sum(builds.values()) < len(reports)
    assert all(builds[name] <= count for name, count in created.items())


def test_report_copies_are_isolated_and_evicted_windows_release_their_basis(completed):
    _, reports, created, builds, _, caches = completed
    assert all(
        len(window) <= 2 and len(basis) <= 2 and basis <= window for window, basis in caches
    )
    assert builds["fit1"] == created["fit1"] == 2
    first = reports[0]["research_opportunity_basis"]
    others = [report["research_opportunity_basis"] for report in reports[1:]]
    expected = deepcopy(others)
    assert all(
        other is not first and other["comparison"] is not first["comparison"] for other in others
    )
    first["comparison"]["capital"] = -1
    first["admission"]["eligible"] = -1
    assert others == expected
