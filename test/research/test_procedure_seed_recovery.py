"""Real Optuna prefix recovery with explicit synthetic accounting/gate doubles.

These short tests qualify sampler provenance, replay and immutable bindings.
They do not qualify native indicator arithmetic, fills or full-procedure power.
"""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest
from calibration_runner import controlled_audit
from test_automatic_walk_forward import AccountEngine, Stopped
from test_expanded_walk_forward import synthetic_prepare_method_gate
from test_recommendation_seeds import prepared

from research.automatic_walk_forward import run
from research.development_evidence import MAX_TOTAL_BYTES, MAX_TOTAL_MARKS, build
from research.procedure_seed_calibration import seed_plan
from research.report_contract import fingerprint

pytestmark = pytest.mark.timeout(300)


class Prefix:
    """Bound one test's checkpoint, controlled accounts and real sealed shards."""

    def __init__(self, evidence):
        self.evidence = evidence
        self.engine = AccountEngine(evidence)
        self.latest = None
        self.shards = {}
        self.descriptors = {}
        self.inputs_id = fingerprint(evidence)

    def retain(self, report, *, search_identity, **context):
        binding = {"inputs_artifact": self.inputs_id, "search_identity": search_identity}
        shard = build(report, binding=binding, **context)
        key = context["evaluation_id"]
        if key in self.shards:
            assert self.shards[key] == shard
            return deepcopy(self.descriptors[key])
        size = len(json.dumps(shard, allow_nan=False).encode())
        descriptor = {
            "evaluation_id": key,
            "id": shard["id"],
            "binding": binding,
            "period": context["period"],
            "stress": context["stress"],
            "status": "retained",
            "basis_id": fingerprint(report["evaluation_basis"]),
            "marks": len(shard["curve"]),
            "bytes": size,
        }
        self.shards[key], self.descriptors[key] = shard, descriptor
        assert len(self.shards) <= 512
        assert sum(item["marks"] for item in self.descriptors.values()) <= MAX_TOTAL_MARKS
        assert sum(item["bytes"] for item in self.descriptors.values()) <= MAX_TOTAL_BYTES
        return deepcopy(descriptor)

    def prefix(self, plan, count, *, saved=None):
        def checkpoint(state, counts):
            self.latest = state
            if len(state["fits"].get("fit1", {}).get("trials", [])) >= count:
                raise Stopped()

        with (
            patch(
                "research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate
            ),
            patch("research.portfolio_constraint_evidence.assess", controlled_audit),
            pytest.raises(Stopped),
        ):
            run(
                self.evidence,
                evaluate=self.engine,
                saved=saved,
                checkpoint=checkpoint,
                progress=lambda *args: None,
                cancelled=lambda: None,
                defer_study_analysis=True,
                retain_evaluation=self.retain,
                read_evaluation=lambda key: deepcopy(self.shards.get(key)),
                calibration_seed_plan=plan,
            )
        return deepcopy(self.latest)


@pytest.fixture(scope="module")
def prefixes():
    evidence = prepared()
    original = deepcopy(evidence)
    cases = {}
    for index in (None, 0, 1, 2):
        harness = Prefix(evidence)
        declaration = None if index is None else seed_plan(index)
        first = harness.prefix(declaration, 50)
        calls_at_pause = deepcopy(harness.engine.calls)
        final = harness.prefix(declaration, 53, saved=first)
        cases[index] = (harness, first, final, calls_at_pause)
    assert evidence == original
    return evidence, cases


def test_declared_sampler_streams_change_adaptive_choices_but_not_screening(prefixes):
    evidence, cases = prefixes
    screening = evidence["automatic_recipe"]["search_seeds"]["screening_proposals_per_search"]
    assert screening < 50
    trials = {key: value[2]["fits"]["fit1"]["trials"] for key, value in cases.items()}
    assert trials[None] == trials[0]
    for index in (1, 2):
        assert trials[index][:screening] == trials[0][:screening]
        assert [row["params"] for row in trials[index][screening:]] != [
            row["params"] for row in trials[0][screening:]
        ]
    assert [row["params"] for row in trials[1][screening:]] != [
        row["params"] for row in trials[2][screening:]
    ]


@pytest.mark.parametrize("index", [None, 0, 1, 2])
def test_same_programme_resume_replays_fifty_trials_without_recalculating_accounts(prefixes, index):
    _, cases = prefixes
    harness, first, final, calls_at_pause = cases[index]
    assert len(first["fits"]["fit1"]["trials"]) == 50
    assert len(final["fits"]["fit1"]["trials"]) == 53
    assert final["fits"]["fit1"]["trials"][:50] == first["fits"]["fit1"]["trials"]
    assert harness.engine.calls[: len(calls_at_pause)] == calls_at_pause
    assert len(harness.engine.calls) - len(calls_at_pause) == (
        final["simulations"] - first["simulations"]
    )
    old_calls = {fingerprint(call) for call in calls_at_pause}
    assert not old_calls & {
        fingerprint(call) for call in harness.engine.calls[len(calls_at_pause) :]
    }
    assert final["attempts"] == final["simulations"] == len(harness.engine.calls)


def test_alternate_optimizer_metadata_records_actual_seeds_and_histories_bind_programme(prefixes):
    evidence, cases = prefixes
    bindings = []
    for index, (harness, first, _, _) in cases.items():
        optimizer = first["optimizer"]
        if index is None:
            assert "offline_calibration_seeds" not in optimizer
            assert optimizer["seed"] == 0
            assert first["binding"] == fingerprint(
                {
                    "recipe": evidence["automatic_recipe"],
                    "stock": evidence["automatic_stock_market_context"],
                }
            )
        else:
            declaration = seed_plan(index)
            assert optimizer["offline_calibration_seeds"] == declaration
            mapping = declaration["fit_sampler_seeds"]
            assert optimizer["seed"] == mapping["search"]
            assert optimizer["search_repeat_seeds"] == [
                mapping[key] for key in ("search", "search_seed1", "search_seed2")
            ]
            assert first["binding"] == fingerprint(
                {
                    "recipe": evidence["automatic_recipe"],
                    "stock": evidence["automatic_stock_market_context"],
                    "offline_calibration_seeds": declaration,
                }
            )
        assert harness.shards
        assert all(
            shard["binding"]["search_identity"] == first["binding"]
            for shard in harness.shards.values()
        )
        bindings.append(first["binding"])
    assert len(set(bindings)) == 4
    baseline_key = next(iter(cases[None][0].shards))
    assert len({case[0].shards[baseline_key]["id"] for case in cases.values()}) == 4


@pytest.mark.parametrize("replacement", [None, 0, 2])
def test_changed_programme_rejected_before_account_execution(prefixes, replacement):
    evidence, cases = prefixes
    first = cases[1][1]
    called = []
    declaration = None if replacement is None else seed_plan(replacement)
    with (
        patch("research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate),
        pytest.raises(ValueError, match="different frozen inputs"),
    ):
        run(
            evidence,
            evaluate=lambda *args, **kwargs: called.append(True),
            saved=first,
            checkpoint=lambda *args: None,
            progress=lambda *args: None,
            cancelled=lambda: None,
            calibration_seed_plan=declaration,
        )
    assert not called


@pytest.mark.parametrize("rehash", [False, True])
def test_malformed_bool_seed_rejected_before_account_execution(prefixes, rehash):
    evidence, _ = prefixes
    declaration = seed_plan(0)
    declaration["fit_sampler_seeds"]["search_seed1"] = True
    if rehash:
        declaration["id"] = fingerprint(
            {key: value for key, value in declaration.items() if key != "id"}
        )
    called = []
    with (
        patch("research.automatic_walk_forward.prepare_method_gate", synthetic_prepare_method_gate),
        pytest.raises(ValueError, match="sampler programme changed"),
    ):
        run(
            evidence,
            evaluate=lambda *args, **kwargs: called.append(True),
            checkpoint=lambda *args: None,
            progress=lambda *args: None,
            cancelled=lambda: None,
            calibration_seed_plan=declaration,
        )
    assert not called
