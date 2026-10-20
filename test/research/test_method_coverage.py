"""Synthetic sealed metadata tests; no account replay, broker or history reads."""

import json
from copy import deepcopy

import pytest
from test_automatic_walk_forward import AccountEngine, prepared

from research.automatic_hypotheses import _screening, policy_for_version
from research.automatic_protocol import WALK_FORWARD_VERSION, slice_period
from research.automatic_timing import axes as timing_axes
from research.automatic_trailing import apply_choices
from research.automatic_trailing import axes as trailing_axes
from research.connectors.optuna_portfolio import _candidate, _prepare, _settings, _values
from research.development_evidence import plan
from research.evaluation_basis import build_evaluation_basis
from research.joint_stock_market import catalog, markers
from research.method_coverage import build
from research.report_contract import fingerprint, settings_identity
from research.research_mandate import build as build_mandate


def seal(value, key="id"):
    value.pop(key, None)
    value[key] = fingerprint(value)
    return value


def prepared_metadata(*, fits=("fit1",), proposals=1, retained=False, unknown=0):
    """Return a small, internally consistent synthetic mandate/state/index triple.

    Account metadata and content identifiers are synthetic; no shard existence or
    numerical replay is asserted. The service tests may publish this envelope as
    an owned paused job without running192 proposal accounts.
    """
    value = prepared()
    recipe = value["automatic_recipe"]
    source = "1" * 64
    mandate = build_mandate(value, inputs_artifact=source)
    binding = {
        "inputs_artifact": source,
        "search_identity": fingerprint(
            {"recipe": recipe, "stock": value["automatic_stock_market_context"]}
        ),
    }
    state = {
        "version": WALK_FORWARD_VERSION,
        "binding": binding["search_identity"],
        "optimizer": {"adapter_version": WALK_FORWARD_VERSION},
        "attempts": 0,
        "simulations": 0,
        "evaluations": {},
        "fits": {},
        "active_fit": fits[-1],
        "folds": [],
        "selection": None,
        "selected_report": None,
        "baseline_final_report": None,
        "final_report": None,
    }
    manifest = {
        "version": "research-development-evidence-manifest-v1",
        "binding": binding,
        "plan": plan(value, 320),
        "status": "collecting",
        "entries": {},
        "marks": 0,
        "bytes": 0,
        "reserved_final_excluded": True,
    }
    base, axes = _prepare(value["strategies"])
    axes.update(trailing_axes(recipe["trailing_research"]))
    axes.update(timing_axes(recipe["timing_research"]))
    screens = _screening(catalog(), axes, policy=policy_for_version(WALK_FORWARD_VERSION))
    for params in screens:
        for name, domain in axes.items():
            choices = domain["choices"] if domain.get("type") == "categorical" else _values(domain)
            params[name] = (
                "original" if name.endswith(".timing_choice") else choices[len(choices) // 2]
            )
    by_hypothesis = {row["id"]: row for row in catalog()}
    account = AccountEngine(value)
    for fit_name in fits:
        fit = {"trials": [], "candidates": {}, "selection": None}
        state["fits"][fit_name] = fit
        current = slice_period(value, fit_name)
        seen = set()
        for number in range(proposals):
            params = deepcopy(screens[number] if number < len(screens) else screens[0])
            hypothesis = by_hypothesis[params["hypothesis"]]
            materialized = apply_choices(
                _candidate(base, params), params, recipe["trailing_research"]
            )
            settings = markers(_settings(materialized), hypothesis)
            config_id = settings_identity(settings)
            if config_id not in seen:
                rows = [
                    {**row, **settings[index]} for index, row in enumerate(current["strategies"])
                ]
                report = account(rows, current["snapshot"], value["portfolio"]["capital"])
                basis = build_evaluation_basis({**current, "strategies": rows}, period="selection")
                total = sum(len(row["signals"]) for row in rows)
                missing = unknown if hypothesis["id"] != "none" else 0
                counts = {
                    "allowed": total - missing,
                    "filtered": 0,
                    "unknown": missing,
                    "original_excluded": 0,
                    "pending": 0,
                }
                condition = {
                    "hypothesis": hypothesis,
                    "bundle_id": recipe["stock_evidence_id"],
                    "gate_id": fingerprint(
                        {"synthetic": True, "fit": fit_name, "config": config_id}
                    ),
                    "counts": counts,
                }
                evaluation = {
                    "summary": report["summary"],
                    "entry_dates": len(report["ledger"]),
                    "counts": counts,
                    "automatic_stock_condition": condition,
                    "evaluation_basis": basis,
                }
                key = fingerprint(
                    {
                        "settings": settings,
                        "hypothesis": hypothesis,
                        "period": fit_name,
                        "stress": False,
                    }
                )
                descriptor = {
                    "evaluation_id": key,
                    "binding": binding,
                    "period": fit_name,
                    "stress": False,
                    "basis_id": fingerprint(basis),
                    "hypothesis_id": fingerprint(hypothesis),
                    "condition_id": fingerprint(condition),
                    "status": "retained" if retained else "unavailable",
                }
                if retained:
                    descriptor.update(
                        id="3" * 64,
                        config_id=config_id,
                        evaluation_basis_id=basis["evidence_id"],
                        inputs_artifact="4" * 64,
                        marks=5,
                        bytes=100,
                    )
                    manifest["marks"] += 5
                    manifest["bytes"] += 100
                else:
                    descriptor["reason"] = "unverified_account_history"
                manifest["entries"][key] = descriptor
                state["evaluations"][key] = {
                    **deepcopy(evaluation),
                    "development_evidence": descriptor,
                }
                fit["candidates"][config_id] = {
                    "config_id": config_id,
                    "strategies": settings,
                    "hypothesis": hypothesis,
                    "condition_label": hypothesis["label"],
                    "trial_number": number,
                    "score": report["summary"]["net_return_pct"]
                    - report["summary"]["max_drawdown_pct"],
                    "stage": "family_screening",
                    **deepcopy(evaluation),
                }
                state["simulations"] += 1
                state["attempts"] += 1
            fit["trials"].append(
                {
                    "number": number,
                    "params": params,
                    "config_id": config_id,
                    "state": "complete",
                    "reused": config_id in seen,
                    "phase": "family_screening" if number < 32 else "joint_refinement",
                    "value": fit["candidates"][config_id]["score"],
                }
            )
            seen.add(config_id)
    if not retained:
        manifest["status"] = "partial"
    return mandate, seal(state, "checkpoint_id"), seal(manifest)


metadata = prepared_metadata


def test_partial_work_does_not_call_pending_families_budget_deferred_or_verified():
    values = prepared_metadata()
    before = deepcopy(values)
    result = build(*values)
    assert values == before
    assert result["status"] == "available"
    assert result["totals"]["proposals"] == result["totals"]["evaluated"] == 1
    assert result["totals"]["unavailable_histories"] == 1
    assert result["totals"]["retained_histories"] == 0
    assert [fit["status"] for fit in result["fits"]] == ["running", "pending", "pending", "pending"]
    assert all(row["status"] != "budget_deferred" for row in result["families"])
    assert result["id"] == fingerprint({key: item for key, item in result.items() if key != "id"})


def test_all_four_fits_count_unique_accounts_and_duplicate_proposals_separately():
    result = build(
        *prepared_metadata(fits=("fit1", "fit2", "fit3", "search"), proposals=48, retained=True)
    )
    assert result["totals"]["proposals"] == 192
    assert result["totals"]["evaluated"] == 128
    assert result["totals"]["reused"] == 64
    assert result["totals"]["retained_histories"] == 128
    assert all(row["status"] == "complete" for row in result["fits"])
    assert sum(row["evaluated"] for row in result["families"]) == 128
    assert sum(row["registered"] for row in result["families"]) == 92
    assert result["totals"]["budget_deferred"] == 60


def test_unknown_entries_are_not_unsupported_methods_or_claimed_missing_bar_counts():
    result = build(*prepared_metadata(proposals=2, unknown=3))
    row = next(item for item in result["families"] if item["id"] == "ema_trend:all")
    assert row["unknown_entry_checks"] == 3
    assert row["data_status"] == "unknown_entries_reported"
    assert row["status"] == "evaluated"
    assert all(item["id"] != row["id"] for item in result["registry"]["blocked_families"])


@pytest.mark.parametrize(
    "damage",
    [
        "mandate_seal",
        "binding",
        "catalog",
        "trial",
        "candidate",
        "evaluation",
        "descriptor",
        "context",
        "totals",
    ],
)
def test_rehashed_inconsistent_metadata_fails_closed(damage):
    mandate, state, manifest = json.loads(json.dumps(prepared_metadata()))
    candidate = next(iter(state["fits"]["fit1"]["candidates"].values()))
    key = next(iter(state["evaluations"]))
    if damage == "mandate_seal":
        mandate["source"]["recipe_id"] = "9" * 64
    elif damage == "binding":
        state["binding"] = "9" * 64
    elif damage == "catalog":
        mandate["search"]["hypothesis_catalog"] = []
        seal(mandate)
    elif damage == "trial":
        state["fits"]["fit1"]["trials"][0]["params"]["scanner.target_pct"] += 1
    elif damage == "candidate":
        candidate["summary"]["net_return_pct"] += 1
    elif damage == "evaluation":
        state["evaluations"][key]["summary"]["net_return_pct"] += 1
    elif damage == "descriptor":
        manifest["entries"][key]["period"] = "final"
    elif damage == "context":
        state["evaluations"][key]["automatic_stock_condition"]["bundle_id"] = "9" * 64
    else:
        manifest["marks"] += 1
    seal(state, "checkpoint_id")
    seal(manifest)
    with pytest.raises(ValueError):
        build(mandate, state, manifest)


def test_absent_or_other_recipe_metadata_is_honestly_unavailable():
    assert build(None, None, None)["status"] == "unavailable"
    mandate, state, manifest = prepared_metadata()
    mandate["source"]["recipe_version"] = "automatic-hypotheses-v7"
    seal(mandate)
    assert build(mandate, state, manifest)["status"] == "unavailable"


def test_builder_never_replays_accounts_proposes_trials_or_reads_shards(monkeypatch):
    values = prepared_metadata()

    def forbidden(*args, **kwargs):
        pytest.fail("Coverage attempted numerical work or an artifact read")

    monkeypatch.setattr("optuna.create_study", forbidden)
    monkeypatch.setattr("research.connectors.vectorbt_portfolio.evaluate", forbidden)
    monkeypatch.setattr("services.scanner_research_service.read_artifact", forbidden)
    assert build(*values)["totals"]["evaluated"] == 1


def test_native_publication_checkpoint_before_proposal_commit_stays_pending():
    mandate, state, manifest = prepared_metadata(retained=True)
    state["fits"]["fit1"]["trials"] = []
    state["fits"]["fit1"]["candidates"] = {}
    seal(state, "checkpoint_id")
    result = build(mandate, state, manifest)
    assert result["fits"][0]["status"] == "running"
    assert result["totals"]["proposals"] == result["totals"]["evaluated"] == 0
    assert result["totals"]["native_evaluations"] == 1
    assert result["totals"]["budget_deferred"] == 0


@pytest.mark.parametrize("damage", ["number", "reused", "params", "basis", "index_status"])
def test_malformed_or_rehashed_receipt_fields_are_not_reported_as_work(damage):
    mandate, state, manifest = json.loads(json.dumps(prepared_metadata(retained=True)))
    trial = state["fits"]["fit1"]["trials"][0]
    key = next(iter(state["evaluations"]))
    if damage == "number":
        trial["number"] = False
    elif damage == "reused":
        trial["reused"] = 0
    elif damage == "params":
        trial["params"]["scanner.target_pct"] = True
    elif damage == "basis":
        state["evaluations"][key]["evaluation_basis"]["evidence_id"] = "9" * 64
    else:
        manifest["plan"]["status"] = "unavailable"
    seal(state, "checkpoint_id")
    seal(manifest)
    with pytest.raises(ValueError):
        build(mandate, state, manifest)


def test_oversized_metadata_is_rejected_before_traversing_history_objects():
    mandate, state, manifest = prepared_metadata()
    state["padding"] = "x" * (32 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="size limit"):
        build(mandate, state, manifest)
