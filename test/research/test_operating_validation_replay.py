"""Exact saved inputs for a derived account; no account engine or price calls."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from research.operating_validation import VERSION
from research.portfolio import execution_versions, normalize
from research.report_contract import fingerprint, settings_identity
from services import research_portfolio as portfolio


def frozen():
    original = normalize(
        {
            "capital": 100000.0,
            "risk_limits": {},
            "strategies": [
                {
                    "id": "scan",
                    "name": "scan",
                    "source_id": "a" * 32,
                    "allocation_pct": 100,
                    "config": {"order_size_pct": 3},
                }
            ],
        }
    )
    settings = deepcopy(original["strategies"])
    for row in settings:
        row["config"].update(initial_capital=200000, order_size_pct=1.5)
    identity = settings_identity(settings)
    native = {
        "strategies": settings,
        "summary": {"initial_capital": 200000, "net_return_pct": 5},
        "portfolio_constraint_evidence": {"status": "supported"},
        "ledger": [],
    }
    receipt = {
        "version": VERSION,
        "outcome": "validated",
        "historically_validated": True,
        "independently_validated": False,
        "selection_changed": False,
        "capital": 200000,
        "selected_config_id": identity,
        "selected_report_id": fingerprint(native),
    }
    receipt["id"] = fingerprint(receipt)
    row = {
        "config_id": identity,
        "trial_number": 999,
        "strategies": deepcopy(settings),
        "summary": deepcopy(native["summary"]),
        "account_capital": 200000,
        "provenance": "automatic_operating_validation",
        "operating_validation_id": receipt["id"],
    }
    report = {
        "strategies": deepcopy(original["strategies"]),
        "automatic_research": {
            "version": "automatic-hypotheses-v16",
            "operating_validation": receipt,
        },
        "experiment": {"rows": [row], "selected_reports": {identity: native}},
    }
    evidence = {
        "portfolio": original,
        "versions": execution_versions(original),
        "strategies": deepcopy(original["strategies"]),
        "signals": [],
        "snapshot": {"sessions": []},
    }
    job = SimpleNamespace(id="b" * 32, status="completed", result_artifact="c" * 64)
    bundle = {"kind": "portfolio_optimize", "inputs_artifact": "d" * 64, "result": report}
    return evidence, report, job, bundle, identity


def wire(monkeypatch, value):
    evidence, _, job, bundle, _ = value
    monkeypatch.setattr(portfolio.service, "get_job", lambda *a: job)
    monkeypatch.setattr(
        portfolio.service,
        "read_artifact",
        lambda _, ref: deepcopy(bundle if ref == job.result_artifact else evidence),
    )
    return job


def reseal(report):
    receipt = report["automatic_research"]["operating_validation"]
    receipt["id"] = fingerprint({k: v for k, v in receipt.items() if k != "id"})
    report["experiment"]["rows"][0]["operating_validation_id"] = receipt["id"]


def test_trusted_derived_capital_precedes_normalization_and_keeps_exact_settings(monkeypatch):
    value = frozen()
    before = deepcopy(value[:2])
    job = wire(monkeypatch, value)
    _, replay = portfolio.replay_inputs(None, "owner", job.id, trial_id=value[-1])
    assert replay["portfolio"]["capital"] == 200000
    assert settings_identity(replay["portfolio"]["strategies"]) == value[-1]
    assert settings_identity(replay["strategies"]) == value[-1]
    assert replay["portfolio"]["strategies"][0]["config"]["order_size_pct"] == 1.5
    assert replay["portfolio"]["risk_limits"] == value[0]["portfolio"]["risk_limits"]
    assert replay["replay_origin"]["account_capital"] == 200000
    assert (
        replay["replay_origin"]["operating_validation_id"]
        == value[1]["automatic_research"]["operating_validation"]["id"]
    )
    assert replay["frozen_prices"] is True
    assert value[:2] == before


@pytest.mark.parametrize("explicit", [False, True])
def test_separate_operating_candidate_replays_without_inventing_an_optuna_trial(
    monkeypatch, explicit
):
    from services.research_candidates import _selected

    value = frozen()
    report = value[1]
    row = report["experiment"]["rows"].pop()
    row["trial_number"] = None
    report["experiment"].update(operating_candidate=row, recommendation_id=value[-1])
    report["strategies"] = deepcopy(row["strategies"])
    report["summary"] = deepcopy(row["summary"])
    selected = _selected(report["experiment"], value[-1], report)
    assert selected["trial_number"] is None
    job = wire(monkeypatch, value)
    _, replay = portfolio.replay_inputs(
        None, "owner", job.id, trial_id=value[-1] if explicit else None
    )
    assert replay["portfolio"]["capital"] == 200000
    assert settings_identity(replay["strategies"]) == value[-1]
    assert report["experiment"]["rows"] == []
    assert replay["replay_origin"]["trial_number"] is None


@pytest.mark.parametrize(
    "key,changed",
    [
        ("outcome", "rejected"),
        ("historically_validated", False),
        ("independently_validated", True),
        ("selection_changed", True),
        ("capital", True),
        ("capital", 300000),
        ("selected_config_id", "f" * 64),
    ],
)
def test_resealed_nonvalidated_or_wrong_account_receipts_cannot_change_capital(
    monkeypatch, key, changed
):
    value = frozen()
    value[1]["automatic_research"]["operating_validation"][key] = changed
    reseal(value[1])
    job = wire(monkeypatch, value)
    with pytest.raises(ValueError, match="validation binding"):
        portfolio.replay_inputs(None, "owner", job.id, trial_id=value[-1])


@pytest.mark.parametrize(
    "where",
    [
        "account_capital",
        "operating_validation_id",
        "native_summary",
        "row_summary",
        "native_settings",
        "missing_report",
    ],
)
def test_exact_report_and_provenance_are_required(monkeypatch, where):
    value = frozen()
    row = value[1]["experiment"]["rows"][0]
    native = value[1]["experiment"]["selected_reports"][value[-1]]
    if where == "native_summary":
        native["summary"]["net_return_pct"] = 999
    elif where == "native_settings":
        native["strategies"][0]["config"]["order_size_pct"] = 99
    elif where == "row_summary":
        row["summary"]["net_return_pct"] = 999
    elif where == "missing_report":
        value[1]["experiment"]["selected_reports"].clear()
    else:
        row[where] = 300000 if where == "account_capital" else "f" * 64
    job = wire(monkeypatch, value)
    with pytest.raises(ValueError, match="operating-point|validation binding"):
        portfolio.replay_inputs(None, "owner", job.id, trial_id=value[-1])


def test_presentation_findings_do_not_change_native_report_identity(monkeypatch):
    value = frozen()
    value[1]["experiment"]["selected_reports"][value[-1]]["automatic_research"] = {
        "decision": {"outcome": "insufficient_evidence"}
    }
    job = wire(monkeypatch, value)
    _, replay = portfolio.replay_inputs(None, "owner", job.id, trial_id=value[-1])
    assert replay["portfolio"]["capital"] == 200000


def test_legacy_row_stays_at_original_capital_and_untrusted_account_field_is_rejected(monkeypatch):
    value = frozen()
    original = value[0]["portfolio"]["strategies"]
    identity = settings_identity(original)
    value[1]["experiment"]["rows"] = [
        {"config_id": identity, "trial_number": 1, "strategies": original}
    ]
    job = wire(monkeypatch, value)
    _, replay = portfolio.replay_inputs(None, "owner", job.id, trial_id=identity)
    assert replay["portfolio"]["capital"] == 100000
    assert settings_identity(replay["portfolio"]["strategies"]) == identity
    value[1]["experiment"]["rows"][0]["account_capital"] = 200000
    with pytest.raises(ValueError, match="provenance"):
        portfolio.replay_inputs(None, "owner", job.id, trial_id=identity)
